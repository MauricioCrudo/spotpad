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
}

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
    """Mac: lista para elegir encima de Pro Tools y vuelve a Pro Tools. Devuelve lo elegido o None.
    (La primera vez macOS pide permiso para que SpotPad use «System Events».)"""
    if sys.platform != "darwin" or not options:
        return None
    esc = lambda t: str(t).replace("\\", "\\\\").replace('"', '\\"')
    items = ", ".join(f'"{esc(o)}"' for o in options)
    script = f'''
tell application "System Events"
    set prev to name of first application process whose frontmost is true
    activate
    set r to choose from list {{{items}}} with title "SpotPad" with prompt "{esc(prompt)}" default items {{"{esc(options[0])}"}}
end tell
try
    tell application prev to activate
end try
if r is false then return ""
return item 1 of r
'''
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        pick = out.stdout.strip()
        if out.returncode != 0:
            log.info("Elegir: osascript %s", out.stderr.strip()[:200])
        return pick if pick in options else None
    except Exception as e:                       # noqa: BLE001
        log.info("Elegir: %s", e)
        return None


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

    def __init__(self, rec, path, icon_ref=lambda: None):
        self.rec, self.path, self.icon_ref = rec, path, icon_ref
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
        }

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
        try:
            from pynput import keyboard
        except Exception as e:                  # noqa: BLE001
            self.error = f"no se pudo cargar el teclado: {e}"
            log.warning("Atajos: %s", self.error)
            return False
        mapping = {}
        for action, binding in self.cfg["bindings"].items():
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
        return [(ACTIONS[a], label(b)) for a, b in self.cfg["bindings"].items() if a in ACTIONS]
