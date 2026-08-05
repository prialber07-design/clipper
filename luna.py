"""Luna elige los momentos del directo, en vez de juzgar los que otro encontro.

Hasta ahora el detector buscaba picos de chat y Luna opinaba sobre lo que el
detector hubiera pillado. Eso tiene un techo: si el pico cae en el sitio
equivocado -o si el momento bueno no genero pico- Luna nunca lo ve. Medido
sobre la version anterior: de 90 clips detectados en una noche, 12 valian.

Aqui se le da el directo entero, ya transcrito barato (escucha.py) y con el
chat fechado (DiarioChat), y se le pide que BUSQUE.

    python luna.py --canal lopezfnx --desde 17:00 --hasta 17:30
    python luna.py --canal lopezfnx --contexto        # solo enseña lo que se le manda
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import clipper
from clipper import DATA
from registro import obtener

LOG = obtener("luna")
TIMEOUT_S = 300


# --- el contexto: una linea por minuto de directo -----------------------------

def _cargar(carpeta: Path, canal: str, dia: str) -> list[dict]:
    f = DATA / carpeta / canal / f"{dia}.jsonl"
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def construir_contexto(canal: str, dia: str, desde: float = 0,
                       hasta: float = 9e9) -> str:
    """Cruza voz y chat por minuto.

    Se agrupa por minuto y no frase a frase porque lo que Luna tiene que ver es
    el ritmo del directo: donde hay conversacion, donde hay silencio y donde el
    chat se enciende. Del chat solo entran los mensajes con peso -risa, sorpresa
    o «clipealo»- porque el resto es saludos y no aporta.
    """
    voz = [x for x in _cargar("transcripcion", canal, dia) if desde <= x["t"] <= hasta]
    chat = [x for x in _cargar("chat", canal, dia) if desde <= x["t"] <= hasta]
    if not voz:
        return ""

    minutos: dict[int, dict] = {}
    for v in voz:
        m = int(v["t"] // 60)
        minutos.setdefault(m, {"voz": [], "chat": [], "n": 0})["voz"].append(v["m"])
    for c in chat:
        m = int(c["t"] // 60)
        d = minutos.setdefault(m, {"voz": [], "chat": [], "n": 0})
        d["n"] += 1
        if c["p"] > 1:
            d["chat"].append(c["m"])

    lineas = []
    for m in sorted(minutos):
        d = minutos[m]
        if not d["voz"]:
            continue
        reloj = time.strftime("%H:%M", time.localtime(m * 60))
        lineas.append(f"[{reloj}] ({m * 60})")
        lineas.append("  VOZ: " + " ".join(d["voz"])[:600])
        if d["n"]:
            reaccion = " · ".join(d["chat"][:4])[:220]
            lineas.append(f"  CHAT: {d['n']} mensajes"
                          + (f" — {reaccion}" if reaccion else ""))
    return "\n".join(lineas)


# --- lo que se le pide --------------------------------------------------------

PROMPT = """Eres Luna, la editora de un canal de clips de streamers en español.

Te doy un directo entero, minuto a minuto: lo que se dice (VOZ) y cómo reacciona
el chat (CHAT, con el número de mensajes y los que llevan risa, sorpresa o piden
clip). El número entre paréntesis es el segundo absoluto: es la referencia que
tienes que devolver.

AVISO SOBRE LA VOZ, importante: está transcrita con un modelo rápido y barato.
Pierde y deforma palabras — dice «cliques» por «clics», «me lejan marca» por «me
dejan marca». Sirve para saber DE QUÉ se habla, no para citar. Así que:

- NO copies frases literales: casi seguro están mal.
- Si un momento parece bueno pero no entiendes lo que dice, márcalo igual con
  confianza baja: luego se vuelve a transcribir bien y se decide.
- Si el texto es ilegible en todo el tramo, es que hay ruido o jerga de juego.
  Eso ya te dice algo: normalmente significa que no hay nada.

TU TRABAJO: encontrar los momentos que merecen un clip. No los que tienen más
chat — el chat se acelera con cualquier cosa del juego. Los que alguien
mandaría a un amigo.

QUÉ FUNCIONA, medido sobre clips reales de millones de visitas:

1. El gancho NO cuenta lo que hace el streamer. Describe algo del que mira. Los
   que más rinden son literalmente «Yo:» o «Yo una semana después de entender la
   indirecta que me tiró». El streamer solo pone la cara. Si no consigues
   escribir el gancho en esa forma, probablemente el momento no vale.
2. Se comparte lo que retrata a alguien conocido. Un titular informativo se ve y
   se olvida; una escena reconocible se reenvía.
3. Los 3 primeros segundos deciden. El clip tiene que abrir por lo más fuerte
   que tenga, no por el contexto. El contexto va en el gancho, que se lee en
   medio segundo.
4. Una historia con planteamiento y remate también vale, aunque no sea un meme,
   si se entiende entera sin saber quién es el streamer.

QUÉ NO VALE, y esto es tajante:
- Comentario de partida, mecánica de juego, lobby, configuración, wifi, pedir
  que se unan a un grupo.
