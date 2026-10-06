"""
SpotPad · Analizador — ventana para analizar un capítulo y marcar la sesión de Pro Tools.

1. Elegís el video (y la EDL de montaje si la tenés).
2. «Analizar»: corre todo en esta computadora (nada se sube a ningún lado).
3. «Marcar en Pro Tools»: le manda el resultado a SpotPad (en esta compu o en la del estudio, por la
   red local) y SpotPad crea los markers por escena, las superficies y las dudas en «IA Dudas».
"""
import json
import os
import queue
import sys
import threading
import traceback
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, "..", "spotpad"))

import tkinter as tk                          # noqa: E402
from tkinter import filedialog, messagebox, ttk   # noqa: E402

APP = "SpotPad Analizador"


def config_path():
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/SpotPad")
    elif sys.platform == "win32":
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SpotPad")
    else:
        base = os.path.expanduser("~/.config/spotpad")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "analizador.json")


def load_cfg():
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(d):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except OSError:
        pass


def spotpad_url(addr: str, path: str) -> str:
    a = (addr or "localhost:8765").strip().rstrip("/")
    if not a.startswith("http"):
        a = "http://" + a
    if ":" not in a.split("//", 1)[1]:
        a += ":8765"
    return a + path


def call(addr, path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(spotpad_url(addr, path), data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error") or str(e)
        except Exception:
            msg = str(e)
        raise RuntimeError(msg)
    except urllib.error.URLError as e:
        raise RuntimeError(f"No encuentro SpotPad en {addr} ({e.reason}). ¿Está abierto?")


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.cfg = load_cfg()
        self.result = None
        self.cancel = False
        self.busy = False
        root.title(APP)
        root.minsize(560, 520)
        f = ttk.Frame(root, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        r = 0
        ttk.Label(f, text="Video del capítulo").grid(row=r, column=0, sticky="w")
        self.video = tk.StringVar()
        ttk.Entry(f, textvariable=self.video).grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Elegir…", command=self.pick_video).grid(row=r, column=2)
        r += 1
        ttk.Label(f, text="EDL de montaje (opcional)").grid(row=r, column=0, sticky="w", pady=(6, 0))
        self.edl = tk.StringVar()
        ttk.Entry(f, textvariable=self.edl).grid(row=r, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(f, text="Elegir…", command=self.pick_edl).grid(row=r, column=2, pady=(6, 0))
        r += 1
        ttk.Label(f, text="TC de inicio del video").grid(row=r, column=0, sticky="w", pady=(6, 0))
        self.tc = tk.StringVar()
        ttk.Entry(f, textvariable=self.tc, width=14).grid(row=r, column=1, sticky="w", padx=6, pady=(6, 0))
        r += 1
        ttk.Label(f, text="Escenas").grid(row=r, column=0, sticky="w", pady=(6, 0))
        sf = ttk.Frame(f)
        sf.grid(row=r, column=1, columnspan=2, sticky="ew", padx=6, pady=(6, 0))
        ttk.Label(sf, text="menos").pack(side="left")
        self.sens = tk.DoubleVar(value=float(self.cfg.get("sens", 1.0)))
        ttk.Scale(sf, from_=0.5, to=2.0, variable=self.sens).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(sf, text="más").pack(side="left")
        r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=12)
        r += 1
        ttk.Label(f, text="SpotPad (dirección)").grid(row=r, column=0, sticky="w")
        self.addr = tk.StringVar(value=self.cfg.get("addr", "localhost:8765"))
        ttk.Entry(f, textvariable=self.addr).grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Probar", command=self.test_spotpad).grid(row=r, column=2)
        r += 1
        ttk.Label(f, text="Si SpotPad está en otra computadora, poné la dirección que muestra su página "
                          "«Conectar» (p. ej. 192.168.0.20:8765).", foreground="#777", wraplength=520) \
            .grid(row=r, column=0, columnspan=3, sticky="w", pady=(2, 0))
        r += 1
        of = ttk.Frame(f)
        of.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.o_markers = tk.BooleanVar(value=self.cfg.get("markers", True))
        self.o_surf = tk.BooleanVar(value=self.cfg.get("surfaces", True))
        ttk.Checkbutton(of, text="Markers por escena", variable=self.o_markers).pack(side="left")
        ttk.Checkbutton(of, text="Superficies (y dudas en «IA Dudas»)", variable=self.o_surf).pack(side="left", padx=12)
        r += 1
        bf = ttk.Frame(f)
        bf.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.b_an = ttk.Button(bf, text="Analizar", command=self.analyze)
        self.b_an.pack(side="left")
        self.b_mark = ttk.Button(bf, text="Marcar en Pro Tools", command=self.mark, state="disabled")
        self.b_mark.pack(side="left", padx=8)
        self.b_save = ttk.Button(bf, text="Guardar análisis…", command=self.save, state="disabled")
        self.b_save.pack(side="left")
        self.b_open = ttk.Button(bf, text="Abrir análisis…", command=self.open_result)
        self.b_open.pack(side="left", padx=8)
        self.b_cancel = ttk.Button(bf, text="Cancelar", command=self.do_cancel, state="disabled")
        self.b_cancel.pack(side="right")
        r += 1
        self.prog = ttk.Progressbar(f, maximum=1000)
        self.prog.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(12, 2))
        r += 1
        self.status = tk.StringVar(value="Elegí el video del capítulo.")
        ttk.Label(f, textvariable=self.status).grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        self.log = tk.Text(f, height=12, wrap="word", state="disabled", relief="flat",
                           background="#f4f4f4" if sys.platform != "darwin" else "#ececec")
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        f.rowconfigure(r, weight=1)
        self.say("Todo el análisis corre en esta computadora: el video no se sube a ningún lado.")
        root.after(100, self.pump)

    # -- utilidades de UI -------------------------------------------------------- #
    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.say(val)
                elif kind == "prog":
                    frac, label = val
                    self.prog["value"] = int(frac * 1000)
                    self.status.set(f"{label}… {int(frac * 100)} %")
                elif kind == "status":
                    self.status.set(val)
                elif kind == "call":
                    val()
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def say(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def ui(self, fn):
        self.q.put(("call", fn))

    def set_busy(self, b):
        self.busy = b
        st = "disabled" if b else "normal"
        self.b_an.configure(state=st)
        self.b_open.configure(state=st)
        self.b_mark.configure(state="disabled" if b or not self.result else "normal")
        self.b_save.configure(state="disabled" if not self.result else "normal")
        self.b_cancel.configure(state="normal" if b else "disabled")

    def remember(self):
        self.cfg.update(addr=self.addr.get().strip(), sens=round(self.sens.get(), 2),
                        markers=self.o_markers.get(), surfaces=self.o_surf.get(),
                        dir=os.path.dirname(self.video.get()) or self.cfg.get("dir", ""))
        save_cfg(self.cfg)

    # -- acciones -------------------------------------------------------------- #
    def pick_video(self):
        p = filedialog.askopenfilename(title="Video del capítulo", initialdir=self.cfg.get("dir") or None,
                                       filetypes=[("Video", "*.mov *.mp4 *.mxf *.m4v *.avi"), ("Todos", "*.*")])
        if not p:
            return
        self.video.set(p)
        self.result = None
        self.set_busy(False)
        try:
            import video
            info = video.probe(p)
            self.tc.set(info.start_tc)
            self.say(f"{os.path.basename(p)}: {info.width}×{info.height}, {float(info.fps):.3f} fps, "
                     f"{info.duration / 60:.1f} min" + (f", TC {info.start_tc}" if info.start_tc else
                                                         " — no trae TC: escribilo"))
            self.status.set("Listo para analizar.")
        except Exception as ex:
            self.say(f"No pude leer el video: {ex}")

    def pick_edl(self):
        p = filedialog.askopenfilename(title="EDL de montaje (CMX 3600)", filetypes=[("EDL", "*.edl"), ("Todos", "*.*")])
        if p:
            self.edl.set(p)

    def test_spotpad(self):
        def work():
            try:
                st = call(self.addr.get(), "/api/status", timeout=5)
                msg = (f"SpotPad OK · sesión «{st.get('session')}»" if st.get("connected")
                       else f"SpotPad responde, pero sin Pro Tools: {st.get('error', '')}")
            except Exception as ex:
                msg = str(ex)
            self.q.put(("log", msg))
        threading.Thread(target=work, daemon=True).start()

    def do_cancel(self):
        self.cancel = True
        if self.marking:
            try:
                call(self.addr.get(), "/api/ai/cancel", {}, timeout=5)
            except Exception:
                pass

    marking = False

    def analyze(self):
        path = self.video.get().strip()
        if not os.path.exists(path):
            messagebox.showwarning(APP, "Elegí el video del capítulo.")
            return
        edl = self.edl.get().strip() or None
        if edl and not os.path.exists(edl):
            messagebox.showwarning(APP, "No encuentro la EDL.")
            return
        self.remember()
        self.cancel = False
        self.result = None
        self.set_busy(True)
        tc, sens = self.tc.get().strip(), float(self.sens.get())

        def work():
            try:
                from analyzer import analyze
                res = analyze(path, edl, tc or None, sens,
                              progress=lambda f, l: self.q.put(("prog", (f, l))),
                              cancel=lambda: self.cancel, log=lambda m: self.q.put(("log", m)))
                self.result = res
                nd = sum(1 for l in res["locations"] if l["doubt"])
                self.q.put(("status", f"{len(res['scenes'])} escenas, {len(res['locations'])} locaciones "
                                      f"({nd} con duda). Revisá y tocá «Marcar en Pro Tools»."))
                self.ui(self.show_result)
                self.ui(self.preview)
            except InterruptedError:
                self.q.put(("status", "Cancelado."))
            except Exception as ex:
                self.q.put(("log", "Error: " + str(ex)))
                self.q.put(("log", traceback.format_exc(limit=3)))
                self.q.put(("status", "No se pudo analizar."))
            finally:
                self.ui(lambda: self.set_busy(False))
        threading.Thread(target=work, daemon=True).start()

    def show_result(self):
        res = self.result
        labels = {s["key"]: s["label"] for s in res["surfaces"] + res.get("extras", [])}
        self.say("— Locaciones —")
        for l in res["locations"]:
            what = l["doubt"] or labels.get(l["surface"], "?")
            ex = ", ".join(labels.get(k, k) for k in l.get("extras", []))
            first = next(s for s in res["scenes"] if s["location"] == l["id"])
            self.say(f"L{l['id']}: {what}{' + ' + ex if ex else ''} · escenas {', '.join(map(str, l['scenes']))} "
                     f"(desde {first['tc_in']})")

    def preview(self):
        if not self.result:
            return
        opts = {"markers": self.o_markers.get(), "surfaces": self.o_surf.get()}

        def work():
            try:
                p = call(self.addr.get(), "/api/ai/preview", {"result": self.result, "options": opts})
                s = p["summary"]
                self.q.put(("log", f"En Pro Tools se van a crear: {s['markers']} markers, {s['surfaces']} "
                                   f"superficies y {s['doubts']} dudas" +
                                   (f" ({s['skipped']} cosas ya estaban y se saltean)" if s["skipped"] else "")))
                for n in p.get("notes", []):
                    self.q.put(("log", n))
            except Exception as ex:
                self.q.put(("log", f"(Vista previa en SpotPad: {ex})"))
        threading.Thread(target=work, daemon=True).start()

    def mark(self):
        if not self.result:
            return
        self.remember()
        if not messagebox.askokcancel(APP, "SpotPad va a marcar la sesión abierta en Pro Tools.\n\n"
                                           "Mientras tanto no toques Pro Tools (tarda uno o dos minutos)."):
            return
        opts = {"markers": self.o_markers.get(), "surfaces": self.o_surf.get()}
        self.cancel = False
        self.marking = True
        self.set_busy(True)

        def work():
            import time
            try:
                call(self.addr.get(), "/api/ai/apply", {"result": self.result, "options": opts})
                while True:
                    time.sleep(1)
                    st = call(self.addr.get(), "/api/ai/status", timeout=10)
                    tot = max(1, st.get("total") or 1)
                    self.q.put(("prog", (st.get("done", 0) / tot, st.get("phase") or "Marcando")))
                    if not st.get("running"):
                        break
                self.q.put(("status", st.get("msg") or st.get("phase")))
                self.q.put(("log", st.get("msg") or ""))
                for e in st.get("errors", []):
                    self.q.put(("log", "· " + e))
            except Exception as ex:
                self.q.put(("log", "Error: " + str(ex)))
                self.q.put(("status", "No se pudo marcar."))
            finally:
                self.marking = False
                self.ui(lambda: self.set_busy(False))
        threading.Thread(target=work, daemon=True).start()

    def save(self):
        if not self.result:
            return
        base = os.path.splitext(os.path.basename(self.result.get("video", "analisis")))[0]
        p = filedialog.asksaveasfilename(title="Guardar análisis", defaultextension=".json",
                                         initialfile=base + "_spotpad.json", filetypes=[("Análisis", "*.json")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.result, f, ensure_ascii=False, indent=1)
            self.say("Guardado: " + p)

    def open_result(self):
        p = filedialog.askopenfilename(title="Abrir análisis", filetypes=[("Análisis", "*.json")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                res = json.load(f)
            if res.get("kind") != "spotpad-analisis":
                raise ValueError("no es un análisis de SpotPad")
            self.result = res
            self.set_busy(False)
            self.say(f"Abierto: {os.path.basename(p)} ({len(res['scenes'])} escenas)")
            self.show_result()
            self.preview()
        except Exception as ex:
            messagebox.showerror(APP, f"No pude abrirlo: {ex}")


def selftest():
    """Para la compilación: que carguen ffmpeg, onnxruntime, el modelo y la ventana."""
    import numpy as np

    import video
    from model import Vision
    print("ffmpeg:", video.ffmpeg_exe())
    v = Vision()
    e = v.embed([np.zeros((224, 224, 3), np.uint8)])
    print("modelo:", v.provider, e.shape)
    import surfaces
    surfaces.check_text(surfaces.load_config(), v.text)
    print("selftest OK")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    if len(sys.argv) > 1 and sys.argv[1].endswith((".mov", ".mp4", ".mxf")):
        import analyzer
        analyzer.main()
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
