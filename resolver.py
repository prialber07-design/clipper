"""
Resuelve en que plataforma vive cada canal y si existe.

    python resolver.py "La Cobra" "Davoo Xeneize" ...

Prueba variantes del nombre contra Kick (API publica) y Twitch (GQL publico),
y dice: existe / no existe / en directo. Sin credenciales.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def variantes(nombre: str):
    base = nombre.lower().strip()
    sin_tildes = base.translate(str.maketrans("áéíóúñ", "aeioun"))
    limpio = re.sub(r"[^a-z0-9]", "", sin_tildes)
    guion = re.sub(r"[^a-z0-9]+", "_", sin_tildes).strip("_")
    fuera = re.sub(r"^(el|la|los|las)", "", limpio)
    vistos, salida = set(), []
    for v in (limpio, guion, limpio + "aaa", fuera, limpio + "oficial"):
        if v and v not in vistos:
            vistos.add(v)
            salida.append(v)
    return salida


def kick(slug: str):
    req = urllib.request.Request(f"https://kick.com/api/v2/channels/{slug}", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError):
        return None
    if not d.get("slug"):
        return None
    return {
        "slug": d["slug"],
        "directo": bool(d.get("livestream")),
        "seguidores": d.get("followers_count"),
        "nombre": (d.get("user") or {}).get("username"),
    }


def twitch(slug: str):
    """Existencia y estado desde el HTML del canal.

    La API GQL con hash persistido dejo de responder (devolvia None hasta para
    'ibai'), asi que se lee la pagina: un canal que existe pone su nombre en el
    <title>; si ademas esta emitiendo, el titulo lleva 'Live on Twitch'.
    Twitch no da el numero de seguidores por esta via.
    """
    # Twitch falla de vez en cuando y devuelve HTML sin <title>: sin reintento
    # eso se traduce en un falso "no existe", que es el peor error posible aqui.
    html = ""
    for intento in range(3):
        req = urllib.request.Request(f"https://www.twitch.tv/{slug}", headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "ignore")
            if "<title>" in html or "og:title" in html:
                break
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            pass
        time.sleep(1.5)
    if not html:
        return None

    titulo = None
    for patron in (r'<meta property="og:title" content="([^"]*)"', r"<title>([^<]*)</title>"):
        m = re.search(patron, html)
        if m and m.group(1).strip():
            titulo = m.group(1).strip()
            break
    if not titulo:
        return None

    generico = titulo.lower().startswith("twitch")
    if generico or slug.lower() not in titulo.lower().replace(" ", ""):
        return None

    return {"slug": slug, "directo": "live on twitch" in titulo.lower(),
            "seguidores": None, "nombre": titulo.split(" - ")[0]}


def resolver(nombre: str):
    """Devuelve TODAS las coincidencias, no la primera.

    Los handles cortos suelen estar ocupados por cuentas vacias o suplantadores:
    quedarse con el primer acierto te haria clipear al canal equivocado.
    """
    encontrados = []
    for v in variantes(nombre):
        for plat, fn in (("kick", kick), ("twitch", twitch)):
            r = fn(v)
            if r:
                encontrados.append((plat, r))
    encontrados.sort(key=lambda x: _num(x[1].get("seguidores")), reverse=True)
    return encontrados


def _num(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def main():
    for nombre in sys.argv[1:]:
        hallazgos = resolver(nombre)
        if not hallazgos:
            print(f"{nombre:22s} NO ENCONTRADO (dame el handle o la URL exacta)")
            continue
        for i, (plat, r) in enumerate(hallazgos):
            n = _num(r.get("seguidores"))
            segs = f"{n:>10,} seg" if n else "     ? seg"
            estado = "EN DIRECTO" if r["directo"] else "offline"
            chat = "chat IRC" if plat == "twitch" else "solo audio"
            aviso = ""
            if len(hallazgos) > 1 and i > 0:
                aviso = "  <-- otra cuenta con nombre parecido"
            elif n and n < 5000:
                aviso = "  <-- OJO: muy pocos seguidores, puede no ser el real"
            etiqueta = nombre if i == 0 else ""
            print(f"{etiqueta:22s} {plat:7s} {r['slug']:22s} {estado:11s} "
                  f"{chat:11s} {segs}{aviso}")


if __name__ == "__main__":
    main()
