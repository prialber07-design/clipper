"""Fotogramas temporales para el análisis visual de candidatos RAW."""

import tempfile
from contextlib import contextmanager
from pathlib import Path

import clipper
from registro import obtener


ANCHO = 768
MAX_FOTOGRAMAS = 105
PICO_OFFSETS = (-1.0, -0.5, 0.0, 0.5, 1.0)
LOG = obtener("storyboard")
ESCALA = (f"scale={ANCHO}:432:force_original_aspect_ratio=decrease,"
          f"pad={ANCHO}:432:(ow-iw)/2:(oh-ih)/2")


def _cerca_de_entero(valor: float) -> bool:
    return abs(valor - round(valor)) < 0.2


def _muestrear(items: list, limite: int) -> list:
    if len(items) <= limite:
        return items
    if limite <= 1:
        return items[:limite]
    return [items[round(i * (len(items) - 1) / (limite - 1))]
            for i in range(limite)]


@contextmanager
def extraer(video: Path, duracion: float, pico: float):
    """Entrega `(segundo, jpeg)` ordenados y elimina todo al terminar."""
    if not video.is_file():
        raise FileNotFoundError(f"vídeo RAW ausente: {video}")

    with tempfile.TemporaryDirectory(prefix="clipper-storyboard-") as temporal:
        carpeta = Path(temporal)
        clipper.run([
            clipper.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video), "-vf", f"fps=1,{ESCALA}",
            "-q:v", "5", str(carpeta / "base-%04d.jpg"),
        ])

        base = [
            (float(indice), path)
            for indice, path in enumerate(sorted(carpeta.glob("base-*.jpg")))
        ]
        pico_frames = []

        for indice, segundo in enumerate(float(pico) + d for d in PICO_OFFSETS):
            segundo = min(max(0.0, segundo), max(0.0, float(duracion) - 0.05))
            if _cerca_de_entero(segundo):
                continue
            salida = carpeta / f"pico-{indice:02d}.jpg"
            try:
                clipper.run([
                    clipper.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{segundo:.3f}", "-i", str(video),
                    "-frames:v", "1", "-vf", ESCALA,
                    "-q:v", "5", str(salida),
                ])
            except Exception as error:
                LOG.warning("⚠️ FOTOGRAMA DEL PICO OMITIDO · SEGUNDO: %.2f · MOTIVO: %s",
                            segundo, error)
                continue
            if salida.is_file():
                pico_frames.append((round(segundo, 3), salida))

        fotogramas = _muestrear(base, MAX_FOTOGRAMAS - len(pico_frames))
        fotogramas.extend(pico_frames)
        fotogramas.sort(key=lambda item: item[0])
        if not fotogramas:
            raise RuntimeError("FFmpeg no extrajo ningún fotograma")
        yield fotogramas
