#!/usr/bin/env python3
"""
SpotPad bridge — corre en la Mac de Pro Tools.

  * Sirve la botonera (static/index.html) para abrir desde el iPad / celu.
  * Recibe los toques y los traduce a comandos del Pro Tools Scripting SDK (PTSL).

Uso:
    python3 bridge.py            # conecta a Pro Tools (localhost:31416)
    python3 bridge.py --mock     # sin Pro Tools, para probar la interfaz
    python3 bridge.py --port 8765 --debug
"""
import argparse
import asyncio
import json
import logging
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

from aiohttp import web

from edl import clip_at_selection, parse_session_text
from tc import TcConverter, rate_from_enum
import naming

VERSION = "0.7.0"
HERE = Path(__file__).parent
# Archivos de la app (página, presets por defecto): dentro del .app cuando está compilada
RES = Path(getattr(sys, "_MEIPASS", HERE))
FROZEN = getattr(sys, "frozen", False)


def _data_dir() -> Path:
    """Dónde se guarda la configuración. En la app: ~/Library/Application Support/SpotPad
    (en Windows: %APPDATA%\\SpotPad), así sobrevive a las actualizaciones.
    Corriendo el .py: la carpeta del proyecto."""
    if os.environ.get("SPOTPAD_DATA"):
        d = Path(os.environ["SPOTPAD_DATA"]).expanduser()
    elif FROZEN and sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "SpotPad"
    elif FROZEN:
        d = Path.home() / "Library" / "Application Support" / "SpotPad"
    else:
        d = HERE
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "presets.json").exists() and (RES / "presets.json").exists():
        shutil.copy(RES / "presets.json", d / "presets.json")
    return d


DATA = _data_dir()
PRESETS_FILE = DATA / "presets.json"
TARGETS_FILE = DATA / "targets.json"   # tracks destino pinneados (por ID de track)
STATE_FILE = DATA / "state.json"       # prefijo activo (Prps / Fts)
SPOT_TRACKS_FILE = DATA / "spot_tracks.json"   # tracks que muestra la lista de spotting (por ID)
DIAG_FILE = DATA / "diag.txt"
HIDDEN_FILE = DATA / "hidden.json"      # carpetas y tracks ocultos en la botonera (por nombre)
RULES_FILE = DATA / "footsteps.json"    # reglas de nombres de pasos (calzados, colores, alias de superficies)
REC_FILE = DATA / "rec.json"            # modo grabación: track de grabación, qué barrer, elecciones por sesión


def load_rules() -> dict:
    """Reglas por defecto + lo que el usuario cambió (sus claves pisan las de fábrica)."""
    import copy
    r = copy.deepcopy(naming.DEFAULT_RULES)
    user = load_json(RULES_FILE, {})
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict) and k != "folders":
            r[k] = {**r[k], **v}
        else:
            r[k] = v
    return r


def top_folders(infos) -> dict:
    """nombre de track → carpeta de primer nivel (o "")."""
    by_id = {t["id"]: t for t in infos}
    out = {}
    for t in infos:
        pid, top, seen = t["parent_id"], "", set()
        while pid and pid in by_id and pid not in seen:
            seen.add(pid); top = by_id[pid]["name"]; pid = by_id[pid]["parent_id"]
        out[t["name"]] = top or t.get("parent_name", "")
    return out
LOG_FILE = DATA / "spotpad.log"


# ---- Presets: cada botón puede tener un track destino ------------------------ #
#   item: "Hands clap"  ó  {"name": "Hands clap", "track": "Hands Body"}
#   categoría: "track" opcional = destino por defecto de sus botones
def item_name(item) -> str:
    return (item.get("name", "") if isinstance(item, dict) else str(item)).strip()


def item_track(item, cat=None) -> str:
    t = item.get("track", "") if isinstance(item, dict) else ""
    return (t or (cat or {}).get("track", "") or "").strip()


def migrate_presets():
    """Las instalaciones viejas tienen Manos sin track asignado: les sumamos el destino
    por defecto (Hands Body / Hands Surfaces) sin tocar nada que hayan editado."""
    try:
        user = json.loads(PRESETS_FILE.read_text("utf-8"))
        default = json.loads((RES / "presets.json").read_text("utf-8"))
    except Exception:
        return
    dflt = {c["id"]: {item_name(i): item_track(i) for i in c.get("items", [])} for c in default.get("categories", [])}
    changed = False
    for cat in user.get("categories", []):
        tracks = dflt.get(cat.get("id"), {})
        if not tracks or any(isinstance(i, dict) for i in cat.get("items", [])):
            continue
        new = [{"name": item_name(i), "track": tracks[item_name(i)]} if tracks.get(item_name(i)) else i
               for i in cat.get("items", [])]
        if new != cat.get("items"):
            cat["items"], changed = new, True
    if changed:
        PRESETS_FILE.write_text(json.dumps(user, indent=2, ensure_ascii=False), "utf-8")


def clean_presets(data) -> dict:
    """Valida lo que manda el editor del iPad antes de guardarlo."""
    cats = []
    for i, c in enumerate(data.get("categories", [])):
        label = str(c.get("label", "")).strip()
        if not label:
            continue
        items = []
        for it in c.get("items", []):
            n, t = item_name(it), item_track(it)
            if n:
                items.append({"name": n, "track": t} if t else n)
        cid = str(c.get("id") or "").strip() or f"cat{i + 1}"
        cat = {"id": cid, "label": label, "color": str(c.get("color") or "#E8A33D"), "items": items}
        if str(c.get("track", "")).strip():
            cat["track"] = str(c["track"]).strip()
        cats.append(cat)
    ids = [c["id"] for c in cats]
    if len(ids) != len(set(ids)):
        raise ValueError("Hay dos categorías con el mismo id")
    return {"categories": cats}


migrate_presets()


def setup_logging(debug=False, console=True):
    """Registro en archivo (rota a 1 MB, guarda 3) + consola."""
    from logging.handlers import RotatingFileHandler
    handlers = [RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(message)s", handlers=handlers, force=True)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)   # sin una línea por cada pedido del iPad


def build_report(pt) -> str:
    """Todo lo necesario para diagnosticar un problema, en un solo texto para copiar y pegar."""
    import platform
    out = [f"# Informe de SpotPad {VERSION}",
           f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}",
           f"Sistema: {platform.platform()} · Python {platform.python_version()}",
           f"Datos: {DATA}", ""]
    h = pt.health()
    out += ["## Estado de la conexión con Pro Tools", json.dumps(h, indent=1, ensure_ascii=False), ""]
    out += ["## Estado ahora", json.dumps(pt.status(), ensure_ascii=False), ""]
    if not pt.health().get("stuck"):
        try:
            out += ["## Diagnóstico de la sesión", pt.diag(), ""]
        except Exception as e:     # noqa: BLE001
            out += ["## Diagnóstico de la sesión", f"No se pudo: {e}", ""]
    else:
        out += ["## Diagnóstico de la sesión", "Salteado: Pro Tools tiene un pedido sin contestar.", ""]
    try:
        lines = LOG_FILE.read_text("utf-8", errors="replace").splitlines()[-150:]
        out += ["## Registro (últimas 150 líneas)", *lines]
    except FileNotFoundError:
        out += ["## Registro", "(vacío)"]
    return "\n".join(out)


def track_name_for(prefix: str, clip: str) -> str:
    """«Prps» + «Hands clap» → «Prps Hands clap». Si el clip ya trae el prefijo, no lo duplica."""
    clip = clip.strip()
    if not prefix or clip.lower().startswith(prefix.lower() + " "):
        return clip
    return f"{prefix} {clip}"

log = logging.getLogger("spotpad")


def _op(name):
    """Operación de py-ptsl por nombre; si la librería no la trae (comandos nuevos del SDK),
    se arma igual: Operation deduce request/response del nombre."""
    from ptsl import ops
    cls = getattr(ops, name, None)
    if cls is None:
        from ptsl.ops.operation import Operation
        cls = type(name, (Operation,), {})
    return cls


def track_info(t, pt=None) -> dict:
    """Track del SDK → dict simple (lo que usa la botonera)."""
    try:
        ttype = pt.TrackType.Name(t.type) if pt else str(t.type)
    except Exception:
        ttype = str(t.type)
    return {"id": t.id, "name": t.name, "index": t.index, "type": ttype,
            "color": getattr(t, "color", ""), "parent_id": getattr(t, "parent_folder_id", ""),
            "parent_name": getattr(t, "parent_folder_name", "")}


def build_layout(tracks, exclude=()):
    """Una pestaña por carpeta de primer nivel; adentro, un botón por track.
    Subcarpetas → grupos dentro de la pestaña. Tracks fuera de carpetas no aparecen
    (video, diálogos, pre, grabación)."""
    by_id = {t["id"]: t for t in tracks}
    is_folder = lambda t: "Folder" in t["type"]
    excl = {x.lower() for x in exclude}

    def chain(t):          # carpetas desde la de arriba hasta el padre directo
        out, pid, seen = [], t["parent_id"], set()
        while pid and pid in by_id and pid not in seen:
            seen.add(pid); out.insert(0, by_id[pid]); pid = by_id[pid]["parent_id"]
        if not out and t["parent_name"]:          # sin id de carpeta: usar el nombre
            out = [{"id": "name:" + t["parent_name"], "name": t["parent_name"], "index": t["index"]}]
        return out

    tabs = {}
    for t in sorted(tracks, key=lambda t: t["index"]):
        if is_folder(t):
            continue
        folders = chain(t)
        if not folders or folders[0]["name"].lower() in excl:
            continue
        top = folders[0]
        tab = tabs.setdefault(top["id"], {"id": top["id"], "name": top["name"], "index": top["index"], "groups": []})
        gname = " › ".join(f["name"] for f in folders[1:])
        if not tab["groups"] or tab["groups"][-1]["name"] != gname:
            tab["groups"].append({"name": gname, "tracks": []})
        tab["groups"][-1]["tracks"].append({"id": t["id"], "name": t["name"], "color": t["color"]})
    return {"tabs": sorted(tabs.values(), key=lambda x: x["index"])}


