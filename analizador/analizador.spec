# Receta de PyInstaller: genera «SpotPad Analizador.app» (Mac) o la carpeta «SpotPad Analizador» (Windows).
#   python export_model.py      (una vez: deja el modelo en ./modelo)
#   pyinstaller analizador.spec
import os
import sys

from PIL import Image, ImageDraw
from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, ".")
from analyzer import VERSION              # noqa: E402

is_mac, is_win = sys.platform == "darwin", sys.platform == "win32"
NAME = "SpotPad Analizador"

assert os.path.exists("modelo/vision.onnx"), "Falta el modelo: corré primero export_model.py"

# Ícono: cuadrado oscuro con tres franjas (superficies)
icon_png = "build_icon.png"
im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((16, 16, 496, 496), 110, fill=(28, 28, 32, 255))
for i, c in enumerate([(232, 163, 61), (90, 160, 110), (110, 140, 200)]):
    d.rounded_rectangle((96, 120 + i * 100, 416, 190 + i * 100), 30, fill=c + (255,))
im.save(icon_png)

datas = [("modelo", "modelo"), ("surfaces.json", ".")]
binaries, hidden = [], ["analyzer", "cuts", "scenes", "surfaces", "video", "model", "edl_cmx", "tc"]
for pkg in ("onnxruntime", "imageio_ffmpeg"):
    d_, b_, h_ = collect_all(pkg)
    datas += d_; binaries += b_; hidden += h_

a = Analysis(["gui.py"], pathex=[".", "../spotpad"], datas=datas, binaries=binaries, hiddenimports=hidden,
             excludes=["torch", "transformers", "matplotlib", "scipy", "pandas", "onnx"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name=NAME, console=False, icon=icon_png, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
if is_mac:
    app = BUNDLE(coll, name=NAME + ".app", icon=icon_png, bundle_identifier="ar.mauriciocrudo.spotpad.analizador",
                 info_plist={"CFBundleName": NAME, "CFBundleShortVersionString": VERSION,
                             "NSHighResolutionCapable": True,
                             "NSLocalNetworkUsageDescription": "El analizador le manda el resultado a SpotPad por la red local."})
