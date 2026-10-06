"""
Superficie por locación (zero-shot con SigLIP: se compara cada cuadro con descripciones de texto).

Por cada cuadro muestreado:
  · w        = probabilidad de que se vea el piso (cuadro entero contra «se ve el piso» / «primer plano…»)
  · probs    = reparto entre superficies (franja de abajo del cuadro contra las descripciones)
  · extras   = alfombra / agua, que se suman encima de la superficie
Por locación se promedia pesando por w: los planos donde no se ve el piso casi no cuentan.
Lo que no queda claro sale como duda («Hardwood / Loose Wood?» o «Piso no visible»).
"""
import hashlib
import json
import os

import numpy as np


def load_config(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "surfaces.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def prompt_groups(cfg):
    """Nombre del grupo → lista de descripciones (es lo que export_model.py convierte en embeddings)."""
    g = {"floor_yes": cfg["floor_yes"], "floor_no": cfg["floor_no"]}
    for s in cfg["surfaces"]:
        g["s_" + s["key"]] = s["prompts"]
    for x in cfg.get("extras", []):
        g["x_" + x["key"]] = x["prompts"]
    return g


def prompts_hash(cfg) -> str:
    return hashlib.sha1(json.dumps(prompt_groups(cfg), sort_keys=True).encode()).hexdigest()[:12]


def check_text(cfg, text):
    got = str(text.get("hash", np.array(""))) if "hash" in text else ""
    if got and got != prompts_hash(cfg):
        raise RuntimeError("Las descripciones de superficies cambiaron y el modelo no está recompilado "
                           "(hay que volver a correr export_model.py).")
    missing = [k for k in prompt_groups(cfg) if k not in text]
    if missing:
        raise RuntimeError("Faltan embeddings de texto para: " + ", ".join(missing))


def _sig(x):
    return 1.0 / (1.0 + np.exp(-x))


def _best(E, text, key):
    return (E @ text[key].T).max(axis=1)


def floor_weight(full, text, scale):
    """Probabilidad de que se vea el piso en cada cuadro (cuadro entero)."""
    return _sig(scale * (_best(full, text, "floor_yes") - _best(full, text, "floor_no")))


def frame_scores(full, floor, text, cfg, scale):
    """full/floor: (N, D) normalizados. Devuelve w (N,), probs (N, S), extras (N, X)."""
    return (floor_weight(full, text, scale),) + surface_scores(floor, text, cfg, scale)


def surface_scores(floor, text, cfg, scale):
    """Reparto entre superficies y probabilidad de extras (franja de abajo de cada cuadro)."""
    best = lambda E, key: _best(E, text, key)
    keys = [s["key"] for s in cfg["surfaces"]]
    sims = np.stack([best(floor, "s_" + k) for k in keys], axis=1)            # (N, S)
    logits = scale * sims
    logits -= logits.max(axis=1, keepdims=True)
    probs = np.exp(logits)
    probs /= probs.sum(axis=1, keepdims=True)
    top = sims.max(axis=1)
    xs = [x["key"] for x in cfg.get("extras", [])]
    extras = np.stack([_sig(scale * (best(floor, "x_" + k) - top)) for k in xs], axis=1) if xs \
        else np.zeros((len(floor), 0))
    return probs, extras


def decide(idx, w, probs, extras, cfg):
    """Decisión para un conjunto de cuadros (una locación)."""
    r = cfg["rules"]
    surf = cfg["surfaces"]
    xs = cfg.get("extras", [])
    idx = np.asarray(idx, int)
    out = {"surface": None, "label": "", "doubt": None, "conf": 0.0, "floor": 0.0, "alts": [],
           "extras": [], "extras_doubt": []}
    if not len(idx):
        out["doubt"] = "Piso no visible"
        return out
    ww = w[idx]
    out["floor"] = round(float(ww.mean()), 3)
    if ww.mean() < r["min_floor"] or ww.sum() < 1.0:
        out["doubt"] = "Piso no visible"
        return out
    p = (ww[:, None] * probs[idx]).sum(axis=0) / ww.sum()
    order = np.argsort(-p)
    out["alts"] = [[surf[i]["key"], round(float(p[i]), 3)] for i in order[:3]]
    a, b = order[0], order[1] if len(order) > 1 else order[0]
    out["conf"] = round(float(p[a]), 3)
    if p[a] >= r["confident"] and (p[a] - p[b] >= r["margin"] or a == b):
        out["surface"], out["label"] = surf[a]["key"], surf[a]["label"]
    else:
        out["doubt"] = f"{surf[a]['label']} / {surf[b]['label']}?"
    if len(xs):
        px = (ww[:, None] * extras[idx]).sum(axis=0) / ww.sum()
        for x, v in zip(xs, px):
            if v >= r["extra_yes"]:
                out["extras"].append(x["key"])
            elif v >= r["extra_maybe"]:
                out["extras_doubt"].append(x["key"])
    return out
