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

## Si algo falla
- **Punto rojo / «Pro Tools no responde»:** tocá **↻ Reconectar** (aparece arriba en el iPad, o en el menú del ícono: *Reconectar con Pro Tools*). Si Pro Tools deja un pedido sin contestar, SpotPad además reconecta solo a los 15 s.
- Antes de reconectar, fijate que Pro Tools no tenga una ventana o aviso abierto esperando respuesta: mientras lo tiene, no le contesta al SDK.
- **Estado:** tocá el punto o el nombre de la sesión arriba en el iPad → versión, conexión, último error, qué comando está esperando respuesta y los últimos comandos.
- **Informe para soporte:** desde el panel de Estado o desde el ícono (*Generar informe para soporte*). Junta el estado, el diagnóstico de la sesión y el final del registro en un solo texto. En la Mac queda copiado en el portapapeles y guardado en la carpeta de datos: pegalo en la conversación con Claude.

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
- **Ocultar carpetas o tracks:** en **✎ Editar**, «Ocultar carpeta» oculta la pestaña entera y tocar un track lo oculta solo a él. Todo lo oculto va a la pestaña **Ocultos** (se puede seguir usando desde ahí); en modo edición, tocarlo lo vuelve a mostrar. Se guarda por nombre en `hidden.json`, así vale para todas las sesiones. Los tracks ocultos siguen disponibles como destino de las categorías.
- Las categorías de `presets.json` (p. ej. *Manos*) siguen como pestañas punteadas: crean el clip group donde esté la selección, como antes.
- Atajo por nombre de track: `curl -X POST http://localhost:8765/api/group-on-name/Wood`

### Diagnóstico (correr una vez con la sesión de spotting abierta)
```bash
python3 bridge.py --diag
```
Imprime la sesión, el timecode, la selección, **todos los tracks con su carpeta y color**, cómo quedaría la botonera y las primeras líneas del export de EDL. Lo guarda también en `diag.txt`. Antes de correrlo, marcá una región sobre algún clip group y seleccioná ese track, así el export muestra algo.

## Categorías de genéricos (botones preseteados)
Pestañas con borde punteado (p. ej. *Manos*). Cada botón crea un clip group con su nombre **en su track destino**: *Hands clap* → track **Hands Body**, *Hands surface wood* → **Hands Surfaces**. Si un botón no tiene track, lo crea en el track que esté seleccionado. Abajo de cada botón se ve a dónde va (en rojo si ese track no existe en la sesión abierta).

**✎ Editar** (arriba de la botonera) pasa a modo edición:
- Tocar un botón → cambiar nombre, track destino o borrarlo.
- **+ Botón** agrega uno a la categoría; **✎ Categoría** cambia nombre, color y el track por defecto de sus botones.
- **+ Categoría** (al final de las pestañas) crea una nueva.
- **✓ Listo** vuelve al modo normal. Todo se guarda en `presets.json` (carpeta de datos).

Los tracks destino se guardan por **nombre**, así sirven para cualquier sesión que tenga un track con ese nombre.

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

## Modo grabación (pestaña «Grabar»)
Para el grabador: barre el spotting clip por clip y deja el track de grabación nombrado solo.

1. **Leer sesión** y elegir **qué barrer**: uno o más tracks de las carpetas de pasos, props o superficies (p. ej. *SNEAKERS 01* + *KYLE*, o *Chairs*). Opcional: **solo pasos sobre la superficie** X.
2. Elegir el **track de grabación** (el que recibe el input del micrófono). Con «Renombrar el track al ir» activado, cada vez que vas a un clip ese track toma el nombre sugerido.
3. **Siguiente ▶ / ◀ Anterior** recorren los pendientes en orden de tiempo: posicionan Pro Tools en el clip y renombran. **⟳ Ir** repite. Tocar un clip de la lista va directo.
4. **Pendiente** = no muteado en Pro Tools y no marcado. Podés mutear el clip de spotting como siempre (Cmd+M; se ve al tocar «Leer sesión») o tocar **✓ Grabado**, que lo marca en SpotPad y pasa al siguiente.

**Nombres sugeridos** (tocá el nombre para editarlo; la edición queda guardada para ese clip):
- Props: `Prps` + nombre del clip group → *Prps Chair sit*.
- Pasos: `Fts` + personaje + calzado + superficie → *Fts Kyle Sneakers Hardwood*.
  - Track de **personaje** (KYLE, JUNE): el calzado sale del nombre del clip si lo trae (*Kyle Barefoot*), si no del fijado para ese personaje, si no del **color del track** (el mismo color que el track de calzado: celeste = SNEAKERS). *EMILY HEELS* = Emily con Heels.
  - Track de **calzado** (SNEAKERS 01, Varios FEM Shoes 04): el personaje es el nombre del clip group (*Extra Left*, *Sonia*); si el clip se llama como el track, va sin personaje.
  - **Superficie**: el clip de la carpeta de superficies que está debajo del paso. Si hay dos (**choque**), aparecen dos botones grandes para elegir; queda guardado.
  - Si no se puede deducir el calzado, lo pregunta una vez y lo recuerda para ese personaje.
- **⚙ Reglas de pasos**: cómo se interpretó cada track, calzado fijo por personaje y nombre corto de cada superficie (*Gritty Concrete Street* → *Gritty*). Se guardan en `footsteps.json`.

### Atajos de teclado (desde Pro Tools, sin tocar el iPad)
Funcionan con Pro Tools en primer plano:

| Mac | Windows | Acción |
|---|---|---|
| **⌃⌥⌘R** | Ctrl+Alt+Shift+R | **Renombrar el track de grabación con el clip seleccionado**: clic en un clip de spotting y atajo |
| **⌃⌥⌘→** | Ctrl+Alt+Shift+→ | Siguiente pendiente (posiciona y renombra) |
| **⌃⌥⌘←** | Ctrl+Alt+Shift+← | Anterior pendiente |
| **⌃⌥⌘G** | Ctrl+Alt+Shift+G | Marcar grabado y pasar al siguiente |

- Usan la misma configuración que la pestaña Grabar (track de grabación, qué barrer, reglas). El iPad sigue solo lo que hacés con las teclas.
- Si falta elegir superficie o calzado, aparece una notificación y lo resolvés en el iPad.
- **Mac:** la primera vez macOS pide permiso de **Accesibilidad** para SpotPad (Ajustes del Sistema → Privacidad y seguridad → Accesibilidad). Como la app no está firmada, después de actualizarla puede pedirlo de nuevo: sacá SpotPad de la lista y volvé a agregarlo.
- Para cambiar las teclas: ícono → *Atajos de teclado → Cambiar teclas (hotkeys.json)*, y después *Reactivar atajos*.
- Las mismas acciones para Stream Deck / Keyboard Maestro: `POST /api/rec/from-selection`, `/api/rec/next`, `/api/rec/prev`, `/api/rec/done-next`.

Las carpetas se reconocen por nombre: superficies (*SUPERFICIES/SURFACES*), pasos (*FTS/FOOTSTEPS*), props (*PRPS/PROPS*).

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
