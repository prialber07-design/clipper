"""
Recoge que esta clipeando la comunidad de cada canal.

    python investigar.py            # todos
    python investigar.py rdjavi     # uno

Escribe `investigacion/<fecha>.json` y `investigacion/ultimo.md`. No decide
nada: junta los datos para que alguien (o yo en la revision semanal) actualice
los `temas` de config.json y CREADORES.md.

Lo que hacen cambia rapido -- La Velada pasa, el Mundial acaba, RdJavi cambia
de juego -- asi que estos datos caducan en semanas.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import clipper

CABECERAS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
             "Accept": "application/json,text/html"}
SALIDA = clipper.DATA / "investigacion"


def _pedir(url: str, intentos: int = 3):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers=CABECERAS)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "ignore")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            if i == intentos - 1:
                return None
    return None


def clips_kick(canal: str, periodo: str = "all") -> list:
    txt = _pedir(f"https://kick.com/api/v2/channels/{canal}/clips"
                 f"?sort=view&time={periodo}")
    if not txt:
        return []
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        return []
    return [{"titulo": c.get("title", ""), "vistas": int(c.get("view_count") or 0),
             "segundos": c.get("duration"),
             "categoria": (c.get("category") or {}).get("name")}
            for c in (d.get("clips") or d.get("data") or [])][:15]


def clips_twitch(canal: str, rango: str = "all") -> list:
    """Twitch pinta los clips con JavaScript: en el HTML inicial no vienen.

    Se intenta igual por si algun dia los sirven en el servidor, pero lo normal
    es que devuelva vacio. Los clips de Twitch hay que sacarlos con navegador:
    https://www.twitch.tv/<canal>/clips?filter=clips&range=30d
    """
    html = _pedir(f"https://www.twitch.tv/{canal}/clips?filter=clips&range={rango}")
    if not html:
        return []
    salida, vistos = [], set()
    for m in re.finditer(r'"title":"((?:[^"\\]|\\.)*)"[^}]*?"viewCount":(\d+)', html):
        titulo = m.group(1).encode().decode("unicode_escape", "ignore")
        if titulo not in vistos:
            vistos.add(titulo)
            salida.append({"titulo": titulo, "vistas": int(m.group(2))})
    return sorted(salida, key=lambda x: -x["vistas"])[:15]


def directos_twitch(canal: str) -> list:
    """Titulos de los ultimos directos: dicen que esta haciendo ahora."""
    import subprocess
    p = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", "6",
         "--print", "%(duration)s|%(title)s", f"https://www.twitch.tv/{canal}/videos"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **clipper.SIN_VENTANA)
    salida = []
    for linea in (p.stdout or "").splitlines():
        if "|" in linea:
            dur, _, tit = linea.partition("|")
            try:
                salida.append({"horas": round(float(dur) / 3600, 1), "titulo": tit})
            except ValueError:
                pass
    return salida


def main():
    filtro = sys.argv[1] if len(sys.argv) > 1 else None
    canales = [c for c in clipper.CONFIG.get("canales", [])
               if c.get("verificado") and (not filtro or c.get("canal") == filtro)]

    datos = {"fecha": datetime.now().strftime("%Y-%m-%d %H:%M"), "canales": {}}
    for c in canales:
        canal, plat = c["canal"], c.get("plataforma")
        print(f"[>] {canal}")
        if plat == "kick":
            datos["canales"][canal] = {
                "plataforma": "kick",
                "historico": clips_kick(canal, "all"),
                "reciente": clips_kick(canal, "month"),
            }
        else:
            datos["canales"][canal] = {
                "plataforma": "twitch",
                "historico": clips_twitch(canal, "all"),
                "reciente": clips_twitch(canal, "30d"),
                "directos": directos_twitch(canal),
            }

    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / f"{datetime.now():%Y-%m-%d}.json").write_text(
        json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")

    lineas = [f"# Investigacion {datos['fecha']}", ""]
    for canal, d in datos["canales"].items():
        temas = next((x.get("temas", []) for x in clipper.CONFIG["canales"]
                      if x.get("canal") == canal), [])
        lineas.append(f"## {canal} ({d['plataforma']})")
        lineas.append(f"temas actuales en config: {', '.join(temas) or '(ninguno)'}")
        for etiqueta, clave in (("Mas vistos", "historico"), ("Ultimo mes", "reciente")):
            if d.get(clave):
                lineas.append(f"\n**{etiqueta}**\n")
                for x in d[clave][:8]:
                    seg = f" · {x['segundos']}s" if x.get("segundos") else ""
                    lineas.append(f"- {x['vistas']:>7,} · {x['titulo']}{seg}")
        if d["plataforma"] == "twitch" and not d.get("historico"):
            lineas.append("\n> Clips de Twitch NO recogidos: los pinta JavaScript. "
                          "Sacalos con navegador en "
                          f"`twitch.tv/{canal}/clips?filter=clips&range=30d`")
        if d.get("directos"):
            lineas.append("\n**Ultimos directos**\n")
            for x in d["directos"]:
                lineas.append(f"- {x['horas']}h · {x['titulo']}")
        lineas.append("")

    (SALIDA / "ultimo.md").write_text("\n".join(lineas), encoding="utf-8")
    print(f"\n[ok] {SALIDA / 'ultimo.md'}")


if __name__ == "__main__":
    main()
