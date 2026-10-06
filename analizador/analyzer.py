"""
Análisis de un capítulo: video → planos → escenas → locaciones → superficie por locación.
Todo local: lee el archivo, usa el modelo incluido y escribe en una carpeta temporal que se borra.

    python analyzer.py capitulo.mov [--edl montaje.edl] [--tc 01:00:00:00] [--out analisis.json]
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
from fractions import Fraction

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(HERE, "..", "spotpad"))        # tc.py compartido con SpotPad

import cuts as cutmod            # noqa: E402
import scenes as scmod           # noqa: E402
import surfaces as sfmod         # noqa: E402
import video as vmod             # noqa: E402
from model import views          # noqa: E402
from tc import TcRate, frames_to_tc, tc_to_frames      # noqa: E402

VERSION = "0.1.0"


def video_rate(fps: Fraction, start_tc: str) -> TcRate:
    nominal = int(round(float(fps)))
    return TcRate(nominal, Fraction(fps), ";" in (start_tc or ""))


class Steps:
    """Reparte la barra de progreso entre las etapas."""
    PLAN = [("video", "Leyendo el video", 0.45), ("cortes", "Buscando cortes de plano", 0.05),
            ("modelo", "Mirando los cuadros", 0.42), ("escenas", "Armando escenas y locaciones", 0.08)]

    def __init__(self, cb):
        self.cb, self.done = cb or (lambda *a: None), 0.0

    def stage(self, key):
        acc = 0.0
        for k, label, w in self.PLAN:
            if k == key:
                self.base, self.w, self.label = acc, w, label
                self.cb(self.base, label)
                return lambda f: self.cb(self.base + self.w * max(0.0, min(1.0, f)), self.label)
            acc += w
        raise KeyError(key)


def analyze(path, edl_path=None, start_tc=None, sensitivity=1.0, progress=None, cancel=None,
            log=print, vision=None, cfg=None):
    t0 = time.time()
    steps = Steps(progress)
    cfg = cfg or sfmod.load_config()
    if vision is None:
        from model import Vision
        vision = Vision(log=log)
    sfmod.check_text(cfg, vision.text)
    tmp = tempfile.mkdtemp(prefix="spotpad_analisis_")
    try:
        f = steps.stage("video")
        info, small = vmod.decode(path, os.path.join(tmp, "cuadros"), progress=f, cancel=cancel)
        fps = float(info.fps)
        tc0 = (start_tc or info.start_tc or "").strip()
        if not tc0:
            raise ValueError("El video no trae timecode de inicio: escribilo (p. ej. 01:00:00:00).")
        rate = video_rate(info.fps, tc0)
        duration = len(small) / fps if len(small) else info.duration
        log(f"Video: {info.width}×{info.height}, {fps:.3f} fps, {duration / 60:.1f} min, TC {tc0}")

        f = steps.stage("cortes")
        if edl_path:
            from edl_cmx import cut_frames
            with open(edl_path, encoding="utf-8", errors="replace") as fh:
                cf = cut_frames(fh.read(), tc0, rate)
            cut_list = sorted(set([0] + [c for c in cf if c < len(small)]))
            log(f"EDL: {len(cut_list)} planos")
        else:
            cut_list = cutmod.detect(small, fps, sensitivity=sensitivity)
            log(f"Cortes detectados: {len(cut_list)} planos")
        f(1.0)
        del small

        f = steps.stage("modelo")
        samples = vmod.sample_files(os.path.join(tmp, "cuadros"))
        times = [t for t, _ in samples]
        # 1) cuadro entero de todos los muestreados → locación y «se ve el piso»
        # 2) franja de abajo solo donde se ve el piso (ahorra la mitad del trabajo)
        tm = time.time()
        B = 32
        full = []
        for a in range(0, len(samples), B):
            if cancel and cancel():
                raise InterruptedError("Cancelado")
            full.append(vision.embed([views(p, floor=False) for _, p in samples[a:a + B]]))
            f(0.6 * (a + B) / max(1, len(samples)))
        full = np.concatenate(full) if full else np.zeros((0, 1), np.float32)
        w = sfmod.floor_weight(full, vision.text, vision.scale)
        idx = [i for i in range(len(samples)) if w[i] >= 0.1]
        floor = np.zeros_like(full)
        for a in range(0, len(idx), B):
            if cancel and cancel():
                raise InterruptedError("Cancelado")
            part = idx[a:a + B]
            floor[part] = vision.embed([views(samples[i][1], floor=True) for i in part])
            f(0.6 + 0.4 * (a + B) / max(1, len(idx)))
        log(f"Modelo: {len(samples)} cuadros, piso visible en {len(idx)} "
            f"({getattr(vision, 'provider', '?')}, {time.time() - tm:.0f} s)")

        f = steps.stage("escenas")
        shots = scmod.make_shots([c / fps for c in cut_list], duration, times, full)
        thr = scmod.adaptive_threshold(shots, sensitivity)
        scs = scmod.segment(shots, thr)
        scs = scmod.locations(scs, thr)
        probs, extras = sfmod.surface_scores(floor, vision.text, cfg, vision.scale)
        locs = {}
        for s in scs:
            locs.setdefault(s.location, []).extend(i for k in s.shots for i in shots[k].samples)
        decisions = {loc: sfmod.decide(sorted(set(idx)), w, probs, extras, cfg) for loc, idx in locs.items()}
        f(1.0)

        start_f = tc_to_frames(tc0, rate)
        tc = lambda sec: frames_to_tc(start_f + int(round(sec * fps)), rate)
        result = {
            "kind": "spotpad-analisis", "version": 1, "analyzer": VERSION,
            "created": dt.datetime.now().isoformat(timespec="seconds"),
            "video": os.path.basename(path), "fps": [info.fps.numerator, info.fps.denominator],
            "start_tc": tc0, "duration": round(duration, 3), "threshold": round(thr, 4),
            "sensitivity": sensitivity, "cuts_from": "edl" if edl_path else "video",
            "provider": getattr(vision, "provider", ""), "seconds": round(time.time() - t0, 1),
            "surfaces": [{k: s[k] for k in ("key", "label", "tracks")} for s in cfg["surfaces"]],
            "extras": [{k: s[k] for k in ("key", "label", "tracks")} for s in cfg.get("extras", [])],
            "locations": [{"id": loc, "scenes": [i + 1 for i, s in enumerate(scs) if s.location == loc],
                           **decisions[loc]} for loc in sorted(locs)],
            "scenes": [{"n": i + 1, "start": round(s.start, 3), "end": round(s.end, 3),
                        "tc_in": tc(s.start), "tc_out": tc(s.end), "location": s.location,
                        "shots": len(s.shots)} for i, s in enumerate(scs)],
        }
        nd = sum(1 for l in result["locations"] if l["doubt"])
        log(f"Listo: {len(scs)} escenas, {len(locs)} locaciones ({nd} con duda) en {result['seconds']} s")
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Analizador de superficies de SpotPad")
    ap.add_argument("video")
    ap.add_argument("--edl")
    ap.add_argument("--tc", help="TC de inicio si el video no lo trae")
    ap.add_argument("--sens", type=float, default=1.0)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    last = [0]

    def prog(frac, label):
        p = int(frac * 100)
        if p != last[0]:
            last[0] = p
            print(f"\r{label}… {p}%   ", end="", flush=True)
    res = analyze(a.video, a.edl, a.tc, a.sens, progress=prog, log=lambda m: print("\n" + m))
    out = a.out or os.path.splitext(a.video)[0] + "_spotpad.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("\nGuardado:", out)


if __name__ == "__main__":
    main()
