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
