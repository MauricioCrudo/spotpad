"""ProTools.set_active: prueba el valor de `enabled`, verifica leyendo y corrige si quedó al revés."""
from types import SimpleNamespace as N
from bridge import ProTools, SpotError


def fake(enabled_means_inactive=True):
    st = {"x": 2}                                    # 2 = inactivo explícito, 1 = activo
    calls = []

    class Client:
        def run(self, op):
            en = op.request.enabled; assert list(op.request.track_names) == ["Group"]
            calls.append(en)
            st["x"] = 2 if (en == enabled_means_inactive) else 1
    eng = N(client=Client(),
            track_list=lambda: [N(id="x", name="Group", track_attributes=N(is_inactive=st["x"]))])
    pt = ProTools.__new__(ProTools)
    pt._call = lambda f, timeout=None: f(eng)
    return pt, calls


for means in (True, False):
    pt, calls = fake(means)
    r = pt.set_active("x", True)
    assert r["ok"] and "activado" in r["msg"], r
    assert len(calls) == (1 if means else 2), calls
pt, _ = fake()
pt.set_active("x", True)
assert "ya estaba" in pt.set_active("x", True)["msg"]
try:
    pt.set_active("nope", True); raise AssertionError
except SpotError:
    pass
print("active OK")
