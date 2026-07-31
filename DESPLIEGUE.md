# Poner el clipper en un servidor

El código ya es multiplataforma: `servidor.py` sustituye a `vigilar.ps1` y
funciona igual en Windows y en Linux. Levanta un vigilante por canal y relanza
solo el que se caiga, con espera creciente para no machacar la plataforma.

```bash
python servidor.py                      # todos los canales verificados
python servidor.py --canales elcalvolol,lopezfnx
python servidor.py --estado
```

## Qué hardware hace falta de verdad

Lo que manda es la **transcripción**, no la captura.

| Recurso | Por canal | 10 canales |
|---|---|---|
| Descarga continua | 3–6 Mbps | **30–60 Mbps sostenidos** |
| Disco (buffer 15 min) | ~1 GB | ~10 GB rodando |
| RAM | ~300 MB | ~3 GB + 2–4 GB del modelo |

**Transcripción de una ventana de 90 s** con `large-v3-turbo`:

- GPU NVIDIA (lo que tienes ahora): ~15 s
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

## Opción C — Docker (recomendada para servidor)

```bash
cp .env.ejemplo .env      # y ajusta topic, marca y modelo
docker compose up -d --build
docker compose logs -f
```

Con GPU (necesita NVIDIA Container Toolkit en el host):

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

### Qué hace la imagen

- **Datos fuera del código**: `CLIPPER_DATA=/data`, montado como volumen. La
  imagen no guarda nada; borrarla y reconstruirla no pierde clips.
- **Buffer en tmpfs** (16 GB): el buffer rodante escribe sin parar y en disco lo
  desgasta para nada, porque es material que se descarta.
- **tini como PID 1**: cada canal lanza `streamlink` y `ffmpeg`; sin él quedan
  zombis.
- **Usuario sin privilegios** (uid 10001) sobre el volumen.
- **Healthcheck**: si no queda ningún vigilante vivo, el contenedor se marca
  como enfermo y el orquestador lo reinicia.
- **Modelo en el volumen**, no en la imagen: son 1,6 GB que se descargan una vez.

### Recoger los clips

Dentro del contenedor no existe OneDrive, así que `CLIPPER_CARPETA_SINCRONIZADA`
va vacía y los clips se quedan en el volumen:

```bash
docker cp clipper:/data/out/LISTOS ./clips
```

Para sincronización continua, monta una carpeta del host en vez del volumen y
apunta ahí tu rclone/Nextcloud/OneDrive del servidor.

### Variables (`.env`)

| Variable | Para qué |
|---|---|
| `CLIPPER_NTFY_TOPIC` | Topic de avisos. Es la única credencial: cámbialo |
| `CLIPPER_MARCA` | Marca de agua quemada en el vídeo |
| `CLIPPER_MODELO` | `large-v3-turbo` con GPU, `small` en CPU |
| `CLIPPER_COMPUTE` | `float16` con GPU, `int8` en CPU |
| `CLIPPER_CARPETA_SINCRONIZADA` | Ruta de sincronización; vacía en contenedor |
| `CLIPPER_DOCKERFILE` | `Dockerfile` o `Dockerfile.gpu` |

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
