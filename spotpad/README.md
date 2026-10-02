# SpotPad

Botonera en iPad/celu para spottear foley en Pro Tools vía el **Pro Tools Scripting SDK (PTSL)**.

1. **Crear clip groups:** marcás la región en Pro Tools, tocás un botón (p. ej. *Hands clap*) → se crea el clip group y queda nombrado.
2. **Renombrar track con el nombre del clip group:** clic sobre un clip group, tocás uno de tus tracks destino → el track toma ese nombre.

## Instalar la app (lo más fácil)
Bajá el zip de tu computadora desde la release **«Última versión»** del repo:
- **SpotPad-Mac-AppleSilicon.zip** (M1, M2, M3…) o **SpotPad-Mac-Intel.zip**: descomprimí y arrastrá **SpotPad.app** a Aplicaciones.
  La primera vez macOS la bloquea porque no está firmada por Apple: andá a *Ajustes del Sistema → Privacidad y seguridad* y tocá **«Abrir igualmente»** (o en Terminal: `xattr -dr com.apple.quarantine /Applications/SpotPad.app`).
- **SpotPad-Windows.zip**: descomprimí **SpotPad.exe** donde quieras (es portable). Si aparece SmartScreen: *Más información → Ejecutar de todas formas*. Cuando el firewall pregunte, permití **redes privadas**.

Al abrirla aparece un ícono en la barra de menú (Mac) o junto al reloj (Windows), y la primera vez se abre una página con el **QR para conectar el iPad**. Desde el ícono: dirección del iPad, QR, diagnóstico de la sesión, carpeta de configuración, registro y salir.
La configuración queda en `~/Library/Application Support/SpotPad` (Mac) o `%APPDATA%\SpotPad` (Windows), así que actualizar la app no borra nada.

Cada cambio que se sube al repo se compila solo (GitHub Actions) y reemplaza la «Última versión».

## Correr desde el código (desarrollo)

## Requisitos
- Pro Tools **2024.6 o posterior** (es la versión donde Avid agregó `GroupClips` al SDK). Fijate en *Pro Tools → About*.
- Python 3.10+ en la Mac.
- iPad y Mac en la misma red.

## Instalación
```bash
cd spotpad
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-app.txt   # solo para app.py (ícono de bandeja)
```

## Uso
```bash
python3 bridge.py          # con Pro Tools abierto y una sesión cargada
python3 bridge.py --mock   # para ver la interfaz sin Pro Tools
python3 bridge.py --debug  # loguea cada comando PTSL (útil si algo falla)
```
Al arrancar muestra la dirección (p. ej. `http://192.168.0.20:8765`). Abrila en Safari del iPad → Compartir → **Agregar a inicio** para usarla a pantalla completa.
La primera vez macOS va a preguntar si permite conexiones entrantes a Python: aceptá.

## Botonera armada desde la sesión
Las pestañas salen de las **carpetas** de la sesión: una por carpeta de primer nivel (Surfaces, Footsteps, Props…) y, adentro, un botón por track, en el mismo orden y con el color del track. Las subcarpetas aparecen como grupos dentro de la pestaña. Los tracks que no están en ninguna carpeta (video, diálogos, pre, grabación) no aparecen.

- Marcás el rango en **cualquier** track (video, ref de diálogos) y tocás «Wood» → el clip group se crea en el track Wood con ese rango.
- El clip group se llama como el track. Si escribís algo en «Nombre del clip» (p. ej. *Sonia* o *Extra Left* en un track de calzado), usa ese nombre. El campo se limpia después de cada uso; los nombres usados quedan como fichas para repetirlos con un toque.
- Sumar o borrar tracks/carpetas en Pro Tools cambia la botonera sola (o tocá «↻ Tracks»). No hay plantilla fija.
- Para ocultar alguna carpeta: en `presets.json`, `"exclude_folders": ["Nombre"]`.
- Las categorías de `presets.json` (p. ej. *Manos*) siguen como pestañas punteadas: crean el clip group donde esté la selección, como antes.
- Atajo por nombre de track: `curl -X POST http://localhost:8765/api/group-on-name/Wood`

### Diagnóstico (correr una vez con la sesión de spotting abierta)
```bash
python3 bridge.py --diag
```
Imprime la sesión, el timecode, la selección, **todos los tracks con su carpeta y color**, cómo quedaría la botonera y las primeras líneas del export de EDL. Lo guarda también en `diag.txt`. Antes de correrlo, marcá una región sobre algún clip group y seleccioná ese track, así el export muestra algo.

## Tracks destino
"Elegir tracks" → marcás los tracks que vas a renombrar. Se guardan por **ID de track** (en `targets.json`), así siguen funcionando después de cambiarles el nombre.

## Prefijo Prps / Fts
Arriba de los tracks destino elegís **Prps** o **Fts** según lo que estés grabando. El track queda como `Prps Hands clap`. El prefijo elegido se recuerda (en `state.json`) y lo usan también los atajos; para forzarlo en un atajo: `?prefix=Fts`. Los prefijos disponibles se editan en `presets.json` → `track_prefixes`.

## Lista de spotting (en vivo)
Arriba a la derecha, **Lista** muestra todos los clips de los tracks de spotting con TC de entrada y salida, leídos de la sesión abierta (no del .ptx: no hace falta guardar).
- Se actualiza sola cada 8 s mientras está a la vista. Desde la botonera se revisa cada 30 s, y el número rojo de la pestaña avisa si hay clips nuevos.
- **No lee mientras Pro Tools graba o reproduce**, para no meterle carga al transporte: retoma cuando parás.
- Los clips que aparecieron desde la última vez quedan marcados **NUEVO**. «Marcar vistos» los limpia (se recuerda por sesión, en el iPad).
- Tocar un clip lleva la selección/cursor de Pro Tools a ese clip.
- «Tracks de spotting» elige qué tracks se leen. **Solo esos**: los clips grabados en otros tracks no aparecen ni cuentan como nuevos. Si no elegiste ninguno, la lista queda vacía (nunca toma toda la sesión). Se guardan por ID de track en `spot_tracks.json`, así que renombrar un track no los desarma.
- El TC se calcula con el inicio de sesión, el frame rate (incluye 23.976 y drop frame) y la frecuencia de muestreo que da el SDK.

## Atajos de teclado / Stream Deck
Cualquier app que dispare un comando (Keyboard Maestro, BetterTouchTool, Stream Deck) puede llamar al bridge:
```bash
curl -X POST http://localhost:8765/api/group/hands/3     # 3er botón de Manos (Hands clap)
curl -X POST http://localhost:8765/api/rename-slot/1     # renombra el track destino 1 (prefijo activo)
curl -X POST "http://localhost:8765/api/rename-slot/1?prefix=Fts"
```

## Editar los nombres
Todo está en `presets.json`. Para sumar categorías agregá otro bloque con `id`, `label`, `color` e `items`; aparecen como pestañas arriba de la botonera.

## Cómo lee el nombre del clip seleccionado
El SDK de 2024 no tiene un comando "nombre del clip seleccionado", así que el bridge:
1. Pide la lista de tracks y busca el que tiene la selección de edición.
2. Lee el in/out de la selección en samples.
3. Exporta el *Session Info as Text* con los EDL de los tracks seleccionados (como texto, sin escribir archivos) y busca el clip que cae en esa selección.

Si el track del clip no está seleccionado, repite el export con toda la sesión (más lento en sesiones grandes).
