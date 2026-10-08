"""
Actualizador: busca la versión nueva en GitHub («Última versión»), la baja y reemplaza la app.

- La versión publicada sale de las notas de la release («SpotPad 0.10.3»), que escribe GitHub Actions.
- Mac: baja el zip de su procesador, lo descomprime con ditto y un script espera a que SpotPad se
  cierre, cambia SpotPad.app por la nueva y la abre. Bajado así no tiene la marca de «descargado de
  internet», así que macOS no pide clic derecho → Abrir.
- Windows: SpotPad.exe es un solo archivo; un .cmd espera a que se cierre, lo reemplaza y lo abre.
Los datos (presets, proyectos, reglas) viven en la carpeta de configuración y no se tocan.
"""
import json
import logging
import os
import platform
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("spotpad")

REPO = "MauricioCrudo/spotpad"
API = f"https://api.github.com/repos/{REPO}/releases/tags/ultima"
PAGE = f"https://github.com/{REPO}/releases/tag/ultima"


def parse_version(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def asset_name() -> str:
    if sys.platform == "darwin":
        return "SpotPad-Mac-AppleSilicon.zip" if platform.machine() == "arm64" else "SpotPad-Mac-Intel.zip"
    return "SpotPad-Windows.zip"


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:                            # noqa: BLE001
        return ssl.create_default_context()


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "SpotPad", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx())


def installed_app():
    """Dónde está la app que está corriendo (None si se corre desde el código)."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for p in exe.parents:
            if p.suffix == ".app":
                return p
        return None
    return exe


class Updater:
    def __init__(self, current: str):
        self.current = current
        self.latest = None          # {"version", "url", "size", "notes"}
        self.state = "idle"         # idle | checking | downloading | installing | error
        self.progress = 0.0
        self.error = ""
        self.checked_at = 0.0
        self.lock = threading.Lock()

    # -- buscar -------------------------------------------------------------- #
    def check(self):
        self.state, self.error = "checking", ""
        try:
            with _get(API) as r:
                rel = json.load(r)
            m = re.search(r"SpotPad (\d+\.\d+\.\d+)", (rel.get("body") or "") + " " + (rel.get("name") or ""))
            asset = next((a for a in rel.get("assets", []) if a.get("name") == asset_name()), None)
            self.latest = {"version": m.group(1) if m else "", "url": asset and asset["browser_download_url"],
                           "size": asset and asset.get("size"), "published": rel.get("published_at", "")}
            self.state = "idle"
        except Exception as e:                   # noqa: BLE001  (sin internet, GitHub caído…)
            self.state, self.error = "error", f"No pude consultar GitHub: {e}"
            log.info("Actualizar: %s", self.error)
        self.checked_at = time.time()
        return self.status()

    def available(self) -> bool:
        lv = (self.latest or {}).get("version")
        return bool(lv and self.latest.get("url") and parse_version(lv) > parse_version(self.current))

    def status(self):
        return {"current": self.current, "latest": (self.latest or {}).get("version", ""),
                "available": self.available(), "state": self.state, "progress": round(self.progress, 3),
                "error": self.error, "can_install": installed_app() is not None, "page": PAGE}

    def check_later(self, delay=8):
        """Revisión al abrir la app, sin trabar el arranque."""
        threading.Timer(delay, self.check).start()

    # -- instalar ------------------------------------------------------------ #
    def install(self, on_quit):
        """Baja la versión nueva, deja listo el reemplazo y cierra SpotPad (on_quit). En otro hilo."""
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "error": "Ya estoy actualizando"}
        app = installed_app()
        if app is None:
            self.lock.release()
            return {"ok": False, "error": "Esta copia corre desde el código: actualizá con git pull."}
        if not self.latest or not self.available():
            self.check()
        if not self.available():
            self.lock.release()
            return {"ok": False, "error": self.error or "Ya tenés la última versión"}

        def work():
            try:
                tmp = Path(tempfile.mkdtemp(prefix="spotpad-update-"))
                zpath = tmp / asset_name()
                self.state, self.progress = "downloading", 0.0
                with _get(self.latest["url"], timeout=60) as r, open(zpath, "wb") as f:
                    total = int(r.headers.get("Content-Length") or self.latest.get("size") or 0)
                    done = 0
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk); done += len(chunk)
                        if total:
                            self.progress = done / total
                self.state = "installing"
                script = self._prepare(zpath, tmp, app)
                log.info("Actualizar: %s → %s, reinicio", self.current, self.latest["version"])
                self._launch(script)
                time.sleep(0.5)
                on_quit()
            except Exception as e:               # noqa: BLE001
                log.exception("Actualizar")
                self.state, self.error = "error", f"No se pudo actualizar: {e}"
                self.lock.release()
        threading.Thread(target=work, daemon=True, name="update").start()
        return {"ok": True, "msg": f"Bajando SpotPad {self.latest['version']}…"}

    def _prepare(self, zpath: Path, tmp: Path, app: Path) -> Path:
        pid = os.getpid()
        if sys.platform == "darwin":
            out = tmp / "nueva"
            subprocess.run(["ditto", "-x", "-k", str(zpath), str(out)], check=True)
            new = next(out.glob("*.app"))
            if not os.access(app.parent, os.W_OK):
                raise RuntimeError(f"no tengo permiso para escribir en {app.parent}")
            old = app.with_name(app.stem + " (anterior).app")
            script = tmp / "actualizar.sh"
            script.write_text(f"""#!/bin/sh
# Espera a que se cierre SpotPad, cambia la app y la abre
while kill -0 {pid} 2>/dev/null; do sleep 0.3; done
rm -rf "{old}"
mv "{app}" "{old}" && mv "{new}" "{app}" && rm -rf "{old}" || {{ [ -d "{old}" ] && [ ! -d "{app}" ] && mv "{old}" "{app}"; }}
xattr -dr com.apple.quarantine "{app}" 2>/dev/null
open "{app}"
""", "utf-8")
            script.chmod(0o755)
            return script
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp / "nueva")
        new = next((tmp / "nueva").rglob("*.exe"))
        script = tmp / "actualizar.cmd"
        script.write_text(f"""@echo off
rem Espera a que se cierre SpotPad, cambia el .exe y lo abre
:espera
tasklist /FI "PID eq {pid}" /NH | find " {pid} " >nul && (timeout /t 1 /nobreak >nul & goto espera)
copy /y "{app}" "{app}.anterior" >nul
copy /y "{new}" "{app}" >nul || copy /y "{app}.anterior" "{app}" >nul
del "{app}.anterior" >nul 2>&1
start "" "{app}"
""", "utf-8")
        return script

    def _launch(self, script: Path):
        if sys.platform == "darwin":
            subprocess.Popen(["/bin/sh", str(script)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
