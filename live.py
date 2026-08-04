"""
clipper v2 - captura en directo y clipa el momento mientras sigue el stream.

    python live.py watch kingteka --plataforma twitch
    python live.py watch bitraderx --plataforma kick --solo-audio

Que hace:
  1. Comprueba cada 45s si el canal esta en directo (streamlink, sin API keys).
  2. Al arrancar el directo, graba a buffer rodante en segmentos de 10s (copy, sin recodificar).
  3. Escucha el chat de Twitch por IRC anonimo y mide mensajes/segundo.
  4. Mide energia de audio por segmento.
  5. Pico combinado -> espera la cola -> monta la ventana -> transcribe -> guarda RAW.

El gancho en modo automatico es la frase textual mas fuerte del propio clip, no una
plantilla: extrae, no inventa. Revisalo antes de publicar.
"""

import argparse
import asyncio
import collections
import json
import math
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import bloqueo
import calidad
import clipper
import notify
import raw
from clipper import CONFIG, DATA, FFMPEG, WORK
from registro import obtener

LIVE = CONFIG["live"]
BUF = Path(os.environ.get("CLIPPER_BUFFER_DIR", str(DATA / "buffer")))
LOG = obtener("live")


def aplicar_ajustes_canal(canal: str):
    """Cada canal tiene su montaje: un IRL de una camara no se compone igual que
    un streamer con webcam. La ficha del canal manda sobre el render global."""
    ficha = next((c for c in CONFIG.get("canales", []) if c.get("canal") == canal), None)
    if not ficha:
        return
    rc = CONFIG["render"]
    if ficha.get("layout"):
        rc["layout"] = ficha["layout"]
    if ficha.get("cam_rect"):
        rc.setdefault("reaccion", {})["cam_rect"] = ficha["cam_rect"]
    # Cualquier ajuste de render puede fijarse por canal, no solo el montaje.
    rc.update(ficha.get("render") or {})
    LOG.info("🎛️ MONTAJE DEL CANAL\n   CANAL: %s\n   LAYOUT: %s", canal, rc["layout"].upper())


def recargar():
    """Refresca config.json en todos los modulos sin reiniciar el vigilante.

    Se mutan los dicts en vez de reasignarlos: LIVE y NOTIF son referencias
    tomadas al importar, y una reasignacion las dejaria apuntando a lo viejo.
    """
    clipper.recargar_config()
    LIVE.clear()
    LIVE.update(CONFIG["live"])
    notify.NOTIF.clear()
    notify.NOTIF.update(CONFIG.get("notificaciones", {}))


# --- deteccion de estado del canal -------------------------------------------

def canal_url(canal: str, plataforma: str) -> str:
    return f"https://kick.com/{canal}" if plataforma == "kick" else f"https://www.twitch.tv/{canal}"


