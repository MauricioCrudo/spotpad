"""
Detección de cortes de plano sobre miniaturas (N, 36, 64, 3).

Puntaje por cuadro = mezcla de diferencia de píxeles (con un poco de desenfoque, para que el
movimiento de cámara pese menos) y diferencia de histograma de color. Hay corte cuando el puntaje
supera un mínimo absoluto y además se despega del entorno (mediana local), y no es un flash:
el cuadro de antes del salto tiene que seguir siendo distinto un par de cuadros después.
"""
import numpy as np

BINS = 16


def _prep(frames, chunk=2000):
    """Por tandas, para no llenar la memoria con un capítulo entero (~70.000 cuadros)."""
    n = len(frames)
    h, w = frames.shape[1] // 2 * 2, frames.shape[2] // 2 * 2
    f = np.empty((n, h // 2, w // 2, 3), np.float32)
    hist = np.empty((n, 3, BINS), np.float32)
    offs = (np.arange(3) * BINS)[None, None, :]
    for a in range(0, n, chunk):
        part = frames[a:a + chunk]
        # 64×36 → 32×18 promediando (desenfoque barato)
        f[a:a + len(part)] = part[:, :h, :w].reshape(len(part), h // 2, 2, w // 2, 2, 3).mean(axis=(2, 4))
        q = (part.reshape(len(part), -1, 3) >> 4).astype(np.int64) + offs       # 256/16 = 16 → >>4
        flat = (q + (np.arange(len(part)) * 3 * BINS)[:, None, None]).ravel()
        hist[a:a + len(part)] = np.bincount(flat, minlength=len(part) * 3 * BINS).reshape(len(part), 3, BINS)
    hist /= hist.sum(axis=2, keepdims=True)
    return f, hist


def _pair(f, hist, a, b):
    pix = np.abs(f[a] - f[b]).mean(axis=(-3, -2, -1)) / 255.0
    hd = 0.5 * np.abs(hist[a] - hist[b]).sum(axis=-1).mean(axis=-1)
    return 0.5 * np.minimum(pix * 3.0, 1.0) + 0.5 * hd


def scores(frames):
    """s[i] = cuánto cambia el cuadro i respecto del i-1 (s[0] = 0)."""
    f, hist = _prep(frames)
    if len(frames) < 2:
        return np.zeros(len(frames), np.float32), f, hist
    idx = np.arange(1, len(frames))
    s = np.zeros(len(frames), np.float32)
    s[1:] = _pair(f, hist, idx - 1, idx)
    return s, f, hist


def detect(frames, fps: float, sensitivity: float = 1.0, min_shot: float = 0.3):
    """Cuadros donde arranca cada plano (siempre incluye el 0).
    sensitivity > 1 → más cortes; < 1 → menos."""
    n = len(frames)
    if n < 2:
        return [0]
    s, f, hist = scores(frames)
    thr = 0.12 / max(sensitivity, 0.1)
    win = max(4, int(round(fps / 2)))
    cand = []
    for i in range(1, n):
        if s[i] < thr:
            continue
        lo, hi = max(1, i - win), min(n, i + win + 1)
        around = np.concatenate([s[lo:i], s[i + 1:hi]])
        local = float(np.median(around)) if len(around) else 0.0
        if s[i] < 2.5 * local + 0.03:
            continue
        # flash (fogonazo, relámpago): si algún cuadro de los 3 de antes se parece a alguno de los
        # 3 de después (salteando el salto), no es corte
        before = np.array([k for k in (i - 1, i - 2, i - 3) if k >= 0])
        after = np.array([k for k in (i, i + 1, i + 2) if k < n])
        A, Bb = np.meshgrid(before, after)
        if _pair(f, hist, A.ravel(), Bb.ravel()).min() < max(thr * 0.6, 0.35 * s[i]):
            continue
        cand.append(i)
    # plano mínimo: entre dos candidatos muy cercanos queda el más fuerte
    min_len = max(1, int(round(min_shot * fps)))
    cuts = [0]
    for i in cand:
        if i - cuts[-1] < min_len and cuts[-1] != 0:
            if s[i] > s[cuts[-1]]:
                cuts[-1] = i
            continue
        if i - cuts[-1] < min_len:
            continue
        cuts.append(i)
    return cuts
