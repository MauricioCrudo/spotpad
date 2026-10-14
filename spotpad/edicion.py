"""
Herramientas de edición (en prueba, apagadas por defecto). Viven aparte del resto de SpotPad:
solo se usan desde sus propias rutas (/api/edit/...) y atajos, y no cambian nada de lo demás.

- Consolidar: consolida la selección en el mismo track y le pone al clip nuevo (y a su archivo)
  el nombre del primer clip de la selección, sin «_03», «-01», «.L», etc.
- Regrabar: lo que suena de los tracks seleccionados, en el rango, pasa a un único clip en el
  track «Regrabación». Se hace con un bounce offline de la salida que escuchás con esos tracks
  en solo (no se toca el ruteo). Después se restauran los solos y la selección queda en los
  originales: ⌘M (Mute Clips) los mutea, porque el SDK no tiene un comando para mutear clips.
"""
import glob
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata

log = logging.getLogger("spotpad")

DEFAULTS = {"enabled": False, "regrab_track": "Regrabación", "regrab_source": "", "regrab_mono": True}


class EditError(Exception):
    pass


def sugerir_track(names) -> str:
    """El track de regrabación más probable por nombre: «Regrabación», «REGRAB», «Rerec»…"""
    for want in ("regrabacion", "regrab", "rerec", "re-rec", "re rec"):
        hit = next((n for n in names if want in _plain(n)), None)
        if hit:
            return hit
    return ""


def base_clip_name(name: str) -> str:
    """«Prps Chair Wood_03-01.L» → «Prps Chair Wood» (sin los agregados de Pro Tools)."""
    n = str(name or "").strip()
    n = re.sub(r"\.(L|R|C|Ls|Rs|LFE|dup\d+|grp.*|\d+)$", "", n, flags=re.I)
    n = n.split(".")[0]
    n = re.sub(r"([_\-]\d+)+$", "", n).strip()
    return n


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def first_clip(events, a: int, b: int):
    """El primer clip (el que empieza antes) que se superpone con [a, b)."""
    hits = [ev for ev in events if min(ev.end, b) > max(ev.start, a)]
    return min(hits, key=lambda ev: ev.start) if hits else None


PT_BUNDLE = "com.avid.ProTools"


