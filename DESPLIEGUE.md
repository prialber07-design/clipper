# Poner el clipper en un servidor

El código ya es multiplataforma: `servidor.py` sustituye a `vigilar.ps1` y
funciona igual en Windows y en Linux. Levanta un vigilante por canal y relanza
solo el que se caiga, con espera creciente para no machacar la plataforma.

```bash
python servidor.py                      # todos los canales verificados
python servidor.py --canales elcalvolol,lopezfnx
python servidor.py --estado
```

## Estado actual del proyecto — 2 de agosto de 2026, 22:10 CEST

Este apartado es la referencia operativa vigente. Los documentos de
`docs/plans/` conservan las decisiones de diseño, pero no sustituyen este
estado real.

### Qué está implementado

- Captura continua de Twitch/Kick, buffer rodante y detección por reacción del
  chat más energía de audio.
- Una sola cola de Whisper para todos los canales. En el servidor se usa
  `small` con `int8` porque no hay GPU disponible para el contenedor.
- `CLIPPER_RAW_MODO=manual`: cada candidato se recorta sin subtítulos, hook,
  marca de agua ni recodificación y se detiene como pareja `.mp4` + `.json` en
  `/app/clips/out/RAW/`.
- Dashboard autenticado con pestañas `RAW`, `LISTOS` y `REVISAR`, vista previa,
  logs y acciones **Analizar con Gemini** o **Procesar con Luna**.
- Luna hace una sola evaluación editorial y devuelve decisión, puntuación,
  confianza, hook, descripción y 4–6 hashtags. Solo `publicar`, score mínimo
  80, confianza mínima 0,75 y controles técnicos válidos llegan a `LISTOS`.
- Render vertical 1080×1920 con subtítulos y hook permanente: TikTok Sans Bold,
  texto negro sobre caja blanca opaca, centrado aproximadamente al 18% de la
  altura y con cero, uno o dos emojis opcionales al final.
- Cada salida genera `.mp4` y `.txt`; el TXT contiene únicamente la descripción,
  una línea en blanco y los hashtags listos para copiar.
- `windows-sync/` instala una tarea de Windows cada diez minutos. Descarga solo
  parejas completas de `LISTOS` directamente a la carpeta elegida, usa DPAPI
  para la contraseña, temporales `.part`, comprobación de tamaño y nunca borra
  archivos locales.
- Logs RAW estructurados y persistentes en
  `/app/clips/logs/raw-processing.jsonl`; no incluyen prompts, transcripciones,
  chat, secretos ni respuestas completas.

### Estado real del servidor

- EasyPanel mantiene el volumen persistente en
  `/etc/easypanel/projects/automatizaciones/clips-alberto/volumes/clips/` y el
  contenedor lo ve como `/app/clips/`.
- El CLI del host es `agy 1.1.9`, autenticado con OAuth de Google AI Pro.
- Se eliminó la aplicación gráfica Antigravity 2.0 junto con XFCE, XRDP y
  Chrome; no son necesarios para el flujo actual.
- Se validó que `agy` puede abrir un MP4 real directamente con `view_file`. La
  prueba sintética identificó correctamente el contenido de ambas mitades en
  aproximadamente un segundo.
- Tras limpiar el histórico se conservaron cuatro clips con análisis de
  identidad v2. Como los vigilantes siguen activos, a las 22:10 había siete
  RAW: cuatro analizados y tres candidatos nuevos pendientes. No había errores
  en `_gemini/errors/`.
- En ese momento no había ningún proceso `agy` vivo: la tarea creada con
  `Schedule` dentro de Antigravity es local a esa sesión y desaparece al cerrar
  o reiniciar el CLI. Por tanto, **el análisis temporal no está ejecutándose
  automáticamente ahora mismo**.

### Dos rutas Gemini distintas

1. **Integración de Clipper**: `POST /api/raw/process` con `mode=gemini` encola
   `MP4 → Antigravity → Luna → render → LISTOS/REVISAR`. El código valida cuota,
   OAuth, JSON, timeout de 120 s y errores, pero esta ruta no está operativa en
   producción porque el `agy` autenticado vive en el host y no dentro del
   contenedor.
2. **Validación temporal del host**: una tarea interactiva de `agy` lee como
   máximo tres MP4 por ejecución desde el volumen RAW y escribe
   `_gemini/<id>.json`. El esquema v2 exige descripción temporal, personas,
   identidad solo con evidencia contextual y al menos dos URLs independientes,
   rol, texto visible, lugar, momento clave, hechos editoriales y advertencias.

