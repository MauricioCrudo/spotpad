"""
Lectura del video con ffmpeg (el binario viene con imageio-ffmpeg, no hay que instalar nada).

Una sola pasada por el video:
  · stdout: todos los cuadros en miniatura (64×36) para detectar cortes;
  · carpeta temporal: 1 cuadro por segundo en 448×448 (JPG) para el análisis visual.
Nada sale de la máquina: ffmpeg lee el archivo local y escribe en una carpeta temporal local.
"""
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction

import numpy as np

SMALL_W, SMALL_H = 64, 36       # para cortes
SAMPLE_FPS = 1                  # cuadros por segundo para el análisis visual
SAMPLE_SIZE = 448               # lado del cuadro muestreado (cuadrado, como lo ve el modelo)

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0     # sin consola negra en Windows


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    if sys.platform != "win32" and not os.access(exe, os.X_OK):     # al empaquetar puede perder el +x
        try:
            os.chmod(exe, 0o755)
        except OSError:
            pass
    return exe


@dataclass
class VideoInfo:
    path: str
    fps: Fraction            # cuadros por segundo reales (24000/1001…)
    duration: float          # segundos
    start_tc: str            # TC de inicio embebido en el QuickTime ("" si no tiene)
    width: int = 0
    height: int = 0

    @property
    def frames(self) -> int:
        return int(round(self.duration * float(self.fps)))


def parse_probe(text: str, path: str = "") -> VideoInfo:
    """Lee lo que imprime `ffmpeg -i` (no hay ffprobe en imageio-ffmpeg)."""
    dur = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    fps = Fraction(25)
    w = h = 0
    for line in text.splitlines():
        if "Video:" not in line:
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*fps", line) or re.search(r"(\d+(?:\.\d+)?)\s*tbr", line)
        if m:
            fps = _fps_from_float(float(m.group(1)))
        m = re.search(r",\s*(\d{2,5})x(\d{2,5})", line)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        break
    m = re.search(r"timecode\s*:\s*(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)", text)
    return VideoInfo(path, fps, dur, m.group(1) if m else "", w, h)


def _fps_from_float(f: float) -> Fraction:
    for nom in (24, 30, 48, 60, 120):
        if abs(f - nom * 1000 / 1001) < 0.01:
            return Fraction(nom * 1000, 1001)
    return Fraction(f).limit_denominator(1001)


def probe(path: str) -> VideoInfo:
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True,
                       creationflags=_NO_WINDOW)
    text = p.stderr.decode("utf-8", "replace")
    if "Video:" not in text:
        raise RuntimeError("No encontré video en el archivo: " + (text.strip().splitlines() or ["?"])[-1])
    return parse_probe(text, path)


def decode(path: str, sample_dir: str, progress=None, cancel=None):
    """Devuelve las miniaturas de todos los cuadros como array (N, 36, 64, 3) y deja los cuadros
    muestreados en sample_dir (000001.jpg = t 0 s, 000002.jpg = t 1 s…)."""
    os.makedirs(sample_dir, exist_ok=True)
    info = probe(path)
    total = max(1, info.frames)
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path, "-an", "-sn", "-dn",
           "-filter_complex",
           f"[0:v]split=2[a][b];[a]scale={SMALL_W}:{SMALL_H}:flags=area,format=rgb24[s];"
           f"[b]fps={SAMPLE_FPS}:round=down,scale={SAMPLE_SIZE}:{SAMPLE_SIZE}:flags=bicubic[t]",
           "-map", "[s]", "-f", "rawvideo", "-pix_fmt", "rgb24", "-vsync", "passthrough", "pipe:1",
           "-map", "[t]", "-q:v", "3", os.path.join(sample_dir, "%06d.jpg")]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    size = SMALL_W * SMALL_H * 3
    chunks, n = [], 0
    try:
        while True:
            buf = proc.stdout.read(size * 240)
            if not buf:
                break
            k = len(buf) // size
            if len(buf) % size:                     # resto (raro): completar el cuadro
                extra = proc.stdout.read(size - len(buf) % size)
                buf += extra
                k = len(buf) // size
            chunks.append(np.frombuffer(buf[:k * size], np.uint8).reshape(k, SMALL_H, SMALL_W, 3))
            n += k
            if progress:
                progress(min(1.0, n / total))
            if cancel and cancel():
                proc.kill()
                raise InterruptedError("Cancelado")
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg no pudo leer el video: " + err.strip()[-300:])
    finally:
        if proc.poll() is None:
            proc.kill()
    frames = np.concatenate(chunks) if chunks else np.zeros((0, SMALL_H, SMALL_W, 3), np.uint8)
    return info, frames


def sample_files(sample_dir: str):
    """[(t_segundos, ruta)] de los cuadros muestreados, en orden."""
    names = sorted(f for f in os.listdir(sample_dir) if f.endswith(".jpg"))
    return [((int(f[:-4]) - 1) / SAMPLE_FPS, os.path.join(sample_dir, f)) for f in names]
