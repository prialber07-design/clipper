"""
Apunta cuantas visitas hizo cada clip y dice que funciona de verdad.

    python resultados.py 047 15200        # el clip 047 lleva 15.200 visitas
    python resultados.py                  # que esta funcionando y que no

Sin esto vamos a ciegas: el estilo de gancho, la duracion y el creador se
eligen por lo que parece razonable, no por lo que rinde. Dos docenas de clips
apuntados ya dicen mas que cualquier teoria.
"""

import csv
import statistics
import sys
from pathlib import Path

import clipper
import notify

INDEX = notify.INDEX_CSV
COLUMNAS = notify.COLUMNAS_INDICE


def _leer():
    if not INDEX.exists():
        sys.exit(f"[x] No existe {INDEX}")
    with INDEX.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _escribir(filas):
    with INDEX.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore")
        w.writeheader()
        for fila in filas:
            fila.setdefault("visitas", "")
            w.writerow(fila)


def apuntar(numero: str, visitas: str):
    numero = numero.zfill(3)
    filas = _leer()
    for fila in filas:
        if (fila.get("n") or "").zfill(3) == numero:
            fila["visitas"] = visitas
            fila["subido"] = "SI"
            _escribir(filas)
            print(f"[ok] {numero}: {int(visitas):,} visitas  ·  "
                  f"{fila.get('gancho', '')[:52]}")
            return
    sys.exit(f"[x] No encuentro el clip {numero}")


def _estilo(canal: str) -> str:
    ficha = next((c for c in clipper.CONFIG.get("canales", [])
                  if c.get("canal") == canal), {})
    return ficha.get("estilo_gancho", "?")


def _resumen(titulo, grupos):
    print(f"\n{titulo}")
    print("-" * 58)
    for clave, valores in sorted(grupos.items(),
                                 key=lambda kv: -statistics.median(kv[1])):
        print(f"  {clave:<22} {len(valores):2d} clips  "
              f"mediana {int(statistics.median(valores)):>8,}  "
              f"mejor {int(max(valores)):>8,}")


def informe():
    filas = [f for f in _leer() if (f.get("visitas") or "").strip().isdigit()]
    total = len(_leer())
    if not filas:
        print(f"[.] Ningun clip tiene visitas apuntadas todavia ({total} en la "
              f"bandeja).\n\n    python resultados.py <numero> <visitas>")
        return

    print(f"{len(filas)} clips con visitas apuntadas, de {total} publicados\n")
    por_visitas = sorted(filas, key=lambda f: -int(f["visitas"]))
    print("Los que mejor han ido:")
    for f in por_visitas[:6]:
        print(f"  {int(f['visitas']):>9,}  {f['canal']:<16} {f.get('gancho','')[:44]}")
    if len(por_visitas) > 6:
        print("\nLos que peor:")
        for f in por_visitas[-3:]:
            print(f"  {int(f['visitas']):>9,}  {f['canal']:<16} {f.get('gancho','')[:44]}")

    estilos, canales, duraciones = {}, {}, {}
    for f in filas:
        v = int(f["visitas"])
        estilos.setdefault(_estilo(f["canal"]), []).append(v)
        canales.setdefault(f["canal"], []).append(v)
        try:
            d = float(f.get("duracion_s") or 0)
        except ValueError:
            d = 0
        tramo = ("menos de 20s" if d < 20 else "20-40s" if d < 40
                 else "40-60s" if d < 60 else "mas de 1 min")
        duraciones.setdefault(tramo, []).append(v)

    # Mediana y no media: un solo clip que se dispare no debe decidir la
    # estrategia de los demas.
    _resumen("Por estilo de gancho", estilos)
    _resumen("Por duracion", duraciones)
    _resumen("Por creador", canales)

    if len(filas) < 8:
        print(f"\n[!] Solo {len(filas)} clips medidos: aun no da para concluir "
              f"nada. Con unos 20 empieza a significar algo.")


def main():
    if len(sys.argv) == 3:
        apuntar(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 1:
        informe()
    else:
        sys.exit("Uso: python resultados.py [<numero> <visitas>]")


if __name__ == "__main__":
    main()
