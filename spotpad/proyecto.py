"""
Lo que es de cada PROYECTO (una serie, una película), aparte de la configuración genérica:

  * Personajes principales con cara (carímetro), alias y calzado fijo.
  * Botonera del proyecto: la genérica + agregados (categorías y botones propios) y lo que
    se oculta de la genérica solo en este proyecto. La genérica no se toca.
  * Exportar / importar: un archivo «.spotpad» (zip) por proyecto, y otro con la genérica,
    para llevarlo a otra computadora. No sale a ningún lado: lo movés vos (NDA).

Se guarda en <datos>/proyectos/<nombre>/proyecto.json + caras/. Sin Pro Tools ni red: se testea solo.
"""
import io
import json
import re
import shutil
import threading
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path

import naming

FORMATO = 1                    # versión del archivo .spotpad
MAX_CARA = 3 * 1024 * 1024     # la página ya la achica; esto es el techo
MAX_PAQUETE = 300 * 1024 * 1024
MAX_ENTRADAS = 2000

# Archivos de la configuración genérica que viajan en «Exportar genérica». Los que dependen de una
# sesión puntual (tracks por ID, elecciones de grabación) se quedan: en otra compu no significan nada.
GENERICA = ("presets.json", "footsteps.json", "hidden.json", "hotkeys.json", "settings.json")

_ENTRADA_OK = re.compile(r"^(manifest\.json|proyecto\.json|nombres\.json|caras/[A-Za-z0-9_-]{1,40}\.(jpg|png|webp)"
                         r"|generica/[a-z_]{1,30}\.json)$")


def slug(nombre: str) -> str:
    """Nombre de carpeta seguro: «Serie Netflix» → «Serie Netflix», «a/b:c» → «a b c»."""
    s = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 _-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")[:60]
    return s or "Sin nombre"


def tipo_imagen(data: bytes):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None, None


MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def vacio(nombre: str) -> dict:
    return {"nombre": nombre, "personajes": [], "char_shoes": {},
            "botonera": {"categorias": [], "agregados": {}, "ocultas": [], "ocultos": {}}}


def _nombres_de(p: dict) -> set:
    return {naming.norm(x) for x in [p.get("nombre", "")] + list(p.get("alias") or []) if str(x).strip()}


