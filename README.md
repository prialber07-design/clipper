# clipper

Detecta el mejor momento de un directo mientras sigue emitiendo, lo corta en
vertical 9:16 con subtítulos y gancho quemados, y lo deja listo para subir a
TikTok, Reels y Shorts.

Todo local: `streamlink` + `ffmpeg` + `faster-whisper`. Sin servicios de pago ni
claves de API.

## Cómo funciona

```
EventSub / sondeo → captura a buffer rodante (segmentos de 10 s, sin recodificar)
        ↓
detector: velocidad y contenido del chat + energía de audio
        ↓
pico → ventana → faster-whisper (timestamps por palabra)
        ↓
gancho + filtro de calidad → ffmpeg (9:16 + subtítulos + gancho)
        ↓
bandeja numerada + aviso al móvil (ntfy)
```

Del pico al archivo listo: **~30 s con GPU**.

## Qué lo diferencia

**El chat decide.** No cuenta mensajes: los puntúa. Una risa vale 3, una
sorpresa 3, y un «clipealo» vale 6 — es el público diciendo literalmente que
ese momento es clipeable. Con dos peticiones de clip en la misma ventana
dispara al instante, sin esperar a ninguna línea base.

**El gancho sale del contexto.** Se extrae la frase del streamer que comparte
vocabulario con lo que estaba diciendo el chat: casi siempre es la que provocó
la reacción. Nunca inventa texto.

**Filtro de calidad antes de publicar.** Duración, densidad de diálogo, volumen,
pantalla en negro y solidez del gancho. Lo que no pasa va a `out/REVISAR/` con
el motivo escrito, no a tu móvil.

**Dos montajes.** `reaccion` (webcam arriba, contenido abajo) y `irl` (una sola
cámara). Se elige por canal.

**Duración variable.** 1 de cada 3 clips pasa del minuto, porque TikTok solo
monetiza a partir de ahí; el resto se quedan cortos, que rinden mejor en Reels
y Shorts.

## Uso

VOD:

```bash
python clipper.py fetch "https://www.twitch.tv/videos/XXXX" --slug mi-vod
python clipper.py transcribe mi-vod
python clipper.py render mi-vod
```

En directo:

```bash
python live.py watch <canal> --plataforma twitch
python servidor.py                 # todos los canales de config.json
python servidor.py --estado
```

El supervisor mantiene una sola cola de Whisper: los trabajos se procesan en
serie y el modelo se libera al terminar cada transcripcion para no acumular una
copia por canal. Los clips con gancho automatico van a `out/REVISAR/` hasta que
se revisan manualmente; los nombres nuevos incluyen fecha para no colisionar.
El buffer se poda tambien mientras Whisper o ffmpeg estan trabajando y los
datos se conservan siete dias.

Kick usa el chat real cuando `aiohttp` esta instalado. La galeria web exige
`CLIPPER_WEB_CLAVE`, refresca los clips automaticamente y es la interfaz unica
de revision.

Docker:

```bash
cp .env.ejemplo .env
docker compose up -d --build
```

Ver [DESPLIEGUE.md](DESPLIEGUE.md) para servidor, GPU y requisitos reales.

## Requisitos

Python 3.12, `ffmpeg` con libass, y `pip install -r requirements.txt`.

GPU NVIDIA muy recomendable: 90 s de audio se transcriben en ~15 s con GPU y en
90–150 s en CPU. Con varios canales, en CPU los picos se encolan.

## Configuración

Todo en `config.json`, recargado en caliente sin reiniciar. En servidor, las
variables de entorno (`CLIPPER_*`) mandan sobre el fichero.

Los ajustes de render se pueden fijar por canal: montaje, recorte de webcam,
comportamiento de subtítulos.

## Aviso legal

Clipar el directo de alguien y monetizarlo **necesita su permiso**. Cada canal
en `config.json` lleva `permiso_clips: PENDIENTE` a propósito: verifica la
política de clips del creador antes de publicar nada con ánimo de lucro.

Ojo además con lo que suene o se vea de fondo: música con licencia,
retransmisiones deportivas o de esports generan reclamaciones automáticas
aunque el streamer te dé permiso.

## Licencia

MIT.
