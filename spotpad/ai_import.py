"""
Resultado del analizador → lista de acciones para Pro Tools (función pura, sin SDK: se testea sola).

Acciones:
  {"op": "marker", "name": "Esc 3 · L2", "tc": "01:02:10:05"}
  {"op": "group",  "track": "Wood", "start": s, "end": e, "name": "Hardwood"}          ← superficie segura
  {"op": "group",  "track": DOUBT_TRACK, "start": s, "end": e, "name": "Hardwood / Loose Wood?"}

Cuidados:
  · nunca dos clips que se pisen en el mismo track (GroupClips agruparía el clip que ya está);
  · escenas que ya tienen superficie marcada (en cualquier track de la carpeta) se saltean;
  · markers que ya existen (±1 s) no se repiten;
  · escenas seguidas con la misma superficie van en un solo clip group.
"""
import re

from naming import DEFAULT_RULES, folder_kind, norm

DOUBT_TRACK = "IA Dudas"


def _tracks_for(entry, surface_tracks):
    """Track de la sesión para una superficie: nombre igual a alguno de entry['tracks'] y, si no,
    el primero que lo contenga como palabra."""
    names = [t["name"] for t in surface_tracks]
    for cand in entry.get("tracks", []) + [entry.get("label", "")]:
        for n in names:
            if cand and norm(n) == norm(cand):
                return n
    for cand in entry.get("tracks", []) + [entry.get("label", "")]:
        if not cand:
            continue
        pat = re.compile(r"(^|\s)" + re.escape(norm(cand)) + r"(\s|$)")
        for n in names:
            if pat.search(norm(n)):
                return n
    return None


def _overlap(a, b, c, d):
    return max(0, min(b, d) - max(a, c))


def plan(result, tracks, edl, markers, to_samples, opts=None, rules=None):
    """
    result     : JSON del analizador
    tracks     : [{name, folder (carpeta de primer nivel), inactive}]
    edl        : {track: [EdlEvent(start, end, clip…)]} de la sesión (samples)
    markers    : [samples] de los markers que ya hay
    to_samples : TC (texto) → samples de la sesión
    opts       : {"markers": True, "surfaces": True}
    """
    opts = {"markers": True, "surfaces": True, **(opts or {})}
    rules = rules or DEFAULT_RULES
    actions, skipped, notes = [], [], []
    surf_tracks = [t for t in tracks if folder_kind(t.get("folder", ""), rules) == "surfaces"]
    folder = surf_tracks[0]["folder"] if surf_tracks else ""
    usable = [t for t in surf_tracks if not t.get("inactive") and norm(t["name"]) != norm(DOUBT_TRACK)]
    doubt_exists = next((t for t in surf_tracks if norm(t["name"]) == norm(DOUBT_TRACK)), None)

    entries = {e["key"]: e for e in result.get("surfaces", []) + result.get("extras", [])}
    track_of = {k: _tracks_for(e, usable) for k, e in entries.items()}
    missing = sorted({entries[k]["label"] for k, v in track_of.items() if v is None})
    locs = {l["id"]: l for l in result.get("locations", [])}

    # clips que ya están en la carpeta de superficies (incluidas las dudas de una corrida anterior)
    marked = [(ev.start, ev.end) for t in surf_tracks for ev in edl.get(t["name"], [])]
    sr_sec = None
    scenes = []
    for sc in result.get("scenes", []):
        a, b = to_samples(sc["tc_in"]), to_samples(sc["tc_out"])
        if b <= a:
            continue
        if sr_sec is None and sc["end"] > sc["start"]:
            sr_sec = (b - a) / (sc["end"] - sc["start"])
        scenes.append((sc, a, b))
    sr_sec = sr_sec or 48000

    if opts["markers"]:
        for sc, a, b in scenes:
            if any(abs(m - a) <= sr_sec for m in markers):
                skipped.append({"scene": sc["n"], "why": "ya hay un marker"})
                continue
            actions.append({"op": "marker", "name": f"Esc {sc['n']} · L{sc['location']}", "tc": sc["tc_in"],
                            "scene": sc["n"]})

    if opts["surfaces"]:
        if not surf_tracks:
            found = sorted({t.get("folder", "") for t in tracks if t.get("folder")})
            notes.append("No encontré la carpeta de superficies (SUPERFICIES / SURFACES): no marco superficies. "
                         + (f"Carpetas en la sesión: {', '.join(found)}." if found else "La sesión no tiene carpetas."))
        else:
            pending = []                          # (track, a, b, name, scene)
            for sc, a, b in scenes:
                if any(_overlap(a, b, c, d) >= 0.5 * (b - a) for c, d in marked):
                    skipped.append({"scene": sc["n"], "why": "ya tenía superficie"})
                    continue
                loc = locs.get(sc["location"], {})
                doubt = loc.get("doubt")
                maybe = [entries[k]["label"] + "?" for k in loc.get("extras_doubt", []) if k in entries]
                if not doubt and loc.get("surface"):
                    tr = track_of.get(loc["surface"])
                    if tr:
                        pending.append((tr, a, b, entries[loc["surface"]]["label"], sc["n"]))
                    else:
                        doubt = f"{entries[loc['surface']]['label']} (sin track)"
                for k in loc.get("extras", []):
                    if track_of.get(k):
                        pending.append((track_of[k], a, b, entries[k]["label"], sc["n"]))
                    elif k in entries:
                        maybe.append(entries[k]["label"] + " (sin track)")
                dname = " + ".join(x for x in [doubt] + maybe if x)
                if dname:
                    pending.append((DOUBT_TRACK, a, b, dname, sc["n"]))
            # misma superficie en escenas pegadas → un solo clip group
            pending.sort(key=lambda p: (p[0], p[1]))
            merged = []
            for p in pending:
                q = merged[-1] if merged else None
                if q and q[0] == p[0] and q[3] == p[3] and p[1] <= q[2] + 1:
                    merged[-1] = (q[0], q[1], max(q[2], p[2]), q[3], q[4])
                else:
                    merged.append(p)
            merged.sort(key=lambda p: (p[1], p[0]))
            for tr, a, b, name, n in merged:
                # si en ese track ya hay un clip dentro del rango, GroupClips lo metería en el grupo
                if any(_overlap(a, b, ev.start, ev.end) > 0 for ev in edl.get(tr, [])):
                    skipped.append({"scene": n, "why": f"se pisa con un clip que ya está en «{tr}»"})
                    continue
                actions.append({"op": "group", "track": tr, "start": int(a), "end": int(b), "name": name,
                                "scene": n, "doubt": tr == DOUBT_TRACK})
    if missing and opts["surfaces"]:
        notes.append("Sin track en la sesión para: " + ", ".join(missing) + " (van a «IA Dudas»).")
    doubts = sum(1 for x in actions if x.get("doubt"))
    return {
        "actions": actions, "skipped": skipped, "notes": notes,
        "folder": folder, "doubt_track_exists": bool(doubt_exists),
        "doubt_track_inactive": bool(doubt_exists and doubt_exists.get("inactive")),
        "summary": {"markers": sum(1 for x in actions if x["op"] == "marker"),
                    "surfaces": sum(1 for x in actions if x["op"] == "group" and not x.get("doubt")),
                    "doubts": doubts, "skipped": len(skipped)},
    }
