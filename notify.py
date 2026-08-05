"""
Bandeja de salida + aviso al movil.

Cada clip terminado se copia a out/LISTOS/ con nombre plano, se registra en
index.csv, se regenera index.html (galeria para revisar antes de subir) y se
manda una notificacion via ntfy.

ntfy: gratis, sin cuenta. Instala la app, te suscribes al topic de config.json
y listo. El topic es la unica credencial: cualquiera que lo sepa recibe (y puede
mandar) avisos. Por eso se genera aleatorio y no se comparte.
"""

import csv
import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import clipper

ROOT = clipper.ROOT
LISTOS = clipper.OUT / "LISTOS"
INDEX_CSV = LISTOS / "index.csv"
INDEX_HTML = LISTOS / "index.html"
CONFIG = clipper.CONFIG
NOTIF = CONFIG.get("notificaciones", {})


CONTADOR = LISTOS / ".contador"

# Las columnas del indice, en orden. Las comparte resultados.py.
COLUMNAS_INDICE = ["n", "fecha", "canal", "archivo", "duracion_s", "slug",
                   "motivo", "gancho", "subido", "visitas"]


def _titulo_para_archivo(hook: str | None, tope: int = 70) -> str:
    """Convierte el gancho en algo que Windows acepte como nombre de fichero.

    Se conservan tildes y ñ: el nombre lo lees tu en el movil, no lo parsea
    nadie. Lo que se quita son los caracteres que Windows prohibe.
    """
    texto = (hook or "").strip()
    if not texto or texto.startswith("ESCRIBE"):
        return "sin-gancho"
    # Los emojis se van del nombre del fichero: enredan al buscar y algunos
    # sistemas de sincronizacion los rechazan. En el video si aparecen.
    texto = re.sub("[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]", "", texto)
    texto = re.sub(r'[<>:"/\\|?*¿¡,;.…]', "", texto)
    texto = re.sub(r"[\s_]+", "-", texto).strip("-.")
    texto = re.sub(r"-{2,}", "-", texto)
    if len(texto) > tope:
        # Cortar por palabra entera: un nombre partido a mitad se lee peor.
        texto = texto[:tope].rsplit("-", 1)[0]
    return texto or "sin-gancho"