def estado_directo(url: str) -> tuple[bool, str | None]:
    proc = subprocess.run([sys.executable, "-m", "streamlink", "--json", url],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        detalle = (proc.stderr or proc.stdout or "sin detalle").strip().splitlines()
        return False, f"streamlink exit={proc.returncode}: {detalle[-1][:200]}"
    return bool(data.get("streams")), None


# --- captura a buffer rodante -------------------------------------------------

class Captura:
    """streamlink -> ffmpeg segment. Segmento N cubre [N*seg, (N+1)*seg) desde t0."""

    def __init__(self, url: str, destino: Path):
        self.url, self.destino = url, destino
        self.t0 = None
        self.sl = self.ff = None
        self._poda_fin = threading.Event()
        self._poda_hilo = None

    def arrancar(self):
        # En POSIX cada captura va en su propio grupo para poder matarla entera.
        self._grupo = {"start_new_session": True} if os.name != "nt" else {}
        self.destino.mkdir(parents=True, exist_ok=True)
        for viejo in self.destino.glob("*.ts"):
            try:
                viejo.unlink()
            except PermissionError:
                # Un ffmpeg huerfano de una sesion anterior sigue escribiendo aqui.
                raise RuntimeError(
                    f"El buffer {self.destino} esta bloqueado por un proceso anterior. "
                    f"Cierra los ffmpeg/streamlink sueltos y reintenta.")
        self.sl = subprocess.Popen(
            [sys.executable, "-m", "streamlink", "--stdout", "--retry-streams", "5",
             "--twitch-disable-ads", self.url, "best"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **self._grupo)
        self.ff = subprocess.Popen(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-c", "copy", "-f", "segment", "-segment_time", str(LIVE["segmento_s"]),
             "-reset_timestamps", "1", str(self.destino / "%06d.ts")],
            stdin=self.sl.stdout, stderr=subprocess.DEVNULL, **self._grupo)
        self.sl.stdout.close()
        time.sleep(0.5)
        if self.sl.poll() is not None or self.ff.poll() is not None:
            self.parar()
            raise RuntimeError("streamlink/ffmpeg terminó al iniciar la captura")
        self.t0 = time.time()
        self._poda_fin.clear()
        self._poda_hilo = threading.Thread(
            target=self._poda_loop, name=f"poda-{self.destino.name}", daemon=True)
        self._poda_hilo.start()

    def vivo(self) -> bool:
        return self.ff is not None and self.ff.poll() is None

    def parar(self):
        """Mata el arbol entero, no solo el proceso.

        En Windows terminate() deja vivo a streamlink, que sigue alimentando a
        ffmpeg y bloquea el buffer. En Linux pasa lo mismo si no se mata el
        grupo de procesos.
        """
        self._poda_fin.set()
        if self._poda_hilo and self._poda_hilo is not threading.current_thread():
            self._poda_hilo.join(timeout=2)
        for p in (self.ff, self.sl):
            if not p or p.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                   capture_output=True)
                else:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                p.kill()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._poda_hilo = None

    def segmentos(self):
        return sorted(self.destino.glob("*.ts"))

    def podar(self):
        segs = self.segmentos()
        sobran = len(segs) - LIVE["buffer_max_s"] // LIVE["segmento_s"]
        for s in segs[:max(0, sobran)]:
            try:
                s.unlink(missing_ok=True)
            except PermissionError:
                continue

    def _poda_loop(self):
        while not self._poda_fin.wait(LIVE["segmento_s"]):
            self.podar()


# --- chat de Twitch (IRC anonimo, sin credenciales) ---------------------------

# Lo que importa no es cuantos mensajes hay, sino que dicen.
RE_RISA = re.compile(r"(j[aeiou]j[aeiou]|kekw|omegalul|\blul\b|lmao|\bxd+\b|pog|jaj)", re.I)
RE_PIDE_CLIP = re.compile(r"\bclip\w*\b|\bcl1p\w*", re.I)
RE_SORPRESA = re.compile(r"(\?{3,}|!{3,}|wtf|\bomg\b|madre m[ií]a|no way|qu[eé] ha dicho|"
                         r"qu[eé] dijo|\bdios\b|brutal|increible|incre[ií]ble)", re.I)
RE_PRIVMSG = re.compile(r"^:[^!]+![^ ]+ PRIVMSG #[^ ]+ :(.*)$")


def peso_mensaje(texto: str) -> int:
    """Un 'clipealo' del chat vale por cinco mensajes normales: es el publico
    diciendo literalmente que ese momento es clipeable."""
    p = 1
    if RE_RISA.search(texto):
        p += LIVE["peso_risa"]
    if RE_PIDE_CLIP.search(texto):
        p += LIVE["peso_pide_clip"]
    if RE_SORPRESA.search(texto):
        p += LIVE["peso_sorpresa"]
    return p


class ChatTwitch(threading.Thread):
    daemon = True

    def __init__(self, canal: str, eventos: queue.Queue):
        super().__init__()
        self.canal, self.eventos, self.parar_flag = canal.lower(), eventos, threading.Event()
        self.conectado = threading.Event()

    def run(self):
        while not self.parar_flag.is_set():
            s = None
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=20)
                s.settimeout(1.0)
                s.send(f"NICK justinfan{int(time.time()) % 100000}\r\n".encode())
                s.send(f"JOIN #{self.canal}\r\n".encode())
                self.conectado.set()
                LOG.info("💬 CHAT TWITCH CONECTADO\n   CANAL: %s", self.canal)
                resto = ""
                while not self.parar_flag.is_set():
                    try:
                        datos = s.recv(8192).decode("utf-8", "ignore")
                    except socket.timeout:
                        continue
                    if not datos:
                        break
                    resto += datos
                    lineas, resto = resto.split("\r\n")[:-1], resto.split("\r\n")[-1]
                    for ln in lineas:
                        if ln.startswith("PING"):
                            s.send(b"PONG :tmi.twitch.tv\r\n")
                        elif "PRIVMSG" in ln:
                            m = RE_PRIVMSG.match(ln)
                            texto = m.group(1) if m else ""
                            self.eventos.put((time.time(), peso_mensaje(texto), texto))
            except OSError as e:
                if not self.parar_flag.is_set():
                    LOG.warning("⚠️ CHAT TWITCH NO DISPONIBLE\n   CANAL: %s\n   MOTIVO: %s\n   REINTENTO: 5s",
                                self.canal, e)
            finally:
                if self.conectado.is_set():
                    LOG.warning("🔌 CHAT TWITCH DESCONECTADO\n   CANAL: %s", self.canal)
                self.conectado.clear()
                if s:
                    s.close()
                if not self.parar_flag.is_set():
                    self.parar_flag.wait(5)