def _pt_al_frente():
    """Trae Pro Tools al frente (sin Apple Events: `open -b` no pide permiso de Automatización)."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSWorkspace
        front = lambda: str(NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier() or "")
    except Exception:                            # noqa: BLE001
        front = lambda: ""
    if front() == PT_BUNDLE:
        return
    subprocess.run(["open", "-b", PT_BUNDLE], timeout=5)
    for _ in range(20):
        if front() == PT_BUNDLE:
            return
        time.sleep(0.1)
    time.sleep(0.3)


def mandar_consolidar():
    """⌥⇧3 (Consolidate Clip) a Pro Tools, como si se tocara a mano. El comando ConsolidateClip del
    SDK no sirve: en Pro Tools 2025.12 deja un tono de 1 kHz en vez del audio (visto en la prueba real).
    Necesita permiso de Accesibilidad (Mac)."""
    if sys.platform == "darwin":
        import hotkeys
        if not hotkeys.mac_trusted():
            hotkeys.mac_trusted(prompt=True)
            raise EditError("Consolidar necesita el permiso de Accesibilidad: Ajustes del Sistema → Privacidad y "
                            "seguridad → Accesibilidad → SpotPad. Después reabrí SpotPad.")
        import Quartz as Q
        # Esperar a que se suelten las teclas del atajo (⌃⌥⌘K), si no se suman a ⌥⇧3
        for _ in range(30):
            flags = Q.CGEventSourceFlagsState(Q.kCGEventSourceStateHIDSystemState)
            if not flags & (Q.kCGEventFlagMaskControl | Q.kCGEventFlagMaskCommand |
                            Q.kCGEventFlagMaskAlternate | Q.kCGEventFlagMaskShift):
                break
            time.sleep(0.05)
        _pt_al_frente()
        tecla = 20                               # el «3» de la fila de números (igual en teclado español)
        mods = Q.kCGEventFlagMaskAlternate | Q.kCGEventFlagMaskShift
        for down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, tecla, down)
            Q.CGEventSetFlags(ev, mods)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
            time.sleep(0.03)
    else:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        with kb.pressed(Key.alt, Key.shift):
            kb.press("3"); kb.release("3")


class EditTools:
    def __init__(self, pt, settings_loader):
        self.pt = pt
        self.settings = settings_loader          # () -> dict con las claves de DEFAULTS
        self.teclas = mandar_consolidar          # se reemplaza en los tests
        self.espera = 0.15
        self.limite = 10                         # segundos esperando el consolidado

    def cfg(self):
        return {**DEFAULTS, **{k: v for k, v in (self.settings() or {}).items() if k in DEFAULTS}}

    # ---- utilidades sobre el engine (corren dentro de pt._call) ---------------- #
    def _selected(self, e):
        tracks = e.track_list()
        sel = [t for t in tracks if self.pt._is_set(t.track_attributes.has_edit_selection)]
        if not sel:
            raise EditError("Seleccioná un rango en uno o más tracks primero")
        a, b = self.pt._selection_samples(e)
        if b <= a:
            raise EditError("La selección está vacía: marcá un rango")
        return tracks, sorted(sel, key=lambda t: t.index), a, b

    def _edl(self, e, solo_sel=False):
        """Clips por track. Con solo_sel=True exporta solo los tracks seleccionados (mucho más rápido
        en sesiones grandes: sirve mientras se espera el consolidado de un track)."""
        from edl import parse_session_text
        bld = e.export_session_as_text()
        bld.include_track_edls(); bld.time_type("samples"); bld.dont_show_crossfades()
        if solo_sel:
            bld.selected_tracks_only()
        return parse_session_text(bld.export_string())

    def _reselect(self, e, names, a, b):
        from ptsl import PTSL_pb2 as pt
        try:
            e.select_tracks_by_name(names)
            e.set_timeline_selection(in_time=str(a), out_time=str(b), location_type=pt.TLType_Samples)
        except Exception as ex:                  # noqa: BLE001
            log.info("Edición: no pude volver a la selección: %s", ex)

    def _apply_selection(self, e, names, a, b):
        """Selecciona los tracks y el rango, y verifica que quedó. Al cambiar de track con
        SelectTracksByName, Pro Tools a veces pierde el rango (y Consolidar da «No clip is selected»)."""
        from ptsl import PTSL_pb2 as pt
        for _ in range(3):
            e.select_tracks_by_name(names)
            e.set_timeline_selection(in_time=str(a), out_time=str(b), location_type=pt.TLType_Samples)
            try:
                if tuple(self.pt._selection_samples(e)) == (a, b):
                    return
            except Exception:                    # noqa: BLE001
                return
            time.sleep(0.15)
        log.warning("Edición: el rango no quedó aplicado (%s-%s)", a, b)

    def _esperar_consolidado(self, e, track, a, b, antes=(), tol=2):
        """Espera a que el track tenga un solo clip nuevo (que no estaba antes) cubriendo todo [a, b]: lo
        que deja Consolidate Clip. Devuelve su nombre, o None si no pasó en ~10 s. Mientras Pro Tools
        consolida no contesta: el pedido espera solo."""
        fin = time.time() + self.limite
        while time.time() < fin:
            time.sleep(self.espera)
            evs = [ev for ev in self._edl(e, solo_sel=True).get(track, []) if min(ev.end, b) > max(ev.start, a)]
            if len(evs) == 1 and evs[0].clip not in antes and evs[0].start <= a + tol and evs[0].end >= b - tol:
                return evs[0].clip
        return None

    def _rename_free(self, e, name):
        """Renombra el clip seleccionado (y su archivo). Si ya hay un archivo con ese nombre
        («That name is already used by another file») prueba «Nombre 2», «Nombre 3»…"""
        from bridge import _op
        from ptsl import PTSL_pb2 as pt
        for i in range(1, 30):
            want = name if i == 1 else f"{name} {i}"
            try:
                e.client.run(_op("CId_RenameSelectedClip")(new_name=want, rename_file=True,
                                                           clip_location=pt.CL_Timeline))
                return want
            except Exception as ex:              # noqa: BLE001
                if "already used" not in str(ex):
                    raise
        raise EditError(f"Ya hay demasiados archivos llamados «{name}»")

    # ---- consolidar ---------------------------------------------------------- #
    def consolidate(self):
        if not self.cfg()["enabled"]:
            raise EditError("Las herramientas de edición están apagadas (Ajustes)")

        def f(e):
            if e.transport_state() != "TS_TransportStopped":
                raise EditError("Pará Pro Tools primero")
            _tracks, sel, a, b = self._selected(e)
            edl = self._edl(e)
            done, notes = [], []
            for t in sel:
                ev = first_clip(edl.get(t.name, []), a, b)
                if ev is None:
                    notes.append(f"«{t.name}» no tiene clips en la selección")
                    continue
                name = base_clip_name(ev.clip) or t.name
                antes = {ev2.clip for ev2 in edl.get(t.name, []) if min(ev2.end, b) > max(ev2.start, a)}
                self._apply_selection(e, [t.name], a, b)
                self.teclas()                    # ⌥⇧3 en Pro Tools (no el ConsolidateClip del SDK: da un tono)
                hecho = self._esperar_consolidado(e, t.name, a, b, antes)
                if hecho is None:
                    notes.append(f"«{t.name}»: Pro Tools no consolidó (¿estaba Pro Tools al frente?)")
                    log.info("Consolidar: %s no quedó consolidado", t.name)
                    continue
                # Pro Tools nombra el consolidado como el track («Prps 01_02») y después del atajo el clip
                # no queda seleccionado para el SDK («No clip is selected»): se vuelve a marcar el rango.
                self._apply_selection(e, [t.name], a, b)
                try:
                    name = self._rename_free(e, name)
                except Exception as ex:          # noqa: BLE001
                    notes.append(f"«{t.name}» consolidado como «{hecho}», sin renombrar: {ex}")
                    continue
                # Verificar que el nombre quedó en el clip (y no en otra cosa)
                ev2 = first_clip(self._edl(e, solo_sel=True).get(t.name, []), a, b)
                if ev2 is None or base_clip_name(ev2.clip) != name:
                    notes.append(f"«{t.name}»: consolidado, pero el clip quedó como «{ev2.clip if ev2 else '?'}»")
                    log.info("Consolidar: %s → el rename no quedó en el clip (%s)", t.name, ev2.clip if ev2 else None)
                    continue
                done.append(name)
                log.info("Consolidar: %s → «%s»", t.name, name)
            self._reselect(e, [t.name for t in sel], a, b)
            if not done:
                raise EditError("; ".join(notes) or "No había nada para consolidar")
            msg = "Consolidado: " + ", ".join(f"«{n}»" for n in done)
            return {"ok": True, "msg": msg + (" · ⚠ " + "; ".join(notes) if notes else "")}
        return self._run(f, 120)

    # ---- regrabar en un solo clip ---------------------------------------------- #
    # Ojo: NO usar GetExportMixSourceList. En Pro Tools 2025.12 cuelga y cierra Pro Tools (visto en
    # la prueba real al abrir Ajustes). La salida se lee con GetMonitorOutputPath (la de monitoreo de la
    # sesión, que es la que escuchás): EN PRUEBA, solo cuando la persona toca el botón, nunca sola.
    def sesion(self, salida=False):
        """Tracks de audio activos (para elegir el de regrabación, con uno sugerido por nombre) y,
        si se pide, la salida de monitoreo de la sesión."""
        from bridge import _op, is_inactive
        from ptsl import PTSL_pb2 as pt

        def tipo(t):
            try:
                return pt.TrackType.Name(t.type)
            except Exception:                    # noqa: BLE001
                return str(t.type)

        def f(e):
            names = [t.name for t in sorted(e.track_list(), key=lambda t: t.index)
                     if not is_inactive(t) and "Audio" in tipo(t)]
            out = {"ok": True, "tracks": names, "sugerido": sugerir_track(names)}
            if salida:
                log.info("Regrabar: leyendo la salida de monitoreo (GetMonitorOutputPath, en prueba)")
                op = _op("CId_GetMonitorOutputPath")()
                e.client.run(op)
                out["salida"] = str(getattr(op.response, "monitor_path", "") or "").strip()
                if not out["salida"]:
                    raise EditError("Pro Tools no devolvió una salida de monitoreo: escribila a mano")
            return out
        return self._run(f, 10)

    def regrab(self):
        cfg = self.cfg()
        if not cfg["enabled"]:
            raise EditError("Las herramientas de edición están apagadas (Ajustes)")
        from bridge import _op
        from ptsl import PTSL_pb2 as pt

        def f(e):
            if e.transport_state() != "TS_TransportStopped":
                raise EditError("Pará Pro Tools primero")
            tracks, sel, a, b = self._selected(e)
            want = _plain(cfg["regrab_track"])
            dest = next((t for t in tracks if _plain(t.name) == want), None)
            if dest is None:
                raise EditError(f"No hay un track «{cfg['regrab_track']}» en la sesión (crealo una vez en el template)")
            sel = [t for t in sel if t.id != dest.id]
            if not sel:
                raise EditError("Seleccioná el rango en los tracks que querés regrabar (no en Regrabación)")
            edl = self._edl(e)
            name = next((base_clip_name(ev.clip) for t in sel
                         for ev in [first_clip(edl.get(t.name, []), a, b)] if ev), "") or "Regrabacion"

            src = cfg["regrab_source"].strip()
            if not src:
                raise EditError("Escribí en Ajustes la salida que escuchás, como figura en Bounce Mix (p. ej. «Out 1-2»)")

            soloed = {t.name for t in tracks if self.pt._is_set(t.track_attributes.is_soloed)}
            sel_names = [t.name for t in sel]
            tmp = tempfile.mkdtemp(prefix="spotpad-regrab-")
            try:
                # Solo lo seleccionado suena (con sus plugins y faders), como lo escuchás
                off = sorted(soloed - set(sel_names))
                if off:
                    e.set_track_solo_state(off, False)
                e.set_track_solo_state(sel_names, True)
                audio = pt.EM_AudioInfo(compression_type=pt.CType_PCM,
                                        export_format=pt.EFormat_Mono if cfg["regrab_mono"] else pt.EFormat_Interleaved,
                                        delivery_format=pt.EMDFormat_SingleFile)
                try:
                    audio.bit_depth = e.session_bit_depth()
                    audio.sample_rate = e.session_sample_rate()
                except Exception:                # noqa: BLE001 (si no, usa lo del diálogo)
                    pass
                # En Bounce Mix la salida puede ser un «output», una «physical out» o un «bus»: el
                # nombre solo no alcanza. Se prueba en ese orden; si no existe, Pro Tools contesta al
                # toque «None of the specified mix sources are present» y se pasa al siguiente.
                # La carpeta: Pro Tools (Sys_FileLoc… assertion) no acepta bien la de /var/folders;
                # se prueba con la ruta real y barra final, y si no, con una carpeta en la Música.
                tipos = [("output", pt.EMSType_Output), ("physical out", pt.EMSType_PhysicalOut),
                         ("bus", pt.EMSType_Bus)]
                alt = os.path.join(os.path.expanduser("~"), "Music", "SpotPad-regrab")
                os.makedirs(alt, exist_ok=True)
                carpetas = [os.path.realpath(tmp) + "/", alt + "/"]
                err, hecho = None, False
                for carpeta in carpetas:
                    tmp = carpeta
                    falla_carpeta = False
                    for etiqueta, st in tipos:
                        exp = _op("CId_ExportMix")(
                            file_name=name, file_type=pt.EMFType_WAV,
                            mix_source_list=[pt.EM_SourceInfo(source_type=st, name=src)],
                            audio_info=audio,
                            location_info=pt.EM_LocationInfo(file_destination=pt.EMFDestination_Directory,
                                                             directory=carpeta, import_after_bounce=pt.TBool_False),
                            offline_bounce=pt.TBool_True,
                            start_time=pt.TimelineLocation(location=str(a), time_type=pt.TLType_Samples),
                            end_time=pt.TimelineLocation(location=str(b), time_type=pt.TLType_Samples))
                        log.info("Regrabar: bounce de %s (%s «%s») %s-%s → %s",
                                 ", ".join(sel_names), etiqueta, src, a, b, carpeta)
                        try:
                            e.client.run(exp)
                            hecho = True
                            break
                        except Exception as ex:  # noqa: BLE001
                            m = str(ex)
                            if "mix source" in m:
                                err = ex
                                continue
                            if "Assertion" in m or "FileLoc" in m:
                                falla_carpeta = True
                                err = ex
                                break
                            raise
                    if hecho or not falla_carpeta:
                        break
                if not hecho:
                    if err is not None and "mix source" in str(err):
                        raise EditError(f"Pro Tools no encuentra la salida «{src}» (probé output, physical out y bus). "
                                        "Abrí File → Bounce Mix y copiá en Ajustes el nombre exacto de «Bounce Source»; "
                                        "los tracks tienen que estar ruteados a esa salida.")
                    raise EditError(f"Pro Tools no pudo escribir el bounce en una carpeta temporal: {err}")
            finally:
                # Solos como estaban
                try:
                    e.set_track_solo_state(sel_names, False)
                    if soloed:
                        e.set_track_solo_state(sorted(soloed), True)
                except Exception as ex:          # noqa: BLE001
                    log.warning("Regrabar: no pude restaurar los solos: %s", ex)

            files = []
            for _ in range(30):                  # el archivo puede tardar en aparecer
                files = glob.glob(os.path.join(tmp, "*.wav")) + glob.glob(os.path.join(tmp, "*.WAV"))
                if files:
                    break
                time.sleep(0.5)
            if not files:
                raise EditError("El bounce no dejó ningún archivo")
            imp = _op("CId_ImportAudioToClipList")(file_list=[files[0]], audio_operations=pt.AOperations_CopyAudio)
            e.client.run(imp)
            clips = [c for fl in imp.response.file_list for d in fl.destination_file_list for c in d.clip_id_list]
            if not clips:
                raise EditError("Pro Tools no importó el archivo regrabado")
            e.client.run(_op("CId_SpotClipsByID")(
                src_clips=clips[:1], dst_track_id=dest.id,
                dst_location_data=pt.SpotLocationData(
                    location_type=pt.SLType_Start,
                    location=pt.TimelineLocation(location=str(a), time_type=pt.TLType_Samples))))
            self._reselect(e, sel_names, a, b)
            log.info("Regrabar: «%s» en %s", name, dest.name)
            return {"ok": True, "msg": f"«{name}» en {dest.name}. Los originales quedan seleccionados: ⌘M los mutea."}
        return self._run(f, 600)

    def _run(self, f, timeout):
        from bridge import SpotError
        try:
            return self.pt._call(f, timeout=timeout)
        except EditError as e:
            raise SpotError(str(e))


class MockEditTools(EditTools):
    def consolidate(self):
        if not self.cfg()["enabled"]:
            raise EditError("Las herramientas de edición están apagadas (Ajustes)")
        return {"ok": True, "msg": "Consolidado: «Prps Chair Wood» (mock)"}

    def regrab(self):
        if not self.cfg()["enabled"]:
            raise EditError("Las herramientas de edición están apagadas (Ajustes)")
        return {"ok": True, "msg": "«Prps Chair Wood» en Regrabación (mock). Los originales quedan seleccionados: ⌘M los mutea."}

    def sesion(self, salida=False):
        names = ["Pre", "Rec Prps", "Regrabación", "Dialogos"]
        return {"ok": True, "tracks": names, "sugerido": sugerir_track(names), **({"salida": "Out 1-2"} if salida else {})}