def _siguiente_numero() -> int:
    """Numeracion correlativa que nunca se reutiliza, ni tras borrar clips."""
    try:
        n = int(CONTADOR.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        n = 0
    n += 1
    CONTADOR.parent.mkdir(parents=True, exist_ok=True)
    CONTADOR.write_text(str(n), encoding="utf-8")
    return n


SINCRONIZADOS = LISTOS / ".sincronizados"


def _ya_sincronizados() -> set:
    try:
        return set(SINCRONIZADOS.read_text(encoding="utf-8").split("\n")) - {""}
    except OSError:
        return set()


def _guardar_sincronizados(nombres: set):
    SINCRONIZADOS.parent.mkdir(parents=True, exist_ok=True)
    SINCRONIZADOS.write_text("\n".join(sorted(nombres)), encoding="utf-8")


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
        # Cada clip se copia UNA vez. Si luego lo borras porque ya lo subiste,
        # no vuelve a aparecer: la carpeta es tu bandeja de entrada, no un espejo.
        ya = _ya_sincronizados()
        nuevos = 0
        for p in [destino] + ([txt] if txt and txt.exists() else []):
            if p.name in ya:
                continue
            shutil.copy2(p, carpeta / p.name)
            ya.add(p.name)
            nuevos += 1
        _guardar_sincronizados(ya)
        if nuevos:
            print(f"[>] Sincronizado a {carpeta}")
    except OSError as e:
        print(f"[!] No se pudo sincronizar ({e}). El clip esta en out/LISTOS igual.")


def _ya_publicado(mp4: Path) -> Path | None:
    """Busca en la bandeja un clip identico al que se va a publicar.

    Se compara el contenido, no el nombre: el mismo clip se ha llegado a
    publicar tres veces seguidas porque el gancho cambiaba de tildes y el
    nombre le parecia nuevo (033, 034 y 035 eran el mismo riñon). Subir el
    mismo video dos veces es lo peor que se puede hacer: la plataforma lo marca
    como contenido duplicado y hunde el alcance de los dos.
    """
    if not LISTOS.exists():
        return None
    firma = hashlib.md5(mp4.read_bytes()).hexdigest()
    for otro in LISTOS.glob("*.mp4"):
        if otro.stat().st_size != mp4.stat().st_size:
            continue        # el tamano descarta casi todo sin leer el fichero
        if hashlib.md5(otro.read_bytes()).hexdigest() == firma:
            return otro
    return None


HUELLAS = LISTOS / "huellas.json"

# Calibrado comparando los 728 clips con material, todos contra todos:
#   0.62  mismo momento desde dos canales (peereira7-203219 / agustin51-203215)
#   0.27  otros momentos de esa misma colaboracion -> son clips distintos, pasan
#   0.11  momentos sin relacion
# 0.55 cae en el hueco: caza el duplicado y deja pasar el resto de la sesion.
PARECIDO_MAXIMO = 0.55


def _huella(slug: str, mp4: Path | None = None) -> list:
    """Bigramas de lo que se dice dentro del clip.

    El md5 no ve estos duplicados: cuando dos creadores que vigilo juegan en el
    mismo lobby, los dos canales graban la misma conversacion con camaras
    distintas, asi que el video difiere byte a byte pero el clip es el mismo
    momento. Peereira y Agustin51 lo hicieron a las 20:32 y ninguno de los dos
    guardianes se habria enterado.
    """
    d = clipper.WORK / slug
    try:
        clip = json.loads((d / "clips.json").read_text(encoding="utf-8"))["clips"][0]
        pal = json.loads((d / "transcript.json").read_text(encoding="utf-8"))["words"]
    except (OSError, KeyError, IndexError, ValueError):
        return []
    dentro = [re.sub(r"[^\wáéíóúüñ]", "", w["word"].lower())
              for w in pal if clip["start"] <= w["start"] <= clip["end"]]
    dentro = [p for p in dentro if p]
    return sorted({f"{a} {b}" for a, b in zip(dentro, dentro[1:])})


def _mismo_momento(huella: list) -> tuple[str, float] | None:
    """Busca un clip ya publicado que diga practicamente lo mismo."""
    if len(huella) < 12:      # muy corto: cualquier parecido seria casualidad
        return None
    try:
        previas = json.loads(HUELLAS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mia = set(huella)
    for nombre, otra in previas.items():
        otra = set(otra)
        if not otra:
            continue
        parecido = len(mia & otra) / len(mia | otra)
        if parecido >= PARECIDO_MAXIMO:
            return nombre, parecido
    return None


def _guardar_huella(nombre: str, huella: list):
    if not huella:
        return
    try:
        previas = json.loads(HUELLAS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previas = {}
    previas[nombre] = huella
    HUELLAS.write_text(json.dumps(previas, ensure_ascii=False), encoding="utf-8")


def registrar_listo(mp4: Path, meta: dict) -> Path:
    """Copia el clip a la bandeja de salida y lo apunta en el indice."""
    LISTOS.mkdir(parents=True, exist_ok=True)
    if repetido := _ya_publicado(mp4):
        print(f"[!] Ya estaba publicado como {repetido.name}. No se duplica.")
        return repetido

    huella = _huella(meta.get("slug", ""), mp4)
    if gemelo := _mismo_momento(huella):
        nombre, parecido = gemelo
        print(f"[!] Dice lo mismo que {nombre} ({parecido:.0%} igual): "
              f"seria el mismo momento desde otra camara. No se duplica.")
        return LISTOS / nombre
    ts = datetime.now()
    n = _siguiente_numero()
    # Numero + canal + gancho: el nombre del archivo ya dice de que va el clip,
    # sin abrir el .txt.
    base = f"{n:03d}_{meta.get('canal','clip')}_{_titulo_para_archivo(meta.get('hook'))}"
    if (LISTOS / f"{base}.mp4").exists():
        base = f"{base}_{ts:%H%M%S}"
    meta = {**meta, "n": n}

    destino = LISTOS / f"{base}.mp4"
    shutil.copy2(mp4, destino)
    _guardar_huella(destino.name, huella)

    txt = mp4.with_suffix(".txt")
    txt_destino = None
    if txt.exists():
        txt_destino = LISTOS / f"{base}.txt"
        shutil.copy2(txt, txt_destino)

    _sincronizar(destino, txt_destino)

    # Por nombre de columna y no por posicion: escribiendo una lista suelta, en
    # cuanto el indice gano la columna 'visitas' las filas nuevas salian con un
    # campo de menos y el gancho se corria de sitio.
    nuevo = not INDEX_CSV.exists()
    with INDEX_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_INDICE, extrasaction="ignore")
        if nuevo:
            w.writeheader()
        w.writerow({
            "n": f"{meta.get('n', 0):03d}",
            "fecha": f"{ts:%Y-%m-%d %H:%M:%S}",
            "canal": meta.get("canal", ""),
            "archivo": destino.name,
            "duracion_s": meta.get("duracion", ""),
            "slug": meta.get("slug") or meta.get("motivo", ""),
            "motivo": meta.get("motivo", ""),
            "gancho": meta.get("hook", ""),
            "subido": "NO",
            "visitas": "",
        })

    _regenerar_html()
    return destino


def _regenerar_html():
    filas = []
    if INDEX_CSV.exists():
        with INDEX_CSV.open(encoding="utf-8") as f:
            filas = list(csv.DictReader(f))
    filas.reverse()

    tarjetas = []
    for r in filas:
        subido = (r.get("subido", "NO") or "NO").upper() != "NO"
        tarjetas.append(f"""
    <article class="clip{' subido' if subido else ''}">
      <span class="num">#{r.get('n','---')}</span>
      <video src="{r['archivo']}" controls preload="metadata" playsinline></video>
      <div class="meta">
        <p class="gancho">{r.get('gancho','') or '(sin gancho)'}</p>
        <p class="datos">{r['fecha']} &middot; {r['canal']} &middot; {r.get('duracion_s','?')}s</p>
        <a href="{r['archivo']}" download>Descargar</a>
      </div>
    </article>""")

    html = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clips listos para subir</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem;
        background: Canvas; color: CanvasText; }}
 h1 {{ font-size: 1.3rem; margin: 0 0 .25rem; }}
 .sub {{ opacity: .65; margin: 0 0 1.5rem; font-size: .9rem; }}
 .grid {{ display: grid; gap: 1.25rem;
          grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); }}
 .clip {{ border: 1px solid color-mix(in srgb, CanvasText 18%, transparent);
          border-radius: 12px; overflow: hidden; position: relative; }}
 .num {{ position: absolute; top: .5rem; left: .5rem; z-index: 2;
         background: #000c; color: #fff; font-weight: 700; font-size: .85rem;
         padding: .15rem .5rem; border-radius: 999px; letter-spacing: .03em; }}
 .clip.subido {{ opacity: .45; }}
 video {{ width: 100%; display: block; background: #000; aspect-ratio: 9/16;
          object-fit: contain; }}
 .meta {{ padding: .75rem; }}
 .gancho {{ font-weight: 600; margin: 0 0 .4rem; font-size: .95rem; }}
 .datos {{ margin: 0 0 .5rem; font-size: .8rem; opacity: .65; }}
 a {{ font-size: .85rem; }}
