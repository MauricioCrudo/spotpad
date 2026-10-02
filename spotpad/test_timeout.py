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
release.set(); time.sleep(0.2)                   # Pro Tools vuelve
assert pt._call(lambda e: "ok") == "ok"
try:
    pt._call(lambda e: (_ for _ in ()).throw(RuntimeError("ErrType 106: PT_NoOpenedSession (There is no open session.)")))
except SpotError as e:
    assert "sesión abierta" in str(e), e
print("timeout OK")
