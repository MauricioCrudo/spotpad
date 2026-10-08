"""
Atajos de teclado globales: funcionan con Pro Tools (o cualquier app) en primer plano.

Se configuran en hotkeys.json (carpeta de datos). Formato de pynput:
  "<ctrl>+<alt>+<cmd>+r"   →  ⌃⌥⌘R   (en Windows <cmd> es la tecla Windows: por eso ahí
                                       los de fábrica usan <ctrl>+<alt>+<shift>)
En Mac hace falta darle a SpotPad permiso de Accesibilidad (Ajustes del Sistema →
Privacidad y seguridad → Accesibilidad).
"""
import json
import logging
import subprocess
import sys
import threading

log = logging.getLogger("spotpad")

ACTIONS = {
    "from_selection": "Renombrar el track de grabación con el clip seleccionado",
    "next": "Siguiente pendiente (posiciona y renombra)",
    "prev": "Anterior pendiente",
    "done_next": "Marcar grabado y pasar al siguiente",
    "consolidate": "Consolidar con el nombre del primer clip (herramientas de edición)",
    "regrab": "Regrabar la selección en un clip en Regrabación (herramientas de edición)",
}
EDIT_ACTIONS = ("consolidate", "regrab")   # solo se registran si las herramientas de edición están prendidas

if sys.platform == "darwin":
    MOD = "<ctrl>+<alt>+<cmd>"
else:
    MOD = "<ctrl>+<alt>+<shift>"
DEFAULTS = {
    "enabled": True,
    "notify_success": False,
    "bindings": {
        "from_selection": f"{MOD}+r",
        "next": f"{MOD}+n",
        "prev": f"{MOD}+b",
        "done_next": f"{MOD}+g",
        "consolidate": f"{MOD}+k",
        "regrab": f"{MOD}+j",
    },
}

# Teclas para Siguiente / Anterior (las flechas con ⌃⌥⌘ chocan con otras funciones del sistema/Pro Tools)
SCHEMES = {
    "letras": ("n", "b", "N (siguiente) / B (atrás)"),
    "signos": (".", ",", ". (siguiente) / , (atrás)"),
    "fkeys": ("<f14>", "<f13>", "F14 / F13 (teclado extendido)"),
    "flechas": ("<right>", "<left>", "→ / ← (flechas)"),
}
OLD_ARROWS = (f"{MOD}+<right>", f"{MOD}+<left>")


def scheme_of(cfg) -> str:
    b = cfg.get("bindings", {})
    for k, (n, p, _l) in SCHEMES.items():
        if b.get("next") == f"{MOD}+{n}" and b.get("prev") == f"{MOD}+{p}":
            return k
    return ""


def set_scheme(path, scheme: str):
    """Cambia las teclas de Siguiente/Anterior en hotkeys.json (deja el resto como está)."""
    n, p, _l = SCHEMES[scheme]
    try:
        user = json.loads(path.read_text("utf-8"))
    except Exception:                            # noqa: BLE001
        user = json.loads(json.dumps(DEFAULTS))
    user.setdefault("bindings", {}).update({"next": f"{MOD}+{n}", "prev": f"{MOD}+{p}"})
    path.write_text(json.dumps(user, indent=2, ensure_ascii=False), "utf-8")


_SYM_MAC = {"<ctrl>": "⌃", "<alt>": "⌥", "<cmd>": "⌘", "<shift>": "⇧", "<right>": "→", "<left>": "←",
            "<up>": "↑", "<down>": "↓", "<space>": "Espacio", "<enter>": "↩", "<f13>": "F13", "<f14>": "F14"}
_SYM_WIN = {"<ctrl>": "Ctrl+", "<alt>": "Alt+", "<cmd>": "Win+", "<shift>": "Shift+", "<right>": "→",
            "<left>": "←", "<up>": "↑", "<down>": "↓", "<space>": "Espacio", "<enter>": "Enter"}


# --------------------------------------------------------------------------- #
#  Mac: atajos con RegisterEventHotKey (Carbon). No necesitan permiso de Accesibilidad,
#  así que actualizar la app no obliga a volver a darlo, y la tecla no le llega a Pro Tools.
# --------------------------------------------------------------------------- #
_MAC_MODS = {"<ctrl>": 0x1000, "<alt>": 0x800, "<cmd>": 0x100, "<shift>": 0x200}
_MAC_KEYS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
    ".": 47, "`": 50, "<space>": 49, "<enter>": 36, "<f13>": 105, "<f14>": 107, "<f15>": 113, "<f16>": 106,
    "<left>": 123, "<right>": 124, "<down>": 125, "<up>": 126,
}


