"""
Nombres para el track de grabación, armados con lo que ya está marcado en el spotting.

  Pasos:  Fts + personaje + calzado + superficie   →  «Fts Henry Male Shoes Grass»
  Props:  Prps + nombre del clip group             →  «Prps Hands Surface Wood»

Reglas (configurables en footsteps.json, carpeta de datos):
  * Carpetas: cuál es la de superficies, la de pasos y la de props (por nombre).
  * En la carpeta de pasos conviven tracks de PERSONAJE (JUNE, EMILY…) y de CALZADO
    (MALE Shoes 01, SNEAKERS 02, Varios BAREFOOT…). Un track es de calzado si su nombre,
    sin «Varios» ni el número final, es un calzado conocido. «EMILY HEELS» = personaje
    EMILY con calzado Heels.
  * Track de personaje: el calzado sale del nombre del clip si lo trae («Henry Sneakers»),
    si no del calzado fijado para ese personaje, si no del COLOR del track (el mismo color
    que el track de calzado correspondiente).
  * Track de calzado: el personaje es el nombre del clip group (Sonia, Extra Left).
  * Superficie: el clip de la carpeta de superficies que está debajo del paso. Si hay más
    de una, hay que elegir (choque) y la elección se guarda.
"""
import re
from typing import Dict, List, Optional

DEFAULT_RULES = {
    "folders": {
        "surfaces": ["SUPERFICIES", "SURFACES", "SUPERFICIE"],
        "footsteps": ["FTS", "FOOTSTEPS", "PASOS"],
        "props": ["PRPS", "PROPS"],
    },
    # key: cómo aparece en el nombre del track (sin mayúsculas/minúsculas) · label: cómo va en el nombre
    # color: si sirve para deducir el calzado de un personaje por el color del track
    "shoes": [
        {"key": "MALE SHOES", "label": "Male Shoes", "color": True},
        {"key": "FEM SHOES", "label": "Fem Shoes", "color": True},
        {"key": "FEMALE SHOES", "label": "Fem Shoes", "color": True},
        {"key": "FEMME SHOES", "label": "Fem Shoes", "color": True},
        {"key": "SNEAKERS", "label": "Sneakers", "color": True},
        {"key": "BOOTS", "label": "Boots", "color": True},
        {"key": "HEELS", "label": "Heels", "color": True},
        {"key": "SLIPERS", "label": "Slippers", "color": True},
        {"key": "SLIPPERS", "label": "Slippers", "color": True},
        {"key": "BAREFOOT", "label": "Barefoot", "color": True},
        {"key": "OTHER SHOES", "label": "Other Shoes", "color": False},
        {"key": "GROUP", "label": "Group", "color": False},
        {"key": "ANIMALS", "label": "Animals", "color": False},
    ],
    "color_shoes": {},     # "#ff13355f": "Male Shoes"   (corrige lo deducido por color)
    "char_shoes": {},      # "JUNE": "Fem Shoes"         (calzado fijo de un personaje)
    "surface_alias": {},   # "Gritty Concrete Street": "Gritty"
    "title_case_caps": True,   # «JUNE» → «June» en el nombre
}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().upper()


def base_name(track: str) -> str:
    """«Varios FEM Shoes 04» → «FEM SHOES»."""
    n = norm(track)
    n = re.sub(r"^VARIOS\s+", "", n)
    n = re.sub(r"(\s+\d+)+$", "", n)
    n = re.sub(r"\.DUP\d+$", "", n)
    return n.strip()


def nice(s: str, rules) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if rules.get("title_case_caps", True) and s and s.upper() == s and any(c.isalpha() for c in s):
        return " ".join(w.capitalize() for w in s.split(" "))
    return s


def strip_suffix(s: str) -> str:
    """Saca lo que Pro Tools agrega después de un punto: «Hands body.grp.01» → «Hands body»
    (en cada palabra, por si el clip quedó en el medio del nombre)."""
    words = [w.split(".")[0] for w in re.sub(r"\s+", " ", str(s or "")).strip().split(" ")]
    return " ".join(w for w in words if w and w.lower() != "ref")     # «REF» marca el clip, no va al nombre


def has_ref(clip: str) -> bool:
    """¿El clip de spotting está marcado como REF (al principio o al final)?"""
    return any(w.split(".")[0].lower() == "ref" for w in str(clip or "").split())


