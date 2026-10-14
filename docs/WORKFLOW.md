# Workflow de foley y cómo encaja SpotPad

Contexto para entender **para qué** existe cada función. Antes de proponer o cambiar algo, pensar en
qué momento del trabajo real se usa y quién lo usa.

## Las personas

- **Supervisor / spotter** (Mauricio): arma la sesión de spotting en Pro Tools, mirando la imagen y
  escuchando la referencia de diálogos para detectar sonidos que haya que empatar.
- **Grabador**: maneja Pro Tools durante la grabación. Tiene una mano en la compu (teclado/mouse de
  Pro Tools) y como mucho la otra libre para el iPad o el celu. **No puede ponerse a tipear.**
- **Artista de foley**: actúa y realiza los sonidos en la sala. Mira la vista del artista en el celu
  para saber qué viene.

## Material que llega

La película o el capítulo en QuickTime con referencia de audio, y por separado tres stems: música,
efectos y diálogos. Pro Tools trabaja con el contador en **timecode**.

## 1. Spotting

- **Markers** solo para cambios de escena.
- Los clips de spotting son **marca visual, sin audio**: clip groups vacíos con nombre.
- Siempre hay tres carpetas de tracks (los nombres varían por proyecto, a veces anidadas):
  **superficies**, **footsteps** y **props**.

**Superficies**: se marca la superficie durante toda la escena que transcurre en una misma locación.
Habituales: clean concrete, gritty concrete, hardwood, loose wood, grass, gravel, special surfaces.
Carpet y water se suman en simultáneo a otra para dar textura. A veces chocan dos superficies a la vez
(dos personajes en pisos distintos) y solo se sabe mirando cuál pisa cada uno.

**Footsteps**: después de las superficies se marcan solo las regiones de pasos, por personaje y calzado.
Todo paso tiene que tener su superficie marcada.
- Un track por personaje principal, y tracks por calzado para secundarios y extras: male shoes, boots,
  femme shoes, sneakers, slippers, varios, barefoot, group, animals.
- En los tracks de calzado el nombre del clip identifica al personaje: «Extra Left», «Sonia».
- Usa colores para identificar el calzado. El track de un principal va del color de su calzado; si en
  una escena cambia de calzado, cambia el color de ese clip y, si hace falta, el nombre («Henry Sneakers»).
  El SDK no permite setear colores de clip.

**Props**: se marcan solo las regiones donde hay acción, divididos en categorías. Principales: hands body
(manos con ropa y piel), hand surfaces (manos contra mesa, mármol, madera, concreto, metal) y movements
de ropa. Casi siempre también chairs, bags and backpacks, accesorios y bijou (incluye lentes), cell phones,
glass and bottles, keyboards and computers, papers and folders, tableware and dishes, **vocals (siempre hay)**,
y props genéricos. Los props varían mucho por capítulo y proyecto: los nombres escritos a mano se reutilizan
por proyecto (pestaña ★ Proyecto).

Algunos clips se marcan como **REF** (mayúsculas, al final del nombre). REF nunca pasa al nombre del track de grabación.

## 2. Grabación

- Hay un track **«pre»** con el input del micrófono y EQ/procesos, que va a un **track de grabación**.
  El nombre del track de grabación define el nombre del clip grabado, por eso tiene que estar bien nomenclado:
  - Props: `Prps Hands Table Wood`, `Prps Paper Pen Writing`, `Prps Hands Body Hug`, `Prps Movement`
  - Pasos: `Fts Henry Male Shoes Grass`, `Fts Extra Left Sneakers Gritty`
- Antes se renombraba a mano todo el tiempo; **eso es lo principal que resuelve SpotPad**.
- El grabador **barre** la sesión por categoría (pasos por superficie y calzado; props track por track),
  y **mutea lo ya grabado**. Por eso en SpotPad un clip muteado cuenta como grabado.
- A veces se graba una parte de una categoría y después otra (por ejemplo, hand surfaces de metal aparte).
- Se edita un poco durante la grabación para acercar las tomas a los transientes de los PFX (sonido directo de la referencia).
- Tracks ya grabados completos se marcan **inactivos** para que no molesten.

## Cómo lo cubre SpotPad

| Momento | Quién | Dónde | Función |
|---|---|---|---|
| Spotting | Supervisor | iPad, **Botonera** | Marcar región en Pro Tools → tocar un botón → crea el clip group con ese nombre (GroupClips + RenameSelectedClip). Categorías: Superficies, Pasos, Props, categorías preseteadas, ★ Proyecto. Botón REF. |
| Spotting | Supervisor | iPad, **Carpetas** | Ver y usar los tracks por carpeta de la sesión. |
| Spotting | Analizador (app aparte) | Compu | Analiza el video localmente, marca cambios de escena y superficies en la sesión; las dudas van a un track «IA Dudas» inactivo. |
| Grabación | Grabador | Atajo **⌃⌥⌘R** o iPad | Renombra el track de grabación con el clip seleccionado (`Prps`/`Fts` + nombre limpio). |
| Grabación | Grabador | **⌃⌥⌘N / ⌃⌥⌘B**, iPad **Grabar** | Siguiente / anterior pendiente: selecciona el track y el rango del clip, renombra el track de grabación y opcionalmente mueve la vista. Saltea lo muteado. **⌃⌥⌘G** marca grabado y pasa al siguiente. |
| Grabación | Grabador | iPad **Grabar → Qué barrer** | Elige qué tracks recorre; filtro por texto. |
| Grabación | Artista | Celu, **/artista** | Ve «Ahora» y «Después», solo lo pendiente de los tracks que se barren. Se actualiza cuando Pro Tools para. |
| Edición | Grabador | ⌃⌥⌘K / ⌃⌥⌘J (apagado por defecto) | Consolidar con el nombre del primer clip; regrabar varios tracks en un clip en «Regrabación». |

Nombres: Title Case por palabra, se corta lo que sigue a un «.» (`.grp.01`, `-01.L`), prefijo `Prps`/`Fts`.
Las categorías de props preseteadas van siempre con `Prps` y el nombre del ítem tal cual, sin la categoría.

## Restricciones que mandan

- **NDA**: nada del material (video, audio, nombres de proyecto) sale de las máquinas del estudio. La IA corre local.
- **Pro Tools no se puede colgar**: en plena grabación un cuelgue es carísimo. Ante la duda, menos pedidos al SDK.
- **Una mano y nada de teclado** para el grabador en el celu/iPad. Botones grandes, pocos toques.
- Equipos: Pro Tools en Mac; también hay una MacBook Air M4 y una laptop Windows con RTX 3070 (para el analizador).

## Cosas que todavía no lo convencen

- El workflow de spotting y grabación de **pasos** (superficie + personaje + calzado): afinarlo.
- El celu: hoy queda apretado y es más lento que escribir a mano. Ver pendientes en CLAUDE.md.
