"""Tests del analizador sin el modelo real (usa fake_model) y con videos sintéticos de ffmpeg."""
import json
import os
import subprocess
import sys
import tempfile
from fractions import Fraction

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, "..", "spotpad"))

import cuts            # noqa: E402
import edl_cmx         # noqa: E402
import scenes          # noqa: E402
import surfaces        # noqa: E402
import video           # noqa: E402
from tc import rate_from_enum   # noqa: E402

# --- probe ------------------------------------------------------------------ #
txt = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mov':
  Duration: 00:45:12.50, start: 0.000000, bitrate: 2000 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, progressive), 1920x1080, 1800 kb/s, 23.98 fps, 23.98 tbr, 24k tbn (default)
  Stream #0:2[0x3](eng): Data: none (tmcd / 0x64636d74), 0 kb/s
      timecode        : 00:59:58:00
"""
i = video.parse_probe(txt)
assert i.fps == Fraction(24000, 1001) and i.start_tc == "00:59:58:00" and (i.width, i.height) == (1920, 1080), i
assert abs(i.duration - 2712.5) < 1e-6

# --- EDL CMX3600 -------------------------------------------------------------- #
edl = """TITLE: EP01
FCM: NON-DROP FRAME

001  AX       V     C        00:00:10:00 00:00:15:00 01:00:00:00 01:00:05:00
* FROM CLIP NAME: A001C003
002  AX       A     C        00:00:10:00 00:00:15:00 01:00:00:00 01:00:05:00
003  AX       V     C        00:01:00:00 00:01:02:12 01:00:05:00 01:00:07:12
004  AX       V     D    012 00:02:00:00 00:02:04:00 01:00:07:12 01:00:11:12
"""
r24 = rate_from_enum("STCR_Fps24")
assert edl_cmx.cut_frames(edl, "01:00:00:00", r24) == [0, 120, 180], edl_cmx.cut_frames(edl, "01:00:00:00", r24)
assert edl_cmx.cut_frames(edl, "01:00:05:00", r24) == [0, 60]       # video que arranca más tarde

# --- cortes en un video sintético ------------------------------------------- #
ff = video.ffmpeg_exe()
tmp = tempfile.mkdtemp()
S = "s=320x180:r=24"
mov = os.path.join(tmp, "t.mov")
subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc2={S}:d=3",
                "-f", "lavfi", "-i", f"smptebars={S}:d=2",
                "-f", "lavfi", "-i", f"color=c=0x2040B0:{S}:d=2,noise=alls=20:allf=t",
                "-f", "lavfi", "-i", f"testsrc2={S}:d=3,hue=h=90",
                "-filter_complex", "[0][1][2][3]concat=n=4:v=1:a=0,format=yuv420p[v]", "-map", "[v]",
                "-c:v", "libx264", "-preset", "veryfast", "-timecode", "01:00:00:00", mov], check=True)
info, small = video.decode(mov, os.path.join(tmp, "cuadros"))
assert info.start_tc == "01:00:00:00" and len(small) == 240, (info, small.shape)
c = cuts.detect(small, 24.0)
assert c == [0, 72, 120, 168], c
assert len(video.sample_files(os.path.join(tmp, "cuadros"))) >= 10 * video.SAMPLE_FPS - 1

# flash de 2 cuadros en medio de un plano: no es corte
fl = small[:72].copy()
fl[30:32] = 255
assert cuts.detect(fl, 24.0) == [0], cuts.detect(fl, 24.0)

# --- escenas y locaciones con embeddings sintéticos -------------------------- #
rng = np.random.default_rng(1)
base = {k: v / np.linalg.norm(v) for k, v in zip("ABC", rng.normal(size=(3, 64)))}


def emb(loc):
    v = base[loc] + rng.normal(scale=0.12, size=64)
    return v / np.linalg.norm(v)


# A A A (inserto C de 2 s) A | B B B B | A A   → escenas A, B, A; locaciones 1, 2, 1
plan = [("A", 5), ("A", 4), ("A", 6), ("C", 2), ("A", 5), ("B", 6), ("B", 3), ("B", 4), ("B", 5), ("A", 5), ("A", 6)]
t, cut_s, st, se = 0.0, [], [], []
for loc, d in plan:
    cut_s.append(t)
    for k in range(int(d * 2)):
        st.append(t + k / 2)
        se.append(emb(loc))
    t += d
shots = scenes.make_shots(cut_s, t, st, np.array(se))
thr = scenes.adaptive_threshold(shots)
sc = scenes.locations(scenes.segment(shots, thr), thr)
assert [(round(s.start), round(s.end), s.location) for s in sc] == [(0, 22, 1), (22, 40, 2), (40, 51, 1)], \
    [(s.start, s.end, s.location) for s in sc]

# --- decisión de superficie ------------------------------------------------- #
cfg = surfaces.load_config()
S_ = len(cfg["surfaces"])
probs = np.full((6, S_), 0.02)
probs[:, 2] = 0.9                                       # hardwood
w = np.array([0.9, 0.8, 0.9, 0.05, 0.02, 0.9])
ex = np.zeros((6, 2))
d = surfaces.decide(range(6), w, probs / probs.sum(1, keepdims=True), ex, cfg)
assert d["surface"] == "hardwood" and d["label"] == "Hardwood" and not d["doubt"], d
p2 = np.full((6, S_), 0.02)
p2[:, 2], p2[:, 3] = 0.45, 0.4
d = surfaces.decide(range(6), w, p2 / p2.sum(1, keepdims=True), ex, cfg)
assert d["surface"] is None and d["doubt"] == "Hardwood / Loose Wood?", d
d = surfaces.decide(range(6), np.full(6, 0.03), probs, ex, cfg)
assert d["doubt"] == "Piso no visible"
ex[:, 0] = 0.8
assert surfaces.decide(range(6), w, probs, ex, cfg)["extras"] == ["carpet"]

# --- de punta a punta con el modelo falso ------------------------------------ #
import fake_model        # noqa: E402
from analyzer import analyze      # noqa: E402
from model import Vision          # noqa: E402

mdir = fake_model.make(os.path.join(tmp, "modelo"))
res = analyze(mov, vision=Vision(mdir), log=lambda m: None)
assert res["kind"] == "spotpad-analisis" and res["start_tc"] == "01:00:00:00"
assert res["scenes"][0]["tc_in"] == "01:00:00:00" and res["scenes"][-1]["tc_out"] == "01:00:10:00", res["scenes"]
assert all(l["id"] in {s["location"] for s in res["scenes"]} for l in res["locations"])
json.dumps(res)
res_edl = os.path.join(tmp, "e.edl")
open(res_edl, "w").write(edl)
assert analyze(mov, edl_path=res_edl, vision=Vision(mdir), log=lambda m: None)["cuts_from"] == "edl"
print("analizador OK")
