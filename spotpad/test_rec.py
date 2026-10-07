"""Modo grabación: siguiente/anterior/grabado y renombrar desde la selección (con Pro Tools simulado)."""
import os, tempfile
os.environ["SPOTPAD_DATA"] = tempfile.mkdtemp()
from bridge import MockProTools, RecController, SpotError

pt = MockProTools(); rec = RecController(pt)
try:
    rec.from_selection(); raise AssertionError("sin track de grabación debería avisar")
except SpotError as e:
    assert "track de grabación" in str(e)
rec.refresh()
rec.set_state({"sweep": ["Henry", "Sneakers"], "rec_track": "t3"})
names = [rec.step(1)["item"]["name"] for _ in range(3)]
assert names == ["Fts Henry Male Shoes Wood", "Fts Henry Male Shoes Wood", "Fts Henry Male Shoes"], names
r = rec.step(1)                                     # el 3º pedía superficie: igual se puede seguir
assert r["item"]["name"] == "Fts Henry Sneakers Concrete Clean"
r = rec.done_next()                                 # marca el actual y pasa al siguiente
assert r["item"]["name"] == "Fts Extra Left Sneakers Concrete Clean"
assert rec.step(-1)["item"]["start"] == 36 * 48000  # el de 50 s quedó grabado: lo saltea
r = rec.from_selection()                              # falta la superficie: devuelve las opciones
assert r["needs"] == ["surface"] and r["options"]["surface"] == ["Concrete Clean", "Wood"], r
assert "superficie" in r["msg"]
r = rec.choose(r["item"]["key"], {"surface": "Wood"})  # lo que elige el diálogo del atajo
assert pt._tracks[2]["name"] == "Fts Henry Male Shoes Wood", pt._tracks
r = rec.from_selection()
assert r["item"]["name"] == "Fts Henry Male Shoes Wood" and "Fts Henry Male Shoes Wood" in r["msg"]
assert pt._tracks[2]["name"] == "Fts Henry Male Shoes Wood"     # el track de grabación quedó renombrado
# Atajo: el diálogo elige y renombra (Hotkeys.resolve_needs)
import hotkeys
hk = hotkeys.Hotkeys(rec, __import__("pathlib").Path(os.environ["SPOTPAD_DATA"]) / "hk.json")
rec.choice({"key": "Henry|1728000", "surface": ""})
hotkeys.ask_choice = lambda prompt, opts, timeout=120: "Concrete Clean"
r = hk.resolve_needs(rec.from_selection())
assert r["item"]["name"] == "Fts Henry Male Shoes Concrete Clean" and pt._tracks[2]["name"] == r["item"]["name"], r
rec.choice({"key": "Henry|1728000", "surface": "Wood"})
print("rec OK")

# --- Seguir Pro Tools: clic en un clip → clip actual + track de grabación renombrado ---
from bridge import EVENTS
pt.mock_sel = {"tracks": ["Henry"], "in": 12 * 48000, "out": 15 * 48000}
assert rec.follow_tick() is None                    # «Seguir» apagado: no hace nada
rec.set_state({"follow": True})
it = rec.follow_tick()
assert it and it["start"] == 12 * 48000 and rec.store()["cur"] == it["key"]
assert pt._tracks[2]["name"] == "Fts Henry Male Shoes Wood"
assert rec.follow_tick() is None                    # misma selección: no repite
pt.mock_sel = {"tracks": ["Chairs"], "in": 8 * 48000, "out": 9 * 48000}   # track que no se barre
it = rec.follow_tick()
assert it["name"] == "Prps Chair Sit" and any(i.get("extra") and i["key"] == it["key"] for i in rec.build()["items"])
assert pt._tracks[2]["name"] == "Prps Chair Sit"

# --- Avisos: sesión cerrada → la foto de la sesión se descarta ---
assert rec.snap is not None
EVENTS.push("EId_SessionClosed", {})
assert rec.snap is None and EVENTS.since(0)[-1]["label"] == "Se cerró la sesión"
print("follow/eventos OK")

# --- Color de track: si Pro Tools cuenta desde 1, se detecta y se corrige ---
from bridge import ProTools
# --- Nunca usar GetColorPalette (cuelga el SDK de Pro Tools 2025.12) ---
import re, pathlib
src = pathlib.Path(__file__).with_name("bridge.py").read_text("utf-8")
code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
assert "GetColorPalette" not in code and "SetTrackColor" not in code
print("sin paleta OK")

# --- Filtro por palabra: primero una parte de la categoría (p. ej. «metal»), después el resto ---
rec = RecController(MockProTools()); rec.refresh()
rec.set_state({"sweep": ["Chairs", "Hands Surfaces"], "text_filter": "drag", "surface_filter": "", "cur": ""})
got = [i["name"] for i in rec.build()["items"]]
assert got == ["Prps Chair Drag"], got
rec.set_state({"text_filter": ""})
assert len(rec.build()["items"]) == 3
print("filtro texto OK")
