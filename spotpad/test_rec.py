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

# --- Mover la vista: _show_in_edit edita el memory location 999 (o lo crea) y lo trae ---
import types
from tc import TcConverter, rate_from_enum
ran = []
class VC:
    exists = False
    def run(s, op):
        n = type(op).__name__; ran.append(n)
        if n == "CId_EditMemoryLocation" and not s.exists: raise RuntimeError("no existe")
        if n == "CId_CreateMemoryLocation": s.exists = True
pv = ProTools(); pv._tc = lambda e: TcConverter(48000, "01:00:00:00", rate_from_enum("STCR_Fps24"))
ev = types.SimpleNamespace(client=VC())
pv._show_in_edit(ev, 48000, 96000); pv._show_in_edit(ev, 0, 48000)
assert ran == ["CId_EditMemoryLocation", "CId_CreateMemoryLocation", "CId_SelectMemoryLocation",
               "CId_EditMemoryLocation", "CId_SelectMemoryLocation"], ran
print("vista OK")

# --- Markers: número libre explícito (sin número, Pro Tools elegía uno usado) ---
class ME:
    def __init__(s): s.used = {1, 2, 3, 4, 999}; s.made = []
    def get_memory_locations(s): return [types.SimpleNamespace(number=n) for n in s.used]
    def create_memory_location(s, memory_number=None, **kw):
        if memory_number in s.used or memory_number == 5:     # el 5 lo ocupó otro justo antes
            raise RuntimeError("ErrType 126: PT_InvalidParameter (Such a memory location number is already used.)")
        s.used.add(memory_number); s.made.append(memory_number)
me = ME()
assert ProTools()._create_marker(me, {"name": "Esc 1"}) == 6 and me.made == [6]
assert ProTools()._create_marker(me, {"name": "Esc 2"}) == 7
print("markers OK")

# --- Siguiente: la selección va al track del clip ---
pm = MockProTools(); rm = RecController(pm); rm.refresh()
rm.set_state({"sweep": ["Henry"], "rec_track": "t3", "cur": "", "text_filter": "", "surface_filter": ""})
rm.step(1)
assert pm.last_go_track == "Henry", getattr(pm, "last_go_track", None)
print("track del clip OK")

# --- Seguir Pro Tools sin haber tocado «Leer sesión»: lee la sesión sola ---
pf = MockProTools(); rf = RecController(pf)
rf.set_state({"follow": True, "rec_track": "t3", "cur": "", "sweep": [], "text_filter": "", "surface_filter": ""})
pf.mock_sel = {"tracks": ["Chairs"], "in": 8 * 48000, "out": 9 * 48000}
it = rf.follow_tick()
assert it and it["name"] == "Prps Chair Sit" and rf.snap, it
print("seguir sin leer OK")

# --- follow_selection real: usa el track con selección de edición (no solo el resaltado) ---
from ptsl import PTSL_pb2 as ptpb2
class FT:
    def __init__(s, name, idx, edit, sel): s.name, s.index = name, idx; s.track_attributes = types.SimpleNamespace(
        has_edit_selection=edit, is_selected=sel)
class FE:
    def transport_state(s): return "TS_TransportStopped"
    def track_list(s, filters=None): return [FT("Chairs", 2, 2, 1), FT("Video", 1, 1, 1)]
class PF(ProTools):
    def _call(s, fn, timeout=None): return fn(FE())
    def _selection_samples(s, e): return (10, 20)
assert PF().follow_selection() == {"tracks": ["Chairs"], "in": 10, "out": 20}
print("seguir selección OK")
