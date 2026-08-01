"""
clipper v1 - VOD -> clips verticales 9:16 con subtitulos quemados y gancho.

Flujo:
    python clipper.py fetch <url|archivo> [--slug nombre]
    python clipper.py transcribe <slug>
    python clipper.py render <slug>

Tras 'transcribe' tienes work/<slug>/transcript.txt (para leer y elegir a mano o
con un LLM) y work/<slug>/clips.json ya rellenado con candidatos automaticos.
Edita clips.json (start/end/hook/title/hashtags) y lanza 'render'.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# En contenedor el codigo es de solo lectura y los datos van a un volumen.
DATA = Path(os.environ.get("CLIPPER_DATA", ROOT))
WORK = DATA / "work"
OUT = DATA / "out"


def _cargar_env():
    """Lee .env si existe.

    config.json va al repositorio y por eso no lleva valores reales; los tuyos
    viven en .env, que esta en .gitignore. Sin esto, ejecutar en local usaria
    los marcadores de ejemplo.
    """
    ruta = ROOT / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        # Lo que ya venga del entorno manda: en Docker gana el compose.
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def _aplicar_entorno(cfg: dict) -> dict:
    """Variables de entorno por encima del fichero.

    En un servidor la configuracion viaja en el compose, no editando un JSON
    dentro de la imagen.
    """
    env = os.environ.get
    mapa = [
        ("CLIPPER_CARPETA_SINCRONIZADA", ("notificaciones", "carpeta_sincronizada"), str),
        ("CLIPPER_NTFY_TOPIC", ("notificaciones", "ntfy_topic"), str),
        ("CLIPPER_NTFY_ACTIVO", ("notificaciones", "activo"), lambda v: v == "1"),
        ("CLIPPER_ADJUNTAR_VIDEO", ("notificaciones", "adjuntar_video"), lambda v: v == "1"),
        ("CLIPPER_MODELO", ("whisper", "modelo"), str),
        ("CLIPPER_COMPUTE", ("whisper", "compute_type"), str),
        ("CLIPPER_MARCA", ("render", "marca"), str),
    ]
    for var, (seccion, clave), conv in mapa:
        valor = env(var)
        if valor is not None:
            cfg.setdefault(seccion, {})[clave] = conv(valor)
    return cfg


_cargar_env()
CONFIG = _aplicar_entorno(json.loads((ROOT / "config.json").read_text(encoding="utf-8")))


def recargar_config():
    """Relee config.json sin reiniciar el proceso.

    El vigilante puede pasar horas en marcha: si no recarga, sigue usando los
    valores que habia al arrancar y cualquier ajuste queda ignorado. Se muta el
    dict en vez de reasignarlo para que las referencias ya importadas sigan
    apuntando al mismo objeto.
    """
    try:
        nuevo = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[!] config.json ilegible ({e}), sigo con los valores actuales")
        return CONFIG
    CONFIG.clear()
    CONFIG.update(_aplicar_entorno(nuevo))
    return CONFIG

# --- localizacion de binarios -------------------------------------------------

def _find(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        local / "Microsoft" / "WinGet" / "Links" / f"{name}.exe",
        Path(sys.prefix) / "Scripts" / f"{name}.exe",
    ]
    candidates += sorted((local / "Microsoft" / "WinGet" / "Packages").glob(f"**/bin/{name}.exe"))
    for c in candidates:
        if c.exists():
            return str(c)
    sys.exit(f"[x] No encuentro '{name}'. Reinicia la terminal o instalalo.")

FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")


def _cargar_dlls_cuda():
    """Las libs CUDA de pip (nvidia-*) no estan en PATH.

    ctranslate2 carga cublas de forma diferida, ya en plena inferencia, y ahi
    add_dll_directory no siempre aplica: hay que meterlas tambien en PATH y
    hacerlo ANTES de que se importe ctranslate2.
    """
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    dirs = [str(d) for d in sorted(base.glob("*/bin")) if d.is_dir()]
    if not dirs:
        return
    os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        for d in dirs:
            try:
                os.add_dll_directory(d)
            except OSError:
                pass


_cargar_dlls_cuda()


# Sin esto, cada llamada a ffmpeg o streamlink abre una ventana de consola en
# Windows. Con 10 canales comprobandose cada 45s son ~13 parpadeos por minuto.
SIN_VENTANA = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def run(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **SIN_VENTANA)
    if proc.returncode != 0:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        sys.exit(f"[x] Fallo: {' '.join(str(c) for c in cmd[:3])}...")
    return proc


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", text)[:60] or "vod"


# --- 1. fetch -----------------------------------------------------------------

def cmd_fetch(args):
    src = args.source
    slug = args.slug or slugify(Path(src).stem if Path(src).exists() else src.rstrip("/").split("/")[-1])
    d = WORK / slug
    d.mkdir(parents=True, exist_ok=True)
    dest = d / "source.mp4"

    if Path(src).exists():
        print(f"[>] Copiando archivo local -> {dest}")
        shutil.copy(src, dest)
    else:
        print(f"[>] Descargando {src}")
        run([sys.executable, "-m", "yt_dlp",
             "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
             "--merge-output-format", "mp4",
             "-o", str(dest), src])

    print(f"[>] Extrayendo audio 16kHz mono")
    run([FFMPEG, "-y", "-i", str(dest), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(d / "audio.wav")])
    print(f"[ok] slug = {slug}")
    return slug


# --- 2. transcribe ------------------------------------------------------------

def cmd_transcribe(args):
    d = WORK / args.slug
    audio = d / "audio.wav"
    if not audio.exists():
        sys.exit(f"[x] No existe {audio}. Ejecuta 'fetch' primero.")

    from faster_whisper import WhisperModel

    wcfg = CONFIG["whisper"]
    print(f"[>] Cargando modelo {wcfg['modelo']} (la primera vez descarga ~1.6GB)")
    device = args.device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"

    def _intento(dev, comp):
        """Carga + inferencia completa. CUDA puede petar en cualquiera de las dos."""
        model = WhisperModel(wcfg["modelo"], device=dev, compute_type=comp)
        print(f"[>] Transcribiendo en {dev}/{comp}...")
        segments, _ = model.transcribe(
            str(audio), language=wcfg["idioma"], word_timestamps=True,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
        )
        segs, words = [], []
        try:
            for s in segments:  # el generador es perezoso: aqui es donde peta CUDA
                segs.append({"start": s.start, "end": s.end, "text": s.text.strip()})
                for w in (s.words or []):
                    words.append({"start": w.start, "end": w.end, "word": w.word.strip()})
                print(f"\r    {s.end/60:5.1f} min transcritos", end="", flush=True)
        except IndexError:
            # Bug de faster-whisper al alinear palabras cuando el tramo no tiene
            # habla (musica, hype, ruido). Se trata como "sin voz", no como error.
            print("\n[!] Tramo sin habla alineable, lo doy por vacio")
            return [], []
        print()
        return segs, words

    if device == "cuda":
        try:
            segs, words = _intento("cuda", "float16")
        except Exception as e:
            print(f"\n[!] CUDA fallo ({str(e).splitlines()[0]}). Reintento en CPU.")
            device = "cpu"
            segs, words = _intento("cpu", wcfg["compute_type"])
    else:
        segs, words = _intento("cpu", wcfg["compute_type"])

    (d / "transcript.json").write_text(
        json.dumps({"segments": segs, "words": words}, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"[{int(s['start'])//60:02d}:{int(s['start'])%60:02d} | {s['start']:.1f}] {s['text']}" for s in segs]
    (d / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")

    energy = _energy_curve(audio)
    (d / "energy.json").write_text(json.dumps(energy), encoding="utf-8")

    clips = _auto_candidates(segs, energy, args.n)
    (d / "clips.json").write_text(json.dumps({"clips": clips}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] {len(segs)} segmentos -> transcript.txt")
    print(f"[ok] {len(clips)} candidatos -> clips.json  (revisa hook/title antes de render)")


def _energy_curve(audio: Path, hop=0.5):
    """RMS por ventana de hop segundos, normalizado a z-score."""
    with wave.open(str(audio), "rb") as w:
        rate, n = w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    import array
    samples = array.array("h")
    samples.frombytes(raw)
    step = int(rate * hop)
    vals = []
    for i in range(0, len(samples) - step, step):
        chunk = samples[i:i + step]
        vals.append(math.sqrt(sum(float(x) * x for x in chunk) / len(chunk)))
    if not vals:
        return {"hop": hop, "z": []}
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    sd = math.sqrt(var) or 1.0
    return {"hop": hop, "z": [round((v - mean) / sd, 3) for v in vals]}


def _auto_candidates(segs, energy, n):
    """Picos de energia -> ventana ajustada a frontera de frase."""
    rc = CONFIG["render"]
    dmin, dmax = rc["duracion_min_s"], rc["duracion_max_s"]
    hop, z = energy["hop"], energy["z"]
    if not z or not segs:
        return []

    peaks = sorted(((val, i * hop) for i, val in enumerate(z)), reverse=True)
    chosen, clips = [], []
    for _, t in peaks:
        if len(clips) >= n:
            break
        if any(abs(t - c) < dmax for c in chosen):
            continue
        raw_start, raw_end = max(0.0, t - 50), t + 15
        start = min((s["start"] for s in segs if s["start"] >= raw_start), default=raw_start)
        end = max((s["end"] for s in segs if s["end"] <= raw_end), default=raw_end)
        if end - start < dmin:
            end = start + dmin
        if end - start > dmax:
            start = end - dmax
        chosen.append(t)
        texto = " ".join(s["text"] for s in segs if start <= s["start"] < end)
        clips.append({
            "id": f"{len(clips)+1:02d}",
            "start": round(start, 2),
            "end": round(end, 2),
            "hook": "ESCRIBE AQUI EL GANCHO",
            "title": texto[:90],
            "hashtags": ["#clips", "#viral", "#shorts"],
            "_preview": texto[:400],
        })
    return sorted(clips, key=lambda c: c["start"])


# --- 3. render ----------------------------------------------------------------

def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _titulo(txt: str) -> str:
    """Capitaliza cada palabra. `str.title()` no vale: rompe las contracciones
    y los acentos ("D'Or", "AÑo")."""
    return " ".join(p[:1].upper() + p[1:] if p else p for p in txt.split(" "))


def _partir_hook(txt: str, max_linea: int = 22) -> str:
    """Reparte el gancho en lineas cortas: en movil una linea larga no se lee de un vistazo."""
    lineas, actual = [], ""
    for palabra in txt.split():
        if actual and len(actual) + 1 + len(palabra) > max_linea:
            lineas.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    if actual:
        lineas.append(actual)
    return r"\N".join(lineas[:3])


def _build_ass(words, clip, path: Path):
    rc = CONFIG["render"]
    start, end = clip["start"], clip["end"]
    hl = rc["color_resaltado"]

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Arial Black,{rc['sub_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,7,4,5,40,40,40,1
Style: Hook,{rc.get('hook_fuente', 'Franklin Gothic Medium')},{rc['hook_size']},&H00101010,&H00101010,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,14,0,5,60,60,60,1
Style: Marca,{rc.get('marca_fuente', 'Corbel')},{rc.get('marca_size', 40)},&H004FA8E8,&H004FA8E8,&H00202020,&H00000000,-1,1,0,0,100,100,0,0,1,3,0,5,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []

    hook = clip.get("hook", "").strip()
    lineas_hook = 0
    if hook and not hook.startswith("ESCRIBE"):
        # Capitalizacion de titulo, no mayusculas: el todo-alta grita, y el
        # titulo se lee como una noticia. Es lo que hacen los canales de clips.
        txt = _partir_hook(_titulo(hook) if rc.get("hook_titulo", True) else hook.upper())
        lineas_hook = txt.count(r"\N") + 1
        # Persistente: el gancho es lo que sostiene la retencion, no solo el arranque.
        fin_hook = (end - start) if rc.get("hook_persistente") else rc["hook_duracion_s"]
        ev.append(f"Dialogue: 1,{_ts(0)},{_ts(fin_hook)},Hook,,0,0,0,,"
                  f"{{\\pos(540,{rc['hook_y']})\\fad(250,0)}}{txt}")

    marca = (rc.get("marca") or "").strip()
    if marca:
        # Justo debajo del gancho forman un bloque; suelta por ahi parece un
        # elemento mas del montaje.
        if lineas_hook and rc.get("marca_bajo_gancho", True):
            alto = rc["hook_size"] * 1.25
            marca_y = int(rc["hook_y"] + (lineas_hook * alto) / 2 + rc.get("marca_size", 40))
        else:
            marca_y = rc.get("marca_y", 1560)
        ev.append(f"Dialogue: 1,{_ts(0)},{_ts(end - start)},Marca,,0,0,0,,"
                  f"{{\\pos(540,{marca_y})}}{marca}")

    inside = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]
    per = rc["palabras_por_bloque"]
    corte = float(rc.get("silencio_corta_bloque_s", 0) or 0)
    retencion = float(rc.get("sub_max_hold_s", 0) or 0)

    # Un bloque no debe cruzar un silencio: si lo hace, el texto se queda en
    # pantalla mientras no habla nadie.
    bloques, actual = [], []
    for w in inside:
        if actual and (len(actual) >= per or
                       (corte and w["start"] - actual[-1]["end"] > corte)):
            bloques.append(actual)
            actual = []
        actual.append(w)
    if actual:
        bloques.append(actual)

    # Dentro de un bloque las palabras van encadenadas sin huecos: el bloque se
    # ve continuo y solo cambia la palabra resaltada. Cortar cada palabra por
    # separado hacia que el texto parpadease.
    eventos = []
    for b, block in enumerate(bloques):
        sig_bloque = bloques[b + 1][0]["start"] - start if b + 1 < len(bloques) else None
        for j, w in enumerate(block):
            w_start = w["start"] - start
            if j + 1 < len(block):
                w_end = block[j + 1]["start"] - start
            else:
                # El bloque se apaga poco despues de la ultima palabra, sin
                # invadir el siguiente: ahi es donde se superponian.
                w_end = w["end"] - start + (retencion or 0.08)
                if sig_bloque is not None:
                    w_end = min(w_end, sig_bloque)
            eventos.append([w_start, w_end, block, j])

    # Red de seguridad: whisper devuelve de vez en cuando palabras que se pisan.
    # Sin esto se dibujan dos lineas de subtitulo a la vez.
    for i in range(len(eventos) - 1):
        eventos[i][1] = min(eventos[i][1], eventos[i + 1][0])

    esc = rc.get("escala_palabra_activa", 100)
    for w_start, w_end, block, j in eventos:
        if w_end - w_start < 0.06:
            continue  # tramo tan corto que solo produciria un parpadeo
        parts = []
        for k, b in enumerate(block):
            token = b["word"].upper().replace("{", "").replace("}", "")
            if k == j:
                # La palabra activa cambia de color Y crece un poco: el ojo la
                # engancha antes que solo con color.
                parts.append(f"{{\\c{hl}\\fscx{esc}\\fscy{esc}}}{token}"
                             f"{{\\c&H00FFFFFF&\\fscx100\\fscy100}}")
            else:
                parts.append(token)
        ev.append(f"Dialogue: 0,{_ts(w_start)},{_ts(w_end)},Sub,,0,0,0,,"
                  f"{{\\pos(540,{rc['sub_y']})}}" + " ".join(parts))

    path.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


FONDO = ("[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
         "crop=1080:1920,gblur=sigma=30,eq=brightness=-0.06[bgb];")


def _vf(layout: str) -> str:
    rc = CONFIG["render"]
    if layout == "crop":
        return ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,setsar=1,ass=subs.ass[v]")
    if layout in ("completo", "blur"):
        # El video entra entero a ancho completo: no se recorta ni un pixel.
        return ("[0:v]split=2[bg][fg];" + FONDO +
                "[fg]scale=1080:-2[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:{rc.get('video_y', 700)},setsar=1,ass=subs.ass[v]")

    if layout == "reaccion":
        return _vf_reaccion(rc)

    if layout == "irl":
        return _vf_irl(rc)

    # zoom: agranda el video y recorta los lados. En una fuente 16:9 el contenido
    # util esta en el centro, asi que ocupa el doble de pantalla y se lee en movil.
    ancho = int(1080 * rc.get("zoom", 1.6) / 2) * 2
    return ("[0:v]split=2[bg][fg];" + FONDO +
            f"[fg]scale={ancho}:-2,crop=1080:ih:(iw-1080)/2:0[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:{rc.get('video_y', 300)},setsar=1,ass=subs.ass[v]")


def _vf_irl(rc: dict) -> str:
    """Una sola camara: sin banda de webcam.

    En un IRL no hay cara que separar del contenido, asi que la unica banda se
    centra en pantalla y el gancho pasa arriba. El video entra entero, sin
    recortar: en la calle la accion puede estar en cualquier parte del encuadre.
    """
    r = rc.get("irl", {})
    margen = int(r.get("margen_px", 24))
    ancho = 1080 - margen * 2
    alto = int(round(ancho * 9 / 16 / 2)) * 2
    video_y = int(r.get("video_y", 680))

    borde = r.get("borde_color", "white@0.22")
    grosor = int(r.get("borde_px", 3))

    return ("[0:v]split=2[bg][fg];" + FONDO +
            f"[fg]scale={ancho}:-2[fgs];"
            f"[bgb][fgs]overlay={margen}:{video_y},"
            f"drawbox=x={margen}:y={video_y}:w={ancho}:h={alto}:"
            f"color={borde}:t={grosor},setsar=1,ass=subs.ass[v]")


def _vf_reaccion(rc: dict) -> str:
    """Dos bandas apiladas: cara arriba, lo que se comenta abajo.

    Es el formato de reaccion clasico del clipping. Funciona porque el ojo tiene
    dos anclas -- expresion y contexto -- en lugar de una franja de video perdida
    entre desenfoque.
    """
    r = rc.get("reaccion", {})
    rect = r.get("cam_rect")
    cam_h = int(r.get("cam_altura", 480))
    cam_y = int(r.get("cam_y", 170))
    cont_y = int(r.get("contenido_y", 880))

    # Las bandas no llegan al borde: un margen constante a los lados es lo que
    # separa un montaje hecho a mano de uno que parece diseñado.
    margen = int(r.get("margen_px", 30))
    ancho = 1080 - margen * 2
    cont_h = int(round(ancho * 9 / 16 / 2)) * 2

    if rect and len(rect) == 4:
        x, y, w, h = (int(v) for v in rect)
        cam = (f"[cam]crop={w}:{h}:{x}:{y},scale={ancho}:-2,"
               f"crop={ancho}:{cam_h}:0:(ih-{cam_h})/2[camv];")
    else:
        cam = f"[cam]scale={ancho}:-2,crop={ancho}:{cam_h}:0:(ih-{cam_h})/2[camv];"

    borde = r.get("borde_color", "white@0.22")
    grosor = int(r.get("borde_px", 3))
    marcos = (f"drawbox=x={margen}:y={cam_y}:w={ancho}:h={cam_h}:"
              f"color={borde}:t={grosor},"
              f"drawbox=x={margen}:y={cont_y}:w={ancho}:h={cont_h}:"
              f"color={borde}:t={grosor}")

    return ("[0:v]split=3[bg][cam][main];" + FONDO + cam +
            f"[main]scale={ancho}:-2[mainv];"
            f"[bgb][camv]overlay={margen}:{cam_y}[t1];"
            f"[t1][mainv]overlay={margen}:{cont_y},"
            f"{marcos},setsar=1,ass=subs.ass[v]")


def mosaico(slug: str, columnas: int = 4, filas: int = 2) -> Path | None:
    """Contact sheet del clip.

    En un IRL el momento es visual: sin mirar los fotogramas no hay forma de
    saber que pasa, porque la transcripcion trae musica y frases sueltas.
    """
    d = WORK / slug
    fuente = d / "source.mp4"
    if not fuente.exists():
        return None
    destino = d / "mosaico.png"
    total = columnas * filas
    try:
        p = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(fuente)])
        dur = float(p.stdout.strip())
    except (SystemExit, ValueError):
        return None
    # Un fotograma cada 1/total de la duracion, repartidos por todo el clip.
    paso = max(1, int(dur * 30 / total))
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(fuente),
         "-vf", f"select='not(mod(n\\,{paso}))',scale=340:-1,tile={columnas}x{filas}",
         "-frames:v", "1", str(destino)])
    return destino if destino.exists() else None


def cmd_mosaico(args):
    r = mosaico(args.slug)
    print(f"[ok] {r}" if r else "[x] No se pudo generar el mosaico")


def cmd_rejilla(args):
    """Frame del original con rejilla de coordenadas, para leer el cam_rect a ojo."""
    d = WORK / args.slug
    fuente = d / "source.mp4"
    if not fuente.exists():
        sys.exit(f"[x] No existe {fuente}")
    destino = d / "rejilla.png"
    dibujo = ("drawgrid=w=iw/8:h=ih/8:t=2:c=red@0.7,"
              "drawtext=text='%{eif\\:floor(n)\\:d}':x=10:y=10:fontsize=1:fontcolor=black")
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-ss", str(args.t), "-i", str(fuente), "-frames:v", "1",
         "-vf", dibujo.split(",drawtext")[0], str(destino)])
    p = run([FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(fuente)])
    ancho, alto = (int(v) for v in p.stdout.strip().split(","))
    print(f"[ok] {destino}")
    print(f"     Fuente {ancho}x{alto}. La rejilla parte en 8x8, cada celda mide "
          f"{ancho//8}x{alto//8} px.")
    print(f"     cam_rect = [x, y, ancho, alto] en pixeles del original.")


def hashtags_para(canal: str, extra=None, texto: str = "") -> list:
    """Etiquetas del clip, no del canal.

    Los canales de clips con traccion usan exactamente 5, siempre con la misma
    estructura: 2 del creador, 2 del contexto de ESE clip y 1 generico. Los del
    contexto son los que hacen que te encuentren; llenarlo de #viral #parati
    #fyp no descubre a nadie porque los pone todo el mundo.
    """
    h = CONFIG.get("hashtags", {})
    tope = int(h.get("maximo", 5))
    canal_tags = (h.get("por_canal") or {}).get(canal, [])[:2]

    # Contexto: lo que se nombra en el clip. Se usa `etiquetas`, no `temas`:
    # los temas incluyen verbos ("colar", "ligar") que detectan bien el gancho
    # pero como hashtag no los busca nadie. Aqui solo van nombres y eventos.
    contexto = list(extra or [])
    if texto:
        bajo = texto.lower()
        ficha = next((c for c in CONFIG.get("canales", []) if c.get("canal") == canal), {})
        for t in ficha.get("etiquetas", []):
            if t.lower() in bajo:
                contexto.append(re.sub(r"[^\wáéíóúñ]", "", t.lower()))

    salida, vistos = [], set()
    for grupo in (canal_tags, contexto, h.get("base", [])):
        for t in grupo:
            t = t if str(t).startswith("#") else f"#{t}"
            if t.lower() not in vistos and len(t) > 2:
                vistos.add(t.lower())
                salida.append(t)
    return salida[:tope]


def _ficha_texto(slug: str, c: dict) -> str:
    """Lo que copias y pegas al subir.

    El gancho de pantalla y la descripcion son piezas distintas: el gancho gana
    los 3 primeros segundos, la descripcion te encuentra en la busqueda. Ver
    ESTILO.md.
    """
    canal = re.split(r"-\d{6}$", slug)[0]
    hook = (c.get("hook") or "").strip()
    desc = (c.get("descripcion") or "").strip()
    tags = " ".join(c.get("hashtags") or hashtags_para(canal))
    dur = c["end"] - c["start"]

    if not desc:
        desc = "(FALTA: escribe una descripcion distinta del gancho, "
        desc += "con nombre propio y verbo con carga. Ver ESTILO.md)"

    return "\n".join([
        "DESCRIPCION PARA EL POST",
        f"{desc}",
        "",
        tags,
        "",
        "---",
        f"gancho en pantalla: {hook}",
        f"duracion: {dur:.0f}s" + ("  (vale para TikTok Creator Rewards)" if dur > 60
                                   else "  (menos de 1 min: no monetiza en TikTok)"),
        f"origen: {slug}  {c['start']:.1f}s - {c['end']:.1f}s",
    ]) + "\n"


def cmd_render(args):
    recargar_config()
    d = WORK / args.slug
    clips_file = d / "clips.json"
    if not clips_file.exists():
        sys.exit(f"[x] No existe {clips_file}. Ejecuta 'transcribe' primero.")
    words = json.loads((d / "transcript.json").read_text(encoding="utf-8"))["words"]
    clips = json.loads(clips_file.read_text(encoding="utf-8"))["clips"]
    if args.only:
        clips = [c for c in clips if c["id"] in args.only.split(",")]

    rc = CONFIG["render"]
    outdir = OUT / args.slug
    outdir.mkdir(parents=True, exist_ok=True)
    tmp = d / "_render"
    tmp.mkdir(exist_ok=True)

    for c in clips:
        print(f"[>] Clip {c['id']}  {c['start']:.1f}s -> {c['end']:.1f}s  ({c['end']-c['start']:.0f}s)")
        _build_ass(words, c, tmp / "subs.ass")
        target = outdir / f"{args.slug}-{c['id']}.mp4"
        run([FFMPEG, "-y",
             "-ss", str(c["start"]), "-to", str(c["end"]), "-i", str(d / "source.mp4"),
             "-filter_complex", _vf(args.layout or rc["layout"]),
             "-map", "[v]", "-map", "0:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", str(rc["crf"]),
             "-pix_fmt", "yuv420p", "-r", str(rc["fps"]),
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
             str(target)], cwd=tmp)

        (outdir / f"{args.slug}-{c['id']}.txt").write_text(
            _ficha_texto(args.slug, c), encoding="utf-8")

    print(f"[ok] {len(clips)} clips en {outdir}")


# --- cli ----------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="clipper v1")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="descarga o copia el VOD y extrae el audio")
    f.add_argument("source")
    f.add_argument("--slug")
    f.set_defaults(func=cmd_fetch)

    t = sub.add_parser("transcribe", help="transcribe y propone candidatos")
    t.add_argument("slug")
    t.add_argument("--n", type=int, default=10, help="numero de candidatos")
    t.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    t.set_defaults(func=cmd_transcribe)

    m = sub.add_parser("mosaico", help="contact sheet del clip, para ver de que va")
    m.add_argument("slug")
    m.set_defaults(func=cmd_mosaico)

    g = sub.add_parser("rejilla", help="saca un frame con rejilla para localizar la webcam")
    g.add_argument("slug")
    g.add_argument("--t", type=float, default=10.0, help="segundo del que sacar el frame")
    g.set_defaults(func=cmd_rejilla)

    r = sub.add_parser("render", help="renderiza los clips de clips.json")
    r.add_argument("slug")
    r.add_argument("--only", help="ids separados por coma, ej: 01,03")
    r.add_argument("--layout", choices=["blur", "crop"])
    r.set_defaults(func=cmd_render)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
