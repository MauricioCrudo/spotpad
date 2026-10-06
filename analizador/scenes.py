"""
Planos → escenas → locaciones, a partir de los embeddings visuales (vectores normalizados).

· Escena: planos seguidos en el mismo lugar. Un plano abre escena nueva cuando no se parece a
  ninguno de los últimos planos de la escena actual, y el siguiente tampoco (así un inserto o un
  primer plano suelto no corta la escena; el plano/contraplano tampoco, porque compara con varios).
· Locación: escenas (aunque estén separadas en el capítulo) que se parecen entre sí → mismo número.
  La superficie se decide por locación.
El umbral se adapta a cada video (qué tan parecidos son, en general, los planos de una misma escena)
y la «sensibilidad» lo corre: más sensibilidad → más escenas.
"""
from dataclasses import dataclass, field
from typing import List

import numpy as np

RECENT = 6          # planos de la escena actual con los que se compara
MIN_SCENE = 6.0     # segundos: escenas más cortas se pegan a la vecina más parecida
INSERT_MAX = 5.0    # un plano distinto más corto que esto, entre dos de la misma escena, es un inserto
FEW_SHOTS = 12      # con menos planos no hay estadística: umbral fijo


@dataclass
class Shot:
    start: float          # segundos desde el inicio del video
    end: float
    emb: np.ndarray       # embedding medio del plano (normalizado)
    samples: List[int] = field(default_factory=list)    # índices de cuadros muestreados


@dataclass
class Scene:
    shots: List[int]
    start: float
    end: float
    emb: np.ndarray
    location: int = 0


def _norm(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-8)


def make_shots(cuts_sec, duration, sample_times, sample_embs):
    """Un Shot por plano con el promedio de sus cuadros muestreados (si el plano es tan corto que
    no tiene ninguno, usa el más cercano)."""
    times = np.asarray(sample_times, np.float64)
    bounds = list(cuts_sec) + [duration]
    shots = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b <= a:
            continue
        idx = np.nonzero((times >= a) & (times < b))[0].tolist()
        if not idx and len(times):
            idx = [int(np.argmin(np.abs(times - (a + b) / 2)))]
        emb = _norm(sample_embs[idx].mean(axis=0)) if idx else np.zeros(sample_embs.shape[1], np.float32)
        shots.append(Shot(float(a), float(b), emb, idx))
    return shots


def _recent_sim(shots, cur, j):
    ref = np.stack([shots[k].emb for k in cur[-RECENT:]])
    return float((ref @ shots[j].emb).max())


def adaptive_threshold(shots, sensitivity=1.0):
    """Umbral de «sigue la misma escena». Se calcula con la similitud de cada plano con sus vecinos:
    la mayoría son de la misma escena, los cambios de escena son los valores bajos y raros."""
    if len(shots) < FEW_SHOTS:
        return float(np.clip(0.75 + 0.1 * (sensitivity - 1.0), 0.3, 0.97))
    sims = []
    for j in range(1, len(shots)):
        ref = np.stack([s.emb for s in shots[max(0, j - RECENT):j]])
        sims.append(float((ref @ shots[j].emb).max()))
    sims = np.array(sims)
    med = float(np.median(sims))
    mad = float(np.median(np.abs(sims - med))) * 1.4826 + 0.01
    z = 2.5 / max(sensitivity, 0.1)
    return float(np.clip(med - z * mad, 0.3, 0.97))


def segment(shots, threshold, min_scene=MIN_SCENE):
    """Planos → escenas."""
    if not shots:
        return []
    groups = [[0]]
    for j in range(1, len(shots)):
        cur = groups[-1]
        if _recent_sim(shots, cur, j) >= threshold:
            cur.append(j)
            continue
        # ¿el siguiente vuelve a la escena? entonces este es un inserto
        if j + 1 < len(shots) and shots[j].end - shots[j].start <= INSERT_MAX \
                and _recent_sim(shots, cur, j + 1) >= threshold:
            cur.append(j)
            continue
        groups.append([j])
    scenes = [_scene(shots, g) for g in groups]
    return _merge_short(shots, scenes, min_scene)


def _scene(shots, idx):
    w = np.array([shots[k].end - shots[k].start for k in idx])
    emb = _norm((np.stack([shots[k].emb for k in idx]) * w[:, None]).sum(axis=0))
    return Scene(list(idx), shots[idx[0]].start, shots[idx[-1]].end, emb)


def _merge_short(shots, scenes, min_scene):
    changed = True
    while changed and len(scenes) > 1:
        changed = False
        for i, sc in enumerate(scenes):
            if sc.end - sc.start >= min_scene:
                continue
            nb = [k for k in (i - 1, i + 1) if 0 <= k < len(scenes)]
            k = max(nb, key=lambda k: float(scenes[k].emb @ sc.emb))
            a, b = sorted((i, k))
            scenes[a:b + 1] = [_scene(shots, scenes[a].shots + scenes[b].shots)]
            changed = True
            break
    return scenes


def locations(scenes, threshold):
    """Agrupa escenas parecidas (enlace promedio) y numera las locaciones por orden de aparición."""
    n = len(scenes)
    if not n:
        return scenes
    E = np.stack([s.emb for s in scenes]).astype(np.float64)
    S = E @ E.T                          # similitud media entre grupos (Lance-Williams)
    np.fill_diagonal(S, -np.inf)
    members = {i: [i] for i in range(n)}
    alive = np.ones(n, bool)
    while alive.sum() > 1:
        M = np.where(alive[:, None] & alive[None, :], S, -np.inf)
        a, b = np.unravel_index(int(np.argmax(M)), M.shape)
        if M[a, b] < threshold:
            break
        na, nb = len(members[a]), len(members[b])
        S[a, :] = S[:, a] = (na * S[a, :] + nb * S[b, :]) / (na + nb)
        S[a, a] = -np.inf
        members[a] += members.pop(b)
        alive[b] = False
    order = sorted(members.values(), key=min)
    for loc, c in enumerate(order, 1):
        for i in c:
            scenes[i].location = loc
    return scenes