- Agradecer suscripciones o leer mensajes del chat en cadena.
- Insultos como contenido, y CUALQUIER momento con «retrasado», «subnormal»,
  «mongolo», «down», insultos racistas, homófobos o sexuales explícitos.
- Anuncios de lo que hará mañana. Promesas sin entrega.
- Momentos que necesitan 20 segundos de contexto para entenderse.

CÓMO CORTAR:
- Empieza en principio de frase, en la frase que ya es interesante por sí sola.
- Termina en cuanto se remata. Nada detrás.
- Dura lo que tenga que durar: hay clips de 8 segundos y de 70. Lo que no puede
  haber es relleno.

Devuelve SOLO JSON con esta forma:

{"momentos": [{"inicio": 1234, "fin": 1251, "gancho_propuesto": "...",
  "de_que_va": "...", "porque": "...", "confianza": 0.0}]}

- inicio/fin en segundos absolutos, como los paréntesis del contexto. Generoso
  por los dos lados: el corte fino se hace después, con la transcripción buena.
- gancho_propuesto: la DIRECCIÓN del gancho, no el texto definitivo — ese se
  escribe luego, cuando haya palabras fiables. Máximo 8 palabras, en minúscula,
  en la forma del punto 1.
- de_que_va: qué pasa ahí, con tus palabras. No cites.
- porque: por qué alguien se lo mandaría a un amigo. Sé concreto.
- confianza: 0 a 1. Sé duro. Si en todo el directo no hay nada, devuelve la
  lista vacía: es una respuesta correcta y frecuente.

Devuelve como mucho 5 momentos, ordenados de mejor a peor."""


ESQUEMA = {
    "type": "object",
    "properties": {
        "momentos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "inicio": {"type": "number"},
                    "fin": {"type": "number"},
                    "gancho_propuesto": {"type": "string"},
                    "de_que_va": {"type": "string"},
                    "porque": {"type": "string"},
                    "confianza": {"type": "number"},
                },
                "required": ["inicio", "fin", "gancho_propuesto", "de_que_va",
                             "porque", "confianza"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["momentos"],
    "additionalProperties": False,
}


def elegir(contexto: str, modelo: str = "") -> dict:
    """Llama a Codex CLI con la sesion OAuth, igual que el analisis visual."""
    with tempfile.TemporaryDirectory(prefix="luna-") as tmp:
        carpeta = Path(tmp)
        esquema = carpeta / "esquema.json"
        esquema.write_text(json.dumps(ESQUEMA), encoding="utf-8")
        salida = carpeta / "salida.json"

        cmd = ["codex", "exec", "--ephemeral", "--sandbox", "read-only",
               "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
               "--output-schema", str(esquema), "-o", str(salida)]
        if modelo:
            cmd.extend(["--model", modelo])
        cmd.append("-")

        entorno = {k: v for k, v in os.environ.items()
                   if k not in {"OPENAI_API_KEY", "CODEX_API_KEY"}}
        try:
            proc = subprocess.run(
                cmd, cwd=carpeta, input=f"{PROMPT}\n\nDIRECTO:\n{contexto}",
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=TIMEOUT_S, env=entorno)
        except FileNotFoundError as e:
            raise RuntimeError("CODEX_CLI_AUSENTE") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("CODEX_TIMEOUT") from e
        if proc.returncode != 0:
            raise RuntimeError(f"CODEX_ERROR: {proc.stderr[-300:]}")
        return json.loads(salida.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canal", required=True)
    ap.add_argument("--dia", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--desde", default="", help="HH:MM")
    ap.add_argument("--hasta", default="", help="HH:MM")
    ap.add_argument("--contexto", action="store_true",
                    help="solo enseña lo que se le mandaria a Luna")
    ap.add_argument("--modelo", default="")
    args = ap.parse_args()

    def _seg(hhmm: str, porDefecto: float) -> float:
        if not hhmm:
            return porDefecto
        h, m = hhmm.split(":")
        base = time.strptime(args.dia, "%Y-%m-%d")
        t = list(base)
        t[3], t[4] = int(h), int(m)
        return time.mktime(tuple(t))

    contexto = construir_contexto(args.canal, args.dia,
                                  _seg(args.desde, 0), _seg(args.hasta, 9e9))
    if not contexto:
        sys.exit(f"[x] no hay transcripcion de {args.canal} el {args.dia}")

    if args.contexto:
        print(contexto)
        print(f"\n--- {len(contexto)} caracteres, "
              f"~{len(contexto) // 4} tokens aproximados")
        return

    r = elegir(contexto, args.modelo)
    for m in r.get("momentos", []):
        reloj = time.strftime("%H:%M:%S", time.localtime(m["inicio"]))
        print(f"\n[{m['confianza']:.2f}] {reloj}  ({m['fin'] - m['inicio']:.0f}s)")
        print(f"  gancho:  {m['gancho_propuesto']}")
        print(f"  va de:   {m['de_que_va']}")
        print(f"  porque:  {m['porque']}")
    if not r.get("momentos"):
        print("Luna no ha encontrado nada publicable en ese tramo.")


if __name__ == "__main__":
    sys.exit(main())
