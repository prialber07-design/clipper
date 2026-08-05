"""Transcripcion continua y barata de todo el directo.

Pasada 1 de 2. Aqui NO se busca precision: se busca saber de que se habla en
cada minuto de directo, para poder elegir despues los momentos con criterio en
vez de por picos de chat. Los subtitulos definitivos se hacen luego, con el
modelo bueno y solo sobre el tramo elegido.

    python escucha.py                      # todos los canales con buffer vivo
    python escucha.py --canales elxokas    # solo uno
    python escucha.py --una-vuelta         # procesa lo pendiente y sale

Por que `base` y no otro. Medido en esta maquina sobre 120s de directo real:

    modelo            transcribe   x tiempo real
    tiny                   14.2s          8.4x
    base                    9.1s         13.2x
    small                  63.2s          1.9x
    large-v3-turbo         23.0s          5.2x

`base` es el mas rapido Y suficiente: escribe «cliques» por «clics» y «me lejan
marca» por «me dejan marca», errores que no cambian de que se esta hablando.
Con 13x hay margen de sobra para seguir a 5 canales en directo a la vez.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import clipper
from clipper import DATA, FFMPEG
from registro import obtener

LOG = obtener("escucha")

SEGMENTOS_POR_TANDA = 6      # ~60s: bastante contexto sin cargar el modelo a cada rato
MODELO = "base"
ESPERA_SIN_TRABAJO = 20.0


def _carpeta_salida(canal: str) -> Path:
    return DATA / "transcripcion" / canal


def _fichero(canal: str) -> Path:
    return _carpeta_salida(canal) / f"{time.strftime('%Y-%m-%d')}.jsonl"


def _marcador(canal: str) -> Path:
    """Ultimo segmento ya transcrito, para no repetir trabajo al reiniciar."""
    return _carpeta_salida(canal) / ".ultimo"


def _leer_marcador(canal: str) -> str:
    try:
        return _marcador(canal).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _guardar_marcador(canal: str, nombre: str):
    p = _marcador(canal)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(nombre, encoding="utf-8")


def pendientes(canal: str, buffer: Path) -> list[Path]:
    """Segmentos del buffer que aun no se han transcrito.

    Se deja fuera el ultimo: ffmpeg lo esta escribiendo en ese momento.
    """
    d = buffer / canal
    if not d.is_dir():
        return []
    segs = sorted(d.glob("*.ts"), key=lambda p: p.stat().st_mtime)[:-1]
    ultimo = _leer_marcador(canal)
    if ultimo:
        nombres = [s.name for s in segs]
        if ultimo in nombres:
            segs = segs[nombres.index(ultimo) + 1:]
    return segs


def _audio_de(segs: list[Path], destino: Path) -> bool:
    lista = destino.with_suffix(".txt")
    lista.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs),
                     encoding="utf-8")
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat",
                        "-safe", "0", "-i", str(lista), "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", str(destino)],
                       capture_output=True)
    lista.unlink(missing_ok=True)
    return r.returncode == 0 and destino.exists()


def transcribir_tanda(modelo, canal: str, segs: list[Path], tmp: Path) -> int:
    """Transcribe una tanda y la añade al diario. Devuelve lineas escritas."""
    if not _audio_de(segs, tmp):
        LOG.warning("⚠️ NO SE PUDO EXTRAER AUDIO\n   CANAL: %s\n   SEGMENTOS: %d",
                    canal, len(segs))
        return 0

    # La marca de tiempo del primer segmento marca el inicio real: el mtime es
    # cuando ffmpeg lo cerro, o sea el final de esos 10 segundos.
    inicio = segs[0].stat().st_mtime - 10.0

    trozos, _ = modelo.transcribe(str(tmp), language="es",
                                  word_timestamps=False, vad_filter=True)
    lineas = []
    for t in trozos:
        texto = t.text.strip()
        if texto:
            lineas.append(json.dumps(
                {"t": round(inicio + t.start, 2),
                 "d": round(t.end - t.start, 2),
                 "m": texto}, ensure_ascii=False))

    if lineas:
        f = _fichero(canal)
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lineas) + "\n")
    _guardar_marcador(canal, segs[-1].name)
    tmp.unlink(missing_ok=True)
    return len(lineas)


def canales_con_buffer(buffer: Path) -> list[str]:
    if not buffer.is_dir():
        return []
    return sorted(d.name for d in buffer.iterdir()
                  if d.is_dir() and any(d.glob("*.ts")))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canales", default="", help="separados por comas")
    ap.add_argument("--modelo", default=MODELO)
    ap.add_argument("--buffer", default="", help="carpeta de buffers")
    ap.add_argument("--una-vuelta", action="store_true")
    args = ap.parse_args()

    buffer = Path(args.buffer) if args.buffer else clipper.ROOT / "buffer"
    from faster_whisper import WhisperModel
    LOG.info("🧠 CARGANDO MODELO\n   MODELO: %s", args.modelo)
    modelo = WhisperModel(args.modelo, device="cpu", compute_type="int8")

    tmp = DATA / "escucha.wav"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    while True:
        lista = ([c.strip() for c in args.canales.split(",") if c.strip()]
                 or canales_con_buffer(buffer))
        trabajo = False
        for canal in lista:
            segs = pendientes(canal, buffer)
            while len(segs) >= SEGMENTOS_POR_TANDA:
                tanda, segs = segs[:SEGMENTOS_POR_TANDA], segs[SEGMENTOS_POR_TANDA:]
                t0 = time.monotonic()
                n = transcribir_tanda(modelo, canal, tanda, tmp)
                total += n
                trabajo = True
                LOG.info("📝 TRANSCRITO\n   CANAL: %s\n   TRAMO: %ds\n"
                         "   FRASES: %d\n   TARDA: %.1fs",
                         canal, len(tanda) * 10, n, time.monotonic() - t0)
        if args.una_vuelta:
            break
        if not trabajo:
            time.sleep(ESPERA_SIN_TRABAJO)

    LOG.info("✅ ESCUCHA TERMINADA\n   FRASES: %d", total)


if __name__ == "__main__":
    sys.exit(main())
