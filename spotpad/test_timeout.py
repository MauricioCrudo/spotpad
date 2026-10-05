"""Pro Tools colgado: el bridge tiene que contestar error rápido y no trabarse."""
import threading, time
import bridge
from bridge import ProTools, SpotError

class Fake(ProTools):
    TIMEOUT = 0.5
    def _eng(self):
        return object()

pt = Fake()
release = threading.Event()
t0 = time.time()
try:
    pt._call(lambda e: release.wait(5))          # Pro Tools no contesta
    raise AssertionError("debería dar timeout")
except SpotError as e:
    assert "no responde" in str(e)
assert time.time() - t0 < 1.5
t1 = time.time()
try:                                             # mientras sigue colgado: falla al instante
    pt._call(lambda e: "ok")
    raise AssertionError("debería fallar rápido")
except SpotError:
    pass
assert time.time() - t1 < 0.2
pt.RETRY_AFTER = 0.3; time.sleep(0.4)            # pasado un rato: reconecta aunque el viejo siga colgado
assert pt._call(lambda e: "ok") == "ok"
release.set()
try:
    pt._call(lambda e: (_ for _ in ()).throw(RuntimeError("ErrType 106: PT_NoOpenedSession (There is no open session.)")))
except SpotError as e:
    assert "sesión abierta" in str(e), e
print("timeout OK")

# --- El estado dice qué comando quedó colgado, y Reconectar lo destraba ---
from ptsl import PTSL_pb2 as ptpb

class Op:
    def command_id(self): return ptpb.CId_GroupClips

class Client:
    def __init__(self, ev): self.ev = ev
    def run(self, op): self.ev.wait(5)

class Eng:
    def __init__(self, ev): self.client = Client(ev)
    def group_clips(self): self.client.run(Op())

class Fake2(ProTools):
    TIMEOUT = 0.5
    RETRY_AFTER = 60
    def __init__(self):
        super().__init__(); self.hang = threading.Event()
    def _eng(self):
        if self._engine is None:
            e = Eng(self.hang); self._track_commands(e); self._engine = e; self._connections += 1
        return self._engine
    def status(self):
        return {"connected": True, "session": "S"}

pt2 = Fake2()
try:
    pt2._call(lambda e: e.group_clips())
except SpotError:
    pass
h = pt2.health()
assert h["stuck"] and h["inflight"]["command"] == "GroupClips", h
assert "GroupClips" in h["last_error"]["msg"], h
r = pt2.reconnect()
assert r["ok"] and not pt2.health()["stuck"], (r, pt2.health())
pt2.hang.set()
print("health/reconnect OK")

# --- Nunca usar GetEditSelection (cuelga el SDK de Pro Tools 2025.12) ---
class SelEng:
    def get_edit_selection(self, *a): raise AssertionError("no se debe usar GetEditSelection")
    def get_timeline_selection(self, *a): return ("100", "200")
assert ProTools()._selection_samples(SelEng()) == (100, 200)
src = open(bridge.__file__, encoding="utf-8").read()
assert "e.get_edit_selection(" not in src, "volvió GetEditSelection al código"

# --- Si conectar se cuelga dos veces seguidas, pedir reiniciar Pro Tools ---
class Dead(ProTools):
    TIMEOUT = 0.3
    RETRY_AFTER = 0
    def __init__(self):
        super().__init__(); self.ev = threading.Event()
    def _eng(self):
        self._inflight = ("Conectar con Pro Tools", time.time())
        self.ev.wait(3)
pd = Dead()
msgs = []
for _ in range(2):
    try:
        pd._call(lambda e: None)
    except SpotError as e:
        msgs.append(str(e))
assert "reiniciar" in msgs[-1].lower() or "volvé a abrir" in msgs[-1], msgs
assert pd.health()["needs_pt_restart"]
assert not pd.reconnect()["ok"]          # con un intento de conexión en curso, no apila otro
pd.ev.set()
print("edit-selection/dead OK")