def mac_combo(binding: str):
    """'<ctrl>+<alt>+<cmd>+n' → (keycode, modificadores) para RegisterEventHotKey."""
    mods, key = 0, None
    parts = [p.strip().lower() for p in binding.split("+")]
    if binding.endswith("++"):                  # la tecla es «+»
        parts = parts[:-2] + ["+"]
    for p in parts:
        if p in _MAC_MODS:
            mods |= _MAC_MODS[p]
        elif p in _MAC_KEYS:
            key = _MAC_KEYS[p]
        else:
            raise ValueError(f"tecla desconocida: {p}")
    if key is None:
        raise ValueError("falta la tecla")
    return key, mods


class CarbonKeys:
    """Atajos globales de macOS sin permisos. Las llamadas a Carbon se hacen en el hilo principal."""

    def __init__(self):
        import ctypes
        from ctypes import c_uint32, c_int32, c_void_p, c_ulong, POINTER, Structure, CFUNCTYPE
        self.ct = ctypes
        cb = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")

        class HotKeyID(Structure):
            _fields_ = [("signature", c_uint32), ("id", c_uint32)]

        class TypeSpec(Structure):
            _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]
        self.HotKeyID, self.TypeSpec = HotKeyID, TypeSpec
        self.Proc = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)
        cb.GetApplicationEventTarget.restype = c_void_p
        cb.GetApplicationEventTarget.argtypes = []
        cb.InstallEventHandler.restype = c_int32
        cb.InstallEventHandler.argtypes = [c_void_p, self.Proc, c_ulong, POINTER(TypeSpec), c_void_p, POINTER(c_void_p)]
        cb.RegisterEventHotKey.restype = c_int32
        cb.RegisterEventHotKey.argtypes = [c_uint32, c_uint32, HotKeyID, c_void_p, c_uint32, POINTER(c_void_p)]
        cb.UnregisterEventHotKey.restype = c_int32
        cb.UnregisterEventHotKey.argtypes = [c_void_p]
        cb.GetEventParameter.restype = c_int32
        cb.GetEventParameter.argtypes = [c_void_p, c_uint32, c_uint32, c_void_p, c_ulong, c_void_p, c_void_p]
        self.cb = cb
        self.refs, self.actions = [], {}
        self._handler = None
        self._proc = self.Proc(self._on_event)          # referencia viva: si se libera, crashea

    @staticmethod
    def fourcc(s):
        return int.from_bytes(s.encode("mac_roman"), "big")

    def _on_event(self, _call, event, _data):
        try:
            hk = self.HotKeyID()
            err = self.cb.GetEventParameter(event, self.fourcc("----"), self.fourcc("hkid"), None,
                                            self.ct.sizeof(hk), None, self.ct.byref(hk))
            fn = self.actions.get(hk.id) if err == 0 else None
            if fn:
                fn()
        except Exception:                        # noqa: BLE001
            log.exception("atajo")
        return 0

    def _main(self, fn, wait=3.0):
        """Corre fn en el hilo principal (donde está el run loop de la app) y devuelve su resultado."""
        try:
            from Foundation import NSThread
            if NSThread.isMainThread():
                return fn()
            from PyObjCTools import AppHelper
        except Exception:                        # noqa: BLE001
            return fn()
        box, done = {}, threading.Event()

        def run():
            try:
                box["r"] = fn()
            except Exception as e:               # noqa: BLE001
                box["e"] = e
            done.set()
        AppHelper.callAfter(run)
        if not done.wait(wait):
            raise RuntimeError("la app no respondió al registrar los atajos")
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def register(self, mapping):
        """mapping: {binding: callback}. Devuelve [(binding, error)] de los que no se pudieron registrar."""
        def do():
            ct, bad = self.ct, []
            target = self.cb.GetApplicationEventTarget()
            if self._handler is None:
                spec = self.TypeSpec(self.fourcc("keyb"), 5)      # kEventHotKeyPressed
                h = ct.c_void_p()
                st = self.cb.InstallEventHandler(target, self._proc, 1, ct.byref(spec), None, ct.byref(h))
                if st != 0:
                    raise RuntimeError(f"InstallEventHandler {st}")
                self._handler = h
            for i, (binding, fn) in enumerate(mapping.items(), 1):
                try:
                    key, mods = mac_combo(binding)
                except ValueError as e:
                    bad.append((binding, str(e))); continue
                ref = ct.c_void_p()
                st = self.cb.RegisterEventHotKey(key, mods, self.HotKeyID(self.fourcc("SpPd"), i), target, 0, ct.byref(ref))
                if st != 0:
                    bad.append((binding, "ya la usa otra app" if st == -9878 else f"error {st}")); continue
                self.refs.append(ref); self.actions[i] = fn
            return bad
        return self._main(do)

    def stop(self):
        def do():
            for r in self.refs:
                self.cb.UnregisterEventHotKey(r)
            self.refs, self.actions = [], {}
        try:
            self._main(do)
        except Exception:                        # noqa: BLE001
            pass


