"""Proyecto: personajes con cara, botonera genérica + proyecto, destino de pasos, exportar/importar."""
import io, json, os, pathlib, tempfile, zipfile
os.environ["SPOTPAD_DATA"] = tempfile.mkdtemp()
import bridge
import naming
import proyecto as P

DATA = pathlib.Path(os.environ["SPOTPAD_DATA"])
JPG = b"\xff\xd8\xff\xe0" + b"x" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"y" * 200
rules = naming.DEFAULT_RULES

# ---- Destino de un paso: personaje + calzado → track ----------------------- #
fts = [{"name": "Henry", "color": "#3E9B4F"}, {"name": "EMILY HEELS", "color": ""},
       {"name": "Male Shoes", "color": "#3E9B4F"}, {"name": "Sneakers", "color": "#3B7DD8"},
       {"name": "Varios FEM Shoes 02", "color": ""}]
d = P.fts_destino("Henry", "Male Shoes", fts, rules)            # su calzado (por color) → solo el nombre
assert d == {"track": "Henry", "clip": "Henry", "principal": True}, d
d = P.fts_destino("Henry", "Sneakers", fts, rules)              # cambia de calzado en la escena
assert d["track"] == "Henry" and d["clip"] == "Henry Sneakers", d
assert P.fts_destino("Henry", "", fts, rules)["clip"] == "Henry"
d = P.fts_destino("Emily", "Heels", fts, rules)                  # el calzado sale del nombre del track
assert d == {"track": "EMILY HEELS", "clip": "Emily", "principal": True}, d
d = P.fts_destino("Sonia", "Sneakers", fts, rules)               # secundaria: al track del calzado
assert d == {"track": "Sneakers", "clip": "Sonia", "principal": False}, d
assert P.fts_destino("Extra Left", "Fem Shoes", fts, rules)["track"] == "Varios FEM Shoes 02"
d = P.fts_destino("Hank", "", fts, rules, alias=["Henry"])       # alias del personaje
assert d["track"] == "Henry", d
for args in (("", "Sneakers"), ("Sonia", ""), ("Sonia", "Boots")):
    try:
        P.fts_destino(*args, fts, rules); raise SystemExit(f"debería fallar: {args}")
    except ValueError:
        pass
cs_rules = {**rules, "char_shoes": {"HENRY": "Boots"}}           # calzado fijado gana al color
assert P.fts_destino("Henry", "Boots", fts, cs_rules)["clip"] == "Henry"
assert [c["label"] for c in P.calzados_sesion(fts, rules)] == ["Male Shoes", "Sneakers", "Fem Shoes"]

# ---- Botonera: genérica + proyecto ----------------------------------------- #
gen = [{"id": "hands", "label": "Manos", "color": "#E8A33D", "items": ["Hands body", {"name": "Clap", "track": "HB"}]},
       {"id": "papers", "label": "Papers", "color": "#6FA8DC", "items": ["Paper"]}]
bot = {"categorias": [{"id": "p-armas", "label": "Armas", "color": "#ff0000", "items": ["Gun cock"]}],
       "agregados": {"hands": ["Hands slap"]}, "ocultas": ["papers"], "ocultos": {"hands": ["clap"]}}
todas = P.combinar(gen, bot)
assert [c["scope"] for c in todas] == ["generica", "generica", "proyecto"]
assert todas[1].get("oculta") and todas[0]["items"][1].get("oculto")
assert todas[0]["items"][2] == {"name": "Hands slap", "proyecto": True, "pi": 0}
vis = P.visibles(todas)
assert [c["id"] for c in vis] == ["hands", "p-armas"], vis
assert [i["name"] for i in vis[0]["items"]] == ["Hands body", "Hands slap"]
assert gen[0]["items"][1] == {"name": "Clap", "track": "HB"}       # la genérica no se toca
assert P.combinar(gen, {})[0]["items"][1] == {"name": "Clap", "track": "HB"}
clean = P.limpiar_botonera(bot, bridge.clean_presets, ["hands", "papers"])
assert clean["agregados"] == {"hands": ["Hands slap"]} and clean["categorias"][0]["id"] == "p-armas"
try:
    P.limpiar_botonera({"categorias": [{"id": "hands", "label": "X"}]}, bridge.clean_presets, ["hands"])
    raise SystemExit("id repetido con la genérica")
