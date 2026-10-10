"""
Notificaciones del sistema con el nombre y el ícono de SpotPad.

- Mac: Centro de notificaciones nativo (UserNotifications), así aparece «SpotPad» con su ícono
  y no «Editor de Scripts». La primera vez macOS pregunta si SpotPad puede mandar notificaciones;
  como el identificador de la app no cambia, las actualizaciones no lo vuelven a preguntar.
  Si no se puede (se corre desde el código, o macOS lo rechaza), cae a la de AppleScript.
- Windows: la burbuja del ícono de la bandeja.
"""
import logging
import subprocess
import sys
import threading
import uuid

log = logging.getLogger("spotpad")

_center = None          # UNUserNotificationCenter, si se pudo
_allowed = None         # None = todavía no se sabe; True/False según lo que dijo macOS
_delegate = None
_lock = threading.Lock()


def _osascript(title, msg):
    esc = lambda t: str(t).replace("\\", "\\\\").replace('"', '\\"')
    subprocess.Popen(["osascript", "-e", f'display notification "{esc(msg)}" with title "{esc(title)}"'])


def _mac_center():
    """Prepara el centro de notificaciones una sola vez. None si no corresponde."""
    global _center, _allowed, _delegate
    with _lock:
        if _center is not None or _allowed is False:
            return _center
        try:
            from Foundation import NSBundle, NSObject
            if not getattr(sys, "frozen", False) or not NSBundle.mainBundle().bundleIdentifier():
                _allowed = False        # sin .app no hay identidad propia: usaría la de Python
                return None
            import UserNotifications as UN

            class _Delegate(NSObject):
                # Mostrar el aviso aunque SpotPad esté adelante
                def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, c, n, done):
                    done(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList)

            center = UN.UNUserNotificationCenter.currentNotificationCenter()
            _delegate = _Delegate.alloc().init()
            center.setDelegate_(_delegate)

            def answered(granted, err):
                global _allowed
                _allowed = bool(granted)
                if not granted:
                    log.info("Notificaciones: macOS no las permite (%s); uso las de AppleScript", err)

            center.requestAuthorizationWithOptions_completionHandler_(
                UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound, answered)
            _center = center
            return center
        except Exception as e:                   # noqa: BLE001
            log.info("Notificaciones nativas no disponibles: %s", e)
            _allowed = False
            return None


def setup():
    """Llamar al arrancar la app: pide el permiso de entrada, así el primer aviso ya sale bien."""
    if sys.platform == "darwin":
        _mac_center()


def notify(title: str, msg: str, icon=None):
    try:
        if sys.platform == "darwin":
            center = _mac_center()
            if center is not None and _allowed is not False:
                import UserNotifications as UN
                content = UN.UNMutableNotificationContent.alloc().init()
                content.setTitle_(str(title))
                content.setBody_(str(msg))
                req = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
                    str(uuid.uuid4()), content, None)

                def sent(err, title=title, msg=msg):
                    if err is not None:
                        log.info("Notificación nativa falló (%s); uso AppleScript", err)
                        _osascript(title, msg)
                center.addNotificationRequest_withCompletionHandler_(req, sent)
                return
            _osascript(title, msg)
        elif icon is not None:
            icon.notify(msg, title)
    except Exception as e:                       # noqa: BLE001
        log.info("No pude notificar: %s", e)
        try:
            if sys.platform == "darwin":
                _osascript(title, msg)
        except Exception:                        # noqa: BLE001
            pass
