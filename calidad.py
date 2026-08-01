"""
Filtro de calidad: decide si un clip esta para publicar o para revisar.

Un clip tecnicamente correcto puede ser malo igualmente: 30 segundos de silencio,
audio inaudible, pantalla en negro o un gancho vacio. Publicarlos quema la cuenta
y el algoritmo lo nota. Aqui se comprueba antes de que salga.
"""

import json
import re
import subprocess
from pathlib import Path

import clipper
from clipper import CONFIG


def _volumen_medio(mp4: Path) -> float:
    """dB medios. Silencio total ronda -91; una voz normal, entre -30 y -14."""
    p = subprocess.run(
        [clipper.FFMPEG, "-hide_banner", "-i", str(mp4), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **clipper.SIN_VENTANA)
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", p.stderr or "")
    return float(m.group(1)) if m else -99.0


def _negro_segundos(mp4: Path) -> float:
    """Segundos totales en negro (cortes publicitarios, transiciones muertas)."""
    p = subprocess.run(
        [clipper.FFMPEG, "-hide_banner", "-i", str(mp4),
         "-vf", "blackdetect=d=0.5:pix_th=0.10", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **clipper.SIN_VENTANA)
    return sum(float(x) for x in re.findall(r"black_duration:(\d+(?:\.\d+)?)", p.stderr or ""))


# Un gancho que arranca con muletilla no engancha: es como empezar a hablar a
# mitad de frase. Es el fallo que mas veces ha soltado el extractor automatico.
# Ojo: 'si' condicional ("Si falla, se acabo") es un gancho fuerte; el que sobra
# es el 'si' de asentimiento, que siempre lleva coma detras.
MULETILLA = re.compile(
    r"^\s*(s[ií]\s*,|no\s*,|bueno\b|o sea|pues\b|y\s|que\s|es que|claro\b|vale\b|"
    r"a ver|entonces\b|igualmente\b|de hecho|total\b)", re.IGNORECASE)

# Estructura condicional: promete una consecuencia sin desvelarla.
CONDICIONAL = re.compile(r"^\s*(si|cuando|como)\b.{10,},", re.IGNORECASE)

# Giro o contraste ("Perdian la pelea. Acabo en Ace"): la vuelta de tuerca es
# de las estructuras que mejor retienen, y no lleva cifra ni interrogacion.
REVERSO = re.compile(
    r"\b(acab[oó]|termin[oó]|result[oó]|y a[uú]n as[ií]|sin embargo|pero al final|"
    r"de repente|al final|remontad|se dio la vuelta|de la nada)\b", re.IGNORECASE)

# Señales de que el gancho promete algo: cifra, pregunta, exclamacion o carga.
FUERZA = re.compile(
    r"(\d|[?¿!¡]|\bnunca\b|\bjam[aá]s\b|\bnadie\b|\btodo el mundo\b|\bincre[ií]ble\b|"
    r"\bbrutal\b|\bimposible\b|\bperd[ií]\b|\bgan[eé]\b|\beuros?\b|\bmillon\b|"
    r"\bmejor\b|\bpeor\b|\bprimera vez\b|\bse acab[oó]\b|\bbronca\b|\berror\b|"
    r"\bfall[oó]\b|\bnadie sabe\b|\bharto\b|\bcansado\b)", re.IGNORECASE)


def _gancho_flojo(hook: str, tiene_tema: bool = False) -> str | None:
    """`tiene_tema` = la frase nombra algo que su comunidad clipea siempre
    (Iratxe en Lopezfnx, un gol en La Cobra). Eso ya es promesa suficiente,
    aunque la frase no lleve cifra ni interrogacion."""
    q = CONFIG.get("calidad", {})
    palabras = hook.split()

    if MULETILLA.match(hook):
        return "el gancho arranca con muletilla"
    if len(palabras) < 4:
        return "gancho de menos de 4 palabras"
    # Empezar en minuscula delata que la frase venia cortada por la mitad.
    # Ojo con '¿': "¿sabes que..." abre interrogacion pero sigue siendo un
    # trozo suelto, asi que la primera letra real tambien tiene que ir en alta.
    if not re.match(r"^[¿¡]?[A-ZÁÉÍÓÚÑ0-9]", hook.strip()):
        return "el gancho empieza a media frase (minuscula inicial)"

    # Un gancho se lee de un vistazo en un movil. Pasado de ahi ya no engancha,
    # y ademas suele ser señal de que la frase venia partida de la transcripcion.
    tope = q.get("hook_max_palabras", 12)
    if len(palabras) > tope:
        return f"gancho demasiado largo ({len(palabras)} palabras, maximo {tope})"
    if hook.count(",") >= 3:
        return "gancho enredado (tres o mas comas)"
    if re.search(r"[,;:]\s*$|\b(que|de|en|con|por|para|y|o|como|cuanto|cu[aá]nto)\s*$",
                 hook, re.IGNORECASE):
        return "el gancho se corta a media frase"
    if not (tiene_tema or FUERZA.search(hook) or CONDICIONAL.match(hook)
            or REVERSO.search(hook)):
        return "el gancho no promete nada (sin cifra, pregunta ni carga)"
    return None


def evaluar(mp4: Path, clip: dict, segmentos: list,
            limites: tuple[float, float] | None = None) -> tuple[bool, list[str]]:
    """Devuelve (apto, motivos). Motivos vacio = listo para publicar.

    `limites` llega desde fuera a proposito: el modo largo cambia la duracion
    objetivo en memoria, y cualquier recarga de config.json la pisaria. Quien
    decide la duracion es quien la pasa aqui, no el fichero.
    """
    rc = CONFIG["render"]
    q = CONFIG.get("calidad", {})
    fallos = []

    dur_min, dur_max = limites or (rc["duracion_min_s"], rc["duracion_max_s"])
    duracion = clip["end"] - clip["start"]
    if not (dur_min - 2 <= duracion <= dur_max + 2):
        fallos.append(f"duracion fuera de rango ({duracion:.0f}s, "
                      f"esperado {dur_min:.0f}-{dur_max:.0f}s)")

    dentro = [s for s in segmentos if clip["start"] <= s["start"] < clip["end"]]
    palabras = sum(len(s["text"].split()) for s in dentro)
    por_seg = palabras / max(duracion, 1)
    if por_seg < q.get("palabras_por_segundo_min", 1.2):
        fallos.append(f"poco dialogo ({palabras} palabras, {por_seg:.1f}/s)")

    hook = (clip.get("hook") or "").strip()
    # El extractor automatico acierta el momento pero no el gancho: de 10
    # candidatos, dos pasaron todos los controles de forma y aun asi no valian.
    # Un gancho extraido a maquina no se publica solo; se revisa.
    if clip.get("hook_auto") and not q.get("publicar_con_gancho_automatico", False):
        fallos.append("gancho automatico: hay que escribirlo antes de publicar")

    if not hook or hook.startswith("ESCRIBE"):
        fallos.append("sin gancho")
    elif len(hook) < q.get("hook_min_chars", 18):
        fallos.append(f"gancho demasiado corto ({len(hook)} caracteres)")
    else:
        flojo = _gancho_flojo(hook)
        if flojo:
            fallos.append(flojo)

    vol = _volumen_medio(mp4)
    if vol < q.get("volumen_min_db", -40):
        fallos.append(f"audio casi inaudible ({vol:.0f} dB)")

    negro = _negro_segundos(mp4)
    if negro > q.get("negro_max_s", 2.0):
        fallos.append(f"{negro:.1f}s de pantalla en negro")

    return (not fallos), fallos


def apartar(mp4: Path, motivos: list[str], meta: dict) -> Path:
    """Manda el clip a REVISAR con el porque, en vez de publicarlo."""
    destino_dir = clipper.OUT / "REVISAR"
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / mp4.name
    destino.write_bytes(mp4.read_bytes())
    (destino.with_suffix(".motivos.txt")).write_text(
        "NO PUBLICAR. Motivos:\n- " + "\n- ".join(motivos) +
        f"\n\ngancho: {meta.get('hook','')}\ncanal: {meta.get('canal','')}\n",
        encoding="utf-8")
    return destino
