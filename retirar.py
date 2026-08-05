"""Retira un clip de la bandeja, del indice y de las huellas, a la vez.

Borrarlo solo de IA\\clips no sirve: LISTOS es la bandeja de verdad y de ahi
se resincroniza. Con el 087 lo hice a medias y el clip cortado seguia vivo.
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\Alber\IA\clipper")
import notify  # noqa: E402

if len(sys.argv) < 2:
    sys.exit("uso: retirar.py <prefijo>   (ej. 087)")
PREFIJO = sys.argv[1]
BANDEJAS = [notify.LISTOS, Path(r"C:\Users\Alber\IA\clips")]

borrados = []
for carpeta in BANDEJAS:
    for f in sorted(carpeta.glob(f"{PREFIJO}_*")):
        print(f"  borro {f.parent.name}/{f.name}  ({f.stat().st_size // 1024} KB)")
        borrados.append(f.name)
        f.unlink()
if not borrados:
    sys.exit("  no habia nada con ese prefijo")

filas = list(csv.DictReader(notify.INDEX_CSV.open(encoding="utf-8")))
quedan = [f for f in filas if not f.get("archivo", "").startswith(f"{PREFIJO}_")]
with notify.INDEX_CSV.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=notify.COLUMNAS_INDICE, extrasaction="ignore")
    w.writeheader()
    w.writerows(quedan)
print(f"\nindice: {len(filas)} -> {len(quedan)} filas")

h = json.loads(notify.HUELLAS.read_text(encoding="utf-8"))
for n in list(h):
    if n.startswith(f"{PREFIJO}_"):
        del h[n]
notify.HUELLAS.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")
notify._regenerar_html()
print(f"huellas vivas: {len(h)}; galeria regenerada")
