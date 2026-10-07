"""
SpotPad como app de escritorio (lo que se compila a SpotPad.app / SpotPad.exe).

  * Levanta el bridge en segundo plano.
  * Pone un ícono en la barra de menú (Mac) o en la bandeja junto al reloj (Windows)
    con: dirección para el iPad, QR para conectar, diagnóstico, configuración y salir.

  python3 app.py            # como la app
  python3 app.py --mock     # sin Pro Tools
  python3 app.py --selftest # prueba de arranque (la usa la compilación automática)
"""
import argparse
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import bridge
from bridge import (DATA, LOG_FILE, VERSION as APP_VERSION, MockProTools, ProTools, RecController, build_report,
                    ipad_url, setup_logging, start_server)
import hotkeys as hk

HOTKEYS_FILE = DATA / "hotkeys.json"
log = logging.getLogger("spotpad")


def open_path(path):
    """Abre un archivo o carpeta con la app del sistema."""
    if sys.platform == "win32":
        os.startfile(str(path))          # noqa: S606 (solo Windows)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def copy_to_clipboard(text):
    """Copia al portapapeles (Mac: pbcopy, Windows: clip). Si falla, no pasa nada."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=5)
        elif sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode("utf-16le"), timeout=5)
    except Exception:                   # noqa: BLE001
        pass


def make_icon(size=64):
    """Ícono dibujado (no hace falta archivo): cuadrado ámbar con 4 pads."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 5
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=r, fill=(232, 163, 61, 255))
    pad, gap = size // 10 + 4, size // 12
    cell = (size - 2 * pad - gap) // 2
    for i in range(2):
        for j in range(2):
            x, y = pad + i * (cell + gap), pad + j * (cell + gap)
            d.rounded_rectangle([x, y, x + cell, y + cell], radius=max(2, cell // 5), fill=(18, 18, 20, 255))
    return img


def selftest(port):
    """Arranca en modo mock, pide /api/status y /api/layout, y sale. Exit 0 = OK."""
    import ptsl                      # noqa: F401  (que el SDK haya quedado empaquetado)
    from ptsl import PTSL_pb2        # noqa: F401
    if sys.platform in ("darwin", "win32"):     # que la librería de teclado haya quedado empaquetada
        from pynput import keyboard
        for b in list(hk.DEFAULTS["bindings"].values()) + [f"{hk.MOD}+{n}" for n, p, _l in hk.SCHEMES.values()] \
                + [f"{hk.MOD}+{p}" for n, p, _l in hk.SCHEMES.values()]:
            keyboard.HotKey.parse(b)
    pt = MockProTools()
    stop = start_server(pt, port, RecController(pt))
    try:
        for path in ("/api/status", "/api/layout", "/", "/conectar", "/api/health", "/api/report"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                assert r.status == 200, (path, r.status)
        print(f"selftest OK · datos en {DATA}")
        return 0
    finally:
        stop()


def main():
    ap = argparse.ArgumentParser(description="SpotPad")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a, _ = ap.parse_known_args()     # macOS a veces agrega -psn_… al abrir la app

    setup_logging(a.debug)

    if a.selftest:
        sys.exit(selftest(a.port))

    pt = MockProTools() if a.mock else ProTools(debug=a.debug)
    local = f"http://localhost:{a.port}"
    rec = RecController(pt)          # compartido por el iPad y los atajos de teclado
    try:
        stop = start_server(pt, a.port, rec)
    except OSError as e:
        log.error("No pude abrir el puerto %s: %s", a.port, e)
        # Probablemente ya hay un SpotPad abierto: mostramos su página y salimos
        webbrowser.open(local + "/conectar")
        sys.exit(1)
    log.info("SpotPad %s · iPad: %s · datos: %s", APP_VERSION, ipad_url(a.port), DATA)

    import pystray
    from pystray import Menu, MenuItem as Item

    def make_report(icon, _):
        """Informe completo → archivo en la carpeta de datos, se abre y (Mac) queda copiado."""
        def work():
            try:
                txt = build_report(pt)
            except Exception as e:      # noqa: BLE001
                txt = f"No se pudo armar el informe: {e}"
            f = DATA / f"informe-{time.strftime('%Y%m%d-%H%M%S')}.txt"
            f.write_text(txt, "utf-8")
            copy_to_clipboard(txt)
            open_path(f)
            try:
                icon.notify("Informe listo (también copiado). Pegalo en la conversación con Claude.", "SpotPad")
            except Exception:           # noqa: BLE001
                pass
        threading.Thread(target=work, daemon=True).start()

    def do_reconnect(icon, _):
        def work():
            r = pt.reconnect()
            msg = "Conectado con Pro Tools" if r.get("ok") else f"Sin conexión: {r.get('error', '?')}"
            log.info("Reconectar (menú): %s", msg)
            try:
                icon.notify(msg, "SpotPad")
            except Exception:           # noqa: BLE001
                pass
        threading.Thread(target=work, daemon=True).start()

    # Atajos de teclado globales (funcionan con Pro Tools en primer plano)
    keys = hk.Hotkeys(rec, HOTKEYS_FILE, icon_ref=lambda: icon)

    def keys_restart(icon, _):
        ok = keys.start()
        hk.notify("SpotPad", "Atajos activos" if ok else f"Atajos: {keys.error}", icon)
        icon.update_menu()

    def set_scheme(icon, key):
        hk.set_scheme(HOTKEYS_FILE, key)
        keys_restart(icon, None)

    def keys_items():
        rows = [Item(f"{k}   {desc}", None, enabled=False) for desc, k in keys.summary()]
        state = "activos" if keys.listener and not keys.error else (keys.error or "inactivos")
        return Menu(
            Item(f"Estado: {state}", None, enabled=False),
            *rows,
            Menu.SEPARATOR,
            Item("Teclas de Siguiente / Anterior", Menu(*[
                Item(lab, (lambda k: lambda icon, _: set_scheme(icon, k))(key),
                     checked=(lambda k: lambda _: hk.scheme_of(keys.cfg) == k)(key), radio=True)
                for key, (_n, _p, lab) in hk.SCHEMES.items()])),
            Item("Reactivar atajos", keys_restart),
            Item("Cambiar teclas (hotkeys.json)", lambda *_: open_path(HOTKEYS_FILE)),
            *([Item("Permiso de Accesibilidad…", lambda *_: hk.open_accessibility_settings())]
              if sys.platform == "darwin" else []),
        )

    def quit_app(icon, _):
        log.info("Saliendo")
        keys.stop()
        try:
            stop()
            icon.stop()
        finally:
            # Salida inmediata: si Pro Tools dejó un pedido colgado, no esperamos por él
            threading.Timer(0.3, lambda: os._exit(0)).start()

    menu = Menu(
        Item(lambda _: f"iPad: {ipad_url(a.port)}", lambda *_: webbrowser.open(local + "/conectar")),
        Item("Conectar iPad (QR)", lambda *_: webbrowser.open(local + "/conectar"), default=True),
        Item("Abrir SpotPad acá", lambda *_: webbrowser.open(local)),
        Menu.SEPARATOR,
        Item("Atajos de teclado", Menu(lambda: keys_items().items)),
        Item("Reconectar con Pro Tools", do_reconnect),
        Item("Generar informe para soporte", make_report),
        Item("Ver registro", lambda *_: open_path(LOG_FILE)),
        Item("Carpeta de configuración", lambda *_: open_path(DATA)),
        Menu.SEPARATOR,
        Item(f"Salir (v{APP_VERSION})", quit_app),
    )
    icon = pystray.Icon("SpotPad", make_icon(), "SpotPad" + (" · MOCK" if a.mock else ""), menu)

    # La primera vez, mostrar cómo conectar el iPad
    first = DATA / ".first_run_done"
    if not first.exists():
        first.write_text("1")
        threading.Timer(1.0, lambda: webbrowser.open(local + "/conectar")).start()

    def setup(icon):
        icon.visible = True
        keys.start()
        icon.update_menu()

    icon.run(setup=setup)


if __name__ == "__main__":
    main()