# --------------------------------------------------------------------------- #
#  Guardado
# --------------------------------------------------------------------------- #
class ProjectStore:
    def __init__(self, base: Path):
        self.base, self.lock = Path(base), threading.RLock()

    def dir(self, nombre: str) -> Path:
        return self.base / slug(nombre)

    def load(self, nombre: str) -> dict:
        d = vacio(nombre)
        try:
            raw = json.loads((self.dir(nombre) / "proyecto.json").read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            return d
        d.update({k: raw[k] for k in ("personajes", "char_shoes") if isinstance(raw.get(k), type(d[k]))})
        b = raw.get("botonera") or {}
        for k, dflt in d["botonera"].items():
            if isinstance(b.get(k), type(dflt)):
                d["botonera"][k] = b[k]
        return d

    def save(self, nombre: str, d: dict):
        with self.lock:
            p = self.dir(nombre)
            p.mkdir(parents=True, exist_ok=True)
            d = {**d, "nombre": nombre}
            tmp = p / "proyecto.json.tmp"
            tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), "utf-8")
            tmp.replace(p / "proyecto.json")

    def copiar(self, viejo: str, nuevo: str):
        """Cambiar el nombre del proyecto de una sesión: lo del viejo pasa al nuevo si el nuevo no existe
        (el viejo queda: puede haber otras sesiones que lo usan)."""
        with self.lock:
            src, dst = self.dir(viejo), self.dir(nuevo)
            if src.exists() and not dst.exists() and src != dst:
                shutil.copytree(src, dst)
                d = self.load(nuevo); self.save(nuevo, d)

    # -- personajes --------------------------------------------------------- #
    def guardar_personaje(self, proy: str, body: dict) -> dict:
        nombre = re.sub(r"\s+", " ", str(body.get("nombre", ""))).strip()
        if not nombre:
            raise ValueError("Falta el nombre del personaje")
        with self.lock:
            d = self.load(proy)
            pid = str(body.get("id") or "")
            p = next((x for x in d["personajes"] if x["id"] == pid), None) if pid else None
            otro = next((x for x in d["personajes"] if x is not p and naming.norm(nombre) in _nombres_de(x)), None)
            if otro:
                raise ValueError(f"Ya hay un personaje «{otro['nombre']}»")
            if p is None:
                p = {"id": uuid.uuid4().hex[:10]}
                d["personajes"].append(p)
            viejo = p.get("nombre")
            p["nombre"] = nombre
            for k in ("actor", "notas"):
                if k in body:
                    v = str(body.get(k) or "").strip()
                    if v:
                        p[k] = v
                    else:
                        p.pop(k, None)
            if "alias" in body:
                al = body.get("alias") or []
                if isinstance(al, str):
                    al = al.split(",")
                p["alias"] = sorted({re.sub(r"\s+", " ", str(a)).strip() for a in al if str(a).strip()}
                                    - {nombre}, key=str.lower)
                if not p["alias"]:
                    p.pop("alias")
            cs = d["char_shoes"]
            if viejo and naming.norm(viejo) != naming.norm(nombre) and naming.norm(viejo) in cs:
                cs[naming.norm(nombre)] = cs.pop(naming.norm(viejo))       # el calzado sigue al personaje
            if "calzado" in body:
                if body.get("calzado"):
                    cs[naming.norm(nombre)] = str(body["calzado"])
                else:
                    cs.pop(naming.norm(nombre), None)
            if "orden" in body:                                             # mover en el carímetro
                d["personajes"].remove(p)
                d["personajes"].insert(max(0, min(int(body["orden"]), len(d["personajes"]))), p)
            self.save(proy, d)
            return p

    def borrar_personaje(self, proy: str, pid: str):
        with self.lock:
            d = self.load(proy)
            p = next((x for x in d["personajes"] if x["id"] == pid), None)
            if p is None:
                return
            d["personajes"].remove(p)
            d["char_shoes"].pop(naming.norm(p["nombre"]), None)
            if p.get("cara"):
                (self.dir(proy) / "caras" / p["cara"]).unlink(missing_ok=True)
            self.save(proy, d)

    def guardar_cara(self, proy: str, pid: str, data: bytes) -> dict:
        ext, _ = tipo_imagen(data or b"")
        if not ext:
            raise ValueError("La foto tiene que ser JPG, PNG o WebP")
        if len(data) > MAX_CARA:
            raise ValueError("La foto es muy pesada")
        with self.lock:
            d = self.load(proy)
            p = next((x for x in d["personajes"] if x["id"] == pid), None)
            if p is None:
                raise ValueError("No encontré ese personaje")
            caras = self.dir(proy) / "caras"
            caras.mkdir(parents=True, exist_ok=True)
            if p.get("cara"):
                (caras / p["cara"]).unlink(missing_ok=True)
            p["cara"] = f"{pid}.{ext}"
            p["cara_v"] = int(time.time())
            (caras / p["cara"]).write_bytes(data)
            self.save(proy, d)
            return p

    def quitar_cara(self, proy: str, pid: str):
        with self.lock:
            d = self.load(proy)
            p = next((x for x in d["personajes"] if x["id"] == pid), None)
            if p and p.get("cara"):
                (self.dir(proy) / "caras" / p["cara"]).unlink(missing_ok=True)
                p.pop("cara"); p.pop("cara_v", None)
                self.save(proy, d)

    def cara(self, proy: str, pid: str):
        """(bytes, mime) o None."""
        p = next((x for x in self.load(proy)["personajes"] if x["id"] == pid), None)
        if not p or not p.get("cara"):
            return None
        f = self.dir(proy) / "caras" / p["cara"]
        if not f.is_file():
            return None
        return f.read_bytes(), MIME.get(f.suffix.lstrip("."), "image/jpeg")

    def set_char_shoe(self, proy: str, personaje: str, calzado: str):
        with self.lock:
            d = self.load(proy)
            if calzado:
                d["char_shoes"][naming.norm(personaje)] = calzado
            else:
                d["char_shoes"].pop(naming.norm(personaje), None)
            self.save(proy, d)

    def set_botonera(self, proy: str, bot: dict):
        with self.lock:
            d = self.load(proy)
            d["botonera"] = bot
            self.save(proy, d)


# --------------------------------------------------------------------------- #
#  Botonera: genérica + proyecto
# --------------------------------------------------------------------------- #
def _as_item(it) -> dict:
    if isinstance(it, dict):
        d = {"name": str(it.get("name", "")).strip()}
        if str(it.get("track", "")).strip():
            d["track"] = str(it["track"]).strip()
        return d
    return {"name": str(it).strip()}


