# Estado del proyecto Clipper

**Este documento es el punto de entrada para cualquier agente que trabaje en
este repositorio.** Léelo entero antes de tocar nada o de responder al usuario
sobre el estado del proyecto.

Sirve para tres cosas:

1. Saber qué está hecho de verdad y qué solo está escrito.
2. Saber qué no se puede afirmar ni activar todavía.
3. Saber cuál es el siguiente paso, sin volver a investigarlo.

Al final hay un anexo con las recetas de despliegue. Ese anexo es material de
consulta, no el contenido principal.

**Última actualización del código: 3 de agosto de 2026, 02:15 CEST.**
**Última verificación del servidor: 2 de agosto de 2026, 22:10 CEST.**
Si hoy es una fecha muy posterior, trata la sección "Estado real del servidor"
como caducada y verifícala con los comandos del apartado "Cómo comprobar el
estado tú mismo" antes de afirmar nada.

---

## Reglas para agentes

Estas reglas no son sugerencias. Rompen el proyecto si se ignoran.

- **No actives `gemini_auto`.** Ya no es un valor admitido.
  `CLIPPER_RAW_MODO` debe seguir en `manual`: el avance automático ocurre solo
  cuando aparece un `_gemini/<id>.json` v2 válido.
- **No permitas saltarse Gemini.** Se retiraron los botones Gemini/Luna y
  `POST /api/raw/process`. Solo el consumidor interno de resultados v2 puede
  llamar a Luna.
- **No reinstales Antigravity 2.0, XFCE, XRDP ni un navegador en el servidor.**
  Se eliminaron a propósito. El CLI `agy` ya demostró que puede inspeccionar
  un MP4 directamente, así que un escritorio no aporta nada.
- **No monetices ningún canal.** Los diez canales de `config.json` están
  marcados `permiso_clips: PENDIENTE`. Nadie ha verificado aún la política
  oficial de clips de cada uno. Ver "Bloqueantes".
- **Secretos fuera del repositorio.** OAuth, `OPENAI_API_KEY` y la confianza
  del workspace de `agy` viven en el perfil privado del usuario. Nunca en el
  repo, ni copiados al entorno de un proceso hijo, ni en logs.
- **Antes de reescribir varios archivos, commitea o haz stash.** El usuario
  trabaja con el árbol sucio a menudo.

---

## Qué funciona hoy

Esto está implementado y verificado en el servidor.

- Captura continua de Twitch y Kick, buffer rodante y detección de momentos por
  reacción del chat más energía de audio.
- Una sola cola de Whisper compartida por todos los canales. En el servidor va
  con `small` e `int8` porque el contenedor no tiene GPU.
- `CLIPPER_RAW_MODO=manual`: cada candidato se recorta sin subtítulos, hook,
  marca de agua ni recodificación, y se deja como pareja `.mp4` + `.json` en
  `/app/clips/out/RAW/`. El vigilante se detiene ahí y espera un análisis v2.
- Dashboard web autenticado con pestañas `RAW`, `LISTOS` y `REVISAR`, vista
  previa y logs. RAW ya no ofrece acciones manuales que puedan saltarse Gemini.
- El supervisor revisa cada 15 segundos `RAW/_gemini/<id>.json`. Exige esquema
  y política de identidad 2, `raw_id` coincidente, estado `ok`, contrato visual
  estricto y dos fuentes URL para cada identidad nombrada. Solo entonces llama
  a Luna, renderiza y mueve la salida a `LISTOS` o `REVISAR`.
- Los fallos de Luna o render se reintentan automáticamente tras 1, 5 y 15
  minutos y después cada hora, sin duplicar trabajos.
- Luna hace una sola evaluación editorial por candidato y devuelve decisión,
  puntuación, confianza, hook, descripción y de 4 a 6 hashtags. Solo pasan a
  `LISTOS` los que dan `publicar` con score 80 o más, confianza 0,75 o más y
  todos los controles técnicos locales en verde. El resto va a `REVISAR` y no
  se borra.