class SpotError(Exception):
    """Error con mensaje para mostrar en el iPad."""


# --------------------------------------------------------------------------- #
#  Pro Tools real
# --------------------------------------------------------------------------- #
class ProTools:
    APP = ("Mauricio Crudo", "SpotPad")

    TIMEOUT = 10        # segundos que esperamos a Pro Tools antes de darlo por ocupado
    RETRY_AFTER = 15    # después de un cuelgue, cada cuánto probamos con una conexión nueva
    BUSY = "Pro Tools no responde. ¿Hay una ventana o diálogo abierto en Pro Tools?"
    DEAD = ("Pro Tools dejó de aceptar conexiones del SDK. Guardá, cerrá y volvé a abrir Pro Tools; "
            "SpotPad se reconecta solo.")

    def __init__(self, debug=False):
        self._engine = None
        self._debug = debug
        self._guard = threading.Lock()
        self._worker = None          # hilo único que habla con Pro Tools
        self._stuck = None           # pedido colgado (Pro Tools no contestó)
        # Para el diagnóstico: qué se le pidió a Pro Tools y qué pasó
        from collections import deque
        self._history = deque(maxlen=40)   # últimos comandos PTSL
        self._inflight = None               # (comando, desde) mientras espera respuesta
        self._last_error = None             # (mensaje, cuándo)
        self._last_ok = None                # cuándo contestó bien por última vez
        self._connections = 0               # cuántas veces conectamos
        self._connect_fails = 0             # intentos de conexión seguidos sin respuesta

    # -- conexión ---------------------------------------------------------- #
    def _eng(self):
        if self._engine is None:
            import ptsl
            self._inflight = ("Conectar con Pro Tools", time.time())
            try:
                eng = ptsl.Engine(company_name=self.APP[0], application_name=self.APP[1])
            finally:
                self._inflight = None
            if self._debug:
                eng.client.auditor.enabled = True
            self._track_commands(eng)
            self._connections += 1
            self._connect_fails = 0
            log.info("PTSL: conectado (conexión #%s)", self._connections)
            self._engine = eng
        return self._engine

    def _track_commands(self, eng):
        """Anota cada comando que se le manda a Pro Tools: nombre, duración y resultado.
        Si uno queda colgado, el informe dice exactamente cuál fue."""
        from ptsl import PTSL_pb2 as pt
        client, orig = eng.client, eng.client.run

        def run(op):
            try:
                name = pt.CommandId.Name(op.command_id()).replace("CId_", "")
            except Exception:
                name = type(op).__name__
            t0 = time.time()
            self._inflight = (name, t0)
            log.debug("PTSL → %s", name)
            try:
                r = orig(op)
            except Exception as e:
                self._history.append((t0, name, time.time() - t0, str(e).splitlines()[0][:120]))
                raise
            finally:
                self._inflight = None
            self._history.append((t0, name, time.time() - t0, "ok"))
            self._last_ok = time.time()
            return r
        client.run = run

    def _run(self, fn):
        try:
            return fn(self._eng())
        except SpotError as e:
            self._last_error = (str(e), time.time())
            raise
        except Exception as e:          # conexión caída, PT cerrado, etc.
            msg = str(e)
            if "PT_NoOpenedSession" in msg:
                log.info("PTSL: no hay sesión abierta")
                err = "No hay ninguna sesión abierta en Pro Tools"
            elif "InProgress" in msg:
                log.info("PTSL: Pro Tools ocupado (respuesta InProgress)")
                err = "Pro Tools está ocupado (¿abriendo o guardando la sesión?). Probá de nuevo en un momento."
            else:
                log.exception("PTSL")
                if "UNAVAILABLE" in msg or "failed to connect" in msg.lower():
                    self._engine = None
                    err = "No encuentro Pro Tools (¿está abierto?)"
                else:
                    err = msg.splitlines()[0][:200]
            self._last_error = (err, time.time())
            raise SpotError(err)

    def _call(self, fn, timeout=None):
        """Corre fn(engine) en el hilo de Pro Tools, sin dejar que un pedido colgado
        trabe todo: si Pro Tools no contesta a tiempo, devolvemos error y los pedidos
        siguientes fallan rápido hasta que ese termine."""
        from concurrent.futures import Future, TimeoutError as FutTimeout
        with self._guard:
            if self._stuck is not None:
                fut0, since = self._stuck
                if not fut0.done() and time.monotonic() - since < self.RETRY_AFTER:
                    raise SpotError(self.DEAD if self._connect_fails >= 2 else self.BUSY)
                if not fut0.done():
                    # Pedido colgado: lo abandonamos y probamos con una conexión nueva
                    log.warning("PTSL: abandono el pedido colgado y reconecto")
                    self._worker = None
                    self._engine = None
                self._stuck = None
            if self._worker is None:
                import queue
                q = self._q = queue.Queue()

                def loop():
                    while True:
                        f, job = q.get()
                        if f.set_running_or_notify_cancel():
                            try:
                                f.set_result(self._run(job))
                            except BaseException as ex:   # noqa: BLE001
                                f.set_exception(ex)
                # daemon: si Pro Tools queda colgado, igual se puede salir de la app
                self._worker = threading.Thread(target=loop, daemon=True, name="ptsl")
                self._worker.start()
            fut = Future()
            self._q.put((fut, fn))
        try:
            return fut.result(timeout or self.TIMEOUT)
        except FutTimeout:
            with self._guard:
                self._stuck = (fut, time.monotonic())
            cmd = self._inflight[0] if self._inflight else "?"
            log.warning("PTSL: Pro Tools no respondió en %ss (comando: %s)", timeout or self.TIMEOUT, cmd)
            if cmd == "Conectar con Pro Tools":
                self._connect_fails += 1
            msg = self.DEAD if self._connect_fails >= 2 else self.BUSY
            self._last_error = (f"{msg} (comando: {cmd})", time.time())
            raise SpotError(msg)

    # -- diagnóstico y reconexión ------------------------------------------ #
    def reconnect(self):
        """Tira la conexión actual (y cualquier pedido colgado) y vuelve a conectar."""
        if self._inflight and self._inflight[0] == "Conectar con Pro Tools":
            # Ya hay un intento de conexión en curso: no apilar otro (tocar Reconectar varias veces)
            return {"ok": False, "connected": False, "error": "Ya estoy intentando conectar, esperá unos segundos"}
        with self._guard:
            if self._stuck is not None and not self._stuck[0].done():
                log.warning("PTSL: reconexión manual, abandono el pedido colgado")
                self._worker = None
            self._stuck = None
            self._engine = None
        log.info("PTSL: reconexión manual")
        st = self.status()
        return {"ok": st.get("connected", False), **st}

    def health(self):
        """Estado sin hablar con Pro Tools (contesta siempre, aunque esté colgado)."""
        now = time.time()
        fmt = lambda t: time.strftime("%H:%M:%S", time.localtime(t)) if t else None
        inflight = None
        if self._inflight:
            inflight = {"command": self._inflight[0], "seconds": round(now - self._inflight[1], 1)}
        stuck = self._stuck is not None and not self._stuck[0].done()
        return {
            "connected_once": self._connections > 0,
            "connections": self._connections,
            "needs_pt_restart": self._connect_fails >= 2,
            "stuck": stuck,
            "inflight": inflight,
            "last_ok": fmt(self._last_ok),
            "last_error": {"msg": self._last_error[0], "at": fmt(self._last_error[1])} if self._last_error else None,
            "history": [{"at": fmt(t), "command": n, "ms": round(d * 1000), "result": r}
                        for t, n, d, r in list(self._history)[-15:]][::-1],
        }

    def status(self):
        def f(e):
            return {"connected": True, "session": e.session_name(), "ptsl": e.ptsl_version()}
        try:
            return self._call(f)
        except SpotError as err:
            return {"connected": False, "error": str(err)}

    # -- parte 1: crear clip group con nombre -------------------------------- #
    def group_and_name(self, name: str):
        from ptsl import PTSL_pb2 as pt

        def f(e):
            try:
                e.group_clips()            # PT 2024.6+
            except Exception as ex:
                if "Unknown" in str(ex) or "not supported" in str(ex).lower():
                    raise SpotError("Tu Pro Tools no tiene GroupClips en el SDK: hace falta 2024.6 o posterior")
                raise
            # El grupo recién creado queda seleccionado → lo renombramos
            e.rename_selected_clip(new_name=name, rename_file=False, clip_location=pt.CL_Timeline)
            return {"ok": True, "msg": f"Clip group «{name}»"}
        return self._call(f)

    # -- botonera armada desde las carpetas de la sesión --------------------- #
    def layout(self, exclude=()):
        def f(e):
            from ptsl import PTSL_pb2 as pt
            return build_layout([track_info(t, pt) for t in e.track_list()], exclude)
        return self._call(f)

    def group_on_track(self, track_id: str, name: str = ""):
        """Crea el clip group en el track del botón, con el rango marcado en cualquier track."""
        def f(e):
            target = next((t for t in e.track_list() if t.id == track_id), None)
            if target is None:
                raise SpotError("Ese track ya no existe en la sesión")
            return self._group_on(e, target, name)
        return self._call(f)

    def group_on_named(self, track_name: str, name: str = ""):
        """Igual, pero el track se busca por nombre (botones preseteados con track asignado)."""
        def f(e):
            want = track_name.strip().lower()
            target = next((t for t in e.track_list() if t.name.strip().lower() == want), None)
            if target is None:
                raise SpotError(f"No hay un track «{track_name}» en esta sesión. "
                                "Cambiá el destino del botón con ✎ Editar.")
            return self._group_on(e, target, name)
        return self._call(f)

    def _group_on(self, e, target, name: str = ""):
        from ptsl import PTSL_pb2 as pt
        sel_in, sel_out = self._selection_samples(e)
        if sel_out <= sel_in:
            raise SpotError("Marcá una región primero")
        log.info("group-on: rango %s-%s → seleccionar track «%s»", sel_in, sel_out, target.name)
        e.select_tracks_by_name([target.name])
        # Con «Link Track and Edit Selection» el rango ya pasa solo al track nuevo.
        # Si no, lo re-aplicamos; y antes de agrupar verificamos que quedó igual.
        if self._selection_samples(e) != (sel_in, sel_out):
            log.info("group-on: re-aplicar selección")
            e.set_timeline_selection(in_time=str(sel_in), out_time=str(sel_out),
                                     location_type=pt.TLType_Samples)
            got = self._selection_samples(e)
            if got != (sel_in, sel_out):
                log.warning("group-on: la selección quedó en %s (esperaba %s-%s)", got, sel_in, sel_out)
                raise SpotError(f"No pude pasar la selección al track «{target.name}». "
                                "Activá Options → Link Track and Edit Selection y probá de nuevo.")
        log.info("group-on: GroupClips")
        try:
            e.group_clips()
        except Exception as ex:
            if "Unknown" in str(ex) or "not supported" in str(ex).lower():
                raise SpotError("Tu Pro Tools no tiene GroupClips en el SDK: hace falta 2024.6 o posterior")
            raise
        clip = (name or target.name).strip()
        log.info("group-on: renombrar a «%s»", clip)
        e.rename_selected_clip(new_name=clip, rename_file=False, clip_location=pt.CL_Timeline)
        return {"ok": True, "msg": f"«{clip}» en {target.name}"}

    def diag(self):
        """Lo que hace falta ver de tu sesión real, en texto para pegar."""
        from ptsl import PTSL_pb2 as pt
        return self._call(lambda e: self._diag(e, pt, []), timeout=120)

    def _diag(self, e, pt, out):
        def step(title, fn):
            try:
                out.append(f"## {title}\n{fn()}")
            except Exception as ex:
                out.append(f"## {title}\nERROR: {ex}")

        step("Sesión", lambda: f"{e.session_name()}  ·  PTSL {e.ptsl_version()}")
        step("Timecode", lambda: f"start={e.session_start_time()}  rate="
             f"{pt.SessionTimeCodeRate.Name(e.session_timecode_rate())}  sr={e.session_sample_rate()}")
        step("Transporte", lambda: e.transport_state())
        step("Selección (samples)", lambda: self._selection_samples(e))

        def tracks():
            rows = ["idx | tipo | carpeta padre | color | nombre | id"]
            for t in e.track_list():
                i = track_info(t, pt)
                rows.append(f"{i['index']} | {i['type']} | {i['parent_name'] or '-'} | "
                            f"{i['color'] or '-'} | {i['name']} | {i['id']}")
            return "\n".join(rows)
        step("Tracks", tracks)
        step("Botonera que se armaría", lambda: json.dumps(
            build_layout([track_info(t, pt) for t in e.track_list()]), indent=1, ensure_ascii=False))

        def edl_sample():
            b = e.export_session_as_text()
            b.include_track_edls(); b.selected_tracks_only(); b.time_type("samples"); b.dont_show_crossfades()
            txt = b.export_string()
            return "\n".join(txt.splitlines()[:60])
        step("Export de tracks seleccionados (primeras 60 líneas)", edl_sample)
        return "\n\n".join(out)

    # -- parte 2: leer el clip group seleccionado ---------------------------- #
    @staticmethod
    def _is_set(state) -> bool:
        return int(state) >= 2   # SetExplicitly / SetImplicitly / ambos

    def _selection_samples(self, e):
        from ptsl import PTSL_pb2 as pt
        # Ojo: NO usar GetEditSelection. En Pro Tools 2025.12 se queda sin contestar y deja
        # al SDK de Pro Tools sin aceptar conexiones hasta reiniciarlo (visto en la prueba real).
        # GetTimelineSelection da lo mismo con «Link Timeline and Edit Selection» activado.
        # Pro Tools toma la unidad del campo time_scale (py-ptsl solo llena location_type y
        # entonces responde en el formato del contador principal, p. ej. « 2012| 3| 077»).
        from ptsl import ops
        op = ops.CId_GetTimelineSelection(time_scale=pt.TOOptions_Samples, location_type=pt.TLType_Samples)
        e.client.run(op)
        a, b = op.response.in_time, op.response.out_time
        try:
            return int(str(a).strip()), int(str(b).strip())
        except ValueError:
            raise SpotError(f"Pro Tools devolvió la selección como «{str(a).strip()}» y no en samples. "
                            "Mandame el informe para soporte.")

    def selected_clip(self, e):
        tracks = e.track_list()
        cand = [t for t in tracks if self._is_set(t.track_attributes.has_edit_selection)]
        if not cand:
            cand = [t for t in tracks if self._is_set(t.track_attributes.is_selected)]
        if not cand:
            raise SpotError("No hay ningún clip seleccionado")
        sel_in, sel_out = self._selection_samples(e)

        def export(selected_only: bool):
            b = e.export_session_as_text()
            b.include_track_edls()          # (esto pone AllTracks por defecto)
            if selected_only:
                b.selected_tracks_only()
            b.time_type("samples")
            b.dont_show_crossfades()
            return parse_session_text(b.export_string())

        # Rápido: solo tracks seleccionados. Si el track con el clip no está
        # seleccionado (selección de edición sin selección de track), toda la sesión.
        for selected_only in (True, False):
            edl = export(selected_only)
            for t in sorted(cand, key=lambda t: t.index):
                ev = clip_at_selection(edl.get(t.name, []), sel_in, sel_out)
                if ev:
                    return ev
        raise SpotError("No encontré un clip bajo la selección")

    def peek(self):
        def f(e):
            ev = self.selected_clip(e)
            return {"ok": True, "clip": ev.clip, "track": ev.track}
        return self._call(f)

    def tracks(self):
        def f(e):
            return [{"id": t.id, "name": t.name, "index": t.index} for t in e.track_list()]
        return self._call(f)

    def rename_track_to_selection(self, track_id: str, prefix: str = ""):
        def f(e):
            ev = self.selected_clip(e)
            tracks = e.track_list()
            target = next((t for t in tracks if t.id == track_id), None)
            if target is None:
                raise SpotError("El track destino ya no existe en la sesión")
            new = track_name_for(prefix, ev.clip)
            if target.name == new:
                return {"ok": True, "msg": f"«{new}» ya tenía ese nombre"}
            taken = {t.name for t in tracks if t.id != track_id}
            base, n = new, 2
            while new in taken:
                new = f"{base} {n}"
                n += 1
            e.rename_target_track(old_name=target.name, new_name=new)
            return {"ok": True, "msg": f"«{target.name}» → «{new}»"}
        return self._call(f)

    def undo(self):
        return self._call(lambda e: (e.undo(1), {"ok": True, "msg": "Deshecho"})[1])

    # -- lista de spotting en vivo ------------------------------------------- #
    def _tc(self, e):
        from ptsl import PTSL_pb2 as pt
        try:
            rate = rate_from_enum(pt.SessionTimeCodeRate.Name(e.session_timecode_rate()))
            return TcConverter(e.session_sample_rate() or 48000, e.session_start_time(), rate)
        except Exception:
            log.exception("timecode")
            return None

    def spotting(self, track_ids):
        """Todos los clips de los tracks de spotting, leídos del export en vivo.
        No lee mientras Pro Tools está grabando o reproduciendo.
        Solo mira los tracks elegidos como de spotting: sin elección no lee nada,
        para no registrar como «nuevo» cada clip grabado."""
        if not track_ids:
            return {"ok": True, "paused": False, "need_tracks": True, "clips": []}

        def f(e):
            state = e.transport_state()
            if state != "TS_TransportStopped":
                return {"ok": True, "paused": True, "state": state}
            tracks = e.track_list()
            order = {t.name: t.index for t in tracks}
            wanted = {t.name for t in tracks if t.id in track_ids}
            if not wanted:
                return {"ok": True, "paused": False, "need_tracks": True, "clips": []}
            b = e.export_session_as_text()
            b.include_track_edls()
            b.time_type("samples")
            b.dont_show_crossfades()
            edl = parse_session_text(b.export_string())
            tc = self._tc(e)
            clips = []
            for name, events in edl.items():
                if name not in wanted:
                    continue
                for ev in events:
                    clips.append({
                        "track": name, "clip": ev.clip, "start": ev.start, "end": ev.end,
                        "tc_in": tc(ev.start) if tc else str(ev.start),
                        "tc_out": tc(ev.end) if tc else str(ev.end),
                        "dur": tc.duration(ev.end - ev.start) if tc else str(ev.end - ev.start),
                    })
            clips.sort(key=lambda c: (c["start"], order.get(c["track"], 0)))
            return {"ok": True, "paused": False, "clips": clips}
        return self._call(f, timeout=30)

    # -- modo grabación --------------------------------------------------------- #
    def rec_snapshot(self):
        """Tracks (con carpeta y color) + EDL de toda la sesión + conversor a TC. No lee mientras
        Pro Tools graba o reproduce."""
        def f(e):
            from ptsl import PTSL_pb2 as pt
            state = e.transport_state()
            if state != "TS_TransportStopped":
                return {"paused": True, "state": state}
            infos = [track_info(t, pt) for t in e.track_list()]
            tops = top_folders(infos)
            tracks = [{"id": t["id"], "name": t["name"], "color": t["color"], "folder": tops.get(t["name"], "")}
                      for t in infos if "Folder" not in t["type"]]
            b = e.export_session_as_text()
            b.include_track_edls(); b.time_type("samples"); b.dont_show_crossfades()
            edl = parse_session_text(b.export_string())
            return {"paused": False, "session": e.session_name(), "tracks": tracks, "edl": edl, "tc": self._tc(e)}
        return self._call(f, timeout=40)

    def rec_selection(self):
        """Tracks con selección de edición y el rango seleccionado (para «renombrar desde la selección»)."""
        def f(e):
            tracks = e.track_list()
            cand = [t for t in tracks if self._is_set(t.track_attributes.has_edit_selection)]
            if not cand:
                cand = [t for t in tracks if self._is_set(t.track_attributes.is_selected)]
            if not cand:
                raise SpotError("Hacé clic en un clip de spotting en Pro Tools primero")
            a, b = self._selection_samples(e)
            return {"tracks": [t.name for t in sorted(cand, key=lambda t: t.index)], "in": a, "out": b}
        return self._call(f)

    # -- avisos (SubscribeToEvents + PollEvents por streaming) -------------------- #
    EVENT_IDS = ("EId_SessionOpened", "EId_SessionCreated", "EId_SessionClosed", "EId_TrackNameChanged")

    def _raw(self, eng, command, body: dict, streaming=False):
        from ptsl import PTSL_pb2 as pt
        hdr = pt.RequestHeader(task_id="", session_id=eng.client.session_id, command=command, version=2025)
        try:
            hdr.version_minor = 6
        except Exception:                          # noqa: BLE001
            pass
        req = pt.Request(header=hdr, request_body_json=json.dumps(body))
        stub = eng.client.raw_client
        return stub.SendGrpcStreamingRequest(req) if streaming else stub.SendGrpcRequest(req, timeout=5)

    def start_events(self, bus):
        """Hilo que escucha los avisos de Pro Tools. Si Pro Tools es anterior a 2025.06 o falla, queda inactivo."""
        def loop():
            from ptsl import PTSL_pb2 as pt
            while True:
                eng = self._engine
                if eng is None:
                    time.sleep(2); continue
                try:
                    ok = []
                    for eid in self.EVENT_IDS:
                        r = self._raw(eng, pt.CId_SubscribeToEvents, {"events": [{"event_id": eid}]})
                        if r.header.status == pt.Completed:
                            ok.append(eid)
                    if not ok:
                        bus.status = "Pro Tools no ofrece avisos (hace falta 2025.06 o posterior)"
                        time.sleep(60); continue
                    bus.status = "activos: " + ", ".join(EVENT_LABELS[e] for e in ok)
                    log.info("Avisos: %s", bus.status)
                    call = self._raw(eng, pt.CId_PollEvents, {}, streaming=True)
                    self._ev_call = call
                    for resp in call:
                        if self._engine is not eng:        # reconectamos: este stream es viejo
                            call.cancel(); break
                        body = resp.response_body_json
                        if not body:
                            continue
                        try:
                            ev = json.loads(body).get("event") or {}
                            data = json.loads(ev.get("event_data_json") or "{}")
                        except ValueError:
                            continue
                        if ev.get("event_id"):
                            bus.push(ev["event_id"], data)
                except Exception as e:                     # noqa: BLE001
                    bus.status = "reintentando"
                    log.debug("Avisos: %s", e)
                time.sleep(3)
        threading.Thread(target=loop, daemon=True, name="events").start()

    def follow_selection(self):
        """Lectura liviana para «Seguir Pro Tools»: tracks seleccionados + rango (sin leer toda la lista)."""
        from ptsl import PTSL_pb2 as pt

        def f(e):
            state = e.transport_state()
            if state != "TS_TransportStopped":          # grabando/reproduciendo: no tocar nada
                return {"tracks": [], "in": 0, "out": 0, "state": state}
            flt = [pt.TrackListInvertibleFilter(filter=pt.TLFilter_Selected, is_inverted=False)]
            tracks = e.track_list(filters=flt)
            if not tracks:
                return {"tracks": [], "in": 0, "out": 0}
            a, b = self._selection_samples(e)
            return {"tracks": [t.name for t in sorted(tracks, key=lambda t: t.index)], "in": a, "out": b}
        return self._call(f, timeout=5)

    def rename_clip_at(self, track: str, start: int, end: int, new_name: str):
        """Renombra el clip group que está en [start, end) del track: lo selecciona y usa RenameSelectedClip.
        (RenameTargetClip va por nombre y hay muchos clips con el mismo nombre en el spotting.)"""
        from ptsl import PTSL_pb2 as pt

        def f(e):
            e.select_tracks_by_name([track])
            e.set_timeline_selection(in_time=str(int(start)), out_time=str(int(end)),
                                     location_type=pt.TLType_Samples)
            if self._selection_samples(e) != (int(start), int(end)):
                raise SpotError("No pude seleccionar ese clip en Pro Tools (¿Link Track and Edit Selection?)")
            e.rename_selected_clip(new_name=new_name.strip(), rename_file=False, clip_location=pt.CL_Timeline)
            return {"ok": True, "msg": f"Clip renombrado: «{new_name.strip()}»"}
        return self._call(f)

    def color_palette(self):
        def f(e):
            from ptsl import ops
            from ptsl import PTSL_pb2 as pt
            op = _op('CId_GetColorPalette')(color_palette_target=pt.CPTarget_Tracks)
            e.client.run(op)
            return {"ok": True, "colors": list(op.response.color_list)}
        return self._call(f)

    def create_track(self, name: str, after_track: str, color_index: int = -1):
        """Crea un track mono de audio justo después de after_track (queda en su carpeta) y le pone color."""
        from ptsl import PTSL_pb2 as pt
        from ptsl import ops

        def f(e):
            op = _op('CId_CreateNewTracks')(number_of_tracks=1, track_name=name.strip(), track_format=pt.TF_Mono,
                                         track_type=pt.TT_Audio, track_timebase=pt.TTB_Samples,
                                         insertion_point_position=pt.TIPoint_After,
                                         insertion_point_track_name=after_track)
            e.client.run(op)
            created = list(getattr(op.response, "created_track_names", []) or []) or [name.strip()]
            if color_index is not None and int(color_index) >= 0:
                self._paint(e, created, int(color_index))
            return {"ok": True, "msg": f"Track «{created[0]}» creado", "track": created[0]}
        return self._call(f)

    _color_offset = None        # si el índice de color de Pro Tools arranca en 0 o en 1 (se aprende al pintar)

    def _paint(self, e, track_names, i):
        """Pinta con el color i de la paleta y verifica leyendo el color que quedó; si quedó corrido, corrige."""
        from ptsl import ops
        from ptsl import PTSL_pb2 as pt
        pal_op = _op('CId_GetColorPalette')(color_palette_target=pt.CPTarget_Tracks)
        e.client.run(pal_op)
        pal = [c.lower() for c in pal_op.response.color_list]
        off = self._color_offset or 0
        e.client.run(_op('CId_SetTrackColor')(track_names=track_names, color_index=i + off))
        if self._color_offset is None and pal:
            got = next((t.color.lower() for t in e.track_list() if t.name == track_names[0]), "")
            if got and 0 <= i < len(pal) and got != pal[i] and got in pal:
                self._color_offset = i - pal.index(got)
                log.info("Colores: Pro Tools usa índices corridos en %s, corrijo", self._color_offset)
                e.client.run(_op('CId_SetTrackColor')(track_names=track_names, color_index=i + self._color_offset))
            elif got:
                self._color_offset = 0

    def add_marker(self, name: str, color_index: int = -1):
        """Marcador en el inicio de la selección actual (regla principal), con color opcional."""
        from ptsl import PTSL_pb2 as pt

        def f(e):
            a, _b = self._selection_samples(e)
            tc = self._tc(e)
            start = tc(a) if tc else str(a)
            kw = dict(start_time=start, name=name.strip(), time_properties=pt.TP_Marker,
                      reference=pt.MLR_Absolute, location=pt.MarkerLocation_MainRuler)
            if color_index is not None and int(color_index) >= 0:
                kw["color_index"] = int(color_index) + (self._color_offset or 0)
            try:
                e.create_memory_location(**kw)
            except TypeError:                # versiones de py-ptsl sin «location»
                kw.pop("location", None)
                e.create_memory_location(**kw)
            return {"ok": True, "msg": f"Marcador «{name.strip()}» en {start}"}
        return self._call(f)

    def rec_go(self, start: int, end: int, rec_track_id: str, name: str, locate: bool = True):
        """Posiciona Pro Tools en el clip y renombra el track de grabación."""
        from ptsl import PTSL_pb2 as pt

        def f(e):
            notes = []
            if name and "Recording" in e.transport_state():
                raise SpotError("Pro Tools está grabando: no renombro el track ahora")
            if locate:
                e.set_timeline_selection(in_time=str(int(start)), out_time=str(int(end)),
                                         location_type=pt.TLType_Samples)
                if self._selection_samples(e) != (int(start), int(end)):
                    notes.append("no pude posicionar Pro Tools en el clip")
            msg = "Posicionado" if locate else "Listo"
            if rec_track_id and name:
                tracks = e.track_list()
                target = next((t for t in tracks if t.id == rec_track_id), None)
                if target is None:
                    raise SpotError("El track de grabación ya no existe: elegilo de nuevo")
                new = name.strip()
                if target.name != new:
                    taken = {t.name for t in tracks if t.id != rec_track_id}
                    base, n = new, 2
                    while new in taken:
                        new = f"{base} {n}"; n += 1
                    e.rename_target_track(old_name=target.name, new_name=new)
                msg = f"Track de grabación: «{new}»"
            if notes:
                msg += " · ⚠ " + "; ".join(notes)
            return {"ok": True, "msg": msg}
        return self._call(f)

    def locate(self, start: int, end: int):
        from ptsl import PTSL_pb2 as pt

        def f(e):
            e.set_timeline_selection(in_time=str(int(start)), out_time=str(int(end)),
                                     location_type=pt.TLType_Samples)
            return {"ok": True}
        return self._call(f)