def tidy(name: str) -> str:
    """Nombre final del track de grabación: sin sufijos de Pro Tools y cada palabra con la
    primera en mayúscula y el resto en minúscula («PRPS hands TABLE wood.grp.01» → «Prps Hands Table Wood»)."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in strip_suffix(name).split(" ") if w)


def _shoes(rules):
    return sorted(rules.get("shoes", []), key=lambda x: -len(x["key"]))


def shoe_in(text: str, rules) -> Optional[str]:
    """Calzado mencionado en un texto («Henry Sneakers» → «Sneakers»)."""
    n = f" {norm(text)} "
    for sh in _shoes(rules):
        if f" {norm(sh['key'])} " in n:
            return sh["label"]
    return None


def folder_kind(folder: str, rules) -> Optional[str]:
    f = norm(folder)
    for kind, names in rules.get("folders", {}).items():
        if f in {norm(x) for x in names}:
            return kind
    return None


def classify_fts(track: str, rules) -> dict:
    """Track de la carpeta de pasos → {"type": "shoe"|"char", "character", "shoe"}."""
    b = base_name(track)
    for sh in _shoes(rules):
        k = norm(sh["key"])
        if b == k:
            return {"type": "shoe", "character": None, "shoe": sh["label"]}
    for sh in _shoes(rules):
        k = norm(sh["key"])
        if b.endswith(" " + k):
            words = re.sub(r"\s+", " ", track).strip().split(" ")
            char = " ".join(words[: len(words) - len(k.split(" "))])
            return {"type": "char", "character": char, "shoe": sh["label"]}
    return {"type": "char", "character": re.sub(r"\s+", " ", track).strip(), "shoe": None}


def shoe_colors(fts_tracks: List[dict], rules) -> Dict[str, str]:
    """Color → calzado, sacado de los tracks de calzado de la sesión (+ correcciones).
    Si un color está en dos calzados distintos, no se usa (ambiguo)."""
    by_color: Dict[str, set] = {}
    usable = {sh["label"] for sh in rules.get("shoes", []) if sh.get("color", True)}
    for t in fts_tracks:
        c = classify_fts(t["name"], rules)
        if c["type"] == "shoe" and c["shoe"] in usable and t.get("color"):
            by_color.setdefault(t["color"].lower(), set()).add(c["shoe"])
    out = {col: next(iter(s)) for col, s in by_color.items() if len(s) == 1}
    out.update({k.lower(): v for k, v in rules.get("color_shoes", {}).items() if v})
    return out


def overlaps(a0, a1, b0, b1) -> bool:
    if a1 <= a0:                      # punto
        return b0 <= a0 < b1
    return min(a1, b1) - max(a0, b0) > 0


def build_queue(tracks: List[dict], edl: Dict[str, list], sweep: List[str], rules,
                choices: dict, done: set, prefixes=("Prps", "Fts"), surface_filter: Optional[str] = None,
                tc=None) -> dict:
    """tracks: [{name, color, folder}] (folder = carpeta de primer nivel).
    Devuelve {"items": [...], "shoes": [...], "surfaces": [...]}."""
    prps, fts = prefixes[0] if prefixes else "Prps", prefixes[1] if len(prefixes) > 1 else "Fts"
    info = {t["name"]: t for t in tracks}
    kind_of = {t["name"]: folder_kind(t.get("folder", ""), rules) for t in tracks}
    surfaces = [t["name"] for t in tracks if kind_of[t["name"]] == "surfaces"]
    fts_tracks = [t for t in tracks if kind_of[t["name"]] == "footsteps"]
    colors = shoe_colors(fts_tracks, rules)
    char_shoes = {norm(k): v for k, v in rules.get("char_shoes", {}).items() if v}
    alias = {norm(k): v for k, v in rules.get("surface_alias", {}).items() if v}
    surf_label = lambda s: alias.get(norm(s)) or s

    items = []
    for tname in sweep:
        kind = kind_of.get(tname)
        cls = classify_fts(tname, rules) if kind == "footsteps" else None
        for ev in edl.get(tname, []):
            key = f"{tname}|{ev.start}"
            raw_clip = strip_suffix(ev.clip)          # sin «.grp.01» ni «Ref»
            it = {"key": key, "track": tname, "clip": ev.clip, "start": ev.start, "end": ev.end,
                  "ref": has_ref(ev.clip),
                  "muted": bool(getattr(ev, "muted", False)), "done": key in done, "kind": kind or "other",
                  "needs": []}
            if tc:
                it["tc_in"], it["tc_out"] = tc(ev.start), tc(ev.end)
            ch = choices.get(key, {})
            if kind == "footsteps":
                under = [s for s in surfaces if any(overlaps(ev.start, ev.end, x.start, x.end)
                                                    for x in edl.get(s, []))]
                it["surfaces"] = under
                surface = ch.get("surface") if ch.get("surface") in under else (under[0] if len(under) == 1 else None)
                if len(under) > 1 and not surface:
                    it["needs"].append("surface")
                if surface_filter and surface_filter not in (under if not surface else [surface]):
                    continue
                if cls["type"] == "shoe":
                    clip = raw_clip
                    generic = not clip or base_name(clip) == base_name(tname) or norm(clip) == norm(cls["shoe"])
                    character = None if generic else clip
                    shoe, shoe_src = cls["shoe"], "track"
                else:
                    character = cls["character"]
                    shoe, shoe_src = cls["shoe"], "track"
                    if shoe_in(raw_clip, rules):
                        shoe, shoe_src = shoe_in(raw_clip, rules), "clip"
                    elif not shoe and ch.get("shoe"):
                        shoe, shoe_src = ch["shoe"], "elegido"
                    elif not shoe and char_shoes.get(norm(character)):
                        shoe, shoe_src = char_shoes[norm(character)], "personaje"
                    elif not shoe and colors.get(str(info.get(tname, {}).get("color", "")).lower()):
                        shoe, shoe_src = colors[str(info[tname]["color"]).lower()], "color"
                    if not shoe:
                        it["needs"].append("shoe")
                it.update({"character": nice(character, rules) if character else None, "shoe": shoe,
                           "shoe_src": shoe_src if shoe else None, "surface": surface})
                parts = [fts, nice(character, rules) if character else None, shoe,
                         surf_label(surface) if surface else None]
            else:
                clip = raw_clip
                pre = fts if kind == "surfaces" else prps
                body = clip if clip else tname
                if norm(body).startswith(norm(pre) + " "):
                    pre = None
                parts = [pre, surf_label(body) if kind == "surfaces" else body]
            if ch.get("name"):
                it["name"], it["name_src"] = ch["name"], "editado"
            else:
                it["name"], it["name_src"] = tidy(" ".join(p for p in parts if p)), "auto"
            items.append(it)
    items.sort(key=lambda x: (x["start"], x["track"]))
    shoe_labels = []
    for sh in rules.get("shoes", []):
        if sh["label"] not in shoe_labels:
            shoe_labels.append(sh["label"])
    return {"items": items, "shoes": shoe_labels, "surfaces": surfaces}
