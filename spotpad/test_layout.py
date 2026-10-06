from bridge import build_layout
F = lambda i, n, p="": dict(id=f"f{i}", name=n, index=i, type="TT_BasicFolder", color="", parent_id=p, parent_name="")
T = lambda i, n, p="", pn="": dict(id=f"t{i}", name=n, index=i, type="TT_Audio", color="", parent_id=p, parent_name=pn)
tracks = [T(1, "VIDEO REF"), T(2, "DX REF"), F(3, "Surfaces"), T(4, "Gritty", "f3"), T(5, "Wood", "f3"),
          F(6, "Props"), F(7, "Hands", "f6"), T(8, "Hands Body", "f7"), T(9, "Chairs", "f6"),
          T(10, "PRE"), T(11, "Heels", "", "Footsteps")]   # sin id de carpeta: usa el nombre
L = build_layout(tracks)
assert [t["name"] for t in L["tabs"]] == ["Surfaces", "Props", "Footsteps"], L
props = L["tabs"][1]["groups"]
assert [(g["name"], [t["name"] for t in g["tracks"]]) for g in props] == [("Hands", ["Hands Body"]), ("", ["Chairs"])]
assert [t["name"] for t in build_layout(tracks, ["props"])["tabs"]] == ["Surfaces", "Footsteps"]
# Tracks inactivos (dudas de la IA, tracks ya grabados): no aparecen
off = tracks + [dict(T(12, "IA Dudas", "f3"), inactive=True)]
assert [t["name"] for t in build_layout(off)["tabs"][0]["groups"][0]["tracks"]] == ["Gritty", "Wood"]
assert build_layout(off)["inactive"] == [{"name": "Surfaces", "tracks": [{"id": "t12", "name": "IA Dudas", "color": "", "folder": False}]}]
# Carpeta inactiva: se lista la carpeta, no cada track de adentro
fold = [dict(x, inactive=True) if x["id"] in ("f7", "t8") else x for x in tracks]
Lf = build_layout(fold)
assert Lf["inactive"] == [{"name": "Props", "tracks": [{"id": "f7", "name": "Hands", "color": "", "folder": True}]}], Lf
top = [dict(x, inactive=True) if x["id"] in ("f3", "t4", "t5") else x for x in tracks]
assert build_layout(top)["inactive"][0]["tracks"][0]["name"] == "Surfaces"
assert "Surfaces" not in [t["name"] for t in build_layout(top)["tabs"]]

# is_inactive lee el atributo del SDK (explícito=2, implícito por carpeta=3)
from types import SimpleNamespace as N
from bridge import is_inactive
mk = lambda v: N(track_attributes=N(is_inactive=v))
assert [is_inactive(mk(v)) for v in (0, 1, 2, 3)] == [False, False, True, True]
assert is_inactive(N()) is False
print("layout OK")
