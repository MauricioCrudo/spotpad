"""Emparejamiento de dispositivos: 401 sin token, localhost libre, código, intentos, quitar, apagar."""
import asyncio, os, tempfile
os.environ["SPOTPAD_DATA"] = tempfile.mkdtemp()
import bridge
from aiohttp.test_utils import TestClient, TestServer
from types import SimpleNamespace as N

P = bridge.Pairing(os.path.join(os.environ["SPOTPAD_DATA"], "dispositivos.json"))
assert P.d["enabled"] is True and len(P.d["code"]) == 4 and P.d["code"].isdigit()   # prendido por defecto, 4 dígitos

R = lambda ip, cookie="", header="": N(remote=ip, cookies={"spotpad_dev": cookie} if cookie else {},
                                       headers={"X-SpotPad-Token": header} if header else {})
# desde esta Mac no pide nada; desde la red sí
assert P.autorizado(R("127.0.0.1")) and P.autorizado(R("::1"))
assert not P.autorizado(R("192.168.1.20"))
# código incorrecto / correcto
tok, err = P.emparejar(R("192.168.1.20"), "0000" if P.d["code"] != "0000" else "1111", "iPad")
assert tok is None and "código" in err.lower()
tok, err = P.emparejar(R("192.168.1.20"), P.d["code"], "iPad")
assert tok and not err
assert P.autorizado(R("192.168.1.20", cookie=tok)) and P.autorizado(R("192.168.1.99", header=tok))
assert not P.autorizado(R("192.168.1.20", cookie="otro"))
# el archivo guarda el hash, no el token
txt = open(P.path, encoding="utf-8").read()
assert tok not in txt and P.d["devices"][0]["name"] == "iPad"
# sobrevive a reiniciar SpotPad
P2 = bridge.Pairing(P.path)
assert P2.autorizado(R("192.168.1.20", cookie=tok)) and P2.d["code"] == P.d["code"]
# demasiados intentos: se traba (aunque después acierte)
malo = "0000" if P.d["code"] != "0000" else "1111"
for _ in range(bridge.Pairing.INTENTOS_IP):
    P.emparejar(R("10.0.0.5"), malo, "x")
t2, e2 = P.emparejar(R("10.0.0.5"), P.d["code"], "x")
assert t2 is None and "intentos" in e2
# código nuevo destraba y no desempareja
viejo = P.d["code"]; nuevo = P.nuevo_codigo()
assert len(nuevo) == 4 and P.autorizado(R("192.168.1.20", cookie=tok))
assert P.emparejar(R("10.0.0.5"), nuevo, "Celu")[0]
# quitar un dispositivo
dev = P.info()["devices"][0]["id"]
P.quitar(dev)
assert not P.autorizado(R("192.168.1.20", cookie=tok))
# apagado: todo pasa
P.set_enabled(False)
assert P.autorizado(R("192.168.1.77")) and bridge.Pairing(P.path).d["enabled"] is False
P.set_enabled(True)
P.quitar(None)
assert P.info()["devices"] == []


# ---- por HTTP: el middleware y las rutas ----------------------------------------------------- #
async def http():
    bridge.PAIR = bridge.Pairing(os.path.join(os.environ["SPOTPAD_DATA"], "http.json"))
    app = bridge.make_app(bridge.MockProTools())
    async with TestClient(TestServer(app)) as c:
        # TestClient llega por 127.0.0.1: es «esta Mac» → libre
        r = await c.get("/api/health"); assert r.status == 200
        r = await c.get("/api/status"); assert r.status == 200
        r = await c.get("/api/pair/info"); j = await r.json(); assert r.status == 200 and j["code"] == bridge.PAIR.d["code"]
        r = await c.get("/pair.js"); assert r.status == 200 and "/api/pair" in await r.text()
        r = await c.get("/conectar"); t = await r.text(); assert r.status == 200 and bridge.PAIR.d["code"] in t
        # un dispositivo de la red (simulado: el guardia ve otra IP)
        orig = bridge.Pairing.es_local
        bridge.Pairing.es_local = staticmethod(lambda req: False)
        try:
            r = await c.get("/api/status"); j = await r.json()
            assert r.status == 401 and j["pair"] is True
            assert (await c.get("/api/health")).status == 200            # abierto
            assert (await c.get("/api/pair/info")).status == 401         # sin emparejar, ni eso
            assert (await c.get("/conectar")).status == 403              # la página con el código: solo en la Mac
            r = await c.post("/api/pair", json={"codigo": "x"}); assert r.status == 403
            r = await c.post("/api/pair", json={"codigo": bridge.PAIR.d["code"], "nombre": "iPad de prueba"})
            j = await r.json(); assert r.status == 200 and j["token"]
            assert "spotpad_dev" in r.cookies
            r = await c.get("/api/status"); assert r.status == 200       # la cookie quedó en el cliente
            # ya emparejado, el código y el interruptor siguen siendo solo de la Mac
            assert (await c.get("/api/pair/info")).status == 403
            assert (await c.put("/api/pair/config", json={"enabled": False})).status == 403
            assert (await c.delete("/api/pair/devices")).status == 403
            c.session.cookie_jar.clear()
            r = await c.get("/api/status", headers={"X-SpotPad-Token": j["token"]}); assert r.status == 200
        finally:
            bridge.Pairing.es_local = orig

asyncio.run(http())
print("emparejamiento OK")