# --- energia de audio por segmento --------------------------------------------

RE_RMS_DB = re.compile(r"RMS level dB:\s*(-?\d+(?:\.\d+)?|-inf)")


def rms_segmento(ruta: Path) -> float:
    """RMS del segmento en la escala de int16, como antes.

    Lo calcula ffmpeg en vez de Python: sumar 160.000 muestras por segmento y
    por canal bloqueaba el bucle de vigilancia para obtener un dato que astats
    ya da hecho. El resultado se devuelve en la misma escala para que el
    historial de z-scores siga siendo comparable.
    """
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", str(ruta),
         "-vn", "-ac", "1", "-af", "astats=metadata=1:reset=0",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    valores = RE_RMS_DB.findall(proc.stderr or "")
    if not valores or valores[-1] == "-inf":
        return 0.0
    # astats da dBFS; el resto del detector trabaja en amplitud lineal.
    return 32768.0 * (10 ** (float(valores[-1]) / 20))


def zscore(valor: float, historial) -> float:
    if len(historial) < 8:
        return 0.0
    media = sum(historial) / len(historial)
    sd = math.sqrt(sum((v - media) ** 2 for v in historial) / len(historial)) or 1.0
    return (valor - media) / sd


# --- montaje de la ventana ----------------------------------------------------

def elegir_duracion(canal: str) -> tuple[str, dict]:
    """Un único rango: Luna decide después cuánto dura el momento real."""
    del canal
    return "flexible", CONFIG.get("duraciones", {}).get(
        "flexible", {"min": 8, "max": 40}
    )


def montar_ventana(cap: Captura, t_video: float, slug: str, antes: float = None) -> Path:
    seg = LIVE["segmento_s"]
    if antes is None:
        antes = LIVE["ventana_antes_s"]
    ini = max(0.0, t_video - antes)
    fin = t_video + LIVE["ventana_despues_s"]
    i0, i1 = int(ini // seg), int(fin // seg)

    disponibles = {int(p.stem): p for p in cap.segmentos()}
    trozos = [disponibles[i] for i in range(i0, i1 + 1) if i in disponibles]
    if len(trozos) < 4:
        raise RuntimeError(f"buffer insuficiente ({len(trozos)} segmentos)")

    d = WORK / slug
    d.mkdir(parents=True, exist_ok=True)
    lista = d / "concat.txt"
    lista.write_text("".join(f"file '{p.as_posix()}'\n" for p in trozos), encoding="utf-8")

    fuente = d / "source.mp4"
    # Los .ts del buffer ya vienen en H.264 y AAC desde la plataforma, asi que
    # unirlos sin recodificar es casi instantaneo. Recodificar la ventana
    # entera en cada pico era lo mas caro del pipeline y no compraba nada: el
    # recorte RAW posterior tambien copia, con lo que la precision del corte
    # sigue atada al keyframe igual que antes.
    clipper.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-fflags", "+genpts",
                 "-f", "concat", "-safe", "0", "-i", str(lista),
                 "-c", "copy", "-avoid_negative_ts", "make_zero", str(fuente)])
    clipper.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(fuente),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(d / "audio.wav")])

    # El primer segmento empieza en un multiplo de 'seg', no exactamente en 'ini':
    # devolvemos donde cae el pico dentro de la ventana ya montada.
    primero = int(trozos[0].stem)
    return fuente, t_video - primero * seg


SEÑALES_GANCHO = re.compile(
    r"\b(nunca|jam[aá]s|nadie|te juro|de verdad|mira|escucha|no me|"
    r"incre[ií]ble|brutal|imposible|acabo de|mil|mill[oó]n|euros|d[oó]lares|"
    r"perd[ií]|gan[eé]|cuidado|atenci[oó]n|fijate|f[ií]jate)\b", re.IGNORECASE)


