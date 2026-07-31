"""
clipper v2 - captura en directo y clipa el momento mientras sigue el stream.

    python live.py watch kingteka --plataforma twitch
    python live.py watch bitraderx --plataforma kick --solo-audio

Que hace:
  1. Comprueba cada 45s si el canal esta en directo (streamlink, sin API keys).
  2. Al arrancar el directo, graba a buffer rodante en segmentos de 10s (copy, sin recodificar).
  3. Escucha el chat de Twitch por IRC anonimo y mide mensajes/segundo.
  4. Mide energia de audio por segmento.
  5. Pico combinado -> espera la cola -> monta la ventana -> transcribe -> renderiza 9:16.

El gancho en modo automatico es la frase textual mas fuerte del propio clip, no una
plantilla: extrae, no inventa. Revisalo antes de publicar.
"""

import argparse
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

import calidad
import clipper
import notify
from clipper import CONFIG, DATA, FFMPEG, ROOT, WORK

LIVE = CONFIG["live"]
BUF = DATA / "buffer"


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
    print(f"[>] Montaje '{rc['layout']}' para {canal}")


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


def esta_en_directo(url: str) -> bool:
    proc = subprocess.run([sys.executable, "-m", "streamlink", "--json", url],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return False
    return bool(data.get("streams"))


# --- captura a buffer rodante -------------------------------------------------

class Captura:
    """streamlink -> ffmpeg segment. Segmento N cubre [N*seg, (N+1)*seg) desde t0."""

    def __init__(self, url: str, destino: Path):
        self.url, self.destino = url, destino
        self.t0 = None
        self.sl = self.ff = None

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
        self.t0 = time.time()

    def vivo(self) -> bool:
        return self.ff is not None and self.ff.poll() is None

    def parar(self):
        """Mata el arbol entero, no solo el proceso.

        En Windows terminate() deja vivo a streamlink, que sigue alimentando a
        ffmpeg y bloquea el buffer. En Linux pasa lo mismo si no se mata el
        grupo de procesos.
        """
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

    def segmentos(self):
        return sorted(self.destino.glob("*.ts"))

    def podar(self):
        segs = self.segmentos()
        sobran = len(segs) - LIVE["buffer_max_s"] // LIVE["segmento_s"]
        for s in segs[:max(0, sobran)]:
            s.unlink(missing_ok=True)


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

    def run(self):
        while not self.parar_flag.is_set():
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=20)
                s.settimeout(1.0)
                s.send(f"NICK justinfan{int(time.time()) % 100000}\r\n".encode())
                s.send(f"JOIN #{self.canal}\r\n".encode())
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
                s.close()
            except OSError:
                time.sleep(5)


# --- energia de audio por segmento --------------------------------------------