def combinar(generica: list, bot: dict) -> list:
    """Todas las categorías como se ven en el proyecto. Marcas:
    cat «scope» = genérica|proyecto, «oculta» (categoría genérica oculta en el proyecto);
    item «proyecto» (agregado, «pi» = su posición en la lista del proyecto), «oculto»."""
    bot = bot or {}
    ocultas = set(bot.get("ocultas") or [])
    ocultos = {k: {str(n).strip().lower() for n in v} for k, v in (bot.get("ocultos") or {}).items()}
    agreg = bot.get("agregados") or {}
    out = []
    for c in generica or []:
        cc = {k: v for k, v in c.items() if k != "items"}
        cc["scope"] = "generica"
        if c.get("id") in ocultas:
            cc["oculta"] = True
        items = []
        for it in c.get("items", []):
            d = _as_item(it)
            if d["name"].lower() in ocultos.get(c.get("id"), set()):
                d["oculto"] = True
            items.append(d)
        for i, it in enumerate(agreg.get(c.get("id"), []) or []):
            items.append({**_as_item(it), "proyecto": True, "pi": i})
        cc["items"] = items
        out.append(cc)
    for c in bot.get("categorias") or []:
        cc = {k: v for k, v in c.items() if k != "items"}
        cc["scope"] = "proyecto"
        cc["items"] = [{**_as_item(it), "proyecto": True, "pi": i} for i, it in enumerate(c.get("items", []))]
        out.append(cc)
    return out


def visibles(todas: list) -> list:
    """Lo que se usa al spotear: sin lo oculto en el proyecto y sin las marcas internas."""
    out = []
    for c in todas:
        if c.get("oculta"):
            continue
        cc = {k: v for k, v in c.items() if k not in ("items", "scope", "oculta")}
        cc["items"] = [{k: v for k, v in it.items() if k in ("name", "track")}
                       for it in c["items"] if not it.get("oculto")]
        out.append(cc)
    return out


def limpiar_botonera(body: dict, limpiar_categorias, ids_genericos=()) -> dict:
    """Valida lo que manda el iPad. limpiar_categorias = clean_presets del bridge (mismo criterio)."""
    body = body or {}
    cats = limpiar_categorias({"categories": body.get("categorias") or []})["categories"]
    gen = set(ids_genericos)
    if any(c["id"] in gen for c in cats):
        raise ValueError("Una categoría del proyecto tiene el mismo id que una de la genérica")

    def items(lst):
        out = []
        for it in lst or []:
            d = _as_item(it)
            if d["name"]:
                out.append(d if d.get("track") else d["name"])
        return out
    agreg = {str(k): items(v) for k, v in (body.get("agregados") or {}).items() if items(v)}
    ocultas = sorted({str(x) for x in body.get("ocultas") or [] if str(x).strip()})
    ocultos = {str(k): sorted({str(n).strip() for n in v if str(n).strip()}, key=str.lower)
               for k, v in (body.get("ocultos") or {}).items()}
    return {"categorias": cats, "agregados": agreg, "ocultas": ocultas,
            "ocultos": {k: v for k, v in ocultos.items() if v}}


# --------------------------------------------------------------------------- #
#  Personajes en la sesión y destino de un paso
# --------------------------------------------------------------------------- #
def principales(fts_tracks: list, rules) -> list:
    """Tracks de personaje de la carpeta de pasos → [{personaje, track, shoe}] (sin repetir)."""
    out, vistos = [], set()
    for t in fts_tracks:
        c = naming.classify_fts(t["name"], rules)
        if c["type"] == "char" and c["character"] and naming.norm(c["character"]) not in vistos:
            vistos.add(naming.norm(c["character"]))
            out.append({"personaje": c["character"], "track": t["name"], "shoe": c["shoe"],
                        "color": t.get("color", "")})
    return out


def calzados_sesion(fts_tracks: list, rules) -> list:
    """Calzados que tienen track en la sesión, en el orden de la carpeta: [{label, track}]."""
    out, vistos = [], set()
    for t in fts_tracks:
        c = naming.classify_fts(t["name"], rules)
        if c["type"] == "shoe" and c["shoe"].lower() not in vistos:
            vistos.add(c["shoe"].lower())
            out.append({"label": c["shoe"], "track": t["name"]})
    return out


