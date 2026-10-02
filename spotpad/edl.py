"""
Parser del "Export Session Info as Text" de Pro Tools (sección de EDLs por track).

Es la vía que encontramos para LEER qué clip / clip group hay bajo la selección:
PTSL (en 2024) no tiene un comando "dame el nombre del clip seleccionado",
pero sí puede exportar el EDL de cada track como string (ExportSessionInfoAsText,
disponible desde 2022.12). Cruzamos eso con la selección de edición (in/out en samples)
y el track que tiene la selección.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class EdlEvent:
    track: str
    clip: str
    start: int
    end: int


def _cols(line: str) -> List[str]:
    return [c.strip() for c in line.split("\t")]


def parse_session_text(text: str) -> Dict[str, List[EdlEvent]]:
    """Devuelve {nombre_de_track: [eventos]} con tiempos en samples."""
    tracks: Dict[str, List[EdlEvent]] = {}
    current: Optional[str] = None
    header: Optional[List[str]] = None

    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("TRACK NAME:"):
            current = line.split(":", 1)[1].strip()
            tracks.setdefault(current, [])
            header = None
            continue
        if current is None:
            continue
        cols = _cols(line)
        if cols and cols[0].upper() == "CHANNEL":
            header = [c.upper() for c in cols]
            continue
        if header is None or not line.strip():
            continue
        if len(cols) < len(header) - 1:
            continue
        row = dict(zip(header, cols))
        try:
            start = int(row["START TIME"].replace(",", ""))
            end = int(row["END TIME"].replace(",", ""))
        except (KeyError, ValueError):
            # Línea que no es evento (o export no hecho en samples)
            continue
        tracks[current].append(EdlEvent(current, row.get("CLIP NAME", ""), start, end))
    return tracks


def clip_at_selection(events: List[EdlEvent], sel_in: int, sel_out: int) -> Optional[EdlEvent]:
    """El evento que más se superpone con la selección; si la selección es un
    punto (cursor), el evento que lo contiene."""
    if sel_out <= sel_in:
        for ev in events:
            if ev.start <= sel_in < ev.end:
                return ev
        return None
    best, best_overlap = None, 0
    for ev in events:
        overlap = min(ev.end, sel_out) - max(ev.start, sel_in)
        if overlap > best_overlap:
            best, best_overlap = ev, overlap
    return best
