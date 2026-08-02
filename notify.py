"""
Bandeja de salida + aviso al movil.

Cada clip terminado se copia a out/LISTOS/ con nombre plano y se manda una
notificacion via ntfy. La galeria web es la unica interfaz de revision.

ntfy: gratis, sin cuenta. Instala la app, te suscribes al topic de config.json
y listo. El topic es la unica credencial: cualquiera que lo sepa recibe (y puede
mandar) avisos. Por eso se genera aleatorio y no se comparte.
"""

import os
import shutil
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import clipper
import bloqueo
from registro import obtener

LISTOS = clipper.OUT / "LISTOS"
CONFIG = clipper.CONFIG
NOTIF = CONFIG.get("notificaciones", {})


CONTADOR = LISTOS / ".contador"
SALIDA_LOCK = clipper.DATA / ".salida.lock"
LOG = obtener("notify")


def _siguiente_numero() -> int:
    """Numeracion correlativa que nunca se reutiliza, ni tras borrar clips."""
    try:
        n = int(CONTADOR.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        n = 0
    n += 1
    CONTADOR.parent.mkdir(parents=True, exist_ok=True)
    temporal = CONTADOR.with_name(f"{CONTADOR.name}.{os.getpid()}.tmp")
    temporal.write_text(str(n), encoding="utf-8")
    os.replace(temporal, CONTADOR)
    return n


def _sincronizar(destino: Path, txt: Path | None):
    """Copia el clip a la carpeta de OneDrive/Dropbox para que llegue al movil.

    Es la via real para tener el video en el telefono: ntfy.sh solo admite 2MB
    por adjunto y un vertical de 30s no cabe con calidad publicable.
    """
    ruta = NOTIF.get("carpeta_sincronizada", "").strip()
    if not ruta:
        return
    carpeta = Path(ruta)
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destino, carpeta / destino.name)
        if txt and txt.exists():
            shutil.copy2(txt, carpeta / txt.name)
        LOG.info("☁️ CLIP SINCRONIZADO\n   CARPETA: %s\n   ARCHIVO: %s", carpeta, destino.name)
    except OSError as e:
        LOG.warning("⚠️ SINCRONIZACIÓN FALLIDA\n   CARPETA: %s\n   MOTIVO: %s\n   EL CLIP SIGUE EN: LISTOS",
                    carpeta, e)


def registrar_listo(mp4: Path, meta: dict) -> Path:
    with bloqueo.exclusivo(SALIDA_LOCK, etiqueta="registro de clip listo"):
        return _registrar_listo(mp4, meta)


def _registrar_listo(mp4: Path, meta: dict) -> Path:
    """Copia el clip a la bandeja de salida con numeracion exclusiva."""
    LISTOS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now()
    n = _siguiente_numero()
    # El numero va delante y manda: es el mismo que uso al darte el guion, para
    # que "el 007" signifique lo mismo en la carpeta, en el movil y en el chat.
    base = f"{n:03d}_{meta.get('canal','clip')}_{ts:%Y-%m-%d}"
    if (LISTOS / f"{base}.mp4").exists():
        base = f"{base}_{ts:%H%M%S}"
    meta["n"] = n

    destino = LISTOS / f"{base}.mp4"
    shutil.copy2(mp4, destino)

    txt = mp4.with_suffix(".txt")
    txt_destino = None
    if txt.exists():
        txt_destino = LISTOS / f"{base}.txt"
        shutil.copy2(txt, txt_destino)

    _sincronizar(destino, txt_destino)

    return destino