except ValueError:
    pass

# ---- Personajes y caras ---------------------------------------------------- #
S = P.ProjectStore(DATA / "proyectos")
h = S.guardar_personaje("Serie", {"nombre": "Henry", "alias": "Hank, Henry", "actor": "Juan", "calzado": "Boots"})
assert h["alias"] == ["Hank"] and S.load("Serie")["char_shoes"] == {"HENRY": "Boots"}
try:
    S.guardar_personaje("Serie", {"nombre": "hank"}); raise SystemExit("duplicado por alias")
except ValueError:
    pass
S.guardar_personaje("Serie", {"id": h["id"], "nombre": "Henry Jr"})     # renombrar: el calzado lo sigue
assert S.load("Serie")["char_shoes"] == {"HENRY JR": "Boots"}
S.guardar_personaje("Serie", {"id": h["id"], "nombre": "Henry"})
try:
    S.guardar_cara("Serie", h["id"], b"no es imagen"); raise SystemExit("tipo de imagen")
except ValueError:
    pass
S.guardar_cara("Serie", h["id"], JPG)
assert S.cara("Serie", h["id"]) == (JPG, "image/jpeg")
S.guardar_cara("Serie", h["id"], PNG)                                     # reemplaza (y borra la vieja)
assert S.cara("Serie", h["id"])[1] == "image/png"
assert sorted(f.name for f in (S.dir("Serie") / "caras").iterdir()) == [h["id"] + ".png"]
sonia = S.guardar_personaje("Serie", {"nombre": "Sonia"})
v = P.vista_personajes(S.load("Serie"), fts, rules, secundarios=["Extra Left", "sonia"])
by = {p["nombre"]: p for p in v["personajes"]}
assert by["Henry"]["track"] == "Henry" and by["Henry"]["calzado"] == "Boots"
assert by["Emily"]["nuevo"] and by["Emily"]["track"] == "EMILY HEELS"     # de la sesión, sin guardar
assert by["Sonia"]["track"] == "" and v["secundarios"] == ["Extra Left"]
assert P.cara_de(S.load("Serie"), "HANK")["id"] == h["id"] and P.cara_de(S.load("Serie"), "Sonia") is None

# ---- Exportar / importar un proyecto --------------------------------------- #
S.set_botonera("Serie", clean)
paq = P.exportar_proyecto(S, "Serie", [{"track": "Props 1", "name": "Remote"}], "9.9.9")
otra = P.ProjectStore(pathlib.Path(tempfile.mkdtemp()))               # «otra computadora»
nombre, items = P.importar_proyecto(otra, P.leer_paquete(paq))
assert nombre == "Serie" and items == [{"track": "Props 1", "name": "Remote"}]
d2 = otra.load("Serie")
assert [p["nombre"] for p in d2["personajes"]] == ["Henry", "Sonia"]
assert otra.cara("Serie", h["id"])[0] == PNG and d2["botonera"] == clean and d2["char_shoes"] == {"HENRY": "Boots"}

# mezclar: lo propio gana, se suma lo que falta (incluida la cara que no tenía)
local = P.ProjectStore(pathlib.Path(tempfile.mkdtemp()))
mh = local.guardar_personaje("Serie", {"nombre": "HENRY", "notas": "mías", "calzado": "Sneakers"})
local.guardar_personaje("Serie", {"nombre": "Pedro"})
local.set_botonera("Serie", {"categorias": [], "agregados": {"hands": ["Hands rub"]}, "ocultas": [], "ocultos": {}})
P.importar_proyecto(local, P.leer_paquete(paq), "mezclar")
d3 = local.load("Serie")
assert [p["nombre"] for p in d3["personajes"]] == ["HENRY", "Pedro", "Sonia"], d3["personajes"]
yo = d3["personajes"][0]
assert yo["id"] == mh["id"] and yo["notas"] == "mías" and yo["actor"] == "Juan" and yo["alias"] == ["Hank"]
assert local.cara("Serie", mh["id"])[0] == PNG
assert d3["char_shoes"]["HENRY"] == "Sneakers"
assert d3["botonera"]["agregados"]["hands"] == ["Hands rub", "Hands slap"] and d3["botonera"]["ocultas"] == ["papers"]
P.importar_proyecto(local, P.leer_paquete(paq), "mezclar")               # dos veces: no duplica
assert len(local.load("Serie")["personajes"]) == 3
P.importar_proyecto(local, P.leer_paquete(paq), "reemplazar")
assert [p["nombre"] for p in local.load("Serie")["personajes"]] == ["Henry", "Sonia"]

