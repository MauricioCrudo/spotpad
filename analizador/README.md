# SpotPad · Analizador de superficies

Mira el capítulo, lo divide en escenas, agrupa las escenas por locación y propone la superficie de cada locación. Después le manda el resultado a SpotPad, que marca la sesión de Pro Tools:

- **un marker por escena** («Esc 12 · L3»: escena 12, locación 3);
- **la superficie** de cada escena como clip group en su track de la carpeta SUPERFICIES / SURFACES (Hardwood, Grass…), y alfombra o agua como extra cuando las ve;
- **las dudas** en el track **«IA Dudas»** de la misma carpeta («Hardwood / Loose Wood?», «Piso no visible»). SpotPad lo crea si no existe y lo deja **inactivo**, así no entra en la botonera ni en la cola de grabación. Lo encontrás en la pestaña **Inactivos** del iPad.

## Privado (NDA)

Todo corre en tu computadora. El video no se sube a ningún lado: ffmpeg lo lee, el modelo (SigLIP de Google, abierto, incluido en la app) mira los cuadros y los cuadros temporales se borran al terminar. Lo único que viaja es el resultado (timecodes y nombres de superficie), por tu red local, de la compu que analiza a la que tiene SpotPad. No usa internet ni tiene costo por uso.

## Uso

1. Abrí **SpotPad Analizador** (en Mac, la primera vez: clic derecho → Abrir).
2. Elegí el video del capítulo (el QuickTime de referencia). Si tenés la **EDL de montaje** (CMX3600), sumala: los cortes de plano salen exactos.
3. El timecode de inicio se lee del QuickTime. Si el video no lo trae, escribilo (p. ej. `01:00:00:00`).
4. **Analizar.** Podés seguir haciendo otra cosa mientras tanto.
5. En **SpotPad** escribí dónde está SpotPad: `localhost` si es esta misma compu, o la dirección que muestra SpotPad (la misma que usa el iPad, p. ej. `192.168.0.20:8765`) si es la Mac del estudio.
6. Al terminar te lista las locaciones con su superficie y, si SpotPad responde, cuántos markers, superficies y dudas va a crear y qué saltea. **Marcar en Pro Tools** lo hace (uno o dos minutos; no toques Pro Tools mientras tanto). Si Pro Tools está reproduciendo o grabando, espera a que pares.

Es seguro repetirlo: no duplica markers (±1 s) y saltea las escenas que ya tienen superficie marcada en la carpeta, o donde ya hay un clip en el track destino.

## Tiempos aproximados (capítulo de 50 min)

| Computadora | Tiempo |
|---|---|
| Laptop con RTX 3070 (Windows, DirectML) | 4–8 min |
| MacBook Air M4 / MacBook Pro M2 | 6–12 min |

Leer el video es la mitad del tiempo: un proxy liviano (H.264 720p) va más rápido que el master.

## Qué esperar

- **Escenas y locaciones:** bastante bien. Si salen escenas de más o de menos, mové el control **Escenas** (hacia «más» → más escenas).
- **Superficie:** es lo más flojo, porque muchas veces el piso no se ve. Por eso lo dudoso va a «IA Dudas» y no a la superficie.
- **Dos superficies en la misma escena** (dos personajes en pisos distintos): no lo resuelve, sigue siendo a mano.

## Ajustar superficies

`surfaces.json` tiene, para cada superficie, el nombre que se escribe en el clip («label»), los nombres de track donde va («tracks», el primero que exista en la sesión) y las reglas de decisión. Esas tres cosas se cambian sin reexportar el modelo. Las descripciones en inglés («prompts») son lo que el modelo compara con la imagen: si cambian, hay que volver a exportar el modelo (lo hace GitHub Actions al compilar).

## Desarrollo

```
pip install -r requirements.txt onnx
python test_analizador.py                 # con un modelo falso, sin bajar nada
python export_model.py                    # baja SigLIP y deja ./modelo (necesita requirements-modelo.txt)
python gui.py                             # ventana
python analyzer.py capitulo.mov --edl montaje.edl --out analisis.json
```
