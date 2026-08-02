"""
Ordena la cola de clips por lo prometedores que son, para no leerla entera.

    python triaje.py            # los mejores candidatos de la cola
    python triaje.py 25         # los 25 mejores
    python triaje.py peereira7  # solo de ese canal

Con seis o siete canales en directo se acumulan mas de cien clips por noche y
leerlos uno a uno no es viable. Esto descarta lo que ya se sabe que no vale y
puntua el resto por las senales que, mirando los clips que si han funcionado,
distinguen un momento con gancho de una charla de relleno.

No decide: ordena. El gancho sigue escribiendose a mano leyendo la
transcripcion, que es donde el sistema falla y una persona no.
"""

import csv
import re
import sys
from pathlib import Path

import calidad
import clipper

REVISAR = clipper.OUT / "REVISAR"


def _ya_publicados() -> set:
    """El mp4 se queda en REVISAR aunque despues se publique a mano."""
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

# Un clip que dice algo suele traer: nombres propios (el algoritmo los indexa y
# la gente los busca), cifras concretas, preguntas del chat que el streamer
# contesta, y verbos de conflicto o revelacion. Una charla de relleno no.
SENALES = [
    (re.compile(r"\b(ibai|velada|xokas|auron|rubius|elcalvo|el calvo|iratxe|"
                r"urko|kata|willy|agustin|peereira|davoo|cobra|samway|"
                r"fortnite|riot|twitch|kick|tiktok|youtube)\b", re.I), 3,
     "nombre propio"),
    (re.compile(r"\b\d+([.,]\d+)?\s*(euros?|€|mil|millones?|kilos?|años?|"
                r"céntimos?|seguidores|views?|visitas)\b", re.I), 3, "cifra"),
    (re.compile(r"\b(nunca|jamás|por primera vez|te juro|os prometo|"
                r"os lo juro|en serio|de verdad que)\b", re.I), 2, "enfasis"),
    (re.compile(r"\b(me han|me ha pasado|resulta que|os cuento|sabéis qué|"
                r"lo que pasó|resulta)\b", re.I), 2, "relato"),
    (re.compile(r"\b(denuncia|demanda|abogado|banear|baneado|expulsa|"
                r"echaron|despedido|estafa|robó|mentira)\b", re.I), 2, "conflicto"),
    (re.compile(r"¿[^?]{10,80}\?"), 1, "pregunta"),
]

# Contenido sexual explicito. No es lenguaje de odio (eso lo veta calidad.py),
# pero las plataformas lo restringen y un clip asi no llega a nadie. Resta en
# vez de vetar: a veces la palabra cae de pasada en un clip que va de otra cosa.
EXPLICITO = re.compile(
    r"\b(follar|follan|follo|follas|pajas?|polla|coño|tetas|culo|"
    r"masturb\w+|orgasmo|porno|nopor)\b", re.I)

# Relleno: si domina esto, el clip no cuenta nada.
RUIDO = re.compile(
    r"\b(gracias por (el|los|ese)|por ese prime|por esos meses|se ha suscrito|"
    r"vale vale|venga va|a ver a ver|cuidado cuidado|dale dale|vamos vamos|"
    r"push|gankeo|cooldown|ulti|farmear|botlane|midlane|jungla|ronda|"
    r"headshot|clutch)\b", re.I)


def _slug(mp4: Path) -> str:
    return re.sub(r"-\d{2}$", "", mp4.stem)


def _lineas(slug: str) -> list[str]:
    tr = clipper.WORK / slug / "transcript.txt"
    if not tr.exists():
        return []
    return [l.split("]", 1)[1].strip()
            for l in tr.read_text(encoding="utf-8").splitlines() if "]" in l]


def puntuar(lineas: list[str]) -> tuple[int, list[str]]:
    texto = " ".join(lineas)
    palabras = len(texto.split())
    if palabras < 40:
        return -1, ["muy poco dialogo"]

    puntos, motivos = 0, []
    for patron, peso, etiqueta in SENALES:
        n = len(patron.findall(texto))
        if n:
            puntos += peso * min(n, 3)
            motivos.append(f"{etiqueta} x{n}")

    ruido = len(RUIDO.findall(texto))
    if ruido:
        puntos -= 2 * min(ruido, 5)
        motivos.append(f"relleno x{ruido}")

    sexo = len(EXPLICITO.findall(texto))
    if sexo:
        puntos -= 4 * min(sexo, 6)
        motivos.append(f"explicito x{sexo}")

    # Frases largas y seguidas = alguien contando algo. Frases de tres palabras
    # sueltas = comentario de partida.
    largas = sum(1 for l in lineas if len(l.split()) >= 9)
    puntos += min(largas, 8)
    if largas >= 5:
        motivos.append(f"{largas} frases largas")

    return puntos, motivos


def main():
    args = [a for a in sys.argv[1:]]
    tope = 20
    filtro = None
    for a in args:
        if a.isdigit():
            tope = int(a)
        else:
            filtro = a

    hechos = _ya_publicados()
    candidatos = []
    for mp4 in REVISAR.glob("*.mp4"):
        if filtro and not mp4.name.startswith(filtro):
            continue
        slug = _slug(mp4)
        if slug in hechos:
            continue
        lineas = _lineas(slug)
        if not lineas:
            continue

        marcado = calidad._lenguaje_marcado([{"text": l} for l in lineas])
        if marcado:
            continue        # ni se mira: no se puede publicar

        puntos, motivos = puntuar(lineas)
        if puntos < 0:
            continue
        candidatos.append((puntos, slug, motivos, lineas, mp4.stat().st_mtime))

    candidatos.sort(key=lambda x: (-x[0], -x[4]))
    print(f"{len(candidatos)} candidatos, los {min(tope, len(candidatos))} mejores:\n")

    for puntos, slug, motivos, lineas, _ in candidatos[:tope]:
        print("=" * 76)
        print(f"{slug}   {puntos} puntos   ({', '.join(motivos)})")
        for l in sorted(lineas, key=len, reverse=True)[:6]:
            print(f"    {l[:98]}")
        print()

    print("=" * 76)
    print('Para publicar:  python pendientes.py <slug> "gancho" "descripcion" '
          '[tag1,tag2]')


if __name__ == "__main__":
    main()