Los JSON de `_gemini/` son actualmente resultados de validación: `raw.py` no
los busca ni los entrega a Luna. No debe afirmarse que Gemini está enriqueciendo
automáticamente los renders hasta conectar explícitamente ese directorio con el
procesador RAW o hacer operativa la integración dentro del contenedor.

### Próximo paso bloqueante

Hacer persistente una sola ejecución de `agy` sin depender de `Schedule` dentro
de una sesión interactiva y conectar cada JSON v2 validado con la llamada única
a Luna. Hasta verificarlo con logs reales, `CLIPPER_RAW_MODO` debe seguir en
`manual` y `gemini_auto` no debe activarse.

## Qué hardware hace falta de verdad

Lo que manda es la **transcripción**, no la captura.

| Recurso | Por canal | 10 canales |
|---|---|---|
| Descarga continua | 3–6 Mbps | **30–60 Mbps sostenidos** |
| Disco (buffer 15 min) | ~1 GB | ~10 GB rodando |
| RAM | ~300 MB | ~3 GB + 2–4 GB del modelo |

**Transcripción de una ventana de 90 s**:

- GPU NVIDIA con `large-v3-turbo`: ~15 s
- CPU 8 núcleos: ~90–150 s
- CPU 4 núcleos: no llega; se encolan los picos

Un clip cada ~2 min por canal, con 10 canales, son hasta 5 transcripciones por
minuto en hora punta. **En CPU eso no cabe.** Opciones reales:

1. **VPS con GPU** (~0,20–0,60 €/h según proveedor). Es la única que aguanta 10
   canales a la vez sin encolar.
2. **VPS de CPU (8 vCPU, ~25–40 €/mes)** con `modelo: "small"` o `"base"` en
   `config.json` en vez de `large-v3-turbo`. Pierdes precisión en los
   subtítulos, que es justo lo que más se nota en pantalla.
3. **Tu PC como servidor**: ya tiene la GPU. Es la opción más barata con
   diferencia; solo hay que dejarlo encendido.

Mi recomendación: **empieza por la 3**. Si el PC no puede quedarse encendido,
pasa a la 1 con 3–4 canales, no 10.

## Opción A — tu PC como servidor (sin coste)

Tarea programada que arranca al encender y sobrevive a reinicios:

```powershell
$py   = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$raiz = "C:\ruta\a\clipper"   # ajusta a donde lo tengas
$acc = New-ScheduledTaskAction -Execute $py -Argument "servidor.py" -WorkingDirectory $raiz
$dis = New-ScheduledTaskTrigger -AtStartup
$cfg = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName "clipper" -Action $acc -Trigger $dis -Settings $cfg -RunLevel Highest
```

## Opción B — VPS Linux con systemd

```bash
git clone <tu-repo> /opt/clipper && cd /opt/clipper
apt install -y ffmpeg python3-venv
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

`/etc/systemd/system/clipper.service`:

```ini
[Unit]
Description=Clipper
After=network-online.target

[Service]
WorkingDirectory=/opt/clipper
ExecStart=/opt/clipper/.venv/bin/python servidor.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now clipper && journalctl -u clipper -f
```

Con GPU añade `nvidia-cuda-toolkit` y
`pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`.

## Opción C — EasyPanel (lo más rápido si ya lo tienes)

EasyPanel construye la imagen desde el repositorio y ya trae proxy con
certificado automático, así que **no necesitas Caddy ni el docker-compose**.

1. **Create Service → App**, origen **GitHub**, tu repositorio, rama `main`.
2. **Build**: `Dockerfile` (o `Dockerfile.gpu` si el servidor tiene GPU).
3. **Environment**: pega esto ajustando valores.

   ```
   CLIPPER_NTFY_ACTIVO=1
   CLIPPER_NTFY_TOPIC=tu-topic-aleatorio
   CLIPPER_MARCA=@TuCanal
   CLIPPER_CARPETA_SINCRONIZADA=
   CLIPPER_WEB_USUARIO=clips
   CLIPPER_WEB_CLAVE=una-clave-larga
   CLIPPER_WEB_PUERTO=8080
   CLIPPER_URL_PUBLICA=https://clips.tudominio.com
   CLIPPER_MODELO=small
   CLIPPER_COMPUTE=int8
   CLIPPER_RAW_MODO=manual
   CLIPPER_ANTIGRAVITY_ACTIVO=0
   CLIPPER_AGY_BIN=agy
   ```

4. **Volumes**: volumen persistente montado en **`/app/clips`**. Sin esto pierdes
   los clips y el modelo en cada despliegue.
5. **Domains**: tu dominio, puerto **8080**, HTTPS activado.
6. **Deploy**.

La galería queda en `https://tu-dominio` y el aviso del móvil trae el enlace
directo al clip.