def _preparar_adjunto(mp4: Path) -> Path | None:
    """Recomprime el clip para que quepa en el limite del servidor de avisos.

    El limite lo fija 'limite_adjunto_mb'. Con ntfy.sh anonimo son 2MB y un
    vertical de 30s no cabe con calidad publicable, asi que devuelve None y el
    aviso sale en texto con el enlace; por eso 'adjuntar_video' viene en false.
    Tiene sentido con un ntfy propio de limite mayor. El original nunca se toca.
    """
    limite = float(NOTIF.get("limite_adjunto_mb", 14)) * 1024 * 1024
    if mp4.stat().st_size <= limite:
        return mp4

    import clipper
    ligero = mp4.with_name(mp4.stem + "_movil.mp4")
    segundos = max(1.0, _duracion(mp4))
    # 8% de margen para el contenedor y el audio
    bitrate = int((limite * 8 * 0.92) / segundos) - 128_000
    if bitrate < 600_000:
        LOG.warning("⚠️ ADJUNTO OMITIDO\n   LÍMITE: %.0f MB\n   MOTIVO: NO CABE CON CALIDAD PUBLICABLE\n   AVISO: SOLO TEXTO",
                    limite / 1024 / 1024)
        return None

    LOG.info("📱 RECOMPRIMIENDO ADJUNTO PARA MÓVIL\n   TAMAÑO ORIGINAL: %.1f MB",
             mp4.stat().st_size / 1024 / 1024)
    try:
        clipper.run([clipper.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4),
                     "-c:v", "libx264", "-preset", "veryfast",
                     "-b:v", str(bitrate), "-maxrate", str(int(bitrate * 1.2)),
                     "-bufsize", str(bitrate * 2), "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(ligero)])
    except Exception as e:
        LOG.warning("⚠️ ADJUNTO NO PREPARADO\n   MOTIVO: %s", e)
        return None
    return ligero if ligero.exists() and ligero.stat().st_size <= limite else None


def _duracion(mp4: Path) -> float:
    import clipper
    try:
        p = clipper.run([clipper.FFPROBE, "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=nw=1:nk=1", str(mp4)])
    except Exception:
        return 0.0
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def avisar(titulo: str, mensaje: str, adjunto: Path | None = None,
           enlace: str | None = None):
    """Manda el aviso a ntfy. Nunca revienta el pipeline si falla la red."""
    if not NOTIF.get("activo"):
        LOG.info("📱 NTFY OMITIDO\n   TÍTULO: %s\n   MOTIVO: DESACTIVADO", titulo)
        return
    topic = NOTIF.get("ntfy_topic", "").strip()
    if not topic:
        LOG.warning("⚠️ NTFY NO ENVIADO\n   TÍTULO: %s\n   MOTIVO: TOPIC VACÍO", titulo)
        return

    url = f"https://ntfy.sh/{topic}"
    cabeceras = {
        "Title": titulo.encode("utf-8"),
        "Priority": str(NOTIF.get("prioridad", "default")),
        "Tags": "clapper",
    }
    if enlace:
        # Tocar la notificacion abre el clip directamente.
        cabeceras["Click"] = enlace

    envio = None
    if adjunto and NOTIF.get("adjuntar_video"):
        envio = _preparar_adjunto(adjunto)  # OJO: sube el video a un tercero

    try:
        if envio:
            cabeceras["Filename"] = envio.name
            cabeceras["Message"] = mensaje.replace("\n", " | ").encode("utf-8")
            datos = envio.read_bytes()
        else:
            datos = mensaje.encode("utf-8")
        req = urllib.request.Request(url, data=datos,
                                     headers={k: v for k, v in cabeceras.items()},
                                     method="POST")
        urllib.request.urlopen(req, timeout=15)
        LOG.info("📱 NTFY ENVIADO\n   TÍTULO: %s\n   ADJUNTO: %s",
                 titulo, envio.name if envio else "NO")
    except (urllib.error.URLError, OSError) as e:
        LOG.warning("⚠️ NTFY NO ENVIADO\n   MOTIVO: %s\n   EL CLIP SIGUE GUARDADO", e)


def publicar(mp4: Path, meta: dict) -> Path:
    destino = registrar_listo(mp4, meta)
    gancho = meta.get("hook", "") or "(sin gancho)"
    dur = meta.get("duracion", "?")
    canal = meta.get("canal", "desconocido")

    LOG.info("✅ CLIP PUBLICADO EN LISTOS\n   CANAL: %s\n   ARCHIVO: %s\n"
             "   DURACIÓN: %ss\n   GANCHO: %r",
             canal, destino.name, dur, gancho)

    aviso = f"{gancho}\n\n{dur}s"
    if isinstance(dur, int):
        aviso += " (monetiza en TikTok)" if dur > 60 else " (menos de 1 min)"
    aviso += f"\n{destino.name}"

    # Con el PC apagado el enlace es lo unico que convierte el aviso en algo
    # accionable: abres, ves el clip y lo descargas al movil.
    base = os.environ.get("CLIPPER_URL_PUBLICA", "").rstrip("/")
    enlace = (f"{base}/files/out/LISTOS/{quote(destino.name, safe='')}"
              if base else None)
    if enlace:
        aviso += f"\n\n{enlace}"

    avisar(
        titulo=f"Clip #{meta.get('n', '?'):03d} · {canal}"
              if isinstance(meta.get("n"), int) else f"Clip listo: {canal}",
        mensaje=aviso,
        adjunto=destino,
        enlace=enlace,
    )
    return destino


def avisar_inicio_directo(canal: str, plataforma: str, url: str):
    """Envía una notificación vía ntfy al teléfono cuando un streamer inicia directo."""
    avisar(
        titulo=f"🔴 EN DIRECTO: {canal.upper()}",
        mensaje=f"¡{canal} ha iniciado directo en {plataforma.capitalize()}!\nCapturando audio y chat en vivo.",
        enlace=url,
    )