def label(binding: str) -> str:
    """'<ctrl>+<alt>+<cmd>+r' → '⌃⌥⌘R' (Mac) o 'Ctrl+Alt+Shift+R' (Windows)."""
    sym = _SYM_MAC if sys.platform == "darwin" else _SYM_WIN
    out = ""
    for part in binding.split("+"):
        part = part.strip()
        out += sym.get(part.lower(), part.upper().strip("<>"))
    return out


def load(path) -> dict:
    """Lee hotkeys.json (lo crea con los de fábrica si no existe)."""
    try:
        user = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        path.write_text(json.dumps(DEFAULTS, indent=2, ensure_ascii=False), "utf-8")
        return json.loads(json.dumps(DEFAULTS))
    except Exception as e:                       # JSON roto: usar los de fábrica y avisar
        log.warning("hotkeys.json inválido (%s): uso los de fábrica", e)
        return json.loads(json.dumps(DEFAULTS))
    ub = user.get("bindings", {})
    if (ub.get("next"), ub.get("prev")) == OLD_ARROWS:   # los de fábrica viejos: pasar a letras
        ub["next"], ub["prev"] = DEFAULTS["bindings"]["next"], DEFAULTS["bindings"]["prev"]
        try:
            path.write_text(json.dumps(user, indent=2, ensure_ascii=False), "utf-8")
            log.info("Atajos: Siguiente/Anterior pasan de flechas a %s / %s", label(ub["next"]), label(ub["prev"]))
        except Exception:                        # noqa: BLE001
            pass
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg.update({k: v for k, v in user.items() if k != "bindings"})
    cfg["bindings"].update({k: v for k, v in user.get("bindings", {}).items() if k in ACTIONS and v})
    return cfg


def notify(title: str, msg: str, icon=None):
    """Notificación del sistema (Mac: Centro de notificaciones; Windows: la del ícono)."""
    try:
        if sys.platform == "darwin":
            esc = lambda t: str(t).replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'display notification "{esc(msg)}" with title "{esc(title)}"'])
        elif icon is not None:
            icon.notify(msg, title)
    except Exception:                            # noqa: BLE001
        pass


def ask_choice(prompt: str, options, timeout=120):
    """Mac: lista para elegir encima de Pro Tools y después vuelve a Pro Tools. Devuelve lo elegido o None.
    No usa «System Events»: no pide ningún permiso."""
    if sys.platform != "darwin" or not options:
        return None
    esc = lambda t: str(t).replace("\\", "\\\\").replace('"', '\\"')
    items = ", ".join(f'"{esc(o)}"' for o in options)
    script = f'''
activate
set r to choose from list {{{items}}} with title "SpotPad" with prompt "{esc(prompt)}" default items {{"{esc(options[0])}"}}
if r is false then return ""
return item 1 of r
'''
    prev = None
    try:
        from AppKit import NSWorkspace
        prev = NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:                            # noqa: BLE001
        pass
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        pick = out.stdout.strip()
        if out.returncode != 0:
            log.info("Elegir: osascript %s", out.stderr.strip()[:200])
        return pick if pick in options else None
    except Exception as e:                       # noqa: BLE001
        log.info("Elegir: %s", e)
        return None
    finally:
        if prev is not None:                     # volver a Pro Tools
            try:
                prev.activateWithOptions_(1 << 1)    # NSApplicationActivateIgnoringOtherApps
            except Exception:                    # noqa: BLE001
                pass


NEED_LABEL = {"surface": "Superficie", "shoe": "Calzado"}


def mac_trusted(prompt: bool = False) -> bool:
    """¿SpotPad tiene permiso de Accesibilidad? (con prompt=True macOS muestra el aviso)."""
    if sys.platform != "darwin":
        return True
    try:
        import HIServices
        if prompt:
            return bool(HIServices.AXIsProcessTrustedWithOptions({HIServices.kAXTrustedCheckOptionPrompt: True}))
        return bool(HIServices.AXIsProcessTrusted())
    except Exception:                            # noqa: BLE001
        return True


def open_accessibility_settings():
    if sys.platform == "darwin":
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])


