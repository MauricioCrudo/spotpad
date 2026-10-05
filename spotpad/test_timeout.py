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
class SelClient:
    def __init__(self, ans): self.ans, self.ops = ans, []
    def run(self, op):
        self.ops.append(op)
        # Pro Tools solo respeta time_scale: sin él contesta en el contador principal
        ok = op.request.time_scale == ptpb.TOOptions_Samples
        op.response = type("R", (), {"in_time": self.ans[0] if ok else " 2012| 3| 077",
                                     "out_time": self.ans[1] if ok else " 2012| 4| 000"})()
class SelEng:
    def __init__(self, ans=(" 100", "200")): self.client = SelClient(ans)
    def get_edit_selection(self, *a): raise AssertionError("no se debe usar GetEditSelection")
assert ProTools()._selection_samples(SelEng()) == (100, 200)
try:
    ProTools()._selection_samples(SelEng((" 2012| 3| 077", "x")))
    raise AssertionError("debería avisar el formato raro")
except SpotError as e:
    assert "2012| 3| 077" in str(e)
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

# --- Botón de carpeta: mover la selección al track y verificar antes de agrupar ---
class T:
    def __init__(s, i, n): s.id, s.name = i, n
class GEng:
    def __init__(self, follows, set_works=True):
        self.sel = ("1000", "2000"); self.follows = follows; self.set_works = set_works
        self.calls = []; eng = self
        class C:
            def run(_, op):
                op.response = type("R", (), {"in_time": eng.sel[0], "out_time": eng.sel[1]})()
        self.client = C()
    def track_list(self): return [T("{a}", "DX1"), T("{b}", "Hardwood")]
    def select_tracks_by_name(self, names):
        self.calls.append(("select", names))
        if not self.follows: self.sel = ("0", "0")
    def set_timeline_selection(self, **k):
        self.calls.append(("set", k["in_time"], k["out_time"]))
        if self.set_works: self.sel = (k["in_time"], k["out_time"])
    def group_clips(self): self.calls.append(("group",))
    def rename_selected_clip(self, **k): self.calls.append(("rename", k["new_name"]))

class GP(ProTools):
    def __init__(self, eng): super().__init__(); self.eng = eng
    def _eng(self): return self.eng

e1 = GEng(follows=True); r = GP(e1).group_on_track("{b}")
assert r["ok"] and ("group",) in e1.calls and not any(c[0] == "set" for c in e1.calls), e1.calls
e2 = GEng(follows=False); GP(e2).group_on_track("{b}", "Sonia")
assert ("set", "1000", "2000") in e2.calls and ("rename", "Sonia") in e2.calls, e2.calls
e3 = GEng(follows=False, set_works=False)
try:
    GP(e3).group_on_track("{b}"); raise AssertionError("debería frenar")
except SpotError as e:
    assert "Link Track and Edit Selection" in str(e)
assert ("group",) not in e3.calls            # no agrupa si la selección no quedó bien
print("group-on OK")
