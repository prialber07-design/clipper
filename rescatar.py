"""rescatar.py <canal> <slug_nuevo> [segundos]

Monta un trozo reciente del buffer en directo como si fuera un clip, para ir a
buscar un momento que se corto por el borde de la ventana. El buffer guarda los
ultimos ~15 min, asi que da tiempo si se hace pronto.
"""
import subprocess
import sys

sys.path.insert(0, r"C:\Users\Alber\IA\clipper")
import clipper  # noqa: E402

canal = sys.argv[1]
slug = sys.argv[2]
segundos = int(sys.argv[3]) if len(sys.argv) > 3 else 180

BUF = clipper.ROOT / "buffer" / canal
n = max(2, segundos // 10)
segs = sorted(BUF.glob("*.ts"), key=lambda p: p.stat().st_mtime)[-(n + 1):-1]
if not segs:
    sys.exit(f"[x] no hay segmentos en {BUF}")

d = clipper.WORK / slug
d.mkdir(parents=True, exist_ok=True)
lista = d / "segmentos.txt"
lista.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
print(f"{len(segs)} segmentos (~{len(segs) * 10}s)")

FF = clipper._find("ffmpeg")
fuente = d / "source.mp4"
subprocess.run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                "-i", str(lista), "-c", "copy", str(fuente)], check=True)
subprocess.run([FF, "-y", "-loglevel", "error", "-i", str(fuente), "-vn",
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                str(d / "audio.wav")], check=True)
print(f"fuente lista: {fuente.stat().st_size // 1024 // 1024} MB   slug = {slug}")