- Render vertical 1080x1920 con subtítulos y hook permanente: TikTok Sans Bold,
  texto negro sobre caja blanca opaca, centrado en torno al 18% de la altura,
  con cero, uno o dos emojis opcionales al final.
- Cada salida genera `.mp4` y `.txt`. El TXT lleva solo la descripción, una
  línea en blanco y los hashtags listos para copiar.
- `windows-sync/` instala una tarea de Windows cada diez minutos. Descarga solo
  parejas completas desde `LISTOS` a la carpeta elegida, guarda la contraseña
  con DPAPI, usa temporales `.part`, comprueba tamaño y nunca borra archivos
  locales.
- Logs RAW estructurados y persistentes en
  `/app/clips/logs/raw-processing.jsonl`. No contienen prompts,
  transcripciones, chat, secretos ni respuestas completas.
- Limpieza automática a siete días, que expira también trabajos, logs,
  contadores e índices antiguos. El buffer se poda incluso mientras hay una
  transcripción o un render en curso. **`RAW` es la excepción**: ahí solo
  caduca lo que tiene `status: completado`. Un candidato pendiente o con error
  se conserva indefinidamente, porque es material que todavía no ha dado clip.
- Transcripción y render comparten un único cerrojo (`clipper.CPU_LOCK`), que
  respeta `cpu.una_tarea_pesada_a_la_vez` de `config.json`. Con GPU, pon esa
  clave en `false` y dejarán de ir en fila.
- La ventana de captura se concatena sin recodificar. No metas un `libx264` de
  vuelta en `montar_ventana`: los `.ts` ya vienen en H.264 y el recorte RAW
  posterior también copia.
- El adjunto de vídeo de ntfy está desactivado a propósito: con el límite de 2
  MB del ntfy anónimo nunca llegaba a enviarse. El aviso viaja con el enlace.

## Qué NO funciona todavía

- **El análisis temporal automático.** La tarea creada con `Schedule` dentro de
  Antigravity es local a esa sesión interactiva y desaparece al cerrar o
  reiniciar el CLI. A fecha de la última verificación no había ningún proceso
  `agy` vivo.
- **El nuevo consumidor aún necesita despliegue y prueba real.** El código y 52
  pruebas locales pasan, pero no se debe afirmar que el servidor está
  procesando v2 hasta desplegar y comprobar `GEMINI_V2_ACCEPTED`, `LUNA_*` y
  `RAW_COMPLETED` en el log persistente.

## Bloqueantes, por orden

1. **Permiso de clips sin verificar.** Los diez canales de `config.json` están
   en `permiso_clips: PENDIENTE`, con nota explícita de no monetizar. Esto
   bloquea el objetivo del proyecto, no solo el despliegue. Hasta resolverlo,
   todo lo demás es infraestructura sin salida comercial.
2. **`agy` no persiste.** Hace falta una sola ejecución de `agy` que sobreviva
   fuera de una sesión interactiva, sin depender de `Schedule`. Ver
   "Análisis visual dentro del contenedor": el camino está despejado salvo dos
   pasos manuales.
3. **Falta verificar el recorrido desplegado.** El cable v2 → Luna ya está en
   el código; falta desplegarlo y comprobar una salida real y sus reintentos.

## Siguiente paso

Desplegar y verificar el bloqueante 3. Después hacer persistente `agy` para que
los nuevos RAW reciban análisis sin una sesión interactiva. En todo momento
`CLIPPER_RAW_MODO` permanece en `manual`.

## Análisis visual dentro del contenedor

Probado el 3 de agosto de 2026 con Docker en local. Conclusión: `agy` puede
vivir dentro del contenedor y su sesión persiste en el volumen. Lo verificado:

