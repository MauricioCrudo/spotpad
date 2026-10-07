"""Nombres del proyecto: lo escrito a mano queda por proyecto y track."""
import os, tempfile, pathlib
os.environ["SPOTPAD_DATA"] = tempfile.mkdtemp()
from bridge import Projects, default_project
assert default_project("LaCasa_R2_v3") == "LaCasa"
P = Projects(pathlib.Path(os.environ["SPOTPAD_DATA"]) / "p.json")
P.add("Serie_EP03_Foley", "Props 1", "Remote control")
P.add("Serie_EP04_Foley", "Props 1", "remote control")          # otro capítulo, mismo proyecto: no duplica
v = P.view("Serie_EP04_Foley")
assert v["project"] == "Serie" and v["tracks"] == [{"track": "Props 1", "names": ["Remote control"]}], v
P.set_project("Serie_EP04_Foley", "Serie Netflix")             # cambiar el nombre: se lleva lo guardado
assert P.view("Serie_EP04_Foley")["tracks"][0]["names"] == ["Remote control"]
P.remove("Serie_EP04_Foley", "Props 1", "REMOTE CONTROL")
assert P.view("Serie_EP04_Foley")["tracks"] == []
assert P.view("Serie_EP03_Foley")["tracks"]                    # el otro capítulo sigue en «Serie»
print("proyecto OK")