VACIAS = set("""el la los las un una unos unas de del al a en con por para y o que
    se le lo me te nos su sus mi tu es esta este esto eso ese esa asi mas pero
    como cuando si no ya muy tan todo toda todos todas hay ha han he hola jaja
    jajaja xd kekw lul pog omegalul clip clipealo gg ez bro tio""".split())


def palabras_del_chat(mensajes) -> set:
    """De que estaba hablando el chat: palabras con contenido, sin emotes ni muletillas."""
    palabras = set()
    for m in mensajes:
        for p in re.findall(r"[a-záéíóúñü]{4,}", m.lower()):
            if p not in VACIAS:
                palabras.add(p)
    return palabras


def tono_del_chat(mensajes) -> str:
    """risa / sorpresa / neutro. Orienta que tipo de frase funciona de gancho."""
    risa = sum(1 for m in mensajes if RE_RISA.search(m))
    sorpresa = sum(1 for m in mensajes if RE_SORPRESA.search(m))
    if risa > sorpresa and risa >= 3:
        return "risa"
    if sorpresa >= 3:
        return "sorpresa"
    return "neutro"


def gancho_automatico(segs, t_pico: float, chat=None) -> str:
    """Elige la mejor frase del clip como gancho.

    Solo compiten frases que ya pasan el filtro de calidad: asi el gancho se
    elige con el mismo criterio con el que luego se juzga, en vez de proponer
    algo que se va a rechazar. Entre las validas gana la mas cargada y la mas
    pegada al pico, porque ahi esta lo que hizo reaccionar a la gente.
    """
    chat = list(chat or [])
    tema = palabras_del_chat(chat)
    tono = tono_del_chat(chat)

    candidatas = []
    for s in segs:
        txt = s["text"].strip().rstrip(" ,.;:")
        if not 15 <= len(txt) <= 85:
            continue
        if calidad._gancho_flojo(txt):
            continue

        # La cercania al pico orienta, pero no puede enterrar una frase con
        # sustancia: se limita a 3 puntos de penalizacion.
        cerca = min(abs(s["start"] - t_pico) * 0.04, 3.0)
        punt = (len(SEÑALES_GANCHO.findall(txt)) * 3
                + txt.count("?") * 2 + txt.count("!") * 2
                + (3 if calidad.REVERSO.search(txt) else 0)
                + (3 if calidad.CONDICIONAL.match(txt) else 0)
                - cerca)

        # Lo que repite el chat es lo que le importa: una frase que comparte
        # vocabulario con la reaccion es, casi siempre, la que la provoco.
        if tema:
            comunes = tema & {p for p in re.findall(r"[a-záéíóúñü]{4,}", txt.lower())
                              if p not in VACIAS}
            punt += len(comunes) * 3

        if tono == "sorpresa" and ("?" in txt or "!" in txt):
            punt += 2
        elif tono == "risa" and "!" in txt:
            punt += 1

        candidatas.append((punt, txt))

    if not candidatas:
        return ""
    punt, mejor = max(candidatas)
    # Por debajo del listón preferimos no dar gancho: el clip va a REVISAR y se
    # escribe a mano, en vez de publicar algo vago como "¿Y tú sabes por qué?".
    minimo = float(CONFIG.get("calidad", {}).get("gancho_puntuacion_minima", 3))
    if punt < minimo:
        LOG.info("📝 GANCHO DESCARTADO · PUNTUACIÓN BAJA\n   PUNTUACIÓN: %.1f\n   TEXTO: %r",
                 punt, mejor[:50])
        return ""
    return mejor


def contexto_editorial(segmentos: list, inicio: float, fin: float,
                        pico: float) -> tuple[list, float]:
    """Copia el contexto para Luna al reloj relativo del candidato, acotado."""
    duracion = max(0.0, float(fin) - float(inicio))

    def relativo(valor):
        return min(duracion, max(0.0, float(valor) - float(inicio)))

    normalizados = []
    for segmento in segmentos:
        copia = dict(segmento)
        copia["start"] = relativo(segmento["start"])
        copia["end"] = relativo(segmento["end"])
        normalizados.append(copia)
    return normalizados, relativo(pico)