# --------------------------------------------------------------------------- #
#  Mock para probar sin Pro Tools
# --------------------------------------------------------------------------- #
class MockProTools:
    def __init__(self):
        self._tracks = [{"id": f"t{i}", "name": n, "index": i} for i, n in enumerate(
            ["DIAL GUIDE", "HANDS SPOT", "HANDS 1", "HANDS 2", "HANDS 3", "PROPS PAPEL"], 1)]
        self._last = "Hands grab body"

    def status(self):
        return {"connected": True, "session": "MOCK_R1_Foley", "ptsl": 0, "mock": True}

    def group_and_name(self, name):
        self._last = name
        return {"ok": True, "msg": f"Clip group «{name}» (mock)"}

    def peek(self):
        return {"ok": True, "clip": self._last, "track": "HANDS SPOT"}

    def tracks(self):
        return self._tracks

    def rename_track_to_selection(self, track_id, prefix=""):
        t = next((t for t in self._tracks if t["id"] == track_id), None)
        if not t:
            raise SpotError("El track destino ya no existe en la sesión")
        new = track_name_for(prefix, self._last)
        old, t["name"] = t["name"], new
        return {"ok": True, "msg": f"«{old}» → «{new}» (mock)"}

    def undo(self):
        return {"ok": True, "msg": "Deshecho (mock)"}

    def layout(self, exclude=()):
        F = lambda i, n: {"id": f"f{i}", "name": n, "index": i, "type": "TT_BasicFolder", "color": "", "parent_id": "", "parent_name": ""}
        T = lambda i, n, p, c="": {"id": f"t{i}", "name": n, "index": i, "type": "TT_Audio", "color": c,
                                   "parent_id": f"f{p}", "parent_name": ""}
        tr = [F(10, "Surfaces")] + [T(11 + k, n, 10) for k, n in enumerate(
                  ["Gritty", "Concrete Clean", "Wood", "Loose Wood", "Grass", "Gravel", "Carpet", "Water", "Special"])] + \
             [F(30, "Footsteps"), T(31, "Henry", 30, "#3E9B4F"), T(32, "Sonia", 30, "#C9567A"),
              T(33, "Male Shoes", 30, "#3E9B4F"), T(34, "Femme Shoes", 30, "#C9567A"), T(35, "Sneakers", 30, "#3B7DD8"),
              T(36, "Boots", 30, "#8A5A2B"), T(37, "Barefoot", 30), T(38, "Group", 30)] + \
             [F(50, "Props"), F(51, "Hands", ), T(52, "Hands Body", 51), T(53, "Hands Surfaces", 51)] + \
             [T(54 + k, n, 50) for k, n in enumerate(
                  ["Movements", "Chairs", "Bags", "Bijou", "Cell Phones", "Glass Bottles", "Keyboards",
                   "Papers", "Tableware", "Props 1", "Props 2"])]
        tr[[t["id"] for t in tr].index("f51")]["parent_id"] = "f50"
        return build_layout(tr, exclude)

    def group_on_named(self, track_name, name=""):
        t = next((t for g in self.layout()["tabs"] for gr in g["groups"] for t in gr["tracks"]
                  if t["name"].lower() == track_name.strip().lower()), None)
        if not t:
            raise SpotError(f"No hay un track «{track_name}» en esta sesión. Cambiá el destino del botón con ✎ Editar.")
        return self.group_on_track(t["id"], name)

    def group_on_track(self, track_id, name=""):
        t = next((t for g in self.layout()["tabs"] for gr in g["groups"] for t in gr["tracks"] if t["id"] == track_id), None)
        if not t:
            raise SpotError("Ese track ya no existe en la sesión")
        clip = (name or t["name"]).strip()
        self._last = clip
        return {"ok": True, "msg": f"«{clip}» en {t['name']} (mock)"}

    def health(self):
        return {"connected_once": True, "connections": 1, "stuck": False, "inflight": None,
                "last_ok": time.strftime("%H:%M:%S"), "last_error": None,
                "history": [{"at": time.strftime("%H:%M:%S"), "command": "GetSessionName", "ms": 3, "result": "ok"}]}

    def reconnect(self):
        return {"ok": True, **self.status()}

    def diag(self):
        return "MOCK\n" + json.dumps(self.layout(), indent=1, ensure_ascii=False)

    def spotting(self, track_ids):
        import time
        if not track_ids:
            return {"ok": True, "paused": False, "need_tracks": True, "clips": []}
        conv = TcConverter(48000, "00:59:58:00", rate_from_enum("STCR_Fps24"))
        names = ["Hands clap", "Hands grab body", "Hands surface wood", "Hands body", "Hands punch"]
        # Cada 20 s aparece un clip nuevo, para ver cómo se marcan los nuevos
        n = 6 + int(time.time() // 20) % 8
        clips = []
        for i in range(n):
            st = 48000 * (4 + i * 7)
            en = st + 48000 * (1 + i % 3)
            clips.append({"track": "HANDS SPOT", "clip": names[i % len(names)], "start": st, "end": en,
                          "tc_in": conv(st), "tc_out": conv(en), "dur": conv.duration(en - st)})
        return {"ok": True, "paused": False, "clips": clips}

    def locate(self, start, end):
        return {"ok": True}

    def rec_snapshot(self):
        from edl import EdlEvent
        lay = self.layout()
        tracks = [{"id": t["id"], "name": t["name"], "color": t["color"], "folder": tab["name"]}
                  for tab in lay["tabs"] for g in tab["groups"] for t in g["tracks"]]
        S = 48000
        E = lambda tr, clip, a, b, m=False: EdlEvent(tr, clip, int(a * S), int(b * S), m)
        edl = {
            "Wood": [E("Wood", "Wood", 0, 40)],
            "Concrete Clean": [E("Concrete Clean", "Concrete Clean", 35, 90)],
            "Grass": [E("Grass", "Grass", 90, 140)],
            "Henry": [E("Henry", "Henry", 2, 6), E("Henry", "Henry", 12, 15), E("Henry", "Henry Sneakers", 50, 55),
                      E("Henry", "Henry", 36, 39), E("Henry", "Henry", 95, 99, True)],
            "Sonia": [E("Sonia", "Sonia", 20, 24), E("Sonia", "Sonia", 100, 104)],
            "Sneakers": [E("Sneakers", "Extra Left", 60, 64), E("Sneakers", "Sneakers", 110, 113)],
            "Group": [E("Group", "Group", 120, 130)],
            "Chairs": [E("Chairs", "Chair sit", 8, 9), E("Chairs", "Chair drag", 70, 72)],
            "Hands Surfaces": [E("Hands Surfaces", "Hands surface wood", 30, 31)],
        }
        return {"paused": False, "session": "MOCK_R1_Foley", "tracks": tracks, "edl": edl,
                "tc": TcConverter(S, "01:00:00:00", rate_from_enum("STCR_Fps25"))}

    def rec_selection(self):
        return {"tracks": ["Henry"], "in": 36 * 48000, "out": 37 * 48000}

    mock_sel = {"tracks": [], "in": 0, "out": 0}

    def start_events(self, bus):
        bus.status = "activos (mock)"

    def follow_selection(self):
        return dict(self.mock_sel)

    def rename_clip_at(self, track, start, end, new_name):
        return {"ok": True, "msg": f"Clip renombrado: «{new_name}» (mock)"}

    def color_palette(self):
        return {"ok": True, "colors": ["#ff3E9B4F", "#ffC9567A", "#ff3B7DD8", "#ff8A5A2B", "#ffE8A33D", "#ff7c0089"]}

    def create_track(self, name, after_track, color_index=-1):
        return {"ok": True, "msg": f"Track «{name}» creado (mock)", "track": name}

    def add_marker(self, name, color_index=-1):
        return {"ok": True, "msg": f"Marcador «{name}» (mock)"}

    def rec_go(self, start, end, rec_track_id, name, locate=True):
        if not rec_track_id:
            return {"ok": True, "msg": "Posicionado (mock) · sin track de grabación elegido"}
        t = next((t for t in self._tracks if t["id"] == rec_track_id), None)
        if not t:
            raise SpotError("El track de grabación ya no existe: elegilo de nuevo")
        if not name:
            return {"ok": True, "msg": "Posicionado (mock)"}
        t["name"] = name
        return {"ok": True, "msg": f"Track de grabación: «{name}» (mock)"}


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return default


# --------------------------------------------------------------------------- #
#  Avisos de Pro Tools (Event System del SDK, desde Pro Tools 2025.06)
# --------------------------------------------------------------------------- #
EVENT_LABELS = {
    "EId_SessionOpened": "Se abrió la sesión",
    "EId_SessionCreated": "Se creó una sesión",
    "EId_SessionClosed": "Se cerró la sesión",
    "EId_TrackNameChanged": "Track renombrado",
}


class EventBus:
    def __init__(self):
        from collections import deque
        self.seq = 0
        self.items = deque(maxlen=50)
        self.listeners = []
        self.lock = threading.Lock()
        self.status = "inactivos"

    def push(self, event_id: str, data=None):
        with self.lock:
            self.seq += 1
            ev = {"seq": self.seq, "at": time.strftime("%H:%M:%S"), "id": event_id,
                  "label": EVENT_LABELS.get(event_id, event_id.replace("EId_", "")), "data": data or {}}
            self.items.append(ev)
        log.info("Aviso de Pro Tools: %s %s", ev["label"], json.dumps(ev["data"], ensure_ascii=False)[:200])
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:                      # noqa: BLE001
                log.exception("listener de avisos")

    def since(self, seq: int):
        with self.lock:
            return [e for e in self.items if e["seq"] > seq]


EVENTS = EventBus()


# --------------------------------------------------------------------------- #
class RecController:
    def __init__(self, pt):
        self.pt = pt
        self.snap = None            # última foto de la sesión (tracks + EDL)
        self.lock = threading.RLock()
        EVENTS.listeners.append(self.on_event)

    def on_event(self, ev):
        """Sesión abierta/cerrada → la foto vieja no sirve; si se abrió, releer en segundo plano."""
        if ev["id"] in ("EId_SessionClosed", "EId_SessionOpened", "EId_SessionCreated"):
            self.snap = None
            self.follow_item = None
        if ev["id"] in ("EId_SessionOpened", "EId_SessionCreated"):
            def later():
                time.sleep(2)
                try:
                    self.refresh()
                except Exception:                  # noqa: BLE001
                    pass
            threading.Thread(target=later, daemon=True).start()

    # -- persistencia ---------------------------------------------------------- #
    def store(self):
        return load_json(REC_FILE, {"rec_track": "", "sweep": [], "surface_filter": "", "auto_rename": True,
                                    "cur": "", "follow": False, "sessions": {}})

    def save(self, st):
        REC_FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False), "utf-8")

    def set_state(self, patch):
        with self.lock:
            st = self.store()
            for k in ("rec_track", "sweep", "surface_filter", "auto_rename", "cur", "follow"):
                if k in patch:
                    st[k] = patch[k]
            self.save(st)
            return self.build(st)

    # -- foto de la sesión y cola ---------------------------------------------- #
    def refresh(self):
        snap = self.pt.rec_snapshot()
        if snap.get("paused"):
            base = self.build() if self.snap else {"ok": True, "items": []}
            base["paused"] = True
            return base
        snap["at"] = time.strftime("%H:%M:%S")
        self.snap = snap
        return self.build()

    def build(self, st=None, sweep=None):
        st = st or self.store()
        snap = self.snap
        if not snap:
            return {"ok": True, "items": [], "need_refresh": True, "cur": st.get("cur", "")}
        rules = load_rules()
        sess = st.get("sessions", {}).get(snap["session"], {})
        prefixes = load_json(PRESETS_FILE, {}).get("track_prefixes", ["Prps", "Fts"])
        q = naming.build_queue(snap["tracks"], snap["edl"], sweep if sweep is not None else st.get("sweep", []),
                               rules, sess.get("choices", {}), set(sess.get("done", [])), prefixes,
                               None if sweep is not None else (st.get("surface_filter") or None), snap["tc"])
        if sweep is not None:
            return q
        cur = st.get("cur", "")
        fi = self.follow_item
        if cur and fi and fi["key"] == cur and not any(i["key"] == cur for i in q["items"]):
            q["items"].append({**fi, "extra": True})      # clic en Pro Tools sobre un track que no se está barriendo
        kinds = {t["name"]: naming.folder_kind(t["folder"], rules) for t in snap["tracks"]}
        sweepable = [{"name": t["name"], "color": t["color"], "folder": t["folder"], "kind": kinds[t["name"]]}
                     for t in snap["tracks"] if kinds[t["name"]]]
        fts = [t for t in snap["tracks"] if kinds[t["name"]] == "footsteps"]
        colors = naming.shoe_colors(fts, rules)
        char_shoes = {naming.norm(k): v for k, v in rules.get("char_shoes", {}).items()}
        fts_info = []
        for t in fts:
            c = naming.classify_fts(t["name"], rules)
            fts_info.append({"name": t["name"], "color": t["color"], "type": c["type"], "character": c["character"],
                             "shoe_track": c["shoe"], "shoe_color": colors.get(str(t["color"]).lower()),
                             "shoe_fixed": char_shoes.get(naming.norm(c["character"] or "")),
                             "shoe": c["shoe"] or colors.get(str(t["color"]).lower())})
        surf = [t["name"] for t in snap["tracks"] if kinds[t["name"]] == "surfaces"]
        return {"ok": True, "session": snap["session"], "at": snap["at"], **q, "sweepable": sweepable,
                "fts": fts_info, "surface_alias": rules.get("surface_alias", {}), "surface_tracks": surf,
                "cur": st.get("cur", ""), "follow": bool(st.get("follow")), "follow_info": self.follow_info}

    def _sess(self, st):
        return st.setdefault("sessions", {}).setdefault(self.snap["session"], {})

    def choice(self, body):
        with self.lock:
            st = self.store()
            if body.get("character") and "char_shoe" in body:      # calzado fijo de un personaje (todas las sesiones)
                user = load_json(RULES_FILE, {})
                cs = user.setdefault("char_shoes", {})
                if body["char_shoe"]:
                    cs[str(body["character"]).upper()] = body["char_shoe"]
                else:
                    cs.pop(str(body["character"]).upper(), None)
                RULES_FILE.write_text(json.dumps(user, indent=2, ensure_ascii=False), "utf-8")
            if body.get("key") and self.snap:
                ch = self._sess(st).setdefault("choices", {})
                c = ch.setdefault(body["key"], {})
                for k in ("surface", "shoe", "name"):
                    if k in body:
                        if body[k]:
                            c[k] = body[k]
                        else:
                            c.pop(k, None)
                if not c:
                    ch.pop(body["key"], None)
            self.save(st)
            return self.build(st)

    def mark_done(self, key, done=True):
        with self.lock:
            st = self.store()
            if self.snap and key:
                sess = self._sess(st)
                d = set(sess.get("done", []))
                (d.add if done else d.discard)(key)
                sess["done"] = sorted(d)
                self.save(st)
            return self.build(st)

    # -- ir a un clip / navegar ------------------------------------------------- #
    def _item(self, q, key):
        return next((i for i in q["items"] if i["key"] == key), None)

    def go_to(self, key, start, end, name, rename=None):
        with self.lock:
            st = self.store()
            if key:
                st["cur"] = key
                self.save(st)
            do_rename = st.get("auto_rename", True) if rename is None else bool(rename)
            r = self.pt.rec_go(start, end, st.get("rec_track", ""), name.strip() if do_rename else "")
            return {**r, "cur": key or st.get("cur", "")}

    def _go_item(self, it, st):
        rename = st.get("auto_rename", True) and not it["needs"]
        r = self.go_to(it["key"], it["start"], it["end"], it["name"], rename)
        if it["needs"]:
            what = " y ".join({"surface": "la superficie", "shoe": "el calzado"}[n] for n in it["needs"])
            r["msg"] = f"Posicionado · falta elegir {what} en el iPad"
        return {**r, "item": it}

    def step(self, direction):
        """Siguiente/anterior pendiente en orden de tiempo, desde el clip actual."""
        with self.lock:
            if not self.snap:
                self.refresh()
            st = self.store(); q = self.build(st); items = q["items"]
            if not items:
                raise SpotError("No hay nada para barrer: elegí los tracks en la pestaña Grabar")
            pend = lambda x: not x["muted"] and not x["done"]
            i = next((k for k, x in enumerate(items) if x["key"] == st.get("cur")), -1)
            j = (0 if direction > 0 else len(items) - 1) if i < 0 else i + direction
            while 0 <= j < len(items) and not pend(items[j]):
                j += direction
            if not 0 <= j < len(items):
                raise SpotError("No hay más pendientes " + ("adelante" if direction > 0 else "atrás"))
            return self._go_item(items[j], st)

    def done_next(self):
        with self.lock:
            st = self.store()
            if not st.get("cur"):
                raise SpotError("No hay un clip actual: usá Siguiente primero")
            self.mark_done(st["cur"], True)
            return self.step(1)

    # -- Seguir Pro Tools: clic en un clip → clip actual (y nombre del track de grabación) -- #
    _last_sel = None
    _last_auto_refresh = 0.0
    _follow_thread = None
    follow_info = {}
    follow_item = None

    def follow_tick(self):
        st = self.store()
        if not st.get("follow"):
            self._last_sel = None
            return None
        try:
            sel = self.pt.follow_selection()
        except SpotError:
            return None
        sig = (tuple(sel["tracks"]), sel["in"], sel["out"])
        if sig == self._last_sel or not sel["tracks"]:
            return None
        self._last_sel = sig
        if not self.snap:
            return None
        with self.lock:
            st = self.store()
            for tname in sel["tracks"]:
                q = self.build(st, sweep=[tname])
                hits = [i for i in q["items"] if naming.overlaps(sel["in"], sel["out"], i["start"], i["end"])]
                if not hits:
                    continue
                it = max(hits, key=lambda i: min(i["end"], max(sel["out"], sel["in"] + 1)) - max(i["start"], sel["in"]))
                if it["key"] == st.get("cur"):
                    return None
                st["cur"] = it["key"]; self.save(st)
                msg = "Seleccionado"
                if st.get("auto_rename", True) and st.get("rec_track") and not it["needs"]:
                    try:
                        msg = self.pt.rec_go(it["start"], it["end"], st["rec_track"], it["name"], locate=False)["msg"]
                    except SpotError as e:
                        msg = str(e)
                self.follow_info = {"at": time.strftime("%H:%M:%S"), "key": it["key"], "msg": msg}
                self.follow_item = it
                log.info("Seguir Pro Tools: %s → %s", it["name"], msg)
                return it
        # Selección sobre algo que no está en la foto: puede ser un clip nuevo → releer (como mucho cada 20 s)
        kinds = {t["name"]: naming.folder_kind(t["folder"], load_rules()) for t in self.snap["tracks"]}
        if any(kinds.get(t) for t in sel["tracks"]) and time.time() - self._last_auto_refresh > 20:
            self._last_auto_refresh = time.time()
            self._last_sel = None
            log.info("Seguir Pro Tools: clip desconocido, releo la sesión")
            try:
                self.refresh()
            except SpotError:
                pass
        return None

    def start_follow(self, every=0.5):
        """Hilo que revisa la selección de Pro Tools (solo hace algo si «Seguir Pro Tools» está activado)."""
        if self._follow_thread and self._follow_thread.is_alive():
            return

        def loop():
            while True:
                time.sleep(every)
                try:
                    self.follow_tick()
                except Exception:                  # noqa: BLE001
                    log.debug("follow", exc_info=True)
        self._follow_thread = threading.Thread(target=loop, daemon=True, name="follow")
        self._follow_thread.start()

    # -- renombrar desde el clip seleccionado en Pro Tools ---------------------- #
    def from_selection(self):
        """Clic en un clip de spotting en Pro Tools + atajo → el track de grabación toma su nombre."""
        with self.lock:
            st = self.store()
            if not st.get("rec_track"):
                raise SpotError("Elegí el track de grabación en la pestaña Grabar del iPad")
            sel = self.pt.rec_selection()           # {"tracks": [...], "in": n, "out": n}
            for attempt in (0, 1):
                if not self.snap or attempt:
                    self.refresh()
                for tname in sel["tracks"]:
                    q = self.build(st, sweep=[tname])
                    hits = [i for i in q["items"] if naming.overlaps(sel["in"], sel["out"], i["start"], i["end"])]
                    if hits:
                        it = max(hits, key=lambda i: min(i["end"], max(sel["out"], sel["in"] + 1))
                                                     - max(i["start"], sel["in"]))
                        if it["needs"]:
                            st["cur"] = it["key"]; self.save(st)
                            what = " y ".join({"surface": "la superficie", "shoe": "el calzado"}[n] for n in it["needs"])
                            raise SpotError(f"«{it['name']}»: falta elegir {what} (en la pestaña Grabar)")
                        r = self.pt.rec_go(it["start"], it["end"], st["rec_track"], it["name"], locate=False)
                        st["cur"] = it["key"]; self.save(st)
                        return {**r, "item": it, "cur": it["key"]}
            raise SpotError("No encontré un clip de spotting bajo la selección")


