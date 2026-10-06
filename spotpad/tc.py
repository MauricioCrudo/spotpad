"""
Samples → timecode, con los datos que da el SDK:
  GetSessionStartTime   → "00:59:00:00"  (o con ';' en drop frame)
  GetSessionTimeCodeRate → STCR_Fps23976, STCR_Fps25, STCR_Fps2997Drop, ...
  GetSessionSampleRate  → 48000

Supone que las posiciones en samples del EDL arrancan en 0 en el inicio de
la sesión (como la regla de Samples de Pro Tools).
"""
import re
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class TcRate:
    nominal: int          # frames por segundo "de etiqueta" (24, 25, 30…)
    real: Fraction        # frames por segundo reales (24000/1001 para 23.976)
    drop: bool

    @property
    def sep(self):
        return ";" if self.drop else ":"


def rate_from_enum(name: str) -> TcRate:
    """'STCR_Fps2997Drop' → TcRate(30, 30000/1001, drop=True)."""
    m = re.search(r"Fps(\d+)(Drop)?", name or "")
    if not m:
        return TcRate(25, Fraction(25), False)
    digits, drop = m.group(1), bool(m.group(2))
    pulled = {"23976": 24, "2997": 30, "47952": 48, "5994": 60, "11988": 120}
    if digits in pulled:
        nom = pulled[digits]
        return TcRate(nom, Fraction(nom * 1000, 1001), drop)
    nom = int(digits)
    return TcRate(nom, Fraction(nom), drop)


def _drop_per_min(r: TcRate) -> int:
    return round(r.nominal / 15)   # 2 en 29.97, 4 en 59.94, 8 en 119.88


def tc_to_frames(tc: str, r: TcRate) -> int:
    h, m, s, f = (int(x) for x in re.split(r"[:;.]", tc.strip())[:4])
    frames = ((h * 60 + m) * 60 + s) * r.nominal + f
    if r.drop:
        total_min = h * 60 + m
        frames -= _drop_per_min(r) * (total_min - total_min // 10)
    return frames


def frames_to_tc(frames: int, r: TcRate) -> str:
    if r.drop:
        d = _drop_per_min(r)
        per_10min = r.nominal * 600 - d * 9
        per_min = r.nominal * 60 - d
        tens, rem = divmod(frames, per_10min)
        if rem > d:
            frames += d * 9 * tens + d * ((rem - d) // per_min)
        else:
            frames += d * 9 * tens
    f = frames % r.nominal
    s = frames // r.nominal % 60
    m = frames // (r.nominal * 60) % 60
    h = frames // (r.nominal * 3600) % 24
    return f"{h:02d}:{m:02d}:{s:02d}{r.sep}{f:02d}"


class TcConverter:
    def __init__(self, sample_rate: int, start_tc: str, rate: TcRate):
        self.sr = sample_rate
        self.rate = rate
        self.start = tc_to_frames(start_tc or "00:00:00:00", rate)

    def __call__(self, samples: int) -> str:
        elapsed = int(Fraction(samples) * self.rate.real / self.sr)   # floor
        return frames_to_tc(self.start + elapsed, self.rate)

    def samples(self, tc: str) -> int:
        """TC (con la cadencia de la sesión) → samples desde el inicio de la sesión."""
        frames = tc_to_frames(tc, self.rate) - self.start
        return int(round(Fraction(frames) * self.sr / self.rate.real))

    def duration(self, samples: int) -> str:
        """Duración corta: '2.40 s'."""
        return f"{samples / self.sr:.2f} s"