def procesar(cap: Captura, t_video: float, canal: str, motivo: str, device: str, chat=None):
    recargar()  # coge los ajustes de config.json sin reiniciar
    aplicar_ajustes_canal(canal)
    slug = f"{canal}-{time.strftime('%Y%m%d-%H%M%S')}"
    LOG.info("⚡ PICO DETECTADO\n   CANAL: %s\n   MOTIVO: %s\n   TIEMPO: %.0fs\n   JOB: %s",
             canal, motivo.upper(), t_video, slug)
    modo, dur = elegir_duracion(canal)
    rc = CONFIG["render"]
    rc["duracion_min_s"], rc["duracion_max_s"] = dur["min"], dur["max"]
    antes = LIVE["ventana_antes_s"]
    LOG.info("🪟 VENTANA PREPARADA\n   JOB: %s\n   MODO: %s\n   DURACIÓN: %s-%ss\n   CONTEXTO ANTES: %.0fs",
             slug, modo.upper(), dur["min"], dur["max"], antes)

    fuente, t_pico = montar_ventana(cap, t_video, slug, antes=antes)

    args = argparse.Namespace(slug=slug, n=1, device=device, func=None, defer_clips=True)
    # Cloudflare admite tres trabajos simultaneos; el fallback local toma el
    # cerrojo de CPU dentro de cmd_transcribe.
    try:
        clipper.cmd_transcribe(args)
    finally:
        clipper.liberar_whisper_model()

    d = WORK / slug
    datos = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
    segs = datos["segments"]
    if not segs:
        LOG.warning("🗑️ JOB DESCARTADO\n   JOB: %s\n   MOTIVO: SIN VOZ", slug)
        return

    # El clip se centra en el pico: termina justo despues de la reaccion y
    # arranca lo justo para dar contexto, no antes.
    objetivo = rc["duracion_max_s"]
    fin = min(t_pico + LIVE["ventana_despues_s"], segs[-1]["end"])
    ini = max(0.0, fin - objetivo)
    ini = min((s["start"] for s in segs if s["start"] >= ini), default=ini)
    if fin - ini < rc["duracion_min_s"]:
        ini = max(0.0, fin - rc["duracion_min_s"])

    # Si el tramo no da ni para el minimo, no hay clip posible: cortar aqui
    # ahorra el render entero (y la GPU) en vez de fabricar algo que el filtro
    # de calidad va a tirar de todas formas.
    if fin - ini < rc["duracion_min_s"]:
        LOG.warning("🗑️ JOB DESCARTADO\n   JOB: %s\n   MOTIVO: DIÁLOGO INSUFICIENTE\n"
                    "   DISPONIBLE: %.0fs\n   MÍNIMO: %ss",
                    slug, fin - ini, rc["duracion_min_s"])
        return

    dentro = [s for s in segs if ini <= s["start"] < fin]
    duracion = fin - ini
    segmentos_raw, pico_raw = contexto_editorial(dentro, ini, fin, t_pico)
    palabras_raw = raw._normalizar_words(datos.get("words", []), ini, fin)
    raw_id = f"{slug}-01"
    manifest = raw.crear(
        fuente=fuente,
        inicio=ini,
        fin=fin,
        raw_id=raw_id,
        canal=canal,
        motivo=motivo,
        pico=pico_raw,
        segmentos=segmentos_raw,
        words=palabras_raw,
        chat=chat or [],
        limites=(dur["min"], dur["max"]),
    )
    LOG.info("🧊 CANDIDATO DETENIDO EN RAW\n   JOB: %s\n   RAW: %s\n"
             "   SIGUIENTE PASO: ANÁLISIS VISUAL CON LUNA",
             slug, manifest["nombre"])


# --- bucle principal ----------------------------------------------------------

