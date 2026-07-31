"""
Balance del detector: cuantos picos, cuantos clips y por que se descarta.

    python balance.py            # todo el historial de logs
    python balance.py elcalvolol # solo ese canal

Sirve para decidir con datos si el liston de calidad esta bien puesto, en vez
de a ojo.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import clipper

LOGS = clipper.DATA / "logs"
RE_PICO = re.compile(r"\[!\] PICO \(([^)]+)\)")
RE_MOTIVOS = re.compile(r"NO publicado\. Motivos: (.+)")
RE_FLOJO = re.compile(r"demasiado flojo \(([\d.]+)\)")
RE_PUB = re.compile(r"bandeja: .*[\\/]([^\\/]+)$")


def main():
    filtro = sys.argv[1] if len(sys.argv) > 1 else None
    if not LOGS.exists():
        sys.exit("[x] No hay logs todavia")

    picos, motivos, puntos = Counter(), Counter(), []
    publicados, por_canal = [], Counter()

    for log in sorted(LOGS.glob("*.log")):
        canal = log.stem.split(".")[0]
        if filtro and canal != filtro:
            continue
        for linea in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := RE_PICO.search(linea):
                picos[m.group(1)] += 1
                por_canal[canal] += 1
            if m := RE_MOTIVOS.search(linea):
                for motivo in m.group(1).split(";"):
                    motivos[re.sub(r"\s*\(.*", "", motivo).strip()] += 1
            if m := RE_FLOJO.search(linea):
                puntos.append(float(m.group(1)))
            if m := RE_PUB.search(linea.strip()):
                publicados.append(m.group(1))

    total = sum(picos.values())
    print(f"PICOS: {total}   PUBLICADOS: {len(publicados)}"
          f"   ({len(publicados) / total * 100:.0f}%)" if total else "Sin picos aun")
    if not total:
        return

    print("\nPor que dispara:")
    for k, v in picos.most_common():
        print(f"  {v:4d}  {k}")

    print("\nPor canal:")
    for k, v in por_canal.most_common():
        print(f"  {v:4d}  {k}")

    if motivos:
        print("\nPor que se descarta:")
        for k, v in motivos.most_common():
            print(f"  {v:4d}  {k}")

    if puntos:
        puntos.sort()
        minimo = float(clipper.CONFIG.get("calidad", {}).get("gancho_puntuacion_minima", 3))
        cerca = sum(1 for p in puntos if minimo - 1 <= p < minimo)
        print(f"\nGanchos rechazados por flojos: {len(puntos)}")
        print(f"  puntuacion mediana: {puntos[len(puntos) // 2]:.1f}  (liston: {minimo})")
        print(f"  a menos de 1 punto del liston: {cerca}")
        if cerca > len(puntos) / 2:
            print("  -> el liston esta rozando: bajarlo daria bastantes clips mas")
        elif puntos[len(puntos) // 2] < minimo - 1.5:
            print("  -> los ganchos son flojos de verdad, no es cosa del liston")


if __name__ == "__main__":
    main()
