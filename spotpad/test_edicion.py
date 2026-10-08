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
                if n == "CId_RenameSelectedClip": eng.renamed = getattr(eng, "renamed", []) + [op.request.new_name]
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
    t._edl = lambda e: eng.edl
    return t

# Consolidar: un track por vez, con el nombre del primer clip, y la selección vuelve a los originales
eng = Eng([T("Chairs", 1, "a", sel=True), T("Bags", 2, "b", sel=True), T("Video", 0, "v")],
          {"Chairs": [EdlEvent("Chairs", "Prps Chair Wood_03", 90, 150), EdlEvent("Chairs", "x_04", 150, 220)],
           "Bags": [EdlEvent("Bags", "Prps Bag-01.L", 120, 180)]})
r = tools(eng).consolidate()
assert eng.renamed == ["Prps Chair Wood", "Prps Bag"], eng.renamed
assert eng.ran.count("CId_ConsolidateClip") == 2 and eng.sel[-1] == ["Chairs", "Bags"], (eng.ran, eng.sel)

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
# Sin track de regrabación: avisa
eng2 = Eng([T("Chairs", 1, "a", sel=True)], {})
try:
    tools(eng2).regrab(); raise AssertionError
except SpotError as e:
    assert "Regrabación" in str(e)
# Nunca GetExportMixSourceList ni GetColorPalette (cuelgan Pro Tools 2025.12)
import pathlib
for f in ("edicion.py", "bridge.py"):
    code = "\n".join(l for l in pathlib.Path(__file__).with_name(f).read_text("utf-8").splitlines() if not l.strip().startswith("#"))
    assert "GetExportMixSourceList\")" not in code and "GetColorPalette')" not in code, f
print("edición OK")
