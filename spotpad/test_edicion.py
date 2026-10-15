"""Herramientas de edición (consolidar / regrabar) con un Pro Tools simulado."""
import os, tempfile, types
os.environ["SPOTPAD_DATA"] = tempfile.mkdtemp()
import edicion
from edicion import EditTools, base_clip_name, first_clip
from edl import EdlEvent
from bridge import ProTools

assert base_clip_name("Prps Chair Wood_03-01.L") == "Prps Chair Wood"
assert base_clip_name("Fts Henry Male Shoes Grass_12") == "Fts Henry Male Shoes Grass"
assert base_clip_name("Chair sit.dup1") == "Chair sit"
ev = [EdlEvent("T", "B_02", 50, 80), EdlEvent("T", "A_01", 10, 60), EdlEvent("T", "C", 200, 300)]
assert first_clip(ev, 40, 90).clip == "A_01" and first_clip(ev, 100, 150) is None

N = lambda **k: types.SimpleNamespace(**k)
def T(name, idx, tid, sel=False, solo=False):
    return N(name=name, index=idx, id=tid, track_attributes=N(has_edit_selection=3 if sel else 1, is_soloed=3 if solo else 1))

class Eng:
    def __init__(s, tracks, edl, tmpfile=True):
        s.tracks, s.edl, s.ran, s.solo, s.sel, s.tmpfile = tracks, edl, [], [], [], tmpfile
        eng = s
        class C:
            def run(_, op):
                n = type(op).__name__; eng.ran.append(n)
                if n == "CId_ExportMix" and eng.tmpfile:
                    d = op.request.location_info.directory
                    open(os.path.join(d, op.request.file_name + ".wav"), "w").close()
                    eng.export = op.request
                if n == "CId_GetExportMixSourceList":
                    op.response = N(source_list=["Out 1-2"])
                if n == "CId_ImportAudioToClipList":
                    op.response = N(file_list=[N(destination_file_list=[N(clip_id_list=["clip-1"])])])
                if n in ("CId_RenameSelectedClip", "CId_RenameTargetClip"): eng.renamed = getattr(eng, "renamed", []) + [op.request.new_name]
                if n == "CId_SpotClipsByID": eng.spot = op.request
        s.client = C()
    def transport_state(s): return "TS_TransportStopped"
    def track_list(s): return s.tracks
    def export_session_as_text(s):
        b = N(include_track_edls=lambda: 0, time_type=lambda x: 0, dont_show_crossfades=lambda: 0, export_string=lambda: "")
        return b
    def select_tracks_by_name(s, names): s.sel.append(list(names))
    def set_timeline_selection(s, **k): pass
    def set_track_solo_state(s, names, on): s.solo.append((tuple(names), on))
    def session_bit_depth(s): return 3
    def session_sample_rate(s): return 48000

class PT(ProTools):
    def __init__(s, eng): super().__init__(); s.eng = eng
    def _call(s, fn, timeout=None): return fn(s.eng)
    def _selection_samples(s, e): return (100, 200)

def tools(eng, **cfg):
    t = EditTools(PT(eng), lambda: {"enabled": True, **cfg})
    t._edl = lambda e, **k: eng.edl
    return t

# Consolidar: ⌥⇧3 en Pro Tools (no ConsolidateClip del SDK, que deja un tono), un track por vez,
# con el nombre del primer clip; espera el clip consolidado y verifica el nombre en el EDL.
def con_teclas(t, eng, falla=()):
    def teclas():
        tr = eng.sel[-1][0]
        if tr in falla:
            return
        evs = eng.edl.get(tr, [])
        eng.edl[tr] = [ev for ev in evs if ev.end <= 100 or ev.start >= 200] + [EdlEvent(tr, tr + "_07", 100, 200)]
        eng.consolidados = getattr(eng, "consolidados", []) + [tr]
    t.teclas, t.espera, t.limite = teclas, 0, 0.3
    run0 = eng.client.run
    def run(op):
        run0(op)
        if type(op).__name__ == "CId_RenameTargetClip":
            for tr in eng.edl:
                eng.edl[tr] = [EdlEvent(tr, op.request.new_name, ev.start, ev.end) if ev.clip == op.request.clip_name
                               else ev for ev in eng.edl[tr]]
    eng.client.run = run
    return t