def make_app(pt, rec=None):
    routes = web.RouteTableDef()

    async def run(fn, *args):
        loop = asyncio.get_running_loop()
        try:
            return web.json_response(await loop.run_in_executor(None, fn, *args))
        except SpotError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=409)

    @routes.get("/")
    async def index(_):
        return web.FileResponse(RES / "static" / "index.html")

    # Página para la Mac: QR + dirección para abrir SpotPad en el iPad
    @routes.get("/conectar")
    async def conectar(req):
        url = ipad_url(req.app["port"])
        return web.Response(content_type="text/html", text=CONNECT_PAGE.format(url=url, qr=qr_svg(url)))

    @routes.get("/api/status")
    async def status(_):
        loop = asyncio.get_running_loop()
        try:
            st = await loop.run_in_executor(None, pt.status)
        except SpotError as e:
            st = {"connected": False, "error": str(e)}
        last = EVENTS.items[-1] if EVENTS.items else None
        return web.json_response({**st, "ev_seq": EVENTS.seq, "ev_last": last, "ev_status": EVENTS.status})

    @routes.get("/api/events")
    async def events(req):
        try:
            since = int(req.query.get("since", "0"))
        except ValueError:
            since = 0
        return web.json_response({"seq": EVENTS.seq, "status": EVENTS.status, "events": EVENTS.since(since)})

    @routes.get("/api/presets")
    async def presets(_):
        return web.json_response(load_json(PRESETS_FILE, {"categories": []}))

    # El editor del iPad guarda acá las categorías (botones, nombres, tracks destino)
    @routes.put("/api/presets")
    async def put_presets(req):
        try:
            clean = clean_presets(await req.json())
        except (ValueError, AttributeError, TypeError) as e:
            return web.json_response({"ok": False, "error": f"No se pudo guardar: {e}"}, status=400)
        data = load_json(PRESETS_FILE, {})
        data["categories"] = clean["categories"]
        PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        log.info("Presets guardados desde el iPad (%s categorías)", len(clean["categories"]))
        return web.json_response(data)

    # Carpetas / tracks ocultos en la botonera (se guardan por nombre: valen para cualquier sesión)
    @routes.get("/api/hidden")
    async def get_hidden(_):
        return web.json_response(load_json(HIDDEN_FILE, {"folders": [], "tracks": []}))

    @routes.put("/api/hidden")
    async def put_hidden(req):
        body = await req.json()
        clean = {k: sorted({str(x).strip() for x in body.get(k, []) if str(x).strip()}, key=str.lower)
                 for k in ("folders", "tracks")}
        HIDDEN_FILE.write_text(json.dumps(clean, indent=2, ensure_ascii=False), "utf-8")
        return web.json_response(clean)

    # Botón preseteado: con track destino → ahí; sin track → donde esté la selección
    @routes.post("/api/group")
    async def group(req):
        body = await req.json()
        name = str(body.get("name", "")).strip()
        track = str(body.get("track", "") or "").strip()
        if not name:
            return web.json_response({"ok": False, "error": "Falta el nombre"}, status=400)
        if track:
            return await run(pt.group_on_named, track, name)
        return await run(pt.group_and_name, name)

    # Atajo para teclado/Stream Deck: /api/group/hands/3 → 3er botón de "hands"
    @routes.post("/api/group/{cat}/{n}")
    async def group_slot(req):
        cats = load_json(PRESETS_FILE, {"categories": []})["categories"]
        cat = next((c for c in cats if c["id"] == req.match_info["cat"]), None)
        try:
            item = cat["items"][int(req.match_info["n"]) - 1]
        except (TypeError, IndexError, ValueError):
            return web.json_response({"ok": False, "error": "Botón inexistente"}, status=404)
        if item_track(item, cat):
            return await run(pt.group_on_named, item_track(item, cat), item_name(item))
        return await run(pt.group_and_name, item_name(item))

    @routes.get("/api/peek")
    async def peek(_):
        return await run(pt.peek)

    @routes.get("/api/tracks")
    async def tracks(_):
        return await run(pt.tracks)

    @routes.get("/api/targets")
    async def get_targets(_):
        return web.json_response(load_json(TARGETS_FILE, []))

    @routes.put("/api/targets")
    async def put_targets(req):
        ids = [str(x) for x in await req.json()]
        TARGETS_FILE.write_text(json.dumps(ids, indent=2), "utf-8")
        return web.json_response(ids)

    @routes.post("/api/rename/{track_id:[^/]+}")
    async def rename(req):
        return await run(pt.rename_track_to_selection, req.match_info["track_id"], current_prefix(req))

    # Atajo para teclado: /api/rename-slot/2 → 2º track destino pinneado
    @routes.post("/api/rename-slot/{n}")
    async def rename_slot(req):
        ids = load_json(TARGETS_FILE, [])
        try:
            tid = ids[int(req.match_info["n"]) - 1]
        except (IndexError, ValueError):
            return web.json_response({"ok": False, "error": "Slot vacío"}, status=404)
        return await run(pt.rename_track_to_selection, tid, current_prefix(req))

    # Prefijo del track: el elegido en el iPad, o ?prefix=Fts en la llamada (atajos)
    def prefixes():
        return load_json(PRESETS_FILE, {}).get("track_prefixes", ["Prps", "Fts"])

    def current_prefix(req):
        q = req.query.get("prefix")
        if q is not None:
            return q
        return load_json(STATE_FILE, {}).get("prefix", prefixes()[0])

    @routes.get("/api/prefix")
    async def get_prefix(req):
        return web.json_response({"prefix": current_prefix(req), "options": prefixes()})

    @routes.put("/api/prefix")
    async def put_prefix(req):
        p = str((await req.json()).get("prefix", ""))
        if p not in prefixes():
            return web.json_response({"ok": False, "error": "Prefijo desconocido"}, status=400)
        st = load_json(STATE_FILE, {}); st["prefix"] = p
        STATE_FILE.write_text(json.dumps(st, indent=2), "utf-8")
        return web.json_response({"prefix": p, "options": prefixes()})

    # Botonera armada desde las carpetas de la sesión
    def excluded():
        return load_json(PRESETS_FILE, {}).get("exclude_folders", [])

    @routes.get("/api/layout")
    async def layout(_):
        return await run(pt.layout, excluded())

    @routes.post("/api/group-on/{track_id:[^/]+}")
    async def group_on(req):
        try:
            body = await req.json()
        except Exception:
            body = {}
        return await run(pt.group_on_track, req.match_info["track_id"], str(body.get("name", "")))

    # Atajo por nombre de track: /api/group-on-name/Wood
    @routes.post("/api/group-on-name/{name:[^/]+}")
    async def group_on_name(req):
        name = req.match_info["name"]
        lay = await asyncio.get_running_loop().run_in_executor(None, pt.layout, excluded())
        t = next((t for tab in lay["tabs"] for g in tab["groups"] for t in g["tracks"] if t["name"] == name), None)
        if not t:
            return web.json_response({"ok": False, "error": f"No hay un track «{name}» en las carpetas"}, status=404)
        return await run(pt.group_on_track, t["id"], req.query.get("clip", ""))

    # Lista de spotting en vivo
    @routes.get("/api/spotting")
    async def spotting(_):
        return await run(pt.spotting, set(load_json(SPOT_TRACKS_FILE, [])))

    @routes.get("/api/spot-tracks")
    async def get_spot_tracks(_):
        return web.json_response(load_json(SPOT_TRACKS_FILE, []))

    @routes.put("/api/spot-tracks")
    async def put_spot_tracks(req):
        ids = [str(x) for x in await req.json()]
        SPOT_TRACKS_FILE.write_text(json.dumps(ids, indent=2), "utf-8")
        return web.json_response(ids)

    @routes.post("/api/locate")
    async def locate(req):
        body = await req.json()
        try:
            return await run(pt.locate, int(body["start"]), int(body["end"]))
        except (KeyError, ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Faltan start/end"}, status=400)

    # ---- Modo grabación (la lógica vive en RecController: la usan también los atajos) ---- #
    rec = rec or RecController(pt)
    rec.start_follow()
    if not getattr(pt, "_events_started", False):
        pt._events_started = True
        pt.start_events(EVENTS)

    async def rec_call(fn, *args):
        loop = asyncio.get_running_loop()
        try:
            return web.json_response(await loop.run_in_executor(None, fn, *args))
        except SpotError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=409)

    @routes.get("/api/rec/state")
    async def rec_state(_):
        st = rec.store(); st.pop("sessions", None)
        return web.json_response(st)

    @routes.put("/api/rec/state")
    async def rec_put_state(req):
        return web.json_response(rec.set_state(await req.json()))

    @routes.post("/api/rec/refresh")
    async def rec_refresh(_):
        return await rec_call(rec.refresh)

    @routes.get("/api/rec/queue")
    async def rec_queue(_):
        return web.json_response(rec.build())

    @routes.post("/api/rec/choice")
    async def rec_choice(req):
        return web.json_response(rec.choice(await req.json()))

    @routes.post("/api/rec/done")
    async def rec_done(req):
        body = await req.json()
        return web.json_response(rec.mark_done(body.get("key"), body.get("done", True)))

    @routes.post("/api/rec/go")
    async def rec_go(req):
        body = await req.json()
        try:
            start, end = int(body["start"]), int(body["end"])
        except (KeyError, ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Faltan start/end"}, status=400)
        return await rec_call(rec.go_to, body.get("key"), start, end, str(body.get("name", "")), body.get("rename"))

    # Acciones de una tecla (atajos globales, Stream Deck, Keyboard Maestro)
    @routes.post("/api/rec/next")
    async def rec_next(_):
        return await rec_call(rec.step, 1)

    @routes.post("/api/rec/prev")
    async def rec_prev(_):
        return await rec_call(rec.step, -1)

    @routes.post("/api/rec/done-next")
    async def rec_done_next(_):
        return await rec_call(rec.done_next)

    @routes.post("/api/rec/from-selection")
    async def rec_from_sel(_):
        return await rec_call(rec.from_selection)

    # ---- Spotting: renombrar clip group, crear track en carpeta, marcador de escena ---- #
    @routes.post("/api/clip/rename")
    async def clip_rename(req):
        b = await req.json()
        try:
            track, start, end, name = str(b["track"]), int(b["start"]), int(b["end"]), str(b["name"]).strip()
        except (KeyError, ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Faltan datos del clip"}, status=400)
        if not name:
            return web.json_response({"ok": False, "error": "Poné un nombre"}, status=400)
        resp = await run(pt.rename_clip_at, track, start, end, name)
        if resp.status == 200 and rec.snap:                   # que la foto de la sesión ya tenga el nombre nuevo
            for ev in rec.snap["edl"].get(track, []):
                if ev.start == start:
                    ev.clip = name
        return resp

    @routes.get("/api/palette")
    async def palette(_):
        return await run(pt.color_palette)

    @routes.post("/api/track/create")
    async def track_create(req):
        b = await req.json()
        name, after = str(b.get("name", "")).strip(), str(b.get("after", "")).strip()
        if not name or not after:
            return web.json_response({"ok": False, "error": "Faltan el nombre o la carpeta"}, status=400)
        return await run(pt.create_track, name, after, int(b.get("color_index", -1)))

    @routes.post("/api/marker")
    async def marker(req):
        b = await req.json()
        name = str(b.get("name", "")).strip()
        if not name:
            return web.json_response({"ok": False, "error": "Poné un nombre"}, status=400)
        return await run(pt.add_marker, name, int(b.get("color_index", -1)))

    if isinstance(pt, MockProTools):          # solo en modo prueba: simular avisos y clics en Pro Tools
        @routes.post("/api/mock/event")
        async def mock_event(req):
            b = await req.json(); EVENTS.push(b.get("id", "EId_SessionOpened"), b.get("data", {}))
            return web.json_response({"ok": True})

        @routes.post("/api/mock/select")
        async def mock_select(req):
            pt.mock_sel = await req.json()
            return web.json_response({"ok": True})

    @routes.get("/api/hotkeys")
    async def get_hotkeys(_):
        import hotkeys as hk
        cfg = hk.load(DATA / "hotkeys.json")
        return web.json_response({"enabled": cfg.get("enabled", True),
                                  "keys": [{"action": a, "desc": hk.ACTIONS[a], "label": hk.label(b)}
                                           for a, b in cfg["bindings"].items() if a in hk.ACTIONS]})

    @routes.get("/api/rec/rules")
    async def rec_rules(_):
        return web.json_response(load_rules())

    @routes.put("/api/rec/rules")
    async def rec_put_rules(req):
        body = await req.json(); user = load_json(RULES_FILE, {})
        for k in ("color_shoes", "char_shoes", "surface_alias", "shoes", "folders", "title_case_caps"):
            if k in body:
                user[k] = body[k]
        RULES_FILE.write_text(json.dumps(user, indent=2, ensure_ascii=False), "utf-8")
        return web.json_response(rec.build() if rec.snap else {"ok": True})

    # Diagnóstico y reconexión (health no habla con Pro Tools: contesta siempre)
    @routes.get("/api/health")
    async def health(_):
        return web.json_response({"version": VERSION, **pt.health()})

    @routes.post("/api/reconnect")
    async def reconnect(_):
        return await run(pt.reconnect)

    @routes.get("/api/report")
    async def report(_):
        txt = await asyncio.get_running_loop().run_in_executor(None, build_report, pt)
        return web.Response(text=txt, content_type="text/plain", charset="utf-8")

    @routes.post("/api/undo")
    async def undo(_):
        return await run(pt.undo)

    app = web.Application()
    app["port"] = 8765
    app["rec"] = rec
    app.add_routes(routes)
    return app


def ipad_url(port: int) -> str:
    return f"http://{lan_ip()}:{port}"


def qr_svg(text: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
        return img.to_string(encoding="unicode")
    except Exception:
        return ""


CONNECT_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><title>SpotPad · conectar iPad</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#121214;color:#ecebe6;
font:16px -apple-system,system-ui,sans-serif;text-align:center}}.q{{background:#fff;border-radius:18px;padding:14px;
display:inline-block;width:300px}}.q svg{{width:100%;height:auto;display:block}}code{{font:600 22px ui-monospace,Menlo,monospace;
color:#E8A33D}}p{{color:#8d8c94;max-width:420px;margin:14px auto}}</style></head><body><div>
<div class="q">{qr}</div><p>Escaneá con la cámara del iPad, o escribí en Safari:</p><code>{url}</code>
<p>En Safari: Compartir → Agregar a inicio, para usarla a pantalla completa.<br>El iPad y esta computadora tienen que estar en la misma red.</p>
</div></body></html>"""


def start_server(pt, port: int = 8765, rec=None):
    """Levanta el servidor en un hilo aparte (lo usa la app de la barra de menú).
    Devuelve una función para apagarlo."""
    loop = asyncio.new_event_loop()
    app = make_app(pt, rec)
    app["port"] = port
    runner = web.AppRunner(app)
    ready, err = threading.Event(), []

    def serve():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(runner.setup())
            loop.run_until_complete(web.TCPSite(runner, "0.0.0.0", port).start())
        except Exception as e:      # puerto ocupado, etc.
            err.append(e)
            ready.set()
            return
        ready.set()
        loop.run_forever()
        loop.run_until_complete(runner.cleanup())

    threading.Thread(target=serve, daemon=True, name="spotpad-http").start()
    ready.wait(10)
    if err:
        raise err[0]

    def stop():
        loop.call_soon_threadsafe(loop.stop)
    return stop


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser(description="SpotPad bridge para Pro Tools")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mock", action="store_true", help="probar sin Pro Tools")
    ap.add_argument("--debug", action="store_true", help="loguear el tráfico PTSL")
    ap.add_argument("--diag", action="store_true", help="imprimir tracks/carpetas/colores de la sesión y salir")
    a = ap.parse_args()
    setup_logging(a.debug)

    if a.diag:
        txt = (MockProTools() if a.mock else ProTools(debug=a.debug)).diag()
        DIAG_FILE.write_text(txt, "utf-8")
        print(txt + f"\n\n(guardado también en {DIAG_FILE})")
        return

    pt = MockProTools() if a.mock else ProTools(debug=a.debug)
    print(f"\n  SpotPad listo. Abrí en el iPad:  http://{lan_ip()}:{a.port}\n"
          f"  (o http://{socket.gethostname()}:{a.port})\n")
    app = make_app(pt)
    app["port"] = a.port
    web.run_app(app, host="0.0.0.0", port=a.port, print=None)


if __name__ == "__main__":
    main()
