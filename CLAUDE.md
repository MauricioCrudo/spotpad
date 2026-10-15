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
  proyecto.py       Lo de cada proyecto: personajes con cara (carímetro), botonera propia sobre la genérica,
                    calzado fijo por personaje, destino de un paso (personaje + calzado → track) y el archivo
                    .spotpad para exportar/importar. Sin Pro Tools: se testea solo (test_personajes.py).
  presets.json      Categorías de props por defecto (12) y superficies/pasos.
  static/index.html Interfaz del iPad/celu/compu (una sola página, sin build). Temas por [data-theme].
  static/artista.html Vista del artista (celu): lo pendiente de grabar, con la cara del personaje; pestaña Caras.
  static/carimetro.html Carímetro para imprimir (A4, /carimetro).
  test_*.py         Tests sin Pro Tools (scripts simples: `python test_x.py`).
  spotpad.spec      PyInstaller (Mac .app / Windows .exe).
analizador/         App aparte: analiza el video (SigLIP ONNX), marca escenas y superficies en la sesión vía SpotPad.
.github/workflows/build.yml   CI: tests → compila Mac (Apple Silicon + Intel) y Windows → publica release «ultima».
.github/make_dmg.sh           Arma los .dmg de Mac.
.github/crear_firma.sh        Una vez, a mano: certificado propio + secretos MAC_CERT_P12/MAC_CERT_PASS. El CI firma la
                              app de Mac con él para que macOS no vuelva a pedir Accesibilidad en cada actualización.
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
- Push a `main` → GitHub Actions compila y reemplaza la release **«ultima»** (release normal, marcada como «Latest», así se ve en el repo) con título
  «Última versión · SpotPad X.Y.Z». El actualizador lee la versión de las notas («SpotPad X.Y.Z»).
- Assets: `SpotPad-Mac-AppleSilicon.zip/.dmg`, `SpotPad-Mac-Intel.zip/.dmg`, `SpotPad-Windows.zip`,
  y los del Analizador. **No renombrar los .zip**: el actualizador los busca por nombre exacto.
- La página web se sirve sin caché con `__SPOTPAD_VERSION__` inyectado; si cambia la versión, el iPad recarga solo.

## Reglas de PTSL (aprendidas en el Pro Tools real del usuario)

- **Nunca usar** `GetEditSelection`, `GetColorPalette` ni `GetExportMixSourceList`: cuelgan o cierran
  Pro Tools 2025.12. Hay tests que lo vigilan (test_edicion.py, test_rec.py).
- **Nunca usar** `ConsolidateClip`: en la prueba real deja un tono de 1 kHz en vez del audio. Consolidar elige
  track y rango por el SDK y le manda ⌥⇧3 a Pro Tools (pide permiso de Accesibilidad); test_edicion.py lo vigila.
- **Nunca usar** `RenameTargetClip`: contesta «Can't found clip» aunque el clip esté en la Clip List. Para renombrar
  un clip: marcar su rango en su track (SelectTracksByName + SetTimelineSelection) y `RenameSelectedClip`.
- `GetClipList` necesita `pagination_request` y su JSON trae campos que el proto de py-ptsl no conoce: parseado
  por py-ptsl devuelve 0 clips; leer `response_body_json` a mano si hace falta.
- `GetTrackMainOutputAssignments` requiere 2026.04: no disponible.
- `GetMonitorOutputPath` (salida de monitoreo, para el bounce de Regrabar): **sin probar en el Pro Tools real**. Solo se
  llama cuando se toca «Leer la salida de la sesión (prueba)» en Ajustes, nunca sola. Si cuelga, sacarlo como a los otros.
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

## Proyecto vs. genérica

- La **genérica** es la configuración de la compu: `presets.json` (botonera base), `footsteps.json`, `hidden.json`,
  `hotkeys.json`, `settings.json`. Cambiarla afecta a todos los proyectos.
- El **proyecto** sale del nombre de la sesión (`default_project`, cambiable a mano) y vive en
  `<datos>/proyectos/<nombre>/proyecto.json` + `caras/`. Los nombres escritos a mano siguen en `proyectos.json`.
  - Botonera = genérica + `agregados` (botones propios en categorías genéricas) + `categorias` propias
    (id `p-…`) − `ocultas`/`ocultos`. `combinar()` arma la vista; `/api/presets` devuelve `categories`
    (lo visible), `todas` (con marcas) y `generica` (cruda). En Editar se elige qué se toca: Genérica o ★ Proyecto.
  - Personajes: solo principales llevan cara. El calzado fijo (`char_shoes`) es del proyecto y pisa al global
    de `footsteps.json`, que queda de respaldo para lo viejo (`load_rules`).
  - Pasos en dos toques (`/api/fts/group`): principal con track → su track, clip «Henry» o «Henry Sneakers» si
    cambia de calzado; sin track → el track de ese calzado con el nombre del personaje como clip.
- **Exportar/importar** (Ajustes → Llevar a otra computadora): `.spotpad` = zip. Proyecto: mezclar (lo local
  gana) o reemplazar. Genérica: reemplaza y deja la anterior en `<datos>/respaldo-<fecha>/`; los atajos no se
  traen entre Mac y Windows. Al leer se aceptan solo nombres conocidos (sin rutas raras) y tamaño acotado.
- Las fotos se achican en el navegador (640 px, JPEG) antes de subir: el bridge no depende de Pillow.

## Forma de trabajo con el usuario

- Si pide «pensemos antes» o «proponeme», proponer y esperar confirmación antes de implementar.
- Antes de compilar con categorías nuevas, armar primero la lista completa con él.
- Lo nuevo y riesgoso va aislado y apagado por defecto.
- Al terminar: versión nueva, tests, push, verificar que CI publicó la release y avisar qué probar en el Pro Tools real.

## Pendientes conocidos

- **Mejoras del CI (acordadas el 2026-10-14, hacer primero):**
  1. Que una corrida nueva **no cancele a otra mientras publica** (`cancel-in-progress` cortó la 0.16.1 a mitad
     de «publicar» y la release quedó con 8 de 10 archivos). Cancelar solo las compilaciones, nunca el paso publicar.
  2. **No recompilar el Analizador si no cambió**: reusar sus .zip/.dmg de la release anterior (hoy «publicar» borra
     y recrea la release con todo, por eso se recompila siempre). Baja una corrida de ~7 a ~3 min.
  3. Comando para **compilar SpotPad Apple Silicon en la Mac** (M4) y probar la app real antes de publicar.
     Publicar sigue siendo solo desde el CI: el actualizador necesita los 10 archivos con su nombre exacto.
- Diseño a una mano para el celu (sugeridos según el track seleccionado, cambiar de categoría deslizando,
  «Igual que antes», pasos en dos toques, sin teclado) + varias categorías como columnas en iPad/compu. Propuesto, a confirmar.
- Banco de prueba de EmbeddingGemma 2 vs SigLIP (imagen, video, audio) con un episodio spoteado a mano. Falta saber
  si el video trae sonido directo.
- Voz para elegir nombres: buscar una opción mejor que el atajo de iOS (se postergó).
- Traductor español→inglés de nombres (glosario + Opus-MT): el usuario pidió no implementarlo todavía.
- Sin probar en el Pro Tools real: leer la salida de monitoreo para Regrabar (0.16.2), personajes/caras y pasos por personaje + calzado (0.16.0), consolidar/regrabar, vista del artista con mutes, notificaciones nativas, indicador de actualización.
