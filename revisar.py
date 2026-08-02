"""
Lo que espera gancho, listo para escribirlo de una pasada.

    python revisar.py              # que hay, con lo que se dice en cada uno
    python revisar.py elxokas      # solo de ese canal
    python revisar.py --mosaicos   # ademas genera el contact sheet de cada uno

Desde que `publicar_con_gancho_automatico` esta en false, un clip sin gancho no
se publica: se queda en out/REVISAR. Eso sube la calidad pero deja la cola
parada si nadie la mira, y de madrugada es cuando emiten los creadores grandes.
Esto es para vaciarla rapido.

Cuando tengas el gancho:

    python pendientes.py <slug> "gancho" "descripcion" [tag1,tag2]
"""

import csv
import re
import sys
from pathlib import Path

import clipper

REVISAR = clipper.OUT / "REVISAR"


def _ya_publicados() -> set:
    """Slugs que ya salieron a la bandeja.

    El clip se queda en REVISAR aunque despues se publique a mano, asi que sin
    esto la cola muestra trabajo hecho y se relee lo mismo cada vez.
    """
    idx = clipper.OUT / "LISTOS" / "index.csv"
    if not idx.exists():
        return set()
    hechos = set()
    with idx.open(encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            for campo in ("slug", "motivo"):
                if v := (fila.get(campo) or "").strip():
                    hechos.add(v)
    return hechos


def _motivos(mp4: Path) -> list[str]:
    f = mp4.with_name(mp4.stem + ".motivos.txt")
    if not f.exists():
        return []
    return [l[2:].strip() for l in f.read_text(encoding="utf-8").splitlines()
            if l.startswith("- ")]


def _slug(mp4: Path) -> str:
    """De 'elxokas-024315-01.mp4' saca 'elxokas-024315'."""
    return re.sub(r"-\d{2}$", "", mp4.stem)


def _dialogo(slug: str, tope: int = 14) -> list[str]:
    tr = clipper.WORK / slug / "transcript.txt"
    if not tr.exists():
        return []
    lineas = [l.split("]", 1)[1].strip() for l in tr.read_text(encoding="utf-8").splitlines()
              if "]" in l]
    # Las lineas mas largas son las que llevan el contenido; las cortas suelen
    # ser muletillas ("ya", "vale", "bro") y no ayudan a decidir.
    largas = sorted(lineas, key=len, reverse=True)[:tope]
    return [l for l in lineas if l in largas]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    con_mosaico = "--mosaicos" in sys.argv
    filtro = args[0] if args else None

    if not REVISAR.exists():
        sys.exit("[.] No hay carpeta REVISAR")

    hechos = _ya_publicados()
    esperando = []
    for mp4 in sorted(REVISAR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        if filtro and not mp4.name.startswith(filtro):
            continue
        if _slug(mp4) in hechos:
            continue
        motivos = _motivos(mp4)
        # Solo los que fallan por el gancho: el resto tiene algo que arreglar
        # antes, y no vale la pena escribirles un titular.
        if motivos and all("gancho" in m for m in motivos):
            esperando.append((mp4, motivos))

    if not esperando:
        print("[.] Nada esperando gancho" + (f" en {filtro}" if filtro else ""))
        return

    print(f"{len(esperando)} clips esperando gancho\n")
    for mp4, _ in esperando:
        slug = _slug(mp4)
        canal = re.split(r"-\d{6}$", slug)[0]
        mb = mp4.stat().st_size / 1024 / 1024
        ficha = next((c for c in clipper.CONFIG["canales"]
                      if c.get("canal") == canal), {})
        estilo = ficha.get("estilo_gancho", "noticia")
        jerga = ", ".join(ficha.get("jerga", [])[:6])

        print("=" * 74)
        print(f"{slug}   {mb:.1f} MB   estilo: {estilo}")
        if jerga:
            print(f"  jerga del canal: {jerga}")
        for linea in _dialogo(slug):
            print(f"    {linea[:96]}")
        if con_mosaico:
            r = clipper.mosaico(slug)
            print(f"  mosaico: {r}" if r else "  (sin mosaico)")
        print()

    print("=" * 74)
    print('Para publicar:  python pendientes.py <slug> "gancho" "descripcion" '
          '[tag1,tag2]')
    print("Los dos estilos de gancho estan en ESTILO.md.")


if __name__ == "__main__":
    main()
