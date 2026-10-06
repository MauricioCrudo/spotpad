"""Importar el análisis de la IA: plan puro y aplicado contra el mock."""
import json
import time

import ai_import
from bridge import AiJob, MockProTools, SpotError
from edl import EdlEvent
from tc import TcConverter, rate_from_enum

conv = TcConverter(48000, "00:59:58:00", rate_from_enum("STCR_Fps24"))
S = lambda sec: int(sec * 48000) + 2 * 48000          # segundos desde 01:00:00:00 → samples de sesión

result = {
    "kind": "spotpad-analisis", "version": 1,
    "surfaces": [{"key": "hardwood", "label": "Hardwood", "tracks": ["Hardwood", "Wood"]},
                 {"key": "loose_wood", "label": "Loose Wood", "tracks": ["Loose Wood"]},
                 {"key": "clean_concrete", "label": "Clean Concrete", "tracks": ["Clean Concrete", "Concrete Clean"]},
                 {"key": "special", "label": "Special", "tracks": ["Special"]},
                 {"key": "lava", "label": "Lava", "tracks": ["Lava"]}],
    "extras": [{"key": "carpet", "label": "Carpet", "tracks": ["Carpet"]},
               {"key": "water", "label": "Water", "tracks": ["Water"]}],
    "locations": [
        {"id": 1, "surface": "hardwood", "doubt": None, "extras": ["carpet"], "extras_doubt": []},
        {"id": 2, "surface": None, "doubt": "Hardwood / Loose Wood?", "extras": [], "extras_doubt": ["water"]},
        {"id": 3, "surface": "clean_concrete", "doubt": None, "extras": [], "extras_doubt": []},
        {"id": 4, "surface": "lava", "doubt": None, "extras": [], "extras_doubt": []},
    ],
    "scenes": [
        {"n": 1, "start": 0, "end": 10, "tc_in": "01:00:00:00", "tc_out": "01:00:10:00", "location": 1},
        {"n": 2, "start": 10, "end": 20, "tc_in": "01:00:10:00", "tc_out": "01:00:20:00", "location": 1},
        {"n": 3, "start": 20, "end": 30, "tc_in": "01:00:20:00", "tc_out": "01:00:30:00", "location": 2},
        {"n": 4, "start": 30, "end": 40, "tc_in": "01:00:30:00", "tc_out": "01:00:40:00", "location": 3},
        {"n": 5, "start": 40, "end": 50, "tc_in": "01:00:40:00", "tc_out": "01:00:50:00", "location": 4},
        {"n": 6, "start": 50, "end": 60, "tc_in": "01:00:50:00", "tc_out": "01:01:00:00", "location": 3},
    ],
}
assert conv.samples("01:00:00:00") == S(0) and conv.samples("01:00:10:12") == S(10.5)

tracks = [{"name": n, "folder": "SURFACES", "inactive": False} for n in
          ["Wood", "Loose Wood", "Concrete Clean", "Carpet", "Water", "Special"]] + \
         [{"name": "IA Dudas", "folder": "SURFACES", "inactive": True},
          {"name": "Henry", "folder": "FTS", "inactive": False}]
edl = {"Concrete Clean": [EdlEvent("Concrete Clean", "Clean", S(50), S(58))],      # esc 6 ya marcada
       "Wood": [EdlEvent("Wood", "Wood", S(29), S(31))]}                         # se pisa con esc 3? no: esc 3 va a dudas
p = ai_import.plan(result, tracks, edl, [S(10) + 100], conv.samples)
acts = p["actions"]
mk = [a for a in acts if a["op"] == "marker"]
assert [a["name"] for a in mk] == ["Esc 1 · L1", "Esc 3 · L2", "Esc 4 · L3", "Esc 5 · L4", "Esc 6 · L3"], mk  # esc 2 ya tenía
gr = [(a["track"], a["start"], a["end"], a["name"]) for a in acts if a["op"] == "group"]
assert ("Wood", S(0), S(20), "Hardwood") in gr, gr                      # escenas 1+2 juntas
assert ("Carpet", S(0), S(20), "Carpet") in gr
assert ("IA Dudas", S(20), S(30), "Hardwood / Loose Wood? + Water?") in gr
assert ("Concrete Clean", S(30), S(40), "Clean Concrete") in gr
assert ("IA Dudas", S(40), S(50), "Lava (sin track)") in gr
assert not any(a[1] >= S(50) for a in gr), "la escena 6 ya tenía superficie"
assert p["summary"] == {"markers": 5, "surfaces": 3, "doubts": 2, "skipped": 2}, p["summary"]
assert p["doubt_track_exists"] and p["doubt_track_inactive"]
assert any("Lava" in n for n in p["notes"])
# Clip existente dentro del rango del track destino → no se agrupa encima
edl2 = {"Wood": [EdlEvent("Wood", "Wood", S(15), S(16))]}
p2 = ai_import.plan(result, tracks, edl2, [], conv.samples, {"markers": False})
assert not any(a["track"] == "Wood" for a in p2["actions"]) and any("se pisa" in s["why"] for s in p2["skipped"])
# Sin carpeta de superficies
p3 = ai_import.plan(result, [{"name": "Henry", "folder": "FTS"}], {}, [], conv.samples)
assert p3["summary"]["surfaces"] == 0 and p3["notes"]

# --- Aplicado contra el mock (sesión de prueba: Surfaces con IA Dudas inactivo) ---------------- #
m = MockProTools()
job = AiJob(m)
mres = json.loads(json.dumps(result))
prev = job.preview(mres, {})
assert prev["summary"]["doubts"] == 2, prev
job.start(mres, {})
for _ in range(100):
    if not job.status()["running"]:
        break
    time.sleep(0.05)
st = job.status()
assert st["phase"] == "Listo", st
assert ("group", "Wood", S(0), S(20), "Hardwood") in m.ai_log, m.ai_log
assert any(x[0] == "group" and x[1] == "IA Dudas" for x in m.ai_log)
assert "t20" in m._off, "IA Dudas tiene que quedar inactivo"
assert sum(1 for x in m.ai_log if x[0] == "marker") == 6
# Segunda corrida: no repite nada (markers ya están, superficies ya marcadas)
job.start(mres, {})
while job.status()["running"]:
    time.sleep(0.05)
assert job.status()["summary"] == {"markers": 0, "surfaces": 0, "doubts": 0, "skipped": 12}, job.status()
# Sin track de dudas: lo crea y lo deja inactivo
m2 = MockProTools(); m2._dudas = 0
j2 = AiJob(m2); j2.start(mres, {})
while j2.status()["running"]:
    time.sleep(0.05)
assert "IA Dudas" in m2._extra_tracks and "x0" in m2._off, (m2._extra_tracks, m2._off)
try:
    job.start({"kind": "otra"}, {}); raise AssertionError
except SpotError:
    pass
print("ia OK")