- `agy 1.1.9` se instala con el script oficial y corre dentro de la imagen como
  el usuario sin privilegios (uid 10001). Es la misma versión que hay en el host.
- `HOME=/app/clips/antigravity` y `antigravity.workspace()` caen los dos dentro
  del volumen, así que lo que `agy` escriba sobrevive a un redespliegue.

Y el fallo que había, ya corregido en el código:

- El Dockerfile creaba `/app/clips/antigravity` solo en tiempo de build. Con el
  volumen con nombre de `docker-compose.yml` eso bastaba, porque Docker copia el
  contenido de la imagen la primera vez. Pero **EasyPanel monta una carpeta del
  host y un bind mount tapa lo que la imagen trajera**: comprobado, `HOME` no
  existía. Ahora `servidor.preparar_volumen()` crea esas carpetas al arrancar,
  solo si caen dentro del volumen.

### Los dos pasos que siguen siendo manuales

**1. OAuth.** Hay que autenticar `agy` una vez dentro del contenedor. Necesita
una cuenta de Google en un navegador, así que lo hace una persona. Al vivir
`HOME` en el volumen, basta hacerlo una vez.

**2. `useG1Credits`.** `antigravity.analizar()` se niega a invocar el CLI si no
puede confirmar `useG1Credits=false`, y `agy` **no escribe esa clave por su
cuenta**. Comprobado sobre un `settings.json` real: sin la clave, la función
devuelve `credits_unknown` y el análisis se omite aunque todo lo demás esté bien.
Hay que añadirla a mano en
`/app/clips/antigravity/.gemini/antigravity-cli/settings.json`:

```json
{ "useG1Credits": false }
```

**Esto no se automatiza a propósito.** La puerta existe para que una persona
confirme que el CLI no va a gastar créditos extra. Si el código la escribiera
solo, Clipper estaría afirmando algo que nadie ha verificado, que es justo lo
que la comprobación quiere evitar. Es un paso de instalación, no un bug.

---

## Cómo comprobar el estado tú mismo

No te fíes de las fechas de este documento. Comprueba.

```bash
docker logs -f <contenedor-clipper>
tail -f /etc/easypanel/projects/automatizaciones/clips-alberto/volumes/clips/logs/raw-processing.jsonl
find /etc/easypanel/projects/automatizaciones/clips-alberto/volumes/clips/out/RAW -maxdepth 1 -type f -printf '%f\n'
ls /etc/easypanel/projects/automatizaciones/clips-alberto/volumes/clips/out/RAW/_gemini/errors/
pgrep -af agy
```

Qué mirar:

- Cuántos RAW hay y cuántos tienen ya `_gemini/<id>.json`.
- Si `_gemini/errors/` está vacío.
- Si hay algún proceso `agy` vivo. Si no lo hay, el análisis temporal está
  parado, que es el estado conocido.

Estado del supervisor y arranque manual:

```bash
python servidor.py --estado
python servidor.py --canales elcalvolol,lopezfnx
```

## Mapa del repositorio

Para saber dónde tocar sin leerlo todo.

| Archivo | Qué hace |
|---|---|
| `servidor.py` | Supervisor. Un vigilante por canal, relanza el que se cae con espera creciente. Sustituye a `vigilar.ps1` y funciona en Windows y Linux |
| `live.py` | Captura en directo, buffer rodante, detección del momento y recorte |
| `raw.py` | Valida `_gemini/<id>.json` v2, encola Luna y programa reintentos |
| `calidad.py` | Filtro técnico previo: silencio, audio inaudible, pantalla en negro, hook vacío |
| `antigravity.py` | Contrato y validador estricto del análisis visual Gemini |
| `web.py` | Galería autenticada; RAW informa estado sin acciones manuales |
| `notify.py` | Bandeja `LISTOS` y aviso a ntfy |
| `kick.py` | Chat de Kick por WebSocket nativo, sin Playwright |
| `bloqueo.py` | Cerrojo entre procesos: una sola tarea pesada a la vez, transcripción o render |
| `resolver.py` | Resuelve plataforma y existencia de cada canal |
| `clipper.py` | Flujo v1 sobre VOD, de URL a clip vertical |
| `publicar_todo.py` | Pasa a bandeja todo lo que haya en `REVISAR` |
| `registro.py` | Log de consola compartido |
| `config.json` | Canales, layouts, render, umbrales de detección y de Luna |
| `docs/plans/` | Decisiones de diseño. Contexto histórico, no estado actual |