eng = Eng([T("Chairs", 1, "a", sel=True), T("Bags", 2, "b", sel=True), T("Video", 0, "v")],
          {"Chairs": [EdlEvent("Chairs", "Prps Chair Wood_03", 90, 150), EdlEvent("Chairs", "x_04", 150, 220)],
           "Bags": [EdlEvent("Bags", "Prps Bag-01.L", 120, 180)]})
r = con_teclas(tools(eng), eng).consolidate()
assert eng.renamed == ["Prps Chair Wood", "Prps Bag"], eng.renamed
assert eng.consolidados == ["Chairs", "Bags"] and "CId_ConsolidateClip" not in eng.ran, eng.ran
assert eng.sel[-1] == ["Chairs", "Bags"], eng.sel

# Nombre ya usado por otro archivo: «Prps Chair Wood 2»; si un track no se consolida, sigue con el otro y avisa
eng = Eng([T("Chairs", 1, "a", sel=True), T("Bags", 2, "b", sel=True)],
          {"Chairs": [EdlEvent("Chairs", "Prps Chair Wood_03", 90, 150)], "Bags": [EdlEvent("Bags", "Prps Bag", 120, 180)]})
t = con_teclas(tools(eng), eng, falla=("Bags",))
run1 = eng.client.run
def run_usado(op):
    if type(op).__name__ == "CId_RenameTargetClip" and op.request.new_name == "Prps Chair Wood":
        raise Exception("ErrType 100: PT_UnknownError (That name is already used by another file.)")
    run1(op)
eng.client.run = run_usado
r = t.consolidate()
assert eng.renamed == ["Prps Chair Wood 2"] and "no consolidó" in r["msg"], (eng.renamed, r)

# El rename no queda en el clip: avisa, no lo da por hecho
eng = Eng([T("Chairs", 1, "a", sel=True)], {"Chairs": [EdlEvent("Chairs", "Prps Chair Wood_03", 90, 150)]})
t = con_teclas(tools(eng), eng)
eng.client.run = lambda op: None                     # Pro Tools «acepta» el rename pero no cambia el clip
try:
    t.consolidate(); raise AssertionError
except Exception as ex:
    assert "quedó como" in str(ex), ex

# El nombre sale del primer clip con nombre propio: nunca el que Pro Tools arma con el track
np_ = edicion.nombre_propio
assert np_([EdlEvent("Props", "Props_01", 100, 150), EdlEvent("Props", "Campera", 150, 200)], "Props", 100, 200) == "Campera"
assert np_([EdlEvent("Props", "Props_01-02", 100, 200)], "Props", 100, 200) == ""
eng = Eng([T("Props", 1, "p", sel=True)], {"Props": [EdlEvent("Props", "Campera", 100, 200)]})
t = con_teclas(tools(eng), eng)
r = t.consolidate()
assert eng.renamed == ["Campera"] and eng.edl["Props"][0].clip == "Campera", (eng.renamed, eng.edl)
eng = Eng([T("Props", 1, "p", sel=True)], {"Props": [EdlEvent("Props", "Props_01", 100, 200)]})
try:                                                  # reconsolidar algo sin nombre propio: no lo renombra «Props»
    con_teclas(tools(eng), eng).consolidate(); raise AssertionError
except Exception as ex:
    assert "nombre propio" in str(ex) and not getattr(eng, "renamed", []), ex

# Apagadas: no hace nada
from bridge import SpotError
try:
    EditTools(PT(eng), lambda: {"enabled": False}).consolidate(); raise AssertionError
except edicion.EditError:
    pass

# Regrabar: solo de los seleccionados, bounce, import, spot en Regrabación, solos restaurados
eng = Eng([T("Chairs", 1, "a", sel=True), T("Bags", 2, "b", sel=True), T("Regrabacion", 5, "r"),
           T("DX", 0, "d", solo=True)],
          {"Bags": [EdlEvent("Bags", "Prps Bag_02", 100, 190)], "Chairs": [EdlEvent("Chairs", "Prps Chair Wood_01", 150, 200)]})
try:                                                          # sin salida escrita: avisa
    tools(eng, regrab_track="Regrabación").regrab(); raise AssertionError
except SpotError as ex:
    assert "Bounce Mix" in str(ex)
