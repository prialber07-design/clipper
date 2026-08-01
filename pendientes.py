"""
Clips de IRL esperando gancho visual.

    python pendientes.py              # que hay pendiente
    python pendientes.py <slug> "El gancho que sea"   # se lo pone y publica

El detector acierta el momento, pero en un IRL lo que engancha se ve, no se
oye. Estos clips esperan a que alguien mire el contact sheet y escriba el
gancho; despues se renderizan y pasan a la bandeja como cualquier otro.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import clipper
import notify

CARPETA = clipper.OUT / "PENDIENTE_GANCHO"


def listar():
    if not CARPETA.exists() or not any(CARPETA.glob("*.mp4")):
        print("[.] Nada pendiente de gancho visual")
        return
    for mp4 in sorted(CARPETA.glob("*.mp4")):
        slug = mp4.stem
        md = CARPETA / f"{slug}.md"
        dur = ""
        if md.exists():
            if m := re.search(r"·\s*(\d+)s", md.read_text(encoding="utf-8")):
                dur = f"{m.group(1)}s"
        hoja = "mosaico OK" if (CARPETA / f"{slug}_mosaico.png").exists() else "sin mosaico"
        print(f"{slug:24s} {dur:>5s}  {hoja}")
    print(f"\nMira el mosaico y luego:  python pendientes.py <slug> \"tu gancho\"")


def resolver(slug: str, gancho: str, descripcion: str = "", etiquetas=None):
    cj = clipper.WORK / slug / "clips.json"
    if not cj.exists():
        sys.exit(f"[x] No existe {cj}")
    datos = json.loads(cj.read_text(encoding="utf-8"))
    clip = datos["clips"][0]
    clip["hook"] = gancho
    clip["descripcion"] = descripcion
    clip["hook_auto"] = False
    canal = re.split(r"-\d{6}$", slug)[0]

    # Los hashtags de contexto salen de lo que se dice en el clip, no del canal.
    tr = clipper.WORK / slug / "transcript.txt"
    texto = tr.read_text(encoding="utf-8") if tr.exists() else ""
    clip["hashtags"] = clipper.hashtags_para(canal, extra=etiquetas, texto=texto)
    cj.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")

    clipper.cmd_render(argparse.Namespace(slug=slug, only=None, layout=None, func=None))

    mp4 = clipper.OUT / slug / f"{slug}-01.mp4"
    destino = notify.publicar(mp4, {
        "canal": canal, "motivo": slug, "hook": gancho,
        "duracion": round(clip["end"] - clip["start"]),
    })
    for p in CARPETA.glob(f"{slug}*"):
        p.unlink()
    print(f"[ok] {destino.name}")


def main():
    if len(sys.argv) < 2:
        return listar()
    if len(sys.argv) < 3:
        sys.exit('Uso: python pendientes.py <slug> "gancho" "descripcion" [tag1,tag2]\n'
                 'La descripcion NO puede ser el gancho repetido: ver ESTILO.md')
    if len(sys.argv) < 4:
        print("[!] Sin descripcion. Se publica igual, pero el .txt quedara "
              "incompleto: la descripcion es lo que te encuentra en la busqueda.")
    etiquetas = [t.strip() for t in sys.argv[4].split(",")] if len(sys.argv) > 4 else None
    resolver(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "",
             etiquetas)


if __name__ == "__main__":
    main()