</style></head><body>
<h1>Clips listos para subir</h1>
<p class="sub">{len(filas)} clips. Marca <code>subido</code> como SI en index.csv para atenuarlos.</p>
<div class="grid">{''.join(tarjetas)}</div>
</body></html>"""
    INDEX_HTML.write_text(html, encoding="utf-8")


def _preparar_adjunto(mp4: Path) -> Path | None:
    """ntfy.sh corta en 15MB. Si el clip pasa, manda una copia recomprimida:
    peor que el original pero perfectamente subible (las plataformas recodifican
    igual). El original nunca se toca."""
    limite = float(NOTIF.get("limite_adjunto_mb", 14)) * 1024 * 1024
    if mp4.stat().st_size <= limite:
        return mp4

    import clipper
    ligero = mp4.with_name(mp4.stem + "_movil.mp4")
    segundos = max(1.0, _duracion(mp4))
    # 8% de margen para el contenedor y el audio
    bitrate = int((limite * 8 * 0.92) / segundos) - 128_000
    if bitrate < 600_000:
        print(f"[!] No cabe en {limite/1024/1024:.0f}MB con calidad publicable. "
              f"Aviso solo con texto; el video va por la carpeta sincronizada.")
        return None

    print(f"[>] Clip de {mp4.stat().st_size/1024/1024:.1f}MB: recomprimo para el movil")
    try:
        clipper.run([clipper.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4),
                     "-c:v", "libx264", "-preset", "veryfast",
                     "-b:v", str(bitrate), "-maxrate", str(int(bitrate * 1.2)),
                     "-bufsize", str(bitrate * 2), "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(ligero)])
    except SystemExit:
        return None
    return ligero if ligero.exists() and ligero.stat().st_size <= limite else None


def _duracion(mp4: Path) -> float:
    import clipper
    p = clipper.run([clipper.FFPROBE, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", str(mp4)])
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def avisar(titulo: str, mensaje: str, adjunto: Path | None = None,
           enlace: str | None = None):
    """Manda el aviso a ntfy. Nunca revienta el pipeline si falla la red."""
    if not NOTIF.get("activo"):
        return
    topic = NOTIF.get("ntfy_topic", "").strip()
    if not topic:
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
        print("[>] Aviso enviado al movil")
    except (urllib.error.URLError, OSError) as e:
        print(f"[!] No se pudo avisar al movil ({e}). El clip esta guardado igual.")


def publicar(mp4: Path, meta: dict) -> Path:
    destino = registrar_listo(mp4, meta)
    gancho = meta.get("hook", "") or "(sin gancho)"
    dur = meta.get("duracion", "?")
    aviso = f"{gancho}\n\n{dur}s"
    if isinstance(dur, int):
        aviso += " (monetiza en TikTok)" if dur > 60 else " (menos de 1 min)"
    aviso += f"\n{destino.name}"

    # Con el PC apagado el enlace es lo unico que convierte el aviso en algo
    # accionable: abres, ves el clip y lo descargas al movil.
    base = os.environ.get("CLIPPER_URL_PUBLICA", "").rstrip("/")
    enlace = f"{base}/{quote(destino.name)}" if base else None
    if enlace:
        aviso += f"\n\n{enlace}"

    avisar(
        titulo=f"Clip #{meta.get('n', '?'):03d} · {meta.get('canal','')}"
              if isinstance(meta.get("n"), int) else f"Clip listo: {meta.get('canal','')}",
        mensaje=aviso,
        adjunto=destino,
        enlace=enlace,
    )
    return destino
