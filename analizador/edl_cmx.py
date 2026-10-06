"""
EDL de edición (CMX 3600) → cuadros de corte del video.

Solo se usan los «record in» de los eventos de video: son los cortes exactos del montaje.
  001  AX       V     C        00:00:10:00 00:00:15:00 01:00:00:00 01:00:05:00
"""
import re

_EV = re.compile(r"^\s*\d{3,6}\s+\S+\s+(\S+)\s+(C|D|W\d*|K\S*)\s*(\d+)?\s+"
                 r"(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)\s+(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)\s+"
                 r"(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)\s+(\d\d[:;.]\d\d[:;.]\d\d[:;.]\d\d)")


def record_ins(text: str):
    """[(record_in, record_out)] de los eventos de video, como TC en texto."""
    out = []
    for line in text.splitlines():
        m = _EV.match(line)
        if not m:
            continue
        track = m.group(1).upper()
        if not track.startswith("V") and track not in ("B", "AA/V", "A/V"):
            continue
        out.append((m.group(6), m.group(7)))
    return out


def cut_frames(text: str, video_start_tc: str, rate):
    """Cuadros (desde el inicio del video) donde arranca cada plano según la EDL."""
    from tc import tc_to_frames
    start = tc_to_frames(video_start_tc, rate)
    cuts = sorted({tc_to_frames(a, rate) - start for a, _ in record_ins(text)})
    return [c for c in cuts if c >= 0]
