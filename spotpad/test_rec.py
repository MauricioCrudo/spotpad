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
try:
    rec.from_selection(); raise AssertionError("debería pedir la superficie")
except SpotError as e:
    assert "superficie" in str(e)
rec.choice({"key": "Henry|1728000", "surface": "Wood"})
r = rec.from_selection()
assert r["item"]["name"] == "Fts Henry Male Shoes Wood" and "Fts Henry Male Shoes Wood" in r["msg"]
assert pt._tracks[2]["name"] == "Fts Henry Male Shoes Wood"     # el track de grabación quedó renombrado
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
assert it["name"] == "Prps Chair sit" and any(i.get("extra") and i["key"] == it["key"] for i in rec.build()["items"])
assert pt._tracks[2]["name"] == "Prps Chair sit"

# --- Avisos: sesión cerrada → la foto de la sesión se descarta ---
assert rec.snap is not None
EVENTS.push("EId_SessionClosed", {})
assert rec.snap is None and EVENTS.since(0)[-1]["label"] == "Se cerró la sesión"
print("follow/eventos OK")

# --- Color de track: si Pro Tools cuenta desde 1, se detecta y se corrige ---
from bridge import ProTools
class CT:
    def __init__(s, n, c): s.name, s.color = n, c
class PEng:
    def __init__(self, base):
        self.base, self.tcolor = base, ""; eng = self
        self.pal = ["#ff000001", "#ff000002", "#ff000003", "#ff000004"]
        class C:
            def run(_, op):
                n = type(op).__name__
                if n == "CId_GetColorPalette":
                    op.response = type("R", (), {"color_list": eng.pal})()
                elif n == "CId_SetTrackColor":
                    eng.tcolor = eng.pal[op.request.color_index - eng.base]
        self.client = C()
    def track_list(self): return [CT("SNEAKERS 04", self.tcolor)]
for base in (0, 1):
    p2 = ProTools(); e2 = PEng(base)
    p2._paint(e2, ["SNEAKERS 04"], 2)
    assert e2.tcolor == "#ff000003", (base, e2.tcolor)
    assert p2._color_offset == base
print("color OK")
