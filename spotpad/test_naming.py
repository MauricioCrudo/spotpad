"""Reglas de nombres con los tracks reales de la sesión BAES_209 (informe del 5/10)."""
import copy
from edl import EdlEvent
from naming import DEFAULT_RULES as R, classify_fts, shoe_colors, build_queue, base_name

FTS = [("JUNE", "#ff13355f"), ("KYLE", "#ff27c1fd"), ("MOIRA", "#ffbe00c0"), ("EMILY HEELS", "#ffbf00fc"),
       ("SAM", "#ff471c0b"), ("BOOTS 01", "#ff471c0b"), ("MALE Shoes 01", "#ff18800c"), ("HEELS", "#ffbf00fc"),
       ("FEM Shoes 01", "#ffbe00c0"), ("Varios FEM Shoes 04", "#ffbe00c0"), ("EMILY", "#ff27c1fd"),
       ("SNEAKERS 01", "#ff27c1fd"), ("Varios SLIPERS", "#ffc0c514"), ("BAREFOOT 01", "#ffbe8911"),
       ("Varios OTHER shoes", "#ff31005f"), ("Group 01", "#ff13355f"), ("ANIMALS", "#ff13355f"),
       ("EMILY SLIPERS", "#ffc0c514"), ("MEADOWS", "#ff2d800c")]
SURF = [("Clean Concrete Interior", "#ff7c0089"), ("Gritty Concrete Street", "#ffbd520e"), ("Hardwood", "#ff7b510d")]
PRPS = [("Hands Surfaces", "#ffbd0088"), ("Chairs", "#ff20c488")]
tracks = ([{"name": n, "color": c, "folder": "FTS"} for n, c in FTS] +
          [{"name": n, "color": c, "folder": "SUPERFICIES"} for n, c in SURF] +
          [{"name": n, "color": c, "folder": "PRPS"} for n, c in PRPS])

# Clasificación
assert base_name("Varios FEM Shoes 04") == "FEM SHOES"
assert classify_fts("Varios FEM Shoes 04", R) == {"type": "shoe", "character": None, "shoe": "Fem Shoes"}
assert classify_fts("SNEAKERS 01", R)["type"] == "shoe"
assert classify_fts("EMILY HEELS", R) == {"type": "char", "character": "EMILY", "shoe": "Heels"}
assert classify_fts("JUNE", R) == {"type": "char", "character": "JUNE", "shoe": None}
assert classify_fts("Group 01", R)["shoe"] == "Group"

# Color → calzado: sale de los tracks de calzado; Group/Animals no cuentan
col = shoe_colors([t for t in tracks if t["folder"] == "FTS"], R)
assert col["#ff27c1fd"] == "Sneakers" and col["#ffbe00c0"] == "Fem Shoes" and col["#ff471c0b"] == "Boots"
assert "#ff13355f" not in col          # JUNE: color de Group/Animals → no se adivina

E = lambda t, c, a, b, m=False: EdlEvent(t, c, a, b, m)
edl = {
    "Hardwood": [E("Hardwood", "Hardwood", 0, 1000)],
    "Clean Concrete Interior": [E("Clean Concrete Interior", "Clean Concrete Interior", 800, 2000)],
    "KYLE": [E("KYLE", "KYLE", 100, 200), E("KYLE", "Kyle Barefoot", 300, 400), E("KYLE", "KYLE", 900, 950, True)],
    "SNEAKERS 01": [E("SNEAKERS 01", "Extra Left", 1500, 1600), E("SNEAKERS 01", "SNEAKERS 01", 1700, 1800)],
    "JUNE": [E("JUNE", "JUNE", 100, 150)],
    "Chairs": [E("Chairs", "Chair sit", 50, 60)],
}
q = build_queue(tracks, edl, ["KYLE", "SNEAKERS 01", "JUNE", "Chairs"], R, {}, set())
by = {(i["track"], i["start"]): i for i in q["items"]}
assert by[("KYLE", 100)]["name"] == "Fts Kyle Sneakers Hardwood", by[("KYLE", 100)]
assert by[("KYLE", 100)]["shoe_src"] == "color"
assert by[("KYLE", 300)]["name"] == "Fts Kyle Barefoot Hardwood"          # el clip trae el calzado
k9 = by[("KYLE", 900)]
assert k9["muted"] and k9["needs"] == ["surface"] and set(k9["surfaces"]) == {"Hardwood", "Clean Concrete Interior"}
assert k9["name"] == "Fts Kyle Sneakers"                                 # sin superficie hasta elegir
assert by[("SNEAKERS 01", 1500)]["name"] == "Fts Extra Left Sneakers Clean Concrete Interior"
assert by[("SNEAKERS 01", 1700)]["name"] == "Fts Sneakers Clean Concrete Interior"   # clip genérico: sin personaje
assert by[("JUNE", 100)]["needs"] == ["shoe"] and by[("JUNE", 100)]["name"] == "Fts June Hardwood"
assert by[("Chairs", 50)]["name"] == "Prps Chair Sit"