def rms_segmento(ruta: Path) -> float:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(ruta),
         "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
        capture_output=True)
    import array
    m = array.array("h")
    try:
        m.frombytes(proc.stdout[:len(proc.stdout) // 2 * 2])
    except ValueError:
        return 0.0
    if not m:
        return 0.0
    return math.sqrt(sum(float(x) * x for x in m) / len(m))


def zscore(valor: float, historial) -> float:
    if len(historial) < 8:
        return 0.0
    media = sum(historial) / len(historial)
    sd = math.sqrt(sum((v - media) ** 2 for v in historial) / len(historial)) or 1.0
    return (valor - media) / sd


# --- montaje de la ventana ----------------------------------------------------

CONTADORES = ROOT / "work" / "_contadores.json"


def elegir_duracion(canal: str) -> tuple[str, dict]:
    """Alterna clips cortos y largos.

    TikTok solo monetiza vídeos de más de un minuto, pero un feed entero de
    clips largos rinde peor: se alterna para tener de los dos.
    """
    d = CONFIG.get("duraciones", {})
    cada = int(d.get("uno_largo_cada", 3))
    try:
        cont = json.loads(CONTADORES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cont = {}
    n = cont.get(canal, 0) + 1
    cont[canal] = n
    CONTADORES.parent.mkdir(parents=True, exist_ok=True)
    CONTADORES.write_text(json.dumps(cont), encoding="utf-8")

    modo = "largo" if cada > 0 and n % cada == 0 else "corto"
    return modo, d.get(modo, {"min": 26, "max": 34})


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
    clipper.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "concat", "-safe", "0", "-i", str(lista),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                 "-c:a", "aac", "-b:a", "128k", str(fuente)])
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

        punt = (len(SEÑALES_GANCHO.findall(txt)) * 2
                + txt.count("?") * 2 + txt.count("!") * 2
                + (2 if calidad.REVERSO.search(txt) else 0)
                + (2 if calidad.CONDICIONAL.match(txt) else 0)
                - abs(s["start"] - t_pico) * 0.06)

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
    return max(candidatas)[1]


def procesar(cap: Captura, t_video: float, canal: str, motivo: str, device: str, chat=None):
    recargar()  # coge los ajustes de config.json sin reiniciar
    aplicar_ajustes_canal(canal)
    slug = f"{canal}-{time.strftime('%H%M%S')}"
    print(f"\n[!] PICO ({motivo}) en t={t_video:.0f}s -> {slug}")
    t_ini = time.time()

    modo, dur = elegir_duracion(canal)
    rc = CONFIG["render"]
    rc["duracion_min_s"], rc["duracion_max_s"] = dur["min"], dur["max"]
    # Un clip largo necesita mas margen hacia atras del que trae la ventana corta.
    antes = LIVE["ventana_antes_s"] if modo == "corto" else LIVE.get("ventana_antes_largo_s", 115)
    print(f"[>] Clip {modo} ({dur['min']}-{dur['max']}s), ventana de {antes:.0f}s hacia atras")

    _, t_pico = montar_ventana(cap, t_video, slug, antes=antes)

    args = argparse.Namespace(slug=slug, n=1, device=device, func=None)
    clipper.cmd_transcribe(args)

    d = WORK / slug
    datos = json.loads((d / "transcript.json").read_text(encoding="utf-8"))
    segs = datos["segments"]
    if not segs:
        print("[x] Sin voz detectada, descarto")
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
        print(f"[x] Solo {fin - ini:.0f}s de dialogo aprovechable "
              f"(minimo {rc['duracion_min_s']}s), descarto sin renderizar")
        return

    dentro = [s for s in segs if ini <= s["start"] < fin]
    gancho = gancho_automatico(dentro, t_pico, chat)
    clip = {
        "id": "01",
        "start": round(ini, 2),
        "end": round(fin, 2),
        "hook": gancho or "ESCRIBE AQUI EL GANCHO",
        "hook_auto": True,
        "title": " ".join(s["text"] for s in dentro)[:90],
        "hashtags": [f"#{canal}", "#clips", "#envivo"],
    }
    (d / "clips.json").write_text(json.dumps({"clips": [clip]}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")

    clipper.cmd_render(argparse.Namespace(slug=slug, only=None, layout=None, func=None))

    mp4 = clipper.OUT / slug / f"{slug}-01.mp4"
    if mp4.exists():
        meta = {"canal": canal, "motivo": motivo, "hook": clip["hook"],
                "duracion": round(clip["end"] - clip["start"])}
        apto, fallos = calidad.evaluar(mp4, clip, segs, limites=(dur["min"], dur["max"]))
        if apto:
            destino = notify.publicar(mp4, meta)
            print(f"[ok] bandeja: {destino}")
        else:
            destino = calidad.apartar(mp4, fallos, meta)
            print(f"[!] NO publicado. Motivos: {'; '.join(fallos)}")
            print(f"    apartado en {destino}")
    print(f"[ok] {slug} listo en {time.time()-t_ini:.0f}s desde el pico")
    print(f"     gancho: {clip['hook']}")


# --- bucle principal ----------------------------------------------------------

def cmd_watch(args):
    url = canal_url(args.canal, args.plataforma)
    print(f"[>] Vigilando {url} (Ctrl+C para parar)")

    while True:
        if not esta_en_directo(url):
            print(f"\r[.] offline, reintento en {LIVE['poll_online_s']}s   ", end="", flush=True)
            time.sleep(LIVE["poll_online_s"])
            continue

        print(f"\n[>] EN DIRECTO. Arrancando captura.")
        cap = Captura(url, BUF / args.canal)
        cap.arrancar()

        eventos = queue.Queue()
        chat = None
        if args.plataforma == "twitch" and not args.solo_audio:
            chat = ChatTwitch(args.canal, eventos)
            chat.start()
            print("[>] Chat IRC conectado")
        else:
            print("[>] Solo audio (sin lectura de chat)")

        hist_chat, hist_audio = collections.deque(maxlen=40), collections.deque(maxlen=40)
        marcas, vistos, ultimo_clip = [], set(), 0.0

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

                print(f"\r[.] {transcurrido/60:5.1f} min | {n_msgs:3d} msg | reaccion {reaccion:4d} "
                      f"(z {z_chat:+.1f}) | 'clip' x{n_clip} | audio z {z_audio:+.1f}   ",
                      end="", flush=True)

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
                    except Exception as e:
                        print(f"\n[x] Fallo procesando: {e}")

                cap.podar()
        except KeyboardInterrupt:
            print("\n[>] Parando.")
            cap.parar()
            if chat:
                chat.parar_flag.set()
            return
        finally:
            cap.parar()
            if chat:
                chat.parar_flag.set()

        print("\n[>] Directo terminado.")


class BufferExistente:
    """Permite clipar de lo que ya hay en disco, sin captura activa."""

    def __init__(self, destino: Path):
        self.destino = destino

    def segmentos(self):
        return sorted(self.destino.glob("*.ts"))

    def podar(self):
        pass


def cmd_now(args):
    """Clipa YA del buffer existente, sin esperar a ningun pico."""
    d = BUF / args.canal
    cap = BufferExistente(d)
    segs = cap.segmentos()
    if len(segs) < 8:
        sys.exit(f"[x] Buffer insuficiente en {d} ({len(segs)} segmentos)")

    seg = LIVE["segmento_s"]
    ultimo = int(segs[-1].stem)
    print(f"[>] Buffer: {len(segs)} segmentos (~{len(segs)*seg/60:.1f} min)")

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
