"""Botones preseteados con track destino, validación del editor y migración."""
import json, os, tempfile
os.environ["SPOTPAD_DATA"] = tempfile.mkdtemp()
import bridge
from bridge import item_name, item_track, clean_presets

cat = {"track": "Chairs", "items": ["Chair sit", {"name": "Chair drag", "track": "Wood"}]}
assert item_name(cat["items"][0]) == "Chair sit" and item_track(cat["items"][0], cat) == "Chairs"
assert item_track(cat["items"][1], cat) == "Wood"

c = clean_presets({"categories": [{"label": " Sillas ", "items": [" Chair sit ", {"name": "", "track": "x"},
                                                                 {"name": "Chair drag", "track": "Wood"}]},
                                  {"label": ""}]})
assert c == {"categories": [{"id": "cat1", "label": "Sillas", "color": "#E8A33D",
                             "items": ["Chair sit", {"name": "Chair drag", "track": "Wood"}]}]}, c
try:
    clean_presets({"categories": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]})
    raise AssertionError("ids repetidos")
except ValueError:
    pass

# Migración: Manos viejo (sin tracks) → con Hands Body / Hands Surfaces
old = {"track_prefixes": ["Prps", "Fts"], "categories": [{"id": "hands", "label": "Manos", "color": "#E8A33D",
       "items": ["Hands body", "Hands surface wood", "Mi botón propio"]}]}
bridge.PRESETS_FILE.write_text(json.dumps(old), "utf-8")
bridge.migrate_presets()
new = json.loads(bridge.PRESETS_FILE.read_text("utf-8"))["categories"][0]["items"]
assert new == [{"name": "Hands body", "track": "Hands Body"},
               {"name": "Hands surface wood", "track": "Hands Surfaces"}, "Mi botón propio"], new
bridge.migrate_presets()                                   # no vuelve a tocar nada
assert json.loads(bridge.PRESETS_FILE.read_text("utf-8"))["categories"][0]["items"] == new
print("presets OK")

# --- Categorías de fábrica nuevas: se suman una vez a instalaciones viejas y no vuelven si las borrás ---
import json, os, tempfile, importlib
os.environ["SPOTPAD_DATA"] = tempfile.mkdtemp()
import bridge
importlib.reload(bridge)
old = {"track_prefixes": ["Prps", "Fts"], "categories": [{"id": "hands", "label": "Manos", "color": "#E8A33D",
       "items": [{"name": "Hands clap", "track": "Hands Body"}]}]}
bridge.PRESETS_FILE.write_text(json.dumps(old), "utf-8")
bridge.migrate_presets()
u = json.loads(bridge.PRESETS_FILE.read_text("utf-8"))
assert [c["id"] for c in u["categories"]] == ["hands", "papers"], u
pap = u["categories"][1]
assert pap["track"] == "Papers" and "Writing pencil" in pap["items"] and u["categories"][0]["items"] == old["categories"][0]["items"]
u["categories"] = u["categories"][:1]                     # la borra desde el iPad
bridge.PRESETS_FILE.write_text(json.dumps(u), "utf-8")
bridge.migrate_presets()
assert [c["id"] for c in json.loads(bridge.PRESETS_FILE.read_text("utf-8"))["categories"]] == ["hands"]
print("categorías de fábrica OK")