`docs/plans/` conserva por qué se decidió cada cosa, pero **no** describe lo que
está funcionando. Si hay conflicto, manda este documento.

## Cómo actualizar este documento

Cuando termines un trabajo que cambie el estado:

1. Cambia la fecha de "Última actualización verificada" solo si has comprobado
   el servidor de verdad, no si solo has cambiado código.
2. Mueve lo que hayas terminado de "Qué NO funciona todavía" a "Qué funciona
   hoy", y solo con logs que lo demuestren.
3. Reordena "Bloqueantes" y reescribe "Siguiente paso".
4. Si algo deja de ser cierto, bórralo. Un estado desactualizado hace más daño
   que un hueco.

No añadas aquí decisiones de diseño. Esas van a `docs/plans/`.

---

# Anexo: despliegue

Material de consulta. No hace falta leerlo para entender el estado del
proyecto.

## Errores conocidos en este anexo

Antes de seguir cualquiera de estas recetas al pie de la letra:

- El bloque de variables de EasyPanel no incluye `CLIPPER_LLM_ACTIVO` ni
  `OPENAI_API_KEY`. Si lo copias tal cual, Luna no se activa y nada llega a
  `LISTOS`.
- La lista de variables de EasyPanel y la tabla de `.env` no coinciden. La
  tabla es la más completa.
- Precedencia resuelta: **el entorno gana a `config.json`**. `_aplicar_entorno`
  en `clipper.py` pisa el fichero con `CLIPPER_MODELO`, `CLIPPER_COMPUTE`,
  `CLIPPER_MARCA`, `CLIPPER_NTFY_TOPIC` y las demás de esa lista, tanto al
  arrancar como en cada recarga de configuración.
- La tarea programada de la Opción A usa `AtStartup` sin `-LogonType S4U` ni
  credenciales guardadas, y pasa `0` a `-ExecutionTimeLimit`, que espera un
  `TimeSpan`. Pruébala antes de confiar en ella.
- La tabla dice que `CLIPPER_NTFY_TOPIC` es la única credencial. No lo es:
  también están `CLIPPER_WEB_CLAVE` y `OPENAI_API_KEY`.

## Arranque

```bash
python servidor.py                      # todos los canales verificados
python servidor.py --canales elcalvolol,lopezfnx
python servidor.py --estado
```

Los canales se eligen en `config.json` o al arrancar.

## Qué hardware hace falta de verdad

Lo que manda es la **transcripción**, no la captura.

| Recurso | Por canal | 10 canales |
|---|---|---|
| Descarga continua | 3-6 Mbps | **30-60 Mbps sostenidos** |
| Disco (buffer 15 min) | ~1 GB | ~10 GB rodando |
| RAM | ~300 MB | ~3 GB + 2-4 GB del modelo |

**Transcripción de una ventana de 90 s**:

- GPU NVIDIA con `large-v3-turbo`: ~15 s
- CPU 8 núcleos: ~90-150 s
- CPU 4 núcleos: no llega, se encolan los picos

Un clip cada ~2 min por canal, con 10 canales, son hasta 5 transcripciones por
minuto en hora punta. **En CPU eso no cabe.** Opciones reales:

1. **VPS con GPU** (~0,20-0,60 €/h según proveedor). Es la única que aguanta 10
   canales a la vez sin encolar.