# un zip con rutas raras: lo desconocido no se lee
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("manifest.json", json.dumps({"tipo": "proyecto", "formato": 1, "proyecto": "X"}))
    z.writestr("proyecto.json", json.dumps({"personajes": [{"id": "../../x", "nombre": "A", "cara": "../../x.jpg"}]}))
    z.writestr("caras/../../evil.jpg", JPG)
    z.writestr("../fuera.json", "{}")
pk = P.leer_paquete(buf.getvalue())
assert set(pk["files"]) == {"manifest.json", "proyecto.json"}, pk["files"].keys()
evil = P.ProjectStore(pathlib.Path(tempfile.mkdtemp()))
P.importar_proyecto(evil, pk)
pe = evil.load("X")["personajes"][0]
assert pe["id"] == "x" and "cara" not in pe
for bad in (b"hola", P._zip({"manifest.json": b'{"tipo":"otro"}'}),
            P._zip({"manifest.json": b'{"tipo":"proyecto","formato":99}'})):
    try:
        P.leer_paquete(bad); raise SystemExit("debería rechazar")
    except ValueError:
        pass

# ---- Genérica ------------------------------------------------------------- #
src, dst = pathlib.Path(tempfile.mkdtemp()), pathlib.Path(tempfile.mkdtemp())
(src / "presets.json").write_text(json.dumps({"categories": gen}))
(src / "hotkeys.json").write_text('{"bindings": {}}')
(src / "targets.json").write_text('["t1"]')                               # de la sesión: no viaja
(dst / "presets.json").write_text('{"categories": []}')
(dst / "hotkeys.json").write_text('{"mio": 1}')
pg = P.leer_paquete(P.exportar_generica(src, "1", "darwin"))
assert pg["tipo"] == "generica" and "generica/targets.json" not in pg["files"]
assert P.importar_generica(pg, dst, "win32") == ["presets.json"]          # atajos de Mac no van a Windows
assert json.loads((dst / "presets.json").read_text())["categories"] == gen
assert json.loads((dst / "hotkeys.json").read_text()) == {"mio": 1}
bk = [p for p in dst.iterdir() if p.name.startswith("respaldo-")]
assert bk and json.loads((bk[0] / "presets.json").read_text()) == {"categories": []}
assert P.importar_generica(pg, pathlib.Path(tempfile.mkdtemp()), "darwin") == ["hotkeys.json", "presets.json"]

# ---- Bridge: calzado fijo y botonera según el proyecto de la sesión -------- #
bridge.CURRENT["session"] = "Serie_EP02_Foley"                            # proyecto «Serie»
bridge.STORE.set_char_shoe("Serie", "June", "Fem Shoes")
assert bridge.load_rules()["char_shoes"]["JUNE"] == "Fem Shoes"
bridge.PRESETS_FILE.write_text(json.dumps({"categories": gen}))
bridge.STORE.set_botonera("Serie", clean)
assert bridge.preset_prop("Gun cock") and bridge.preset_prop("Hands slap") and not bridge.preset_prop("Paper")
bridge.CURRENT["session"] = "Otra_Peli_R1"
assert "JUNE" not in bridge.load_rules()["char_shoes"] and bridge.preset_prop("Paper")
bridge.PROJECTS.merge("Serie", [{"track": "Props 1", "name": "Remote"}, {"track": "Props 1", "name": "remote"}])
assert bridge.PROJECTS.items("Serie") == [{"track": "Props 1", "name": "Remote", "uses": 1, "last": 0}]
print("personajes OK")
