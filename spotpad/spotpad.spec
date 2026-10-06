# Receta de PyInstaller: genera SpotPad.app (Mac) o SpotPad.exe (Windows).
#   pyinstaller spotpad.spec
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

sys.path.insert(0, ".")
from app import make_icon, APP_VERSION          # noqa: E402

is_mac, is_win = sys.platform == "darwin", sys.platform == "win32"
icon_png = "build_icon.png"
make_icon(512).save(icon_png)                  # PyInstaller lo convierte a .icns / .ico

datas = [("static", "static"), ("presets.json", ".")]
binaries, hidden = [], []
for pkg in ("ptsl", "grpc", "qrcode"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hidden += h
hidden += collect_submodules("pystray") + collect_submodules("pynput") + ["PIL._tkinter_finder", "hotkeys", "naming", "ai_import"]
if is_mac:
    hidden += ["HIServices", "Quartz", "AppKit", "Foundation", "objc"]

a = Analysis(["app.py"], pathex=["."], datas=datas, binaries=binaries, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "numpy"])
pyz = PYZ(a.pure)

if is_win:
    # Un solo .exe portable
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="SpotPad", console=False, icon=icon_png,
              upx=False)
else:
    exe = EXE(pyz, a.scripts, exclude_binaries=True, name="SpotPad", console=False, icon=icon_png)
    coll = COLLECT(exe, a.binaries, a.datas, name="SpotPad")
    app = BUNDLE(
        coll, name="SpotPad.app", icon=icon_png, bundle_identifier="ar.mauriciocrudo.spotpad",
        info_plist={
            "CFBundleName": "SpotPad",
            "CFBundleShortVersionString": APP_VERSION,
            "LSUIElement": True,           # solo ícono en la barra de menú, sin Dock
            "NSLocalNetworkUsageDescription": "SpotPad recibe los toques del iPad por la red local.",
        },
    )