def fts_destino(personaje: str, calzado: str, fts_tracks: list, rules, alias=()) -> dict:
    """Dónde va un paso de «personaje» con «calzado».
      * Principal con track propio → su track. El clip se llama como el personaje; si el calzado no es
        el suyo, «Henry Sneakers» (así lo lee después el modo grabación).
      * Si no tiene track → el track de ese calzado, con el nombre del personaje como clip («Sonia»).
    Devuelve {"track", "clip", "principal"}; ValueError con el motivo si no se puede."""
    personaje = re.sub(r"\s+", " ", str(personaje or "")).strip()
    if not personaje:
        raise ValueError("Elegí el personaje")
    calzado = str(calzado or "").strip()
    quien = {naming.norm(personaje)} | {naming.norm(a) for a in alias if str(a).strip()}
    char_shoes = {naming.norm(k): v for k, v in (rules.get("char_shoes") or {}).items() if v}
    colors = naming.shoe_colors(fts_tracks, rules)
    for t in fts_tracks:
        c = naming.classify_fts(t["name"], rules)
        if c["type"] == "char" and naming.norm(c["character"]) in quien:
            suyo = c["shoe"] or char_shoes.get(naming.norm(c["character"])) or \
                char_shoes.get(naming.norm(personaje)) or colors.get(str(t.get("color", "")).lower())
            if not calzado or (suyo and calzado.lower() == suyo.lower()):
                clip = personaje
            else:
                clip = f"{personaje} {calzado}"
            return {"track": t["name"], "clip": clip, "principal": True}
    if not calzado:
        raise ValueError(f"«{personaje}» no tiene track propio: elegí el calzado")
    for t in fts_tracks:
        c = naming.classify_fts(t["name"], rules)
        if c["type"] == "shoe" and c["shoe"].lower() == calzado.lower():
            return {"track": t["name"], "clip": personaje, "principal": False}
    raise ValueError(f"No hay un track de «{calzado}» en la carpeta de pasos")


def vista_personajes(d: dict, fts_tracks: list, rules, secundarios=()) -> dict:
    """Carímetro: los guardados (con su track en la sesión si lo tienen) + los principales de la sesión
    que todavía no están guardados («nuevo»). Secundarios = nombres usados en tracks de calzado."""
    rules = {**rules, "char_shoes": {**(rules.get("char_shoes") or {}), **d.get("char_shoes", {})}}
    prin = principales(fts_tracks, rules)
    colors = naming.shoe_colors(fts_tracks, rules)
    cs = {naming.norm(k): v for k, v in rules["char_shoes"].items() if v}
    usados, out = set(), []
    for p in d.get("personajes", []):
        nn = _nombres_de(p)
        t = next((x for x in prin if naming.norm(x["personaje"]) in nn), None)
        if t:
            usados.add(naming.norm(t["personaje"]))
        fijo = cs.get(naming.norm(p["nombre"]))
        out.append({**p, "track": t["track"] if t else "", "calzado_fijo": fijo or "",
                    "calzado": (t and t["shoe"]) or fijo or (t and colors.get(str(t["color"]).lower())) or ""})
    for t in prin:
        if naming.norm(t["personaje"]) not in usados:
            fijo = cs.get(naming.norm(t["personaje"]))
            out.append({"id": "", "nuevo": True, "nombre": naming.nice(t["personaje"], rules), "track": t["track"], "calzado_fijo": fijo or "",
                        "calzado": t["shoe"] or fijo or colors.get(str(t["color"]).lower()) or ""})
    todos = {n for p in out for n in _nombres_de(p)}
    sec = []
    for s in secundarios:
        if naming.norm(s) not in todos and s not in sec:
            sec.append(s)
    return {"proyecto": d["nombre"], "personajes": out, "secundarios": sec,
            "calzados": calzados_sesion(fts_tracks, rules)}


def cara_de(d: dict, personaje: str):
    """id del personaje con cara que corresponde a un nombre (o alias), o None."""
    n = naming.norm(personaje)
    p = next((x for x in d.get("personajes", []) if n in _nombres_de(x) and x.get("cara")), None)
    return p and {"id": p["id"], "v": p.get("cara_v", 0)}