def cmd_watch(args):
    url = canal_url(args.canal, args.plataforma)
    LOG.info("🚀 VIGILANTE INICIADO\n   CANAL: %s\n   PLATAFORMA: %s\n   URL: %s",
             args.canal, args.plataforma.upper(), url)

    while True:
        online, error = estado_directo(url)
        if not online:
            if error:
                LOG.warning("⚠️ COMPROBACIÓN FALLIDA\n   CANAL: %s\n   PLATAFORMA: %s\n"
                            "   MOTIVO: %s\n   REINTENTO: %ss",
                            args.canal, args.plataforma.upper(), error, LIVE["poll_online_s"])
            else:
                LOG.info("💤 DIRECTO OFFLINE\n   CANAL: %s\n   PLATAFORMA: %s\n"
                         "   PRÓXIMA COMPROBACIÓN: %ss",
                         args.canal, args.plataforma.upper(), LIVE["poll_online_s"])
            time.sleep(LIVE["poll_online_s"])
            continue

        LOG.info("🔴 DIRECTO DETECTADO\n   CANAL: %s\n   PLATAFORMA: %s\n   URL: %s",
                 args.canal, args.plataforma.upper(), url)
        cap = Captura(url, BUF / args.canal)
        try:
            cap.arrancar()
        except Exception:
            LOG.exception("❌ CAPTURA NO INICIADA\n   CANAL: %s", args.canal)
            time.sleep(LIVE["poll_online_s"])
            continue
        LOG.info("🎙️ CAPTURA INICIADA\n   CANAL: %s\n   BUFFER: %s",
                 args.canal, BUF / args.canal)
        notify.avisar_inicio_directo(args.canal, args.plataforma, url)

        eventos = queue.Queue()
        chat = None
        if args.plataforma == "twitch" and not args.solo_audio:
            chat = ChatTwitch(args.canal, eventos)
            chat.start()
            if not chat.conectado.wait(5):
                LOG.warning("⚠️ CHAT TWITCH NO CONFIRMADO\n   CANAL: %s\n   LA CAPTURA CONTINÚA SIN CHAT",
                            args.canal)
        elif args.plataforma == "kick" and not args.solo_audio:
            import kick
            class ChatKickThread(threading.Thread):
                daemon = True
                def __init__(self, canal: str, data_dir: Path, queue_eventos: queue.Queue):
                    super().__init__()
                    self.canal, self.data_dir, self.queue_eventos = canal, data_dir, queue_eventos
                    self.parar_flag = threading.Event()
                    self.conectado = threading.Event()
                def run(self):
                    listener = kick.KickChatListener(self.canal, self.data_dir)
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    async def poll():
                        if await listener.start():
                            self.conectado.set()
                            LOG.info("💬 CHAT KICK CONECTADO\n   CANAL: %s", self.canal)
                        else:
                            LOG.warning("⚠️ CHAT KICK NO CONFIRMADO\n   CANAL: %s\n   LA CAPTURA CONTINÚA SIN CHAT",
                                        self.canal)
                        try:
                            while not self.parar_flag.is_set():
                                await asyncio.sleep(2)
                                if listener.esta_conectado() and not self.conectado.is_set():
                                    self.conectado.set()
                                    LOG.info("💬 CHAT KICK CONECTADO\n   CANAL: %s", self.canal)
                                elif not listener.esta_conectado() and self.conectado.is_set():
                                    self.conectado.clear()
                                    LOG.warning("🔌 CHAT KICK DESCONECTADO\n   CANAL: %s\n   REINTENTANDO",
                                                self.canal)
                                for texto in listener.poll_and_reset():
                                    self.queue_eventos.put((time.time(), peso_mensaje(texto), texto))
                        finally:
                            await listener.stop()
                    try:
                        loop.run_until_complete(poll())
                    except Exception:
                        LOG.exception("❌ CHAT KICK NO DISPONIBLE\n   CANAL: %s", self.canal)
                    finally:
                        self.conectado.clear()
                        asyncio.set_event_loop(None)
                        loop.close()
            chat_kick = ChatKickThread(args.canal, DATA, eventos)
            chat = chat_kick
            chat_kick.start()
        else:
            LOG.info("🎙️ CAPTURA SOLO AUDIO\n   CANAL: %s", args.canal)

        hist_chat, hist_audio = collections.deque(maxlen=40), collections.deque(maxlen=40)
        marcas, vistos, ultimo_clip = [], set(), 0.0
        ultimo_estado_log = 0.0

        try:
            while cap.vivo():
                time.sleep(LIVE["segmento_s"])
                ahora = time.time()
                transcurrido = ahora - cap.t0

                while not eventos.empty():
                    marcas.append(eventos.get())
                marcas = [m for m in marcas if ahora - m[0] < 60]
                recientes = [m for m in marcas if ahora - m[0] <= LIVE["segmento_s"]]
                n_msgs = len(recientes)
                reaccion = sum(m[1] for m in recientes)
                n_clip = sum(1 for m in recientes if RE_PIDE_CLIP.search(m[2]))
                z_chat = zscore(reaccion, hist_chat)
                hist_chat.append(reaccion)

                segs = cap.segmentos()
                z_audio = 0.0
                if len(segs) >= 2:
                    ultimo = segs[-2]
                    if ultimo.name not in vistos:
                        vistos.add(ultimo.name)
                        r = rms_segmento(ultimo)
                        z_audio = zscore(r, hist_audio)
                        hist_audio.append(r)

                estado = (f"📊 ESTADO CAPTURA | CANAL={args.canal} | "
                          f"TIEMPO_MIN={transcurrido / 60:.1f} | MENSAJES={n_msgs} | "
                          f"REACCIÓN={reaccion} | Z_CHAT={z_chat:+.1f} | "
                          f"PETICIONES_CLIP={n_clip} | Z_AUDIO={z_audio:+.1f}")
                if sys.stdout.isatty():
                    print("\r" + estado, end="", flush=True)
                elif ahora - ultimo_estado_log >= 60:
                    LOG.info(estado)
                    ultimo_estado_log = ahora

                # Que dos o mas espectadores pidan clip a la vez dispara solo:
                # es la señal mas fiable que existe y no necesita linea base.
                piden_clip = n_clip >= 2
                disparo = (piden_clip or z_chat >= LIVE["umbral_reaccion_z"]
                           or z_audio >= LIVE["umbral_audio_z"])
                if disparo and ahora - ultimo_clip > LIVE["cooldown_s"] \
                        and transcurrido > LIVE["ventana_antes_s"] + 30:
                    motivo = ("el chat pide clip" if piden_clip
                              else "reaccion" if z_chat >= LIVE["umbral_reaccion_z"] else "audio")
                    t_video = transcurrido - (0 if motivo == "audio" else LIVE["chat_lag_s"])
                    # Lo que dijo el chat alrededor del pico: es la pista de que
                    # le llamo la atencion, y con eso se elige el gancho.
                    chat_pico = [m[2] for m in marcas if abs(m[0] - ahora) <= 20]
                    time.sleep(LIVE["ventana_despues_s"] + LIVE["segmento_s"])
                    ultimo_clip = time.time()
                    try:
                        procesar(cap, t_video, args.canal, motivo, args.device,
                                 chat=chat_pico)
                    except Exception:
                        LOG.exception("❌ FALLO PROCESANDO\n   CANAL: %s\n   MOTIVO: %s",
                                      args.canal, motivo.upper())

                cap.podar()
        except KeyboardInterrupt:
            LOG.info("🛑 VIGILANTE DETENIENDO\n   CANAL: %s", args.canal)
            cap.parar()
            if chat:
                chat.parar_flag.set()
            return
        finally:
            cap.parar()
            if chat:
                chat.parar_flag.set()
                chat.join(timeout=5)

        LOG.info("⏹️ DIRECTO TERMINADO\n   CANAL: %s", args.canal)