2. **VPS de CPU (8 vCPU, ~25-40 €/mes)** con `modelo: "small"` o `"base"` en
   `config.json` en vez de `large-v3-turbo`. Pierdes precisión en los
   subtítulos, que es justo lo que más se nota en pantalla.
3. **El PC del usuario como servidor**: ya tiene la GPU. Es la opción más
   barata con diferencia, solo hay que dejarlo encendido.

Recomendación original: empezar por la 3. Si el PC no puede quedarse encendido,
pasar a la 1 con 3 o 4 canales, no 10.

## Opción A: el PC como servidor (sin coste)

Tarea programada que arranca al encender y sobrevive a reinicios:

```powershell
$py   = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$raiz = "C:\ruta\a\clipper"   # ajusta a donde lo tengas
$acc = New-ScheduledTaskAction -Execute $py -Argument "servidor.py" -WorkingDirectory $raiz
$dis = New-ScheduledTaskTrigger -AtStartup
$cfg = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName "clipper" -Action $acc -Trigger $dis -Settings $cfg -RunLevel Highest
```

## Opción B: VPS Linux con systemd

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

## Opción C: EasyPanel (la que está en uso)

EasyPanel construye la imagen desde el repositorio y ya trae proxy con
certificado automático, así que **no hace falta Caddy ni el docker-compose**.

1. **Create Service → App**, origen **GitHub**, el repositorio, rama `main`.
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

   Recuerda añadir `CLIPPER_LLM_ACTIVO` y `OPENAI_API_KEY`, que faltan en ese
   bloque y sin ellos Luna no evalúa nada.

4. **Volumes**: volumen persistente montado en **`/app/clips`**. Sin esto se
   pierden los clips y el modelo en cada despliegue.
5. **Domains**: el dominio, puerto **8080**, HTTPS activado.
6. **Deploy**.

La galería queda en `https://tu-dominio` y el aviso del móvil trae el enlace
directo al clip.

### Ajustes obligatorios en EasyPanel

- **`CLIPPER_MODELO=small`** salvo que el servidor tenga GPU. Con
  `large-v3-turbo` en CPU los picos se encolan y se pierden clips.
- **Menos canales**: edita `config.json` o arranca con
  `python servidor.py --canales a,b,c`. Con 3 o 4 va bien; con 10 en CPU, no.
- **El buffer va a disco** (en compose iba a tmpfs). Son ~1 GB por canal
  rodando; cuenta el espacio y el desgaste del SSD.

### Dónde vive todo en el servidor actual

- Volumen persistente en
  `/etc/easypanel/projects/automatizaciones/clips-alberto/volumes/clips/`, que
  el contenedor ve como `/app/clips/`.
- El CLI del host es `agy 1.1.9`, en `/home/fable5/.local/bin`, autenticado con
  OAuth de Google AI Pro.

## Opción D: Docker a pelo

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

- **Datos fuera del código**: `CLIPPER_DATA=/app/clips`, montado como volumen.
  La imagen no guarda nada; borrarla y reconstruirla no pierde clips.
- **Buffer en tmpfs** (16 GB): el buffer rodante escribe sin parar y en disco lo
  desgasta para nada, porque es material que se descarta.
- **tini como PID 1**: cada canal lanza `streamlink` y `ffmpeg`; sin él quedan
  zombis.
- **Usuario sin privilegios** (uid 10001) sobre el volumen.
- **Healthcheck**: si no queda ningún vigilante vivo, el contenedor se marca
  como enfermo y el orquestador lo reinicia.
- **Modelo en el volumen**, no en la imagen: son 1,6 GB que se descargan una vez.

### Recoger los clips con el PC apagado

El contenedor levanta una **galería web** con los clips numerados, en vertical,
con su gancho y botón de descarga, y el aviso de ntfy incluye el **enlace
directo**.

