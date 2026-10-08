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
import tempfile
import time
import unicodedata

log = logging.getLogger("spotpad")

DEFAULTS = {"enabled": False, "regrab_track": "Regrabación", "regrab_source": "", "regrab_mono": True}


class EditError(Exception):
    pass


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


class EditTools:
    def __init__(self, pt, settings_loader):
        self.pt = pt
        self.settings = settings_loader          # () -> dict con las claves de DEFAULTS

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

    def _edl(self, e):
        from edl import parse_session_text
        bld = e.export_session_as_text()
        bld.include_track_edls(); bld.time_type("samples"); bld.dont_show_crossfades()
        return parse_session_text(bld.export_string())

    def _reselect(self, e, names, a, b):
        from ptsl import PTSL_pb2 as pt
        try:
            e.select_tracks_by_name(names)
            e.set_timeline_selection(in_time=str(a), out_time=str(b), location_type=pt.TLType_Samples)
        except Exception as ex:                  # noqa: BLE001
            log.info("Edición: no pude volver a la selección: %s", ex)

    # ---- consolidar ---------------------------------------------------------- #
    def consolidate(self):
        if not self.cfg()["enabled"]:
            raise EditError("Las herramientas de edición están apagadas (Ajustes)")
        from bridge import _op
        from ptsl import PTSL_pb2 as pt

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
                e.select_tracks_by_name([t.name])
                e.set_timeline_selection(in_time=str(a), out_time=str(b), location_type=pt.TLType_Samples)
                e.client.run(_op("CId_ConsolidateClip")())
                e.client.run(_op("CId_RenameSelectedClip")(new_name=name, rename_file=True,
                                                           clip_location=pt.CL_Timeline))
                done.append(name)
                log.info("Consolidar: %s → «%s»", t.name, name)
            self._reselect(e, [t.name for t in sel], a, b)
            if not done:
                raise EditError("; ".join(notes) or "No había nada para consolidar")
            msg = "Consolidado: " + ", ".join(f"«{n}»" for n in done)
            return {"ok": True, "msg": msg + (" · ⚠ " + "; ".join(notes) if notes else "")}
        return self._run(f, 120)

    # ---- regrabar en un solo clip ---------------------------------------------- #
    def sources(self):
        from bridge import _op
        from ptsl import PTSL_pb2 as pt

        def f(e):
            op = _op("CId_GetExportMixSourceList")(type=pt.EMSType_Output)
            e.client.run(op)
            return {"ok": True, "sources": list(getattr(op.response, "source_list", []) or [])}
        return self._run(f, 15)

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

            src = cfg["regrab_source"]
            if not src:
                op = _op("CId_GetExportMixSourceList")(type=pt.EMSType_Output)
                e.client.run(op)
                lst = list(getattr(op.response, "source_list", []) or [])
                if not lst:
                    raise EditError("Pro Tools no informó salidas para el bounce")
                src = lst[0]

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
                exp = _op("CId_ExportMix")(
                    file_name=name, file_type=pt.EMFType_WAV,
                    mix_source_list=[pt.EM_SourceInfo(source_type=pt.EMSType_Output, name=src)],
                    audio_info=audio,
                    location_info=pt.EM_LocationInfo(file_destination=pt.EMFDestination_Directory, directory=tmp,
                                                     import_after_bounce=pt.TBool_False),
                    offline_bounce=pt.TBool_True,
                    start_time=pt.TimelineLocation(location=str(a), time_type=pt.TLType_Samples),
                    end_time=pt.TimelineLocation(location=str(b), time_type=pt.TLType_Samples))
                log.info("Regrabar: bounce de %s (%s) %s-%s → %s", ", ".join(sel_names), src, a, b, tmp)
                e.client.run(exp)
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

    def sources(self):
        return {"ok": True, "sources": ["Out 1-2", "Monitor"]}
