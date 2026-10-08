"""Actualizador: versión publicada, cuándo hay una nueva y el script de reemplazo (Mac)."""
import io, json, os, sys, tempfile, types
from pathlib import Path
import updater
from updater import Updater, parse_version

assert parse_version("0.10.2") > parse_version("0.9.12") > parse_version("0.9.2")
rel = {"name": "Última versión · SpotPad 0.11.0", "body": "SpotPad 0.11.0 · compilado…",
       "assets": [{"name": updater.asset_name(), "browser_download_url": "https://x/y.zip", "size": 10}]}
class R(io.BytesIO):
    def __enter__(s): return s
    def __exit__(s, *a): pass
updater._get = lambda url, timeout=15: R(json.dumps(rel).encode())
u = Updater("0.10.2"); st = u.check()
assert st["latest"] == "0.11.0" and st["available"], st
assert not Updater("0.11.0").check()["available"]
rel["body"] = "sin versión"; rel["name"] = "Última versión"
assert not Updater("0.10.2").check()["available"]          # sin versión en las notas: no ofrece nada
# Sin app instalada (corriendo desde el código) no instala
r = Updater("0.10.2").install(lambda: None)
assert not r["ok"] and "código" in r["error"], r
# Script de Mac: espera el proceso, cambia la app y la abre
if sys.platform != "win32":
    tmp = Path(tempfile.mkdtemp()); app = tmp / "Apps" / "SpotPad.app"; app.mkdir(parents=True)
    u2 = Updater("0.10.2")
    import subprocess as sp
    def fake_run(cmd, check=True):
        out = Path(cmd[-1]); (out / "SpotPad.app").mkdir(parents=True)
    updater.subprocess = types.SimpleNamespace(run=fake_run)
    updater.sys = types.SimpleNamespace(platform="darwin")
    script = u2._prepare(tmp / "x.zip", tmp, app).read_text()
    assert f'kill -0 {os.getpid()}' in script and f'open "{app}"' in script and "quarantine" in script
print("actualizador OK")
