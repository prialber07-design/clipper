"""
Pasa a la bandeja todo lo que haya en REVISAR, de cualquier canal.

    python publicar_todo.py            # todos
    python publicar_todo.py elcalvolol # solo ese canal

Salta el filtro de calidad a proposito: es la valvula manual para cuando quieres
publicar igualmente. Manda un solo aviso al movil, no uno por clip.
"""

import json
import re
import sys
from pathlib import Path

import clipper
import notify

REVISAR = clipper.OUT / "REVISAR"


def main():
    filtro = sys.argv[1] if len(sys.argv) > 1 else None
    if not REVISAR.exists():
        sys.exit("[x] No hay carpeta REVISAR")

    pendientes = sorted(REVISAR.glob("*.mp4"))
    if filtro:
        pendientes = [p for p in pendientes if p.name.startswith(filtro)]
    if not pendientes:
        sys.exit("[.] Nada pendiente en REVISAR")

    publicados = 0
    for mp4 in pendientes:
        canal = re.split(r"[-_]", mp4.name)[0]
        hook, dur = "", ""

        # Los apartados por el filtro conservan el original en out/<slug>/,
        # con su .txt de titulo y hashtags; los movidos a mano ya vienen con el.
        m = re.match(r"(.+?)-(\d{6})-01\.mp4$", mp4.name)
        origen = mp4
        if m:
            slug = f"{m.group(1)}-{m.group(2)}"
            cand = clipper.OUT / slug / f"{slug}-01.mp4"
            if cand.exists():
                origen = cand
            cj = clipper.WORK / slug / "clips.json"
            if cj.exists():
                c = json.loads(cj.read_text(encoding="utf-8"))["clips"][0]
                hook, dur = c.get("hook", ""), round(c["end"] - c["start"])
        else:
            txt = mp4.with_suffix(".txt")
            if txt.exists():
                for linea in txt.read_text(encoding="utf-8").splitlines():
                    if linea.startswith("gancho:"):
                        hook = linea.split(":", 1)[1].strip()

        destino = notify.registrar_listo(origen, {
            "canal": canal, "motivo": "publicado a mano", "hook": hook, "duracion": dur,
        })
        publicados += 1
        print(f"[ok] {destino.name}  <- {mp4.name}")

    print(f"\n{publicados} clips en la bandeja")
    notify.avisar(
        titulo=f"{publicados} clips listos en la bandeja",
        mensaje="Estan en OneDrive/Clips. Revisa el gancho antes de subir.",
    )


if __name__ == "__main__":
    main()