### Ajustes obligatorios en EasyPanel

- **`CLIPPER_MODELO=small`** salvo que el servidor tenga GPU. Con
  `large-v3-turbo` en CPU los picos se encolan y pierdes clips.
- **Menos canales**: edita `config.json` o arranca con
  `python servidor.py --canales a,b,c`. Con 3–4 va bien; con 10 en CPU, no.
- **El buffer va a disco** (en compose iba a tmpfs). Son ~1 GB por canal
  rodando; cuenta el espacio y el desgaste del SSD.

## Opción D — Docker a pelo

```bash
cp .env.ejemplo .env      # y ajusta topic, marca y modelo
docker compose up -d --build
docker compose logs -f
```

Con GPU (necesita NVIDIA Container Toolkit en el host):

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Con HTTPS propio (solo si no usas un panel que ya lo traiga):

```bash
docker compose --profile proxy up -d --build
```

### Qué hace la imagen

- **Datos fuera del código**: `CLIPPER_DATA=/app/clips`, montado como volumen. La
  imagen no guarda nada; borrarla y reconstruirla no pierde clips.
- **Buffer en tmpfs** (16 GB): el buffer rodante escribe sin parar y en disco lo
  desgasta para nada, porque es material que se descarta.
- **tini como PID 1**: cada canal lanza `streamlink` y `ffmpeg`; sin él quedan
  zombis.
- **Usuario sin privilegios** (uid 10001) sobre el volumen.
- **Healthcheck**: si no queda ningún vigilante vivo, el contenedor se marca
  como enfermo y el orquestador lo reinicia.
- **Modelo en el volumen**, no en la imagen: son 1,6 GB que se descargan una vez.

### Recoger los clips con el PC apagado

Esta es la pieza que cierra el círculo. El contenedor levanta una **galería web**
con los clips numerados, en vertical, con su gancho y botón de descarga, y el
aviso de ntfy incluye el **enlace directo**: te llega la notificación, la tocas,
se abre el clip en el móvil y lo descargas. Sin PC.

1. Apunta un dominio por DNS a la IP del servidor.
2. En `.env`: `CLIPPER_DOMINIO`, `CLIPPER_WEB_CLAVE` y
   `CLIPPER_URL_PUBLICA=https://tu-dominio`.
3. `docker compose up -d --build`

Caddy saca el certificado solo y sirve todo por HTTPS.

**La galería no arranca sin `CLIPPER_WEB_CLAVE`**, a propósito: una bandeja de
clips abierta en internet es una fuga, no una comodidad.

#### Sin dominio

Cloudflare Tunnel te da una URL con HTTPS sin abrir puertos ni tener IP fija:

```bash
cloudflared tunnel --url http://localhost:8080
```

Sirve para probar; para uso diario, un túnel con nombre y un dominio.

#### Alternativa sin web

Si prefieres que los clips aparezcan solos en el móvil, monta una carpeta del
host en vez del volumen y sincronízala con rclone a Drive/Dropbox. Es más
cómodo, pero depende de las cuotas del proveedor.

### Estabilidad del servicio

Whisper usa una sola cola entre vigilantes y libera el modelo al terminar cada
trabajo. El buffer se poda tambien mientras hay una transcripcion o un render
en curso. La limpieza automatica conserva el comportamiento intencional de
siete dias y expira tambien trabajos, logs, contadores e indices antiguos.

Luna decide cada candidato una sola vez. Solo `publicar`, score mínimo 80,
confianza mínima 0,75 y todos los controles locales llegan a `LISTOS`; los
demás van a `REVISAR` sin borrarse. La galería web exige `CLIPPER_WEB_CLAVE`;
no existe una credencial por defecto. El panel refresca clips y logs cada 15
segundos.

### Variables (`.env`)