# --------------------------------------------------------------------------- #
#  Exportar / importar
# --------------------------------------------------------------------------- #
def _zip(entradas: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, data in entradas.items():
            z.writestr(nombre, data)
    return buf.getvalue()


def _manifest(tipo: str, app_version: str, **extra) -> bytes:
    return json.dumps({"tipo": tipo, "formato": FORMATO, "app": f"SpotPad {app_version}",
                       "creado": time.strftime("%Y-%m-%d %H:%M"), **extra}, ensure_ascii=False, indent=1).encode()


def exportar_proyecto(store: ProjectStore, nombre: str, nombres_items: list, app_version="") -> bytes:
    d = store.load(nombre)
    ent = {"manifest.json": _manifest("proyecto", app_version, proyecto=nombre),
           "proyecto.json": json.dumps(d, ensure_ascii=False, indent=1).encode(),
           "nombres.json": json.dumps(nombres_items or [], ensure_ascii=False, indent=1).encode()}
    caras = store.dir(nombre) / "caras"
    for p in d["personajes"]:
        if p.get("cara") and (caras / p["cara"]).is_file():
            ent["caras/" + p["cara"]] = (caras / p["cara"]).read_bytes()
    return _zip(ent)


def exportar_generica(datos: Path, app_version="", plataforma="") -> bytes:
    ent = {"manifest.json": _manifest("generica", app_version, plataforma=plataforma)}
    for f in GENERICA:
        if (Path(datos) / f).is_file():
            ent["generica/" + f] = (Path(datos) / f).read_bytes()
    return _zip(ent)


def leer_paquete(data: bytes) -> dict:
    """Abre un .spotpad con cuidado: solo los nombres esperados, tamaño acotado."""
    if len(data) > MAX_PAQUETE:
        raise ValueError("El archivo es demasiado grande")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("No es un archivo de SpotPad (.spotpad)")
    infos = z.infolist()
    if len(infos) > MAX_ENTRADAS or sum(i.file_size for i in infos) > MAX_PAQUETE:
        raise ValueError("El archivo es demasiado grande")
    files = {}
    for i in infos:
        if i.is_dir():
            continue
        if not _ENTRADA_OK.match(i.filename):
            continue                                   # lo que no conocemos, ni se lee
        files[i.filename] = z.read(i)
    try:
        man = json.loads(files["manifest.json"])
    except (KeyError, ValueError):
        raise ValueError("No es un archivo de SpotPad (.spotpad)")
    if man.get("tipo") not in ("proyecto", "generica"):
        raise ValueError("No es un archivo de SpotPad (.spotpad)")
    if int(man.get("formato", 0)) > FORMATO:
        raise ValueError("El archivo es de una versión más nueva de SpotPad: actualizá esta compu")
    return {"tipo": man["tipo"], "manifest": man, "files": files}


def importar_proyecto(store: ProjectStore, pkg: dict, modo="mezclar", nombre=None):
    """Devuelve (nombre del proyecto, items de nombres escritos a mano para sumar a proyectos.json).
    «mezclar»: lo que ya tenés gana; se suma lo que falta (personajes, caras que no tenías, botones).
    «reemplazar»: el proyecto queda igual al del archivo."""
    f = pkg["files"]
    try:
        nuevo = json.loads(f["proyecto.json"])
    except (KeyError, ValueError):
        raise ValueError("Al archivo le falta el proyecto")
    nombre = (nombre or pkg["manifest"].get("proyecto") or nuevo.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("El archivo no dice de qué proyecto es")
    try:
        items = json.loads(f.get("nombres.json") or b"[]")
    except ValueError:
        items = []
    items = [i for i in items if isinstance(i, dict) and i.get("track") and i.get("name")]
    tmp = vacio(nombre)
    # Normalizar lo que viene (mismas validaciones que lo cargado a mano)
    for k in ("personajes", "char_shoes"):
        if isinstance(nuevo.get(k), type(tmp[k])):
            tmp[k] = nuevo[k]
    for k, dflt in tmp["botonera"].items():
        if isinstance((nuevo.get("botonera") or {}).get(k), type(dflt)):
            tmp["botonera"][k] = nuevo["botonera"][k]
    tmp["personajes"] = [p for p in tmp["personajes"]
                         if isinstance(p, dict) and str(p.get("nombre", "")).strip()]
    for p in tmp["personajes"]:
        p["id"] = re.sub(r"[^A-Za-z0-9_-]", "", str(p.get("id") or "")) or uuid.uuid4().hex[:10]
        cara = p.get("cara")
        if cara and f"caras/{cara}" not in f:
            p.pop("cara", None); p.pop("cara_v", None)

    with store.lock:
        destino = store.dir(nombre)
        if modo == "reemplazar":
            if destino.exists():
                shutil.rmtree(destino)
            d = tmp
            fotos = {p["cara"]: f[f"caras/{p['cara']}"] for p in d["personajes"] if p.get("cara")}
        else:
            d = store.load(nombre)
            fotos = {}
            mios = {n: p for p in d["personajes"] for n in _nombres_de(p)}
            ids = {p["id"] for p in d["personajes"]}
            for p in tmp["personajes"]:
                yo = next((mios[n] for n in _nombres_de(p) if n in mios), None)
                if yo is None:
                    if p["id"] in ids:
                        viejo, p["id"] = p["id"], uuid.uuid4().hex[:10]
                        if p.get("cara"):
                            ext = p["cara"].rsplit(".", 1)[-1]
                            f[f"caras/{p['id']}.{ext}"] = f[f"caras/{p['cara']}"]
                            p["cara"] = f"{p['id']}.{ext}"
                    d["personajes"].append(p); ids.add(p["id"])
                    if p.get("cara"):
                        fotos[p["cara"]] = f[f"caras/{p['cara']}"]
                    continue
                for k in ("actor", "notas"):
                    if not yo.get(k) and p.get(k):
                        yo[k] = p[k]
                al = set(yo.get("alias") or []) | set(p.get("alias") or [])
                if al:
                    yo["alias"] = sorted(al - {yo["nombre"]}, key=str.lower)
                if not yo.get("cara") and p.get("cara"):
                    ext = p["cara"].rsplit(".", 1)[-1]
                    yo["cara"], yo["cara_v"] = f"{yo['id']}.{ext}", int(time.time())
                    fotos[yo["cara"]] = f[f"caras/{p['cara']}"]
            for k, v in tmp["char_shoes"].items():
                d["char_shoes"].setdefault(k, v)
            b, nb = d["botonera"], tmp["botonera"]
            have = {c["id"] for c in b["categorias"]}
            b["categorias"] += [c for c in nb["categorias"] if c.get("id") not in have]
            for cid, lst in nb["agregados"].items():
                mine = b["agregados"].setdefault(cid, [])
                names = {_as_item(i)["name"].lower() for i in mine}
                mine += [i for i in lst if _as_item(i)["name"].lower() not in names]
            b["ocultas"] = sorted(set(b["ocultas"]) | set(nb["ocultas"]))
            for cid, lst in nb["ocultos"].items():
                b["ocultos"][cid] = sorted(set(b["ocultos"].get(cid, [])) | set(lst), key=str.lower)
        store.save(nombre, d)
        if fotos:
            (destino / "caras").mkdir(parents=True, exist_ok=True)
            for fn, data in fotos.items():
                if tipo_imagen(data)[0] and len(data) <= MAX_CARA:
                    (destino / "caras" / fn).write_bytes(data)
    return nombre, items


def importar_generica(pkg: dict, datos: Path, plataforma="") -> list:
    """Reemplaza la configuración genérica. Lo que había queda en <datos>/respaldo-<fecha>/.
    Los atajos de teclado solo se traen entre compus del mismo sistema (Mac ⌘ ≠ Windows)."""
    nuevos = {}
    otra = pkg["manifest"].get("plataforma") and plataforma and \
        pkg["manifest"]["plataforma"].startswith("win") != plataforma.startswith("win")
    for f in GENERICA:
        if f == "hotkeys.json" and otra:
            continue
        raw = pkg["files"].get("generica/" + f)
        if raw is None:
            continue
        try:
            json.loads(raw)
        except ValueError:
            raise ValueError(f"«{f}» del archivo está dañado")
        nuevos[f] = raw
    if not nuevos:
        raise ValueError("El archivo no trae configuración")
    datos = Path(datos)
    bk = datos / time.strftime("respaldo-%Y%m%d-%H%M%S")
    for f in nuevos:
        if (datos / f).is_file():
            bk.mkdir(exist_ok=True)
            shutil.copy2(datos / f, bk / f)
    for f, raw in nuevos.items():
        (datos / f).write_bytes(raw)
    return sorted(nuevos)
