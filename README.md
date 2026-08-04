# clipper

Detecta el mejor momento de un directo mientras sigue emitiendo, lo corta en
vertical 9:16 con subtítulos y gancho quemados, y lo deja listo para subir a
TikTok, Reels y Shorts.

Captura con `streamlink` y `ffmpeg`; Workers AI transcribe con timestamps por
palabra y `faster-whisper` queda como respaldo local. Luna analiza los
fotogramas mediante Codex OAuth y toma la decisión editorial.

## Cómo funciona

```
EventSub / sondeo → captura a buffer rodante (segmentos de 10 s, sin recodificar)
        ↓
detector: velocidad y contenido del chat + energía de audio
        ↓
pico → ventana → Cloudflare Whisper (respaldo local, timestamps por palabra)
        ↓
out/RAW (MP4 limpio + manifiesto) → storyboard temporal (1 imagen/s + pico)
        ↓
Luna multimodal + filtro de calidad → ffmpeg (9:16 + subtítulos + gancho)
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

**Puerta editorial antes de publicar.** Luna debe devolver `publicar`, al menos
80/100, confianza 0,75 y hook, descripción y 4-6 hashtags válidos. Además pasan
los controles locales de duración, diálogo, audio, pantalla en negro y gancho.
Todo lo demás va a `out/REVISAR/` con el motivo escrito; nunca se borra solo.

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
serie y el modelo se libera al terminar cada transcripción para no acumular una
copia por canal. La evaluación de Luna también es única por candidato y genera
hook, descripción y hashtags; los nombres nuevos incluyen fecha para no
colisionar.
El buffer se poda tambien mientras Whisper o ffmpeg estan trabajando y los
datos se conservan siete dias.

Kick usa el chat real cuando `aiohttp` esta instalado. La galeria web exige
`CLIPPER_WEB_CLAVE`, refresca los clips automaticamente y es la interfaz unica
de revision.

## Análisis visual de RAW

Después de Whisper cada candidato queda en `out/RAW/` con su MP4 limpio y un
manifiesto privado. El supervisor extrae temporalmente un JPEG 768x432 por
segundo y cinco imágenes adicionales alrededor del pico. Un `codex exec`
autenticado con ChatGPT recibe esas imágenes ordenadas, la transcripción, el chat y el canal, y
devuelve el análisis visual junto con la decisión, el hook, la descripción y
los hashtags. Los JPEG se eliminan siempre al terminar.

Los fallos de Luna o render se reintentan a 1, 5 y 15 minutos y después cada
hora. No se registran prompts, transcripciones, chat, tokens ni análisis
completos ni imágenes en los logs.

## Sincronizacion automatica para Windows

El paquete [windows-sync](windows-sync/) descarga solo `LISTOS` a una carpeta
local cada 10 minutos, sin instalar Python ni dependencias externas.

1. Copia o descomprime `windows-sync/` en el equipo Windows.
2. Ejecuta `Instalar.bat`.
3. Introduce la URL HTTPS de Clipper, tu usuario y contrasena, y elige la
   carpeta de destino.

La contrasena se guarda cifrada con DPAPI para el usuario actual de Windows.
La tarea se ejecuta solo con ese usuario, no solapa ejecuciones y deja las
parejas `clip.mp4` + `clip.txt` directamente en la carpeta elegida. Exige ambos
archivos, compara el tamaño, usa temporales `.part` y renombrado atómico, y
nunca borra clips locales. Los pendientes de `REVISAR` no se sincronizan.

Para comprobar el paquete sin conectarte al servidor, abre Windows PowerShell
5.1 en la raiz del proyecto y ejecuta:

```powershell
.\windows-sync\Sincronizar-Clips.ps1 -SelfTest
```

Para actualizarlo, vuelve a ejecutar `Instalar.bat`. Para quitar la tarea y la
configuracion sin tocar los clips descargados, ejecuta `Desinstalar.bat`.

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
