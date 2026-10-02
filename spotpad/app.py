"""
SpotPad como app de escritorio (lo que se compila a SpotPad.app / SpotPad.exe).

  * Levanta el bridge en segundo plano.
  * Pone un ícono en la barra de menú (Mac) o en la bandeja junto al reloj (Windows)
    con: dirección para el iPad, QR para conectar, diagnóstico, configuración y salir.

  python3 app.py            # como la app
  python3 app.py --mock     # sin Pro Tools
  python3 app.py --selftest # prueba de arranque (la usa la compilación automática)
"""
import argparse
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import bridge
from bridge import DATA, DIAG_FILE, MockProTools, ProTools, ipad_url, start_server

APP_VERSION = "0.3.1"
log = logging.getLogger("spotpad")


def open_path(path):
    """Abre un archivo o carpeta con la app del sistema."""
    if sys.platform == "win32":
        os.startfile(str(path))          # noqa: S606 (solo Windows)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def make_icon(size=64):
    """Ícono dibujado (no hace falta archivo): cuadrado ámbar con 4 pads."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 5
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=r, fill=(232, 163, 61, 255))
    pad, gap = size // 10 + 4, size // 12
    cell = (size - 2 * pad - gap) // 2
    for i in range(2):
        for j in range(2):
            x, y = pad + i * (cell + gap), pad + j * (cell + gap)
            d.rounded_rectangle([x, y, x + cell, y + cell], radius=max(2, cell // 5), fill=(18, 18, 20, 255))
    return img


def selftest(port):
    """Arranca en modo mock, pide /api/status y /api/layout, y sale. Exit 0 = OK."""
    import ptsl                      # noqa: F401  (que el SDK haya quedado empaquetado)
    from ptsl import PTSL_pb2        # noqa: F401
    stop = start_server(MockProTools(), port)
    try:
        for path in ("/api/status", "/api/layout", "/", "/conectar"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                assert r.status == 200, (path, r.status)
        print(f"selftest OK · datos en {DATA}")
        return 0
    finally:
        stop()


def main():
    ap = argparse.ArgumentParser(description="SpotPad")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a, _ = ap.parse_known_args()     # macOS a veces agrega -psn_… al abrir la app

    logging.basicConfig(
        level=logging.DEBUG if a.debug else logging.INFO, format="%(asctime)s %(message)s",
        handlers=[logging.FileHandler(DATA / "spotpad.log", encoding="utf-8"), logging.StreamHandler()])

    if a.selftest:
        sys.exit(selftest(a.port))

    pt = MockProTools() if a.mock else ProTools(debug=a.debug)
    local = f"http://localhost:{a.port}"
    try:
        stop = start_server(pt, a.port)
    except OSError as e:
        log.error("No pude abrir el puerto %s: %s", a.port, e)
        # Probablemente ya hay un SpotPad abierto: mostramos su página y salimos
        webbrowser.open(local + "/conectar")
        sys.exit(1)
    log.info("SpotPad %s · iPad: %s · datos: %s", APP_VERSION, ipad_url(a.port), DATA)

    import pystray
    from pystray import Menu, MenuItem as Item

    def run_diag(icon, _):
        def work():
            try:
                txt = pt.diag()
            except Exception as e:      # noqa: BLE001
                txt = f"ERROR: {e}"
            DIAG_FILE.write_text(txt, "utf-8")
            open_path(DIAG_FILE)
        threading.Thread(target=work, daemon=True).start()

    def quit_app(icon, _):
        log.info("Saliendo")
        try:
            stop()
            icon.stop()
        finally:
            # Salida inmediata: si Pro Tools dejó un pedido colgado, no esperamos por él
            threading.Timer(0.3, lambda: os._exit(0)).start()

    menu = Menu(
        Item(lambda _: f"iPad: {ipad_url(a.port)}", lambda *_: webbrowser.open(local + "/conectar")),
        Item("Conectar iPad (QR)", lambda *_: webbrowser.open(local + "/conectar"), default=True),
        Item("Abrir SpotPad acá", lambda *_: webbrowser.open(local)),
        Menu.SEPARATOR,
        Item("Diagnóstico de la sesión", run_diag),
        Item("Carpeta de configuración", lambda *_: open_path(DATA)),
        Item("Ver registro", lambda *_: open_path(DATA / "spotpad.log")),
        Menu.SEPARATOR,
        Item(f"Salir (v{APP_VERSION})", quit_app),
    )
    icon = pystray.Icon("SpotPad", make_icon(), "SpotPad" + (" · MOCK" if a.mock else ""), menu)

    # La primera vez, mostrar cómo conectar el iPad
    first = DATA / ".first_run_done"
    if not first.exists():
        first.write_text("1")
        threading.Timer(1.0, lambda: webbrowser.open(local + "/conectar")).start()

    icon.run()


if __name__ == "__main__":
    main()