class BufferExistente:
    """Permite clipar de lo que ya hay en disco, sin captura activa."""

    def __init__(self, destino: Path):
        self.destino = destino

    def segmentos(self):
        return sorted(self.destino.glob("*.ts"))

def cmd_now(args):
    """Clipa YA del buffer existente, sin esperar a ningun pico."""
    d = BUF / args.canal
    cap = BufferExistente(d)
    segs = cap.segmentos()
    if len(segs) < 8:
        sys.exit(f"[x] Buffer insuficiente en {d} ({len(segs)} segmentos)")

    seg = LIVE["segmento_s"]
    ultimo = int(segs[-1].stem)
    LOG.info("Buffer existente canal=%s segmentos=%d duracion_aprox_min=%.1f",
             args.canal, len(segs), len(segs) * seg / 60)

    if args.en is not None:
        t_video = args.en
    else:
        t_video = (ultimo - 1) * seg - LIVE["ventana_despues_s"]

    procesar(cap, t_video, args.canal, "manual", args.device)


def main():
    p = argparse.ArgumentParser(description="clipper v2 - clipping en directo")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("now", help="clipa ya del buffer existente, sin esperar picos")
    n.add_argument("canal")
    n.add_argument("--en", type=float, help="segundo del directo a clipar (por defecto, lo mas reciente)")
    n.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    n.set_defaults(func=cmd_now)

    w = sub.add_parser("watch", help="vigila un canal y clipa los picos")
    w.add_argument("canal")
    w.add_argument("--plataforma", default="twitch", choices=["twitch", "kick"])
    w.add_argument("--solo-audio", action="store_true", help="ignora el chat")
    w.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    w.set_defaults(func=cmd_watch)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