# Elecciones guardadas + alias de superficie + calzado fijo del personaje
r2 = copy.deepcopy(R); r2["surface_alias"] = {"Clean Concrete Interior": "Clean"}; r2["char_shoes"] = {"JUNE": "Fem Shoes"}
q2 = build_queue(tracks, edl, ["KYLE", "JUNE"], r2, {"KYLE|900": {"surface": "Clean Concrete Interior"}}, {"KYLE|100"})
by2 = {(i["track"], i["start"]): i for i in q2["items"]}
assert by2[("KYLE", 900)]["name"] == "Fts Kyle Sneakers Clean" and by2[("KYLE", 900)]["needs"] == []
assert by2[("JUNE", 100)]["name"] == "Fts June Fem Shoes Hardwood" and by2[("JUNE", 100)]["shoe_src"] == "personaje"
assert by2[("KYLE", 100)]["done"]

# Filtro por superficie: solo los pasos sobre Hardwood
q3 = build_queue(tracks, edl, ["KYLE", "SNEAKERS 01"], R, {}, set(), surface_filter="Hardwood")
assert {(i["track"], i["start"]) for i in q3["items"]} == {("KYLE", 100), ("KYLE", 300), ("KYLE", 900)}

# Nombre editado a mano gana
q4 = build_queue(tracks, edl, ["Chairs"], R, {"Chairs|50": {"name": "Prps Chair Sit Wood"}}, set())
assert q4["items"][0]["name"] == "Prps Chair Sit Wood"
print("naming OK")

# --- Formato del nombre de grabación: palabras con mayúscula inicial y sin sufijos de Pro Tools ---
from naming import tidy, strip_suffix, build_queue, DEFAULT_RULES
from edl import EdlEvent
assert tidy("PRPS hands TABLE wood.grp.01") == "Prps Hands Table Wood"
assert tidy("Prps Chair sit.01") == "Prps Chair Sit"
assert tidy("Fts Henry.grp.3 Male Shoes Grass") == "Fts Henry Male Shoes Grass"
assert strip_suffix("Sneakers.grp.01") == "Sneakers"
tr = [{"name": "Chairs", "color": "", "folder": "PRPS"}, {"name": "Grass", "color": "", "folder": "SUPERFICIES"},
      {"name": "SNEAKERS", "color": "", "folder": "FTS"}]
edl = {"Chairs": [EdlEvent("Chairs", "chair SIT.grp.01", 0, 10)],
       "Grass": [EdlEvent("Grass", "Grass.grp.02", 0, 100)],
       "SNEAKERS": [EdlEvent("SNEAKERS", "Sneakers.grp.04", 20, 30), EdlEvent("SNEAKERS", "extra LEFT.grp.1", 40, 50)]}
q = build_queue(tr, edl, ["Chairs", "SNEAKERS"], DEFAULT_RULES, {}, set())
names = [i["name"] for i in q["items"]]
assert names == ["Prps Chair Sit", "Fts Sneakers Grass", "Fts Extra Left Sneakers Grass"], names
print("formato OK")