class Hotkeys:
    """Escucha los atajos y llama a las acciones del modo grabación."""

    def __init__(self, rec, path, icon_ref=lambda: None, edit=None, edit_on=lambda: False):
        self.rec, self.path, self.icon_ref = rec, path, icon_ref
        self.edit, self.edit_on = edit, edit_on
        self.listener = None
        self.cfg = load(path)
        self.error = ""
        self.busy = threading.Lock()

    def actions(self):
        return {
            "from_selection": self.rec.from_selection,
            "next": lambda: self.rec.step(1),
            "prev": lambda: self.rec.step(-1),
            "done_next": self.rec.done_next,
            "consolidate": lambda: self.edit().consolidate(),
            "regrab": lambda: self.edit().regrab(),
        }

    def active_bindings(self):
        """Los atajos a registrar: los de edición solo si están prendidas (si no, la tecla queda libre)."""
        on = bool(self.edit and self.edit_on())
        return {a: b for a, b in self.cfg["bindings"].items() if a in ACTIONS and (on or a not in EDIT_ACTIONS)}

    def run_action(self, name):
        """Corre la acción en otro hilo (el de teclado no se puede trabar) y avisa si falla."""
        def work():
            if not self.busy.acquire(blocking=False):
                return                          # ya hay una acción en curso: ignorar repetición
            try:
                r = self.actions()[name]()
                r = self.resolve_needs(r)
                log.info("Atajo %s: %s", name, r.get("msg", "ok"))
                if self.cfg.get("notify_success"):
                    notify("SpotPad", r.get("msg", "Listo"), self.icon_ref())
            except Exception as e:              # noqa: BLE001 (SpotError y cualquier otro)
                log.info("Atajo %s: %s", name, e)
                notify("SpotPad", str(e), self.icon_ref())
            finally:
                self.busy.release()
        threading.Thread(target=work, daemon=True, name=f"hotkey-{name}").start()

    def resolve_needs(self, r):
        """Si al clip le falta la superficie o el calzado, preguntarlo ahí mismo (Mac) y renombrar."""
        if not r or not r.get("needs") or not r.get("options") or not r.get("item"):
            return r
        it, picks = r["item"], {}
        for need in r["needs"]:
            opts = r["options"].get(need) or []
            pick = ask_choice(f"{NEED_LABEL.get(need, need)} para «{it['name']}»", opts)
            if pick is None:
                if sys.platform != "darwin":
                    notify("SpotPad", r.get("msg", "Falta elegir") + " (en la pestaña Grabar)", self.icon_ref())
                return r
            picks[need] = pick
        return self.rec.choose(it["key"], picks)

    def start(self):
        self.stop()
        self.cfg = load(self.path)
        if not self.cfg.get("enabled", True):
            self.error = "desactivados en hotkeys.json"
            return False
        if sys.platform == "darwin":
            try:
                ck = CarbonKeys()
                mapping = {b: (lambda a=a: self.run_action(a)) for a, b in self.active_bindings().items()}
                bad = ck.register(mapping)
                self.listener = ck
                for b, err in bad:
                    log.warning("Atajo %s: %s", label(b), err)
                self.error = ("no se pudieron activar: " + ", ".join(f"{label(b)} ({e})" for b, e in bad)) if bad else ""
                log.info("Atajos activos (macOS, sin permisos): %s",
                         ", ".join(f"{label(b)}={a}" for a, b in self.active_bindings().items()))
                return not bad
            except Exception as e:              # noqa: BLE001
                log.warning("Atajos macOS: %s · pruebo con el método anterior (pide Accesibilidad)", e)
                self.listener = None
        try:
            from pynput import keyboard
        except Exception as e:                  # noqa: BLE001
            self.error = f"no se pudo cargar el teclado: {e}"
            log.warning("Atajos: %s", self.error)
            return False
        mapping = {}
        for action, binding in self.active_bindings().items():
            try:
                keyboard.HotKey.parse(binding)
                mapping[binding] = (lambda a=action: self.run_action(a))
            except Exception as e:              # noqa: BLE001
                log.warning("Atajo inválido %s=%r: %s", action, binding, e)
        try:
            self.listener = keyboard.GlobalHotKeys(mapping)
            self.listener.daemon = True
            self.listener.start()
        except Exception as e:                  # noqa: BLE001
            self.error = f"no se pudieron activar: {e}"
            log.warning("Atajos: %s", self.error)
            return False
        if not mac_trusted(prompt=True):
            self.error = "falta el permiso de Accesibilidad"
            notify("SpotPad", "Para usar los atajos, dale permiso de Accesibilidad a SpotPad "
                              "(Ajustes del Sistema → Privacidad y seguridad → Accesibilidad) y reabrí la app.",
                   self.icon_ref())
            return False
        self.error = ""
        log.info("Atajos activos: %s", ", ".join(f"{label(b)}={a}" for a, b in self.cfg["bindings"].items()))
        return True

    def stop(self):
        if self.listener is not None:
            try:
                self.listener.stop()
            except Exception:                   # noqa: BLE001
                pass
            self.listener = None

    def summary(self):
        return [(ACTIONS[a], label(b)) for a, b in self.active_bindings().items()]
