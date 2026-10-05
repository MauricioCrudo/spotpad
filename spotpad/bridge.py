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

VERSION = "0.3.4"
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
LOG_FILE = DATA / "spotpad.log"


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
        from ptsl import PTSL_pb2 as pt

        def f(e):
            target = next((t for t in e.track_list() if t.id == track_id), None)
            if target is None:
                raise SpotError("Ese track ya no existe en la sesión")
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
        return self._call(f)

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
             [F(50, "Props"), F(51, "Hands", ), T(52, "Hands Body", 51), T(53, "Hands Surface", 51)] + \
             [T(54 + k, n, 50) for k, n in enumerate(
                  ["Movements", "Chairs", "Bags", "Bijou", "Cell Phones", "Glass Bottles", "Keyboards",
                   "Papers", "Tableware", "Props 1", "Props 2"])]
        tr[[t["id"] for t in tr].index("f51")]["parent_id"] = "f50"
        return build_layout(tr, exclude)

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


# --------------------------------------------------------------------------- #
#  HTTP
# --------------------------------------------------------------------------- #
def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return default


def make_app(pt):
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
        return await run(pt.status)

    @routes.get("/api/presets")
    async def presets(_):
        return web.json_response(load_json(PRESETS_FILE, {"categories": []}))

    @routes.post("/api/group")
    async def group(req):
        body = await req.json()
        name = str(body.get("name", "")).strip()
        if not name:
            return web.json_response({"ok": False, "error": "Falta el nombre"}, status=400)
        return await run(pt.group_and_name, name)

    # Atajo para teclado/Stream Deck: /api/group/hands/3 → 3er botón de "hands"
    @routes.post("/api/group/{cat}/{n}")
    async def group_slot(req):
        cats = load_json(PRESETS_FILE, {"categories": []})["categories"]
        cat = next((c for c in cats if c["id"] == req.match_info["cat"]), None)
        try:
            name = cat["items"][int(req.match_info["n"]) - 1]
        except (TypeError, IndexError, ValueError):
            return web.json_response({"ok": False, "error": "Botón inexistente"}, status=404)
        return await run(pt.group_and_name, name)

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


def start_server(pt, port: int = 8765):
    """Levanta el servidor en un hilo aparte (lo usa la app de la barra de menú).
    Devuelve una función para apagarlo."""
    loop = asyncio.new_event_loop()
    app = make_app(pt)
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