1. Apunta un dominio por DNS a la IP del servidor.
2. En `.env`: `CLIPPER_DOMINIO`, `CLIPPER_WEB_CLAVE` y
   `CLIPPER_URL_PUBLICA=https://tu-dominio`.
3. `docker compose up -d --build`

Caddy saca el certificado solo y sirve todo por HTTPS.

**La galería no arranca sin `CLIPPER_WEB_CLAVE`**, a propósito: una bandeja de
clips abierta en internet es una fuga, no una comodidad.

#### Sin dominio

Cloudflare Tunnel da una URL con HTTPS sin abrir puertos ni tener IP fija:

```bash
cloudflared tunnel --url http://localhost:8080
```

Sirve para probar. Para uso diario, un túnel con nombre y un dominio.

#### Alternativa sin web

Si se prefiere que los clips aparezcan solos en el móvil, monta una carpeta del
host en vez del volumen y sincronízala con rclone a Drive o Dropbox. Es más
cómodo, pero depende de las cuotas del proveedor.

## Variables (`.env`)

| Variable | Para qué |
|---|---|
| `CLIPPER_NTFY_TOPIC` | Topic de avisos. Cámbialo, es adivinable |
| `CLIPPER_MARCA` | Marca de agua quemada en el vídeo |
| `CLIPPER_MODELO` | `large-v3-turbo` con GPU, `small` en CPU |
| `CLIPPER_COMPUTE` | `float16` con GPU, `int8` en CPU |
| `CLIPPER_CARPETA_SINCRONIZADA` | Ruta de sincronización; vacía en contenedor |
| `CLIPPER_DOCKERFILE` | `Dockerfile` o `Dockerfile.gpu` |
| `CLIPPER_WEB_CLAVE` | Obligatoria; clave larga y aleatoria |
| `CLIPPER_LLM_ACTIVO` | Activa la evaluación editorial estricta de Luna |
| `OPENAI_API_KEY` | Clave del servidor; nunca al repositorio |
| `CLIPPER_LLM_MODELO` | Por defecto `gpt-5.6-luna` |
| `CLIPPER_RAW_MODO` | Debe ser `manual`; cualquier otro valor cae a `manual` |
| `CLIPPER_ANTIGRAVITY_ACTIVO` | Ruta directa heredada; mantener `0` |
| `CLIPPER_AGY_BIN` | Ruta del binario oficial `agy`; por defecto `agy` |
| `CLIPPER_DOMINIO` | Solo con Caddy propio; el panel no lo usa |

## Contrato Gemini v2

El `agy` autenticado del host es responsable de escribir
`RAW/_gemini/<id>.json`. Clipper no lo invoca desde la web. `raw.py` comprueba
el envoltorio v2 y reutiliza `antigravity.validar()` para sanear y limitar el
resultado antes de pasarlo a Luna como contexto no confiable.

El módulo conserva la implementación directa anterior con timeout de 120 s,
salida máxima de 48 KB, entorno sin secretos y control de créditos, pero no hay
endpoint ni modo de captura que la active en el flujo vigente.

El esquema v2 que valida la tarea del host exige descripción temporal, personas,
identidad solo con evidencia contextual y al menos dos URLs independientes, rol,
texto visible, lugar, momento clave, hechos editoriales y advertencias.

## Antes de mover nada

- **`carpeta_sincronizada`** apunta al OneDrive local del usuario. En un
  servidor no existe: cámbiala por una ruta del servidor y sincroniza aparte
  (rclone, recurso compartido), o déjala vacía y recoge los clips por `scp`.
- **El aviso de ntfy funciona igual** desde cualquier sitio: es una petición
  saliente, no necesita puertos abiertos.
- **Ancho de banda**: comprueba el límite de tráfico del proveedor. 10 canales
  a 5 Mbps son ~1,6 TB al mes si emitieran sin parar.
- **El modelo son ~1,6 GB** y se descarga en el primer arranque. En Docker vive
  en el volumen, no en la imagen.