| Variable | Para qué |
|---|---|
| `CLIPPER_NTFY_TOPIC` | Topic de avisos. Es la única credencial: cámbialo |
| `CLIPPER_MARCA` | Marca de agua quemada en el vídeo |
| `CLIPPER_MODELO` | `large-v3-turbo` con GPU, `small` en CPU |
| `CLIPPER_COMPUTE` | `float16` con GPU, `int8` en CPU |
| `CLIPPER_CARPETA_SINCRONIZADA` | Ruta de sincronización; vacía en contenedor |
| `CLIPPER_DOCKERFILE` | `Dockerfile` o `Dockerfile.gpu` |
| `CLIPPER_WEB_CLAVE` | Obligatoria; usa una clave larga y aleatoria |
| `CLIPPER_LLM_ACTIVO` | Activa la evaluación editorial estricta de Luna |
| `OPENAI_API_KEY` | Clave del servidor; nunca la subas al repositorio |
| `CLIPPER_LLM_MODELO` | Por defecto `gpt-5.6-luna` |
| `CLIPPER_RAW_MODO` | `manual` en el despliegue; `gemini_auto` solo preparado |
| `CLIPPER_ANTIGRAVITY_ACTIVO` | Enriquecimiento visual opcional; `0` por defecto |
| `CLIPPER_AGY_BIN` | Ruta del binario oficial `agy`; por defecto `agy` |

### Cola RAW y validación manual

Con `CLIPPER_RAW_MODO=manual`, cada candidato válido tras Whisper se guarda en
`/app/clips/out/RAW/` como MP4 limpio más manifiesto privado y el vigilante se
detiene. La galería añade la pestaña **RAW** con los botones **Analizar con
Gemini** y **Procesar con Luna**. El POST solo encola el trabajo y devuelve
inmediatamente; el estado, error y latencias quedan en el manifiesto y en
`/app/clips/logs/raw-processing.jsonl`.

Gemini recibe el MP4 RAW directo, Whisper y chat; Luna recibe solo texto y, en
la ruta Gemini, el análisis visual validado como contexto no confiable. Un
fallo conserva el candidato en RAW. `gemini_auto` existe como preparación, pero
no se debe activar durante esta fase.

Para consultar el despliegue por SSH:

```bash
docker logs -f <contenedor-clipper>
tail -f /ruta-del-volumen/clips/logs/raw-processing.jsonl
find /ruta-del-volumen/clips/out/RAW -maxdepth 1 -type f -printf '%f\n'
```

### Antigravity: integración prevista y prueba temporal

`antigravity.py` implementa la ruta integrada: copia un único candidato a un
workspace estable, ejecuta `agy -p` con `Gemini 3.5 Flash (Low)`, limita la
salida a 48 KB, aplica un timeout de 120 s, mata el grupo de procesos en timeout
y valida estrictamente el JSON antes de enviarlo como contexto no confiable a
Luna. También elimina secretos del entorno, serializa las ejecuciones y no usa
`--dangerously-skip-permissions`.

Antes de invocar el CLI exige poder confirmar `useG1Credits=false`; si la
configuración es desconocida o permite créditos adicionales, falla de forma
segura. OAuth, confianza del workspace y ajustes deben permanecer en el perfil
privado del usuario y nunca en el repositorio, variables copiadas al proceso o
logs.

Esta integración directa todavía no está operativa en el despliegue actual. El
OAuth funcional pertenece al `agy 1.1.9` instalado en `/home/fable5/.local/bin`
del host, mientras Clipper se ejecuta dentro de Docker. El experimento funcional
es la tarea temporal descrita al principio de este documento, que escribe JSON
v2 en `/app/clips/out/RAW/_gemini/`; esos archivos aún no son consumidos por
`raw.py`.

No reinstalar Antigravity 2.0, un escritorio, XFCE, XRDP ni navegador en el
servidor: el CLI ya demostró que puede inspeccionar MP4. No activar
`gemini_auto` hasta que exista una ejecución persistente, se conecte el resultado
v2 con Luna y se verifique el recorrido completo mediante logs.

Los canales se eligen en `config.json`, o al arrancar:
`command: python servidor.py --canales elcalvolol,lopezfnx`

## Antes de mover nada

- **`carpeta_sincronizada`** apunta a tu OneDrive local. En un servidor no
  existe: cámbiala por una ruta del servidor y sincroniza tú (rclone, un
  recurso compartido, o lo que uses), o déjala vacía y recoge los clips por
  `scp`/FTP.
- **El aviso de ntfy funciona igual** desde cualquier sitio: es una petición
  saliente, no necesita puertos abiertos.
- **Ancho de banda**: comprueba el límite de tráfico del proveedor. 10 canales
  a 5 Mbps son ~1,6 TB al mes si emitieran sin parar.
- **El modelo son ~1,6 GB** y se descarga en el primer arranque. En Docker vive
  en el volumen, no en la imagen.