eng.solo.clear()
r = tools(eng, regrab_track="Regrabación", regrab_source="Out 1-2").regrab()
assert "Prps Chair Wood" in r["msg"], r                       # el primer clip del track de más arriba
assert eng.spot.dst_track_id == "r" and eng.spot.src_clips == ["clip-1"]
assert eng.solo[0] == (("DX",), False) and eng.solo[1] == (("Chairs", "Bags"), True)
assert eng.solo[-2] == (("Chairs", "Bags"), False) and eng.solo[-1] == (("DX",), True)     # como estaba
assert eng.export.offline_bounce == 3 and eng.export.start_time.location == "100"
# La salida no es «output»: prueba physical out y después bus
eng.solo.clear()
run0 = eng.client.run
def run_bus(op):
    if type(op).__name__ == "CId_ExportMix" and op.request.mix_source_list[0].source_type != ptpb0.EMSType_Bus:
        raise Exception("ErrType 100: PT_UnknownError (None of the specified mix sources are present and in use in the session.)")
    run0(op)
from ptsl import PTSL_pb2 as ptpb0
eng.client.run = run_bus
tools(eng, regrab_track="Regrabación", regrab_source="Bus 1-2").regrab()
assert eng.export.mix_source_list[0].source_type == ptpb0.EMSType_Bus
# Ninguna sirve: mensaje claro y solos restaurados
eng.solo.clear()
eng.client.run = lambda op: (_ for _ in ()).throw(Exception("None of the specified mix sources are present")) \
    if type(op).__name__ == "CId_ExportMix" else run0(op)
try:
    tools(eng, regrab_track="Regrabación", regrab_source="Bocinas").regrab(); raise AssertionError
except SpotError as ex:
    assert "Bounce Source" in str(ex) and eng.solo[-1] == (("DX",), True), (ex, eng.solo)
# Sin track de regrabación: avisa
eng2 = Eng([T("Chairs", 1, "a", sel=True)], {})
try:
    tools(eng2).regrab(); raise AssertionError
except SpotError as e:
    assert "Regrabación" in str(e)
# Leer de la sesión: lista de tracks de audio + sugerido; la salida SOLO si se pide
from ptsl import PTSL_pb2 as ptpb
assert edicion.sugerir_track(["Pre", "REGRABACIÓN 1", "Rec"]) == "REGRABACIÓN 1"
assert edicion.sugerir_track(["Pre", "Rerec Fts"]) == "Rerec Fts" and edicion.sugerir_track(["Pre"]) == ""
def TA(name, idx, ttype="TT_Audio"):
    t = T(name, idx, name); t.type = ptpb.TrackType.Value(ttype); t.track_attributes.is_inactive = 1; return t
eng3 = Eng([TA("Dialogos", 2), TA("FOLEY", 0, "TT_BasicFolder"), TA("Regrabación", 3), TA("Pre", 1)], {})
r = tools(eng3).sesion()
assert r["tracks"] == ["Pre", "Dialogos", "Regrabación"] and r["sugerido"] == "Regrabación" and "salida" not in r, r
assert "CId_GetMonitorOutputPath" not in eng3.ran
run0 = eng3.client.run
def run_mon(op):
    run0(op)
    if type(op).__name__ == "CId_GetMonitorOutputPath":
        op.response = N(monitor_path="Out 1-2 ")
eng3.client.run = run_mon
assert tools(eng3).sesion(salida=True)["salida"] == "Out 1-2"
eng3.client.run = lambda op: setattr(op, "response", N(monitor_path=""))
try:
    tools(eng3).sesion(salida=True); raise AssertionError
except SpotError as ex:
    assert "a mano" in str(ex)
# Nunca GetExportMixSourceList ni GetColorPalette (cuelgan Pro Tools 2025.12)
import pathlib
for f in ("edicion.py", "bridge.py"):
    code = "\n".join(l for l in pathlib.Path(__file__).with_name(f).read_text("utf-8").splitlines() if not l.strip().startswith("#"))
    assert "GetExportMixSourceList\")" not in code and "GetColorPalette')" not in code, f
    assert "CId_ConsolidateClip" not in code, f       # deja un tono de 1 kHz en Pro Tools 2025.12
print("edición OK")
