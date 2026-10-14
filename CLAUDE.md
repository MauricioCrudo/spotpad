# SpotPad — guía para Claude Code

Herramienta de spotting y grabación de foley para **Pro Tools 2025.12.1** (macOS). La usa Mauricio
(supervisor de foley, Buenos Aires). Hablarle en **español rioplatense**, conciso. Toda la interfaz,
los mensajes y los comentarios del código van en español.

**Antes de proponer funciones o cambiar la interfaz, leer `docs/WORKFLOW.md`**: cómo es el trabajo de
spotting y grabación, quién usa cada pantalla y las restricciones (NDA, no colgar Pro Tools, una mano).

## Qué hay en el repo

```
spotpad/            App principal (bridge Python + web)
  bridge.py         Servidor aiohttp + control de Pro Tools por PTSL (py-ptsl). VERSION = "x.y.z" acá.
  app.py            App de escritorio: ícono en la barra de menú (pystray), atajos, actualizador. APP_VERSION sale de bridge.
  hotkeys.py        Atajos globales. Mac: Carbon RegisterEventHotKey por ctypes (no pide Accesibilidad); pynput de respaldo.
  notifier.py       Notificaciones nativas (UserNotifications) con nombre e ícono de SpotPad; cae a osascript.
  updater.py        Busca la release «ultima» de GitHub, baja el .zip de su plataforma y reemplaza la app.
  edicion.py        Consolidar / Regrabar. Aislado, apagado por defecto (Ajustes → edición).
  naming.py         Nombres: prefijo Prps/Fts, Title Case por palabra, se corta todo lo que sigue a un «.», REF.
  edl.py, tc.py     Parseo del texto de ExportSessionInfoAsText (clips, mutes, tiempos).
  ai_import.py      Recibe marcas del analizador (/api/ai/*).
  presets.json      Categorías de props por defecto (12) y superficies/pasos.
  static/index.html Interfaz del iPad/celu/compu (una sola página, sin build). Temas por [data-theme].
  static/artista.html Vista del artista (celu): lo pendiente de grabar.
  test_*.py         Tests sin Pro Tools (scripts simples: `python test_x.py`).
  spotpad.spec      PyInstaller (Mac .app / Windows .exe).
analizador/         App aparte: analiza el video (SigLIP ONNX), marca escenas y superficies en la sesión vía SpotPad.
.github/workflows/build.yml   CI: tests → compila Mac (Apple Silicon + Intel) y Windows → publica release «ultima».
.github/make_dmg.sh           Arma los .dmg de Mac.
```

## Correr en local

```bash
cd spotpad
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-app.txt
python3 bridge.py --mock          # interfaz sin Pro Tools → http://localhost:8765
python3 app.py --mock             # igual, con ícono en la barra de menú y atajos
python3 app.py                    # contra Pro Tools real (PTSL en localhost:31416)
```
`SPOTPAD_DATA=/ruta` cambia la carpeta de datos (presets, proyectos, hotkeys, log). Por defecto
`~/Library/Application Support/SpotPad` en Mac.

Tests (todos deben pasar antes de commitear; CI corre la misma lista que está en build.yml):
```bash
cd spotpad && for t in test_*.py; do python3 $t >/dev/null || echo FALLA $t; done
cd analizador && python3 test_analizador.py
```
Para cambios de interfaz: levantar `bridge.py --mock` y mirar en anchos de iPad (1180×820) y celu (390×844).

## Versiones y releases

- Subir `VERSION` en `spotpad/bridge.py` en cada cambio que se publique (semver: 0.x.y).
- Push a `main` → GitHub Actions compila y reemplaza la release **«ultima»** (prerelease) con título
  «Última versión · SpotPad X.Y.Z». El actualizador lee la versión de las notas («SpotPad X.Y.Z»).
- Assets: `SpotPad-Mac-AppleSilicon.zip/.dmg`, `SpotPad-Mac-Intel.zip/.dmg`, `SpotPad-Windows.zip`,
  y los del Analizador. **No renombrar los .zip**: el actualizador los busca por nombre exacto.
- La página web se sirve sin caché con `__SPOTPAD_VERSION__` inyectado; si cambia la versión, el iPad recarga solo.

## Reglas de PTSL (aprendidas en el Pro Tools real del usuario)

- **Nunca usar** `GetEditSelection`, `GetColorPalette` ni `GetExportMixSourceList`: cuelgan o cierran
  Pro Tools 2025.12. Hay tests que lo vigilan (test_edicion.py, test_rec.py).
- `GetTrackMainOutputAssignments` requiere 2026.04: no disponible.
- La selección se lee con `GetTimelineSelection` en samples. El estado de clips (incluido mute) sale del
  texto de `ExportSessionInfoAsText` (EDL).
- Todo pedido pasa por `ProTools._call` (hilo propio con timeout). Si algo cuelga, el log dice qué función lo pidió.
- Comandos sin wrapper en py-ptsl: `_op("CId_Nombre")(...)` arma request/response dinámicos.
- Memory locations: número libre = máximo + 1 (saltando 999, reservada para «mover la vista»), reintentar si «already used».
- Atributos de track tipo `is_inactive`, `has_edit_selection`, `is_soloed`: estado ≥ 2 = activo (`_is_set`).
- Los colores de clip no se pueden setear por el SDK.
- Mantener el tráfico de fondo bajo: eventos (SubscribeToEvents) apagados por defecto; polling espaciado.
- Si existe la skill `pro-tools-ptsl`, leerla antes de tocar código de PTSL.

## Convenciones del dominio

- Tracks de grabación: `Prps <Nombre>` / `Fts <Nombre>`; las categorías de props preseteadas siempre van con Prps
  y el nombre del ítem tal cual. «REF» (mayúsculas) se agrega al clip, **nunca** al nombre del track.
- Clips muteados en Pro Tools = grabados.
- Tracks inactivos se ignoran pero se listan en «Inactivos». La pista de dudas de la IA es «IA Dudas», inactiva.
- Carpetas se detectan aunque estén anidadas; la botonera muestra categorías, las carpetas van en su vista.

## Forma de trabajo con el usuario

- Si pide «pensemos antes» o «proponeme», proponer y esperar confirmación antes de implementar.
- Antes de compilar con categorías nuevas, armar primero la lista completa con él.
- Lo nuevo y riesgoso va aislado y apagado por defecto.
- Al terminar: versión nueva, tests, push, verificar que CI publicó la release y avisar qué probar en el Pro Tools real.

## Pendientes conocidos

- Diseño a una mano para el celu (sugeridos según el track seleccionado, cambiar de categoría deslizando,
  «Igual que antes», pasos en dos toques, sin teclado) + varias categorías como columnas en iPad/compu. Propuesto, a confirmar.
- Banco de prueba de EmbeddingGemma 2 vs SigLIP (imagen, video, audio) con un episodio spoteado a mano. Falta saber
  si el video trae sonido directo.
- Voz para elegir nombres: buscar una opción mejor que el atajo de iOS (se postergó).
- Traductor español→inglés de nombres (glosario + Opus-MT): el usuario pidió no implementarlo todavía.
- Sin probar en el Pro Tools real: consolidar/regrabar, vista del artista con mutes, notificaciones nativas, indicador de actualización.
