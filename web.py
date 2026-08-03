"""
Galería web y explorador de archivos intuitivo para /app/clips.

Diseñado con HTML, CSS y JavaScript nativos, sin dependencias de frontend.
Interfaz editorial accesible para revisar vídeos, decisiones de Luna y archivos.
Permite explorar todas las carpetas (/app/clips), previsualizar vídeos en modal, ver logs y descargar clips.
"""

import base64
import hmac
import json
import os
import re
import subprocess
import threading
from functools import lru_cache, partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import clipper
import raw

DATA = clipper.DATA
OUT = clipper.OUT


@lru_cache(maxsize=512)
def _duracion_video_cache(path: Path, mtime_ns: int, size: int) -> int:
    try:
        duracion = round(float(clipper.run([
            clipper.FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path)]).stdout.strip()))
    except (ValueError, OSError, subprocess.CalledProcessError):
        return 0
    return duracion


# La cache en memoria moria en cada reinicio del contenedor, y volver a medir
# con ffprobe cuesta segundos por fichero en una maquina cargada: con 87 clips
# eran casi dos minutos de galeria en blanco tras cada despliegue. Se persiste
# en el volumen, firmada con mtime y tamaño para detectar un fichero cambiado.
_DURACIONES = DATA / ".duraciones.json"
_DUR_LOCK = threading.Lock()
_duraciones: dict | None = None
_duraciones_sucio = False


def _duraciones_cargadas() -> dict:
    """Lee el indice del disco una sola vez. Llamar con _DUR_LOCK tomado."""
    global _duraciones
    if _duraciones is None:
        try:
            datos = json.loads(_DURACIONES.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            datos = {}
        _duraciones = datos if isinstance(datos, dict) else {}
    return _duraciones


def guardar_duraciones():
    """Vuelca el indice si cambio, podando lo que ya no existe."""
    global _duraciones_sucio
    with _DUR_LOCK:
        if not _duraciones_sucio:
            return
        cache = _duraciones_cargadas()
        vivos = {k: v for k, v in cache.items() if (DATA / k).exists()}
        cache.clear()
        cache.update(vivos)
        temporal = _DURACIONES.with_name(f".{_DURACIONES.name}.{os.getpid()}.tmp")
        try:
            temporal.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(temporal, _DURACIONES)
            _duraciones_sucio = False
        except OSError:
            temporal.unlink(missing_ok=True)


def _duracion_video(path: Path) -> int:
    global _duraciones_sucio
    try:
        stat = path.stat()
    except OSError:
        return 0
    try:
        clave = path.resolve().relative_to(DATA.resolve()).as_posix()
    except (OSError, ValueError):
        clave = path.name
    firma = f"{stat.st_mtime_ns}:{stat.st_size}"

    with _DUR_LOCK:
        guardado = _duraciones_cargadas().get(clave)
    if isinstance(guardado, dict) and guardado.get("firma") == firma:
        return int(guardado.get("s", 0))

    duracion = _duracion_video_cache(path, stat.st_mtime_ns, stat.st_size)
    with _DUR_LOCK:
        _duraciones_cargadas()[clave] = {"firma": firma, "s": duracion}
        _duraciones_sucio = True
    return duracion


def _leer_motivos(path: Path) -> tuple[str, dict, str]:
    if not path.exists():
        return "", {}, ""
    contenido = path.read_text(encoding="utf-8", errors="replace")
    llm = {}
    motivo = contenido
    marcador = "\nLLM:\n"
    if marcador in contenido:
        motivo, bloque_llm = contenido.rsplit(marcador, 1)
        try:
            candidato = json.loads(bloque_llm.strip())
            if isinstance(candidato, dict):
                llm = candidato
        except json.JSONDecodeError:
            motivo = contenido
    gancho = ""
    encontrado = re.search(r"(?im)^gancho:\s*(.*)$", motivo)
    if encontrado:
        gancho = encontrado.group(1).strip()
    return motivo.strip(), llm, gancho


def _leer_ficha_publicable(path: Path) -> tuple[str, list[str]]:
    """Lee el formato nuevo sin interpretar HTML ni datos de control."""
    try:
        contenido = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", []
    partes = contenido.split("\n\n", 1)
    descripcion = partes[0].strip()
    if descripcion.upper().startswith((
            "TITULO /", "TÍTULO /", "DESCRIPCION SUGERIDA", "DESCRIPCIÓN SUGERIDA")):
        return "", []
    hashtags = partes[1].split() if len(partes) == 2 else []
    return descripcion, [tag for tag in hashtags if tag.startswith("#")]

HTML_TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Clipper · Consola editorial</title>

  <style>
    @import url("https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap");

    :root {
      --bg: #0a0f0d;
      --surface: #111916;
      --surface-2: #17231e;
      --soft: #0e1512;
      --line: #2b3a33;
      --line-strong: #405248;
      --text: #eff5ef;
      --muted: #a8b8ad;
      --dim: #718077;
      --mint: #7ae0b6;
      --mint-strong: #b4f3d5;
      --amber: #f1bb52;
      --red: #ff8b7a;
      --focus: #ffe3a0;
      --shadow: 0 18px 50px rgba(0, 0, 0, .24);
      --radius: 16px;
    }

    *,
    *::before,
    *::after {
      box-sizing: border-box;
    }

    html {
      min-width: 320px;
      background: var(--bg);
    }

    body {
      min-height: 100vh;
      margin: 0;
      overflow-x: hidden;
      color: var(--text);
      background: var(--bg);
      font-family: "IBM Plex Sans", ui-sans-serif, sans-serif;
      font-size: 16px;
      line-height: 1.5;
    }

    body::before {
      content: "";
      position: fixed;
      z-index: -1;
      inset: 0;
      pointer-events: none;
      opacity: .24;
      background-image:
        linear-gradient(rgba(122, 224, 182, .035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(122, 224, 182, .035) 1px, transparent 1px);
      background-size: 44px 44px;
    }

    button,
    input {
      font: inherit;
    }

    button,
    a {
      -webkit-tap-highlight-color: transparent;
    }

    button {
      cursor: pointer;
    }

    :focus-visible {
      outline: 3px solid var(--focus);
      outline-offset: 3px;
    }

    [hidden] {
      display: none !important;
    }

    .icon {
      width: 18px;
      height: 18px;
      flex: 0 0 18px;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .icon-sprite {
      position: absolute;
      width: 0;
      height: 0;
      overflow: hidden;
    }

    .skip-link {
      position: fixed;
      z-index: 20;
      top: 10px;
      left: 10px;
      padding: 10px 14px;
      color: var(--bg);
      background: var(--focus);
      border-radius: 8px;
      transform: translateY(-160%);
      transition: transform .18s ease;
    }

    .skip-link:focus {
      transform: translateY(0);
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      min-height: 86px;
      padding: 16px clamp(18px, 4vw, 64px);
      border-bottom: 1px solid var(--line);
      background: rgba(10, 15, 13, .94);
    }

    .brand,
    .brand-copy,
    .topbar-meta,
    .status-pill,
    .nav-label,
    .stat-head,
    .card-meta,
    .card-actions,
    .modal-head {
      display: flex;
      align-items: center;
    }

    .brand {
      gap: 13px;
      color: inherit;
      text-decoration: none;
    }

    .brand-mark {
      display: grid;
      place-items: center;
      width: 44px;
      height: 44px;
      color: var(--bg);
      background: var(--mint);
      border-radius: 11px;
      box-shadow: 4px 4px 0 var(--amber);
    }

    .brand-mark .icon {
      width: 23px;
      height: 23px;
      stroke-width: 2.2;
    }

    .brand-copy {
      align-items: flex-start;
      flex-direction: column;
      gap: 0;
    }

    .brand-name,
    h1,
    h2,
    h3,
    .stat-value {
      font-family: "Barlow Condensed", Impact, sans-serif;
      letter-spacing: .015em;
    }

    .brand-name {
      font-size: 30px;
      line-height: .9;
      text-transform: uppercase;
    }

    .brand-subtitle {
      color: var(--muted);
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 11px;
      letter-spacing: .12em;
      text-transform: uppercase;
    }

    .topbar-meta {
      gap: 12px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .status-pill {
      gap: 8px;
      min-height: 38px;
      padding: 8px 12px;
      color: var(--muted);
      border: 1px solid var(--line);
      border-radius: 999px;
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 11px;
      letter-spacing: .06em;
      text-transform: uppercase;
    }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--amber);
      box-shadow: 0 0 0 4px rgba(241, 187, 82, .12);
    }

    .status-pill.live .status-dot {
      background: var(--mint);
      box-shadow: 0 0 0 4px rgba(122, 224, 182, .12);
    }

    .status-pill.error .status-dot {
      background: var(--red);
      box-shadow: 0 0 0 4px rgba(255, 139, 122, .12);
    }

    .icon-button,
    .button,
    .text-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-width: 44px;
      min-height: 44px;
      padding: 9px 14px;
      color: var(--text);
      background: var(--surface);
      border: 1px solid var(--line-strong);
      border-radius: 9px;
      text-decoration: none;
      transition:
        background .18s ease,
        border-color .18s ease,
        color .18s ease,
        transform .18s ease;
    }

    .icon-button {
      padding: 10px;
    }

    .icon-button:hover,
    .button:hover,
    .text-link:hover {
      color: var(--mint-strong);
      border-color: var(--mint);
      background: var(--surface-2);
    }

    .button:active,
    .icon-button:active {
      transform: translateY(1px);
    }

    .button.primary {
      color: #09100d;
      background: var(--mint);
      border-color: var(--mint);
      font-weight: 700;
    }

    .button.primary:hover {
      color: #09100d;
      background: var(--mint-strong);
    }

    .button.quiet {
      color: var(--muted);
      background: transparent;
      border-color: var(--line);
    }

    .button:disabled {
      cursor: wait;
      opacity: .5;
    }

    .shell {
      width: min(1440px, calc(100% - 36px));
      margin: 0 auto;
      padding: 38px 0 64px;
    }

    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(240px, 330px);
      gap: 30px;
      align-items: end;
      padding: 15px 0 38px;
    }

    .eyebrow,
    .meta-label,
    .stat-label,
    .llm-kicker,
    .section-note,
    .card-file {
      font-family: "IBM Plex Mono", ui-monospace, monospace;
    }

    .eyebrow {
      margin: 0 0 9px;
      color: var(--amber);
      font-size: 11px;
      letter-spacing: .16em;
      text-transform: uppercase;
    }

    .hero h2 {
      max-width: 720px;
      margin: 0;
      font-size: clamp(42px, 6vw, 76px);
      line-height: .9;
      text-transform: uppercase;
    }

    .hero-copy {
      max-width: 630px;
      margin: 18px 0 0;
      color: var(--muted);
      font-size: 17px;
    }

    .signal-card {
      padding: 18px;
      border-left: 3px solid var(--mint);
      background: var(--surface);
      box-shadow: var(--shadow);
    }

    .signal-label,
    .meta-label,
    .stat-label,
    .llm-kicker {
      display: block;
      color: var(--dim);
      font-size: 10px;
      letter-spacing: .12em;
      text-transform: uppercase;
    }

    .signal-card strong {
      display: block;
      margin: 6px 0;
      color: var(--mint-strong);
      font-family: "Barlow Condensed", Impact, sans-serif;
      font-size: 28px;
      letter-spacing: .04em;
      text-transform: uppercase;
    }

    .signal-card span:last-child {
      color: var(--muted);
      font-size: 13px;
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-bottom: 32px;
    }

    .stat {
      min-height: 112px;
      padding: 16px 18px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-top: 2px solid var(--line-strong);
    }

    .stat.ready {
      border-top-color: var(--mint);
    }

    .stat.review {
      border-top-color: var(--amber);
    }

    .stat.warning {
      border-top-color: #ff8d64;
    }

    .stat-head {
      justify-content: space-between;
      gap: 12px;
    }

    .stat-head .icon {
      color: var(--muted);
    }

    .stat-value {
      display: block;
      margin-top: 7px;
      font-size: 42px;
      line-height: .9;
    }

    .stat-label {
      margin-top: 11px;
      color: var(--muted);
    }

    .workspace-nav {
      display: flex;
      gap: 3px;
      padding: 4px;
      overflow-x: auto;
      background: var(--soft);
      border: 1px solid var(--line);
      scrollbar-width: thin;
    }

    .tab {
      display: inline-flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      min-width: 128px;
      min-height: 48px;
      padding: 10px 14px;
      color: var(--muted);
      background: transparent;
      border: 0;
      border-radius: 7px;
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 12px;
      letter-spacing: .06em;
      text-transform: uppercase;
    }

    .tab:hover {
      color: var(--text);
      background: var(--surface-2);
    }

    .tab[aria-selected="true"] {
      color: var(--bg);
      background: var(--mint);
      font-weight: 600;
    }

    .tab-count {
      display: grid;
      place-items: center;
      min-width: 24px;
      height: 24px;
      padding: 0 6px;
      border-radius: 999px;
      background: rgba(0, 0, 0, .16);
    }

    .panel {
      padding-top: 30px;
    }

    .section-heading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 18px;
    }

    .section-heading h3 {
      margin: 0;
      font-size: 36px;
      line-height: .95;
      text-transform: uppercase;
    }

    .section-note {
      color: var(--muted);
      font-size: 11px;
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 20px;
      padding: 10px;
      background: var(--soft);
      border: 1px solid var(--line);
    }

    .search-wrap {
      display: flex;
      align-items: center;
      gap: 9px;
      flex: 1 1 280px;
      min-height: 44px;
      padding: 0 12px;
      color: var(--dim);
      background: var(--bg);
      border: 1px solid var(--line);
    }

    .search-wrap:focus-within {
      border-color: var(--mint);
      color: var(--mint);
    }

    .search-wrap input {
      width: 100%;
      min-width: 0;
      color: var(--text);
      background: transparent;
      border: 0;
      outline: 0;
    }

    .search-wrap input::placeholder {
      color: var(--dim);
    }

    .toolbar-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }

    .filter-chip {
      min-height: 38px;
      padding: 7px 11px;
      color: var(--muted);
      background: transparent;
      border: 1px solid var(--line);
      border-radius: 999px;
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 11px;
    }

    .filter-chip.active {
      color: var(--bg);
      background: var(--amber);
      border-color: var(--amber);
    }

    .clip-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
    }

    .clip-card {
      min-width: 0;
      overflow: hidden;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: 0 8px 24px rgba(0, 0, 0, .12);
      transition:
        border-color .18s ease,
        transform .18s ease,
        box-shadow .18s ease;
    }

    .clip-card:hover {
      border-color: var(--line-strong);
      transform: translateY(-2px);
      box-shadow: var(--shadow);
    }

    .clip-media {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      aspect-ratio: 9 / 16;
      max-height: 560px;
      background: #050806;
      border-bottom: 1px solid var(--line);
    }

    .clip-media video {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }

    .media-tag {
      position: absolute;
      top: 10px;
      left: 10px;
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 3px 8px;
      color: var(--bg);
      background: var(--mint);
      border-radius: 5px;
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: .1em;
    }

    .media-tag.review {
      background: var(--amber);
    }

    .media-tag.raw {
      color: var(--text);
      background: #7d8cff;
    }

    .duration-tag {
      position: absolute;
      right: 10px;
      bottom: 10px;
      padding: 4px 7px;
      color: var(--text);
      background: rgba(5, 8, 6, .84);
      border: 1px solid rgba(255, 255, 255, .18);
      border-radius: 5px;
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 11px;
    }

    .card-body {
      padding: 16px;
    }

    .card-meta {
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 9px;
    }

    .channel {
      min-width: 0;
      overflow: hidden;
      color: var(--mint);
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 11px;
      text-overflow: ellipsis;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .card-file {
      overflow: hidden;
      color: var(--dim);
      font-size: 10px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .clip-title {
      margin: 0;
      color: var(--text);
      font-size: 20px;
      line-height: 1.16;
    }

    .clip-reason {
      display: -webkit-box;
      min-height: 42px;
      margin: 10px 0 0;
      overflow: hidden;
      color: var(--muted);
      font-size: 13px;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
    }

    .llm-panel {
      margin-top: 14px;
      padding: 11px;
      background: var(--soft);
      border: 1px solid var(--line);
      border-left: 3px solid var(--amber);
    }

    .llm-panel.publish {
      border-left-color: var(--mint);
    }

    .llm-panel.discard {
      border-left-color: var(--red);
    }

    .llm-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
    }

    .llm-decision {
      color: var(--amber);
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
    }

    .llm-panel.publish .llm-decision {
      color: var(--mint);
    }

    .llm-panel.discard .llm-decision {
      color: var(--red);
    }

    .llm-score {
      color: var(--muted);
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 11px;
    }

    .llm-reason {
      margin: 5px 0 0;
      color: var(--muted);
      font-size: 12px;
    }

    .description-panel {
      margin-top: 12px;
      padding: 11px;
      background: var(--surface);
      border: 1px solid var(--line);
    }

    .raw-status {
      display: grid;
      gap: 5px;
      margin-top: 14px;
      padding: 11px;
      background: var(--soft);
      border: 1px solid var(--line);
      border-left: 3px solid #7d8cff;
    }

    .raw-status-value {
      color: #aeb7ff;
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 12px;
      letter-spacing: .08em;
    }

    .raw-error {
      margin: 4px 0 0;
      color: var(--red);
      font-size: 12px;
    }

    .clip-description {
      margin: 6px 0 0;
      color: var(--text);
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
    }

    .description-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 9px;
    }

    .card-actions {
      gap: 8px;
      margin-top: 16px;
    }

    .card-actions .button {
      flex: 1;
      min-width: 0;
    }

    .text-link {
      color: var(--muted);
    }

    .empty-state,
    .error-state {
      display: grid;
      grid-column: 1 / -1;
      place-items: center;
      min-height: 260px;
      padding: 30px;
      text-align: center;
      background: var(--surface);
      border: 1px dashed var(--line-strong);
    }

    .empty-state .icon,
    .error-state .icon {
      width: 34px;
      height: 34px;
      margin-bottom: 13px;
      color: var(--dim);
    }

    .empty-state h4,
    .error-state h4 {
      margin: 0;
      font-size: 20px;
    }

    .empty-state p,
    .error-state p {
      max-width: 480px;
      margin: 8px 0 0;
      color: var(--muted);
    }

    .error-state {
      border-color: rgba(255, 139, 122, .5);
    }

    .error-state .icon {
      color: var(--red);
    }

    .skeleton {
      height: 380px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
    }

    .review-summary {
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 20px;
      padding: 14px 16px;
      color: var(--muted);
      background: var(--surface);
      border-left: 3px solid var(--amber);
    }

    .review-summary strong {
      color: var(--text);
      font-family: "Barlow Condensed", Impact, sans-serif;
      font-size: 28px;
    }

    .explorer-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 14px;
    }

    .breadcrumbs {
      display: flex;
      align-items: center;
      gap: 5px;
      min-height: 44px;
      overflow-x: auto;
      color: var(--muted);
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 12px;
    }

    .breadcrumbs button {
      min-height: 36px;
      padding: 6px 9px;
      color: var(--muted);
      background: transparent;
      border: 1px solid transparent;
      border-radius: 6px;
    }

    .breadcrumbs button:hover {
      color: var(--mint);
      border-color: var(--line);
    }

    .file-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }

    .file-item {
      display: flex;
      align-items: center;
      gap: 11px;
      min-height: 76px;
      padding: 12px;
      overflow: hidden;
      color: var(--text);
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 10px;
      text-decoration: none;
    }

    .file-item:hover {
      border-color: var(--mint);
    }

    .file-item .icon {
      color: var(--mint);
    }

    .file-copy {
      min-width: 0;
    }

    .file-name {
      display: block;
      overflow: hidden;
      font-weight: 600;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .file-size {
      display: block;
      margin-top: 3px;
      color: var(--dim);
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 11px;
    }

    .file-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-left: auto;
    }

    .file-actions .icon-button {
      min-width: 40px;
      min-height: 40px;
      padding: 8px;
    }

    .log-box {
      min-height: 420px;
      margin: 0;
      padding: 18px;
      overflow: auto;
      color: #c5d7c9;
      background: #050806;
      border: 1px solid var(--line);
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 12px;
      line-height: 1.65;
      white-space: pre-wrap;
    }

    .modal-backdrop {
      position: fixed;
      z-index: 10;
      inset: 0;
      display: grid;
      place-items: center;
      padding: 20px;
      background: rgba(3, 6, 5, .88);
    }

    .modal {
      width: min(900px, 100%);
      max-height: calc(100vh - 40px);
      overflow: auto;
      background: var(--surface);
      border: 1px solid var(--line-strong);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }

    .modal-head {
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
    }

    .modal-head h3 {
      margin: 0;
      font-size: 26px;
    }

    .modal-content {
      padding: 18px;
    }

    .modal-content video,
    .modal-content pre {
      display: block;
      width: 100%;
      max-height: 70vh;
      background: #050806;
    }

    .modal-content pre {
      margin: 0;
      padding: 14px;
      overflow: auto;
      color: #c5d7c9;
      font: 12px/1.65 "IBM Plex Mono", ui-monospace, monospace;
      white-space: pre-wrap;
    }

    .toast {
      position: fixed;
      z-index: 30;
      right: 18px;
      bottom: 18px;
      max-width: min(420px, calc(100% - 36px));
      padding: 12px 15px;
      color: var(--text);
      background: var(--surface-2);
      border: 1px solid var(--line-strong);
      border-left: 3px solid var(--mint);
      box-shadow: var(--shadow);
    }

    @media (max-width: 1100px) {
      .clip-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .file-grid {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
    }

    @media (max-width: 760px) {
      .topbar {
        align-items: flex-start;
        flex-direction: column;
        gap: 15px;
      }

      .topbar-meta {
        width: 100%;
        justify-content: space-between;
      }

      .shell {
        width: min(100% - 24px, 680px);
        padding-top: 26px;
      }

      .hero {
        grid-template-columns: 1fr;
        gap: 22px;
        padding-bottom: 28px;
      }

      .hero h2 {
        font-size: clamp(42px, 14vw, 62px);
      }

      .stats {
        grid-template-columns: repeat(2, 1fr);
      }

      .section-heading {
        align-items: flex-start;
        flex-direction: column;
      }

      .toolbar {
        align-items: stretch;
        flex-direction: column;
      }

      .search-wrap {
        flex: 0 1 auto;
        min-height: 44px;
      }

      .toolbar-actions {
        justify-content: space-between;
      }

      .clip-grid {
        grid-template-columns: 1fr;
      }

      .file-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }

    @media (max-width: 420px) {
      .topbar {
        padding: 14px 12px;
      }

      .shell {
        width: min(100% - 20px, 680px);
      }

      .brand-name {
        font-size: 27px;
      }

      .status-pill {
        flex: 1;
      }

      .stats {
        gap: 7px;
      }

      .stat {
        min-height: 98px;
        padding: 13px;
      }

      .stat-value {
        font-size: 35px;
      }

      .tab {
        min-width: 116px;
      }

      .file-grid {
        grid-template-columns: 1fr;
      }

      .card-body {
        padding: 14px;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      *,
      *::before,
      *::after {
        scroll-behavior: auto !important;
        transition-duration: .01ms !important;
        animation-duration: .01ms !important;
      }

      .clip-card:hover {
        transform: none;
      }
    }
  </style>
</head>
<body>
  <svg class="icon-sprite" aria-hidden="true">
    <symbol id="icon-activity" viewBox="0 0 24 24">
      <path d="M3 12h4l2-6 4 12 2-6h6"></path>
    </symbol>
    <symbol id="icon-grid" viewBox="0 0 24 24">
      <rect x="3" y="3" width="7" height="7"></rect>
      <rect x="14" y="3" width="7" height="7"></rect>
      <rect x="3" y="14" width="7" height="7"></rect>
      <rect x="14" y="14" width="7" height="7"></rect>
    </symbol>
    <symbol id="icon-clock" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="8.5"></circle>
      <path d="M12 7v5l3 2"></path>
    </symbol>
    <symbol id="icon-folder" viewBox="0 0 24 24">
      <path d="M3 7.5h6l2 2h10v9H3z"></path>
      <path d="M3 7.5V5h6l2 2"></path>
    </symbol>
    <symbol id="icon-file" viewBox="0 0 24 24">
      <path d="M6 3h8l4 4v14H6z"></path>
      <path d="M14 3v5h5M9 13h6M9 17h6"></path>
    </symbol>
    <symbol id="icon-refresh" viewBox="0 0 24 24">
      <path d="M20 11a8 8 0 0 0-14.5-4L4 9"></path>
      <path d="M4 4v5h5M4 13a8 8 0 0 0 14.5 4L20 15"></path>
      <path d="M20 20v-5h-5"></path>
    </symbol>
    <symbol id="icon-search" viewBox="0 0 24 24">
      <circle cx="10.8" cy="10.8" r="6.2"></circle>
      <path d="m16 16 4.5 4.5"></path>
    </symbol>
    <symbol id="icon-play" viewBox="0 0 24 24">
      <path d="m9 6 9 6-9 6z"></path>
    </symbol>
    <symbol id="icon-external" viewBox="0 0 24 24">
      <path d="M14 4h6v6M20 4l-9 9"></path>
      <path d="M18 13v6H4V5h6"></path>
    </symbol>
    <symbol id="icon-download" viewBox="0 0 24 24">
      <path d="M12 3v12M7 10l5 5 5-5M5 21h14"></path>
    </symbol>
    <symbol id="icon-x" viewBox="0 0 24 24">
      <path d="m6 6 12 12M18 6 6 18"></path>
    </symbol>
    <symbol id="icon-alert" viewBox="0 0 24 24">
      <path d="M12 4 21 20H3z"></path>
      <path d="M12 9v5M12 17h.01"></path>
    </symbol>
  </svg>

  <a class="skip-link" href="#contenido">Saltar al contenido</a>

  <header class="topbar">
    <a class="brand" href="/" aria-label="Clipper, volver al inicio">
      <span class="brand-mark">
        <svg class="icon" aria-hidden="true">
          <use href="#icon-activity"></use>
        </svg>
      </span>
      <span class="brand-copy">
        <span class="brand-name">Clipper</span>
        <span class="brand-subtitle">Consola editorial</span>
      </span>
    </a>

    <div class="topbar-meta">
      <span id="connectionBadge" class="status-pill">
        <span class="status-dot" aria-hidden="true"></span>
        <span id="connectionText">CONECTANDO</span>
        <span id="updatedAt">· --:--</span>
      </span>
      <button
        id="refreshButton"
        class="icon-button"
        type="button"
        aria-label="Actualizar clips ahora"
        title="Actualizar clips ahora">
        <svg class="icon" aria-hidden="true">
          <use href="#icon-refresh"></use>
        </svg>
      </button>
    </div>
  </header>

  <main id="contenido" class="shell">
    <section class="hero" aria-labelledby="hero-title">
      <div>
        <p class="eyebrow">Mesa de selección / tiempo real</p>
        <h2 id="hero-title">Elige el momento que merece salir.</h2>
        <p class="hero-copy">
          Revisa el pulso de tus directos, valida el criterio editorial de Luna
          y deja cada corte listo para publicar.
        </p>
      </div>
      <div class="signal-card">
        <span class="signal-label">Estado del sistema</span>
        <strong id="systemState">Sincronizando</strong>
        <span id="systemDetail">Consultando LISTOS y REVISAR</span>
      </div>
    </section>

    <section class="stats" aria-label="Resumen de la galería">
      <article class="stat ready">
        <div class="stat-head">
          <span class="stat-label">Listos</span>
          <svg class="icon" aria-hidden="true"><use href="#icon-grid"></use></svg>
        </div>
        <strong id="countListos" class="stat-value">0</strong>
        <span id="countListosNote" class="stat-label">publicables</span>
      </article>
      <article class="stat review">
        <div class="stat-head">
          <span class="stat-label">Revisar</span>
          <svg class="icon" aria-hidden="true"><use href="#icon-clock"></use></svg>
        </div>
        <strong id="countRevisar" class="stat-value">0</strong>
        <span id="countRevisarNote" class="stat-label">pendientes</span>
      </article>
      <article class="stat">
        <div class="stat-head">
          <span class="stat-label">Canales</span>
          <svg class="icon" aria-hidden="true"><use href="#icon-activity"></use></svg>
        </div>
        <strong id="countCanales" class="stat-value">0</strong>
        <span class="stat-label">con actividad</span>
      </article>
      <article class="stat warning">
        <div class="stat-head">
          <span class="stat-label">Último corte</span>
          <svg class="icon" aria-hidden="true"><use href="#icon-activity"></use></svg>
        </div>
        <strong id="lastClipTime" class="stat-value">--:--</strong>
        <span id="lastClipNote" class="stat-label">aún sin datos</span>
      </article>
    </section>

    <nav class="workspace-nav" aria-label="Secciones de la galería" role="tablist">
      <button
        id="tab-listos"
        class="tab"
        type="button"
        role="tab"
        aria-selected="true"
        aria-controls="panel-listos"
        data-tab="listos">
        <span class="nav-label">
          <svg class="icon" aria-hidden="true"><use href="#icon-grid"></use></svg>
          Listos
        </span>
        <span id="tabCountListos" class="tab-count">0</span>
      </button>
      <button
        id="tab-revisar"
        class="tab"
        type="button"
        role="tab"
        aria-selected="false"
        aria-controls="panel-revisar"
        data-tab="revisar">
        <span class="nav-label">
          <svg class="icon" aria-hidden="true"><use href="#icon-clock"></use></svg>
          Revisar
        </span>
        <span id="tabCountRevisar" class="tab-count">0</span>
      </button>
      <button
        id="tab-raw"
        class="tab"
        type="button"
        role="tab"
        aria-selected="false"
        aria-controls="panel-raw"
        data-tab="raw">
        <span class="nav-label">
          <svg class="icon" aria-hidden="true"><use href="#icon-clock"></use></svg>
          RAW
        </span>
        <span id="tabCountRaw" class="tab-count">0</span>
      </button>
      <button
        id="tab-explorador"
        class="tab"
        type="button"
        role="tab"
        aria-selected="false"
        aria-controls="panel-explorador"
        data-tab="explorador">
        <span class="nav-label">
          <svg class="icon" aria-hidden="true"><use href="#icon-folder"></use></svg>
          Explorador
        </span>
      </button>
      <button
        id="tab-logs"
        class="tab"
        type="button"
        role="tab"
        aria-selected="false"
        aria-controls="panel-logs"
        data-tab="logs">
        <span class="nav-label">
          <svg class="icon" aria-hidden="true"><use href="#icon-file"></use></svg>
          Registros
        </span>
      </button>
    </nav>

    <section
      id="panel-listos"
      class="panel"
      role="tabpanel"
      aria-labelledby="tab-listos">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Salida validada</p>
          <h3>Clips listos</h3>
        </div>
        <span id="listosNote" class="section-note">Cargando…</span>
      </div>
      <div class="toolbar">
        <label class="search-wrap" for="searchListos">
          <svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg>
          <input
            id="searchListos"
            type="search"
            placeholder="Buscar por canal, título o archivo"
            autocomplete="off">
        </label>
        <div class="toolbar-actions">
          <button
            class="filter-chip active"
            type="button"
            data-filter="all"
            data-scope="listos">
            Todos
          </button>
          <button
            class="filter-chip"
            type="button"
            data-filter="long"
            data-scope="listos">
            Más de 30 s
          </button>
        </div>
      </div>
      <div
        id="listosGrid"
        class="clip-grid"
        aria-live="polite"
        aria-busy="true">
        <div class="skeleton" aria-hidden="true"></div>
        <div class="skeleton" aria-hidden="true"></div>
        <div class="skeleton" aria-hidden="true"></div>
      </div>
    </section>

    <section
      id="panel-revisar"
      class="panel"
      role="tabpanel"
      aria-labelledby="tab-revisar"
      hidden>
      <div class="section-heading">
        <div>
          <p class="eyebrow">Decisión humana obligatoria</p>
          <h3>Candidatos a revisar</h3>
        </div>
        <span id="revisarNote" class="section-note">Cargando…</span>
      </div>
      <div class="review-summary">
        <strong id="reviewTotal">0</strong>
        <span>
          candidatos esperan una lectura editorial. La recomendación de Luna
          aparece en cada ficha; los subtítulos y tiempos siguen siendo los de
          Whisper.
        </span>
      </div>
      <div class="toolbar">
        <label class="search-wrap" for="searchRevisar">
          <svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg>
          <input
            id="searchRevisar"
            type="search"
            placeholder="Buscar en revisión"
            autocomplete="off">
        </label>
        <div class="toolbar-actions">
          <button
            class="filter-chip active"
            type="button"
            data-filter="all"
            data-scope="revisar">
            Todos
          </button>
          <button
            class="filter-chip"
            type="button"
            data-filter="high"
            data-scope="revisar">
            Puntuación ≥ 70
          </button>
        </div>
      </div>
      <div
        id="revisarGrid"
        class="clip-grid"
        aria-live="polite"
        aria-busy="true">
        <div class="skeleton" aria-hidden="true"></div>
        <div class="skeleton" aria-hidden="true"></div>
      </div>
    </section>

    <section
      id="panel-raw"
      class="panel"
      role="tabpanel"
      aria-labelledby="tab-raw"
      hidden>
      <div class="section-heading">
        <div>
          <p class="eyebrow">Procesamiento visual</p>
          <h3>Candidatos RAW</h3>
        </div>
        <span id="rawNote" class="section-note">Cargando…</span>
      </div>
      <div class="review-summary raw-summary">
        <strong>AUTOMÁTICO</strong>
        <span>
          Luna recibe la transcripción y fotogramas de todo el candidato antes
          de decidir, escribir el hook y renderizarlo.
        </span>
      </div>
      <div class="toolbar">
        <label class="search-wrap" for="searchRaw">
          <svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg>
          <input
            id="searchRaw"
            type="search"
            placeholder="Buscar por canal, motivo o archivo"
            autocomplete="off">
        </label>
      </div>
      <div
        id="rawGrid"
        class="clip-grid"
        aria-live="polite"
        aria-busy="true">
        <div class="skeleton" aria-hidden="true"></div>
        <div class="skeleton" aria-hidden="true"></div>
      </div>
    </section>

    <section
      id="panel-explorador"
      class="panel"
      role="tabpanel"
      aria-labelledby="tab-explorador"
      hidden>
      <div class="section-heading">
        <div>
          <p class="eyebrow">Archivos del servidor</p>
          <h3>Explorador</h3>
        </div>
        <span id="explorerNote" class="section-note">
          Navega sin salir de Clipper
        </span>
      </div>
      <div class="explorer-layout">
        <div id="breadcrumbs" class="breadcrumbs" aria-label="Ruta actual"></div>
        <div id="fileGrid" class="file-grid" aria-live="polite"></div>
      </div>
    </section>

    <section
      id="panel-logs"
      class="panel"
      role="tabpanel"
      aria-labelledby="tab-logs"
      hidden>
      <div class="section-heading">
        <div>
          <p class="eyebrow">Diagnóstico operativo</p>
          <h3>Registros recientes</h3>
        </div>
        <span id="logsNote" class="section-note">
          Últimas 60 líneas por archivo
        </span>
      </div>
      <pre id="logsBox" class="log-box" tabindex="0">Cargando registros…</pre>
    </section>
  </main>

  <div id="toast" class="toast" role="status" aria-live="polite" hidden></div>

  <div
    id="previewModal"
    class="modal-backdrop"
    role="dialog"
    aria-modal="true"
    aria-labelledby="previewTitle"
    hidden>
    <div class="modal">
      <div class="modal-head">
        <h3 id="previewTitle">Previsualización</h3>
        <button
          id="closePreview"
          class="icon-button"
          type="button"
          aria-label="Cerrar previsualización">
          <svg class="icon" aria-hidden="true"><use href="#icon-x"></use></svg>
        </button>
      </div>
      <div id="previewBody" class="modal-content"></div>
    </div>
  </div>

  <script>
    (function () {
      "use strict";

      const state = {
        tab: "listos",
        path: "",
        clips: {
          listos: [],
          revisar: [],
          raw: []
        },
        signature: "",
        modalFocus: null
      };

      const $ = (selector) => document.querySelector(selector);
      const $$ = (selector) => Array.from(document.querySelectorAll(selector));
      const icon = (name) =>
        '<svg class="icon" aria-hidden="true"><use href="#icon-' +
        name +
        '"></use></svg>';

      function text(tag, className, value) {
        const node = document.createElement(tag);
        if (className) {
          node.className = className;
        }
        if (value !== undefined) {
          node.textContent = value;
        }
        return node;
      }

      function iconNode(name) {
        const node = document.createElement("span");
        node.innerHTML = icon(name);
        return node.firstElementChild;
      }

      function setText(selector, value) {
        const node = $(selector);
        if (node) {
          node.textContent = value;
        }
      }

      function notify(message, error) {
        const node = $("#toast");
        if (!node) {
          return;
        }
        node.textContent = message;
        node.hidden = false;
        node.style.borderLeftColor = error ? "var(--red)" : "var(--mint)";
        clearTimeout(notify.timer);
        notify.timer = setTimeout(() => {
          node.hidden = true;
        }, 4200);
      }

      function connection(kind, label, detail) {
        const badge = $("#connectionBadge");
        if (badge) {
          badge.className = "status-pill " + kind;
        }
        setText("#connectionText", label);
        setText("#updatedAt", "· " + detail);
      }

      function safeFileUrl(value) {
        const raw = String(value || "").replace(/^[/]files[/]/, "");
        const parts = raw
          .split("/")
          .filter((part) => part && part !== "." && part !== "..");
        return "/files/" + parts.map(encodeURIComponent).join("/");
      }

      function duration(value) {
        const seconds = Math.max(0, Number(value) || 0);
        return (
          Math.floor(seconds / 60) +
          ":" +
          String(Math.floor(seconds % 60)).padStart(2, "0")
        );
      }

      function clipTime(name, timestamp) {
        const match = String(name || "").match(
          /(?:^|-)(\d{2})(\d{2})(\d{2})(?:-\d+)?\.mp4$/i
        );
        if (match) {
          return match[1] + ":" + match[2] + ":" + match[3];
        }

        const seconds = Number(timestamp);
        if (!Number.isFinite(seconds) || seconds <= 0) {
          return "--:--";
        }
        const date = new Date(seconds * 1000);
        return Number.isFinite(date.getTime())
          ? date.toLocaleTimeString("es-ES", {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit"
            })
          : "--:--";
      }

      function decisionLabel(value) {
        return {
          publicar: "Publicar",
          revisar: "Revisar",
          descartar: "Descartar"
        }[value] || "Sin decisión";
      }

      function llmPanel(clip) {
        const llm =
          clip.llm && typeof clip.llm === "object" ? clip.llm : null;
        if (
          !llm ||
          (!llm.decision && llm.score === undefined && !llm.reason)
        ) {
          return null;
        }

        const decision = String(llm.decision || "revisar").toLowerCase();
        const panel = text(
          "div",
          "llm-panel " +
            (decision === "publicar"
              ? "publish"
              : decision === "descartar"
                ? "discard"
                : "")
        );
        const head = text("div", "llm-head");
        head.append(
          text("span", "llm-decision", "Luna · " + decisionLabel(decision)),
          text(
            "span",
            "llm-score",
            llm.score === undefined ? "—/100" : String(llm.score) + "/100"
          )
        );
        panel.appendChild(head);
        panel.appendChild(
          text("p", "llm-reason", llm.reason || "Sin motivo registrado.")
        );
        if (llm.confidence !== undefined) {
          panel.appendChild(
            text(
              "span",
              "llm-kicker",
              "Confianza " + Math.round(Number(llm.confidence) * 100) + "%"
            )
          );
        }
        return panel;
      }

      function descriptionPanel(clip) {
        const panel = text("section", "description-panel");
        panel.setAttribute("aria-label", "Descripción recomendada");
        panel.appendChild(text("span", "llm-kicker", "Descripción recomendada"));
        panel.appendChild(
          text(
            "p",
            "clip-description",
            clip.description || "Sin descripción recomendada."
          )
        );
        if (Array.isArray(clip.hashtags) && clip.hashtags.length) {
          panel.appendChild(
            text("p", "clip-description", clip.hashtags.join(" "))
          );
        }

        const actions = text("div", "description-actions");
        if (clip.description) {
          const copy = text("button", "text-link", "Copiar descripción");
          copy.type = "button";
          copy.addEventListener("click", async () => {
            const tags = Array.isArray(clip.hashtags) ? clip.hashtags.join(" ") : "";
            try {
              await navigator.clipboard.writeText(
                clip.description + "\n\n" + tags
              );
              notify("Descripción y hashtags copiados", false);
            } catch (_error) {
              notify("No se pudo copiar el texto", true);
            }
          });
          actions.appendChild(copy);
        }
        if (clip.txt_url) {
          const ficha = text("a", "text-link", "Descargar TXT");
          ficha.href = safeFileUrl(clip.txt_url);
          ficha.download = "";
          actions.appendChild(ficha);
        }
        if (actions.childNodes.length) {
          panel.appendChild(actions);
        }
        return panel;
      }

      function rawStatusPanel(clip) {
        const panel = text("section", "raw-status");
        panel.setAttribute("aria-label", "Estado de validación RAW");
        panel.appendChild(text("span", "llm-kicker", "Estado RAW"));
        const status = String(clip.status || "pendiente");
        const label = status === "completado"
          ? "Completado"
          : status.startsWith("procesando_")
            ? "Procesando con Luna"
            : status === "error_luna"
              ? "Análisis visual de Luna fallido"
              : clip.next_retry_at
                ? "Esperando reintento"
                : "Esperando turno de Luna";
        panel.appendChild(text("strong", "raw-status-value", label));
        if (clip.last_attempt_at) {
          panel.appendChild(text("p", "clip-reason", "Último intento: " + clip.last_attempt_at));
        }
        if (clip.luna_latency_ms) {
          panel.appendChild(text("p", "clip-reason", "Luna: " + clip.luna_latency_ms + " ms · " + (clip.image_count || 0) + " fotogramas"));
        }
        if (clip.last_error) {
          panel.appendChild(text("p", "raw-error", clip.last_error));
        }
        if (clip.destination) {
          const link = text("a", "text-link", "Abrir salida final");
          link.href = safeFileUrl(clip.destination);
          link.target = "_blank";
          link.rel = "noopener";
          panel.appendChild(link);
        }
        return panel;
      }

      function makeCard(clip, kind) {
        const article = text("article", "clip-card");
        article.dataset.search = [
          clip.canal,
          clip.nombre,
          clip.gancho,
          clip.motivo,
          clip.status,
          clip.last_error,
          clip.description,
          (clip.hashtags || []).join(" ")
        ]
          .join(" ")
          .toLowerCase();
        article.dataset.duration = String(clip.duracion || 0);
        article.dataset.score = String(
          (clip.llm && clip.llm.score) || 0
        );

        const media = text("div", "clip-media");
        const video = document.createElement("video");
        video.src = safeFileUrl(clip.url);
        video.preload = "none";
        video.controls = true;
        video.playsInline = true;
        video.setAttribute(
          "aria-label",
          "Vídeo del clip " + (clip.gancho || clip.nombre)
        );
        video.addEventListener("error", () => {
          notify("No se pudo cargar " + clip.nombre, true);
        });
        media.appendChild(video);
        media.appendChild(
          text(
            "span",
            "media-tag " + (kind === "revisar" ? "review" : kind === "raw" ? "raw" : ""),
            kind === "revisar" ? "REVISAR" : kind === "raw" ? "RAW" : "LISTO"
          )
        );
        media.appendChild(
          text("span", "duration-tag", duration(clip.duracion))
        );
        article.appendChild(media);

        const body = text("div", "card-body");
        const meta = text("div", "card-meta");
        meta.append(
          text("span", "channel", clip.canal || "CANAL DESCONOCIDO"),
          text("span", "card-file", clipTime(clip.nombre, clip.timestamp))
        );
        body.appendChild(meta);
        body.appendChild(
          text(
            "h4",
            "clip-title",
            clip.gancho || (kind === "raw" ? "Candidato RAW" : clip.nombre || "Clip sin título")
          )
        );
        if (clip.motivo) {
          body.appendChild(text("p", "clip-reason", clip.motivo));
        }

        if (kind === "raw") {
          body.appendChild(rawStatusPanel(clip));
        } else {
          const panel = llmPanel(clip);
          if (panel) {
            body.appendChild(panel);
          }
          body.appendChild(descriptionPanel(clip));
        }

        const actions = text("div", "card-actions");
        const preview = text("button", "button primary");
        preview.type = "button";
        preview.innerHTML = icon("play") + "<span>Previsualizar</span>";
        preview.addEventListener("click", () => {
          abrirPrevisualizacionSeguro(
            clip.url,
            clip.gancho || clip.nombre,
            "video"
          );
        });
        actions.appendChild(preview);

        const download = document.createElement("a");
        download.className = "text-link";
        download.href = safeFileUrl(clip.url);
        download.download = "";
        download.setAttribute(
          "aria-label",
          "Descargar " + (clip.nombre || "vídeo")
        );
        download.innerHTML = icon("download") + "<span>Descargar</span>";
        actions.appendChild(download);

        body.appendChild(actions);
        article.appendChild(body);
        return article;
      }

      function emptyState(kind) {
        const box = text("div", "empty-state");
        box.appendChild(iconNode(kind === "revisar" || kind === "raw" ? "clock" : "grid"));

        const copy = text("div");
        copy.appendChild(
          text(
            "h4",
            "",
            kind === "revisar"
              ? "La bandeja está despejada"
              : kind === "raw"
                ? "No hay candidatos en RAW"
              : "Todavía no hay clips listos"
          )
        );
        copy.appendChild(
          text(
            "p",
            "",
            kind === "revisar"
              ? "Cuando el pipeline encuentre un candidato aparecerá aquí con la lectura de Luna."
              : kind === "raw"
                ? "Los candidatos pausados después de Whisper aparecerán aquí."
              : "Los cortes promovidos desde REVISAR aparecerán aquí automáticamente."
          )
        );
        box.appendChild(copy);
        return box;
      }

      function errorState(message) {
        const box = text("div", "error-state");
        box.appendChild(iconNode("alert"));

        const copy = text("div");
        copy.appendChild(text("h4", "", "No se pudo actualizar"));
        copy.appendChild(text("p", "", message));

        const retry = text("button", "button quiet", "Reintentar");
        retry.type = "button";
        retry.addEventListener("click", () => cargarClips(true));
        copy.appendChild(retry);

        box.appendChild(copy);
        return box;
      }

      function render(kind) {
        const grid = {
          listos: $("#listosGrid"),
          revisar: $("#revisarGrid"),
          raw: $("#rawGrid")
        }[kind];
        if (!grid) {
          return;
        }

        grid.replaceChildren();
        grid.setAttribute("aria-busy", "false");

        const clips = state.clips[kind] || [];
        if (!clips.length) {
          grid.appendChild(emptyState(kind));
          return;
        }

        clips.forEach((clip) => {
          grid.appendChild(makeCard(clip, kind));
        });
        filter(kind);
      }

      function filter(kind) {
        const grid = {
          listos: $("#listosGrid"),
          revisar: $("#revisarGrid"),
          raw: $("#rawGrid")
        }[kind];
        const input = $("#search" + kind[0].toUpperCase() + kind.slice(1));
        const query = String(input?.value || "").trim().toLowerCase();
        const active =
          document.querySelector(
            '.filter-chip.active[data-scope="' + kind + '"]'
          )?.dataset.filter || "all";

        if (!grid) {
          return;
        }

        grid.querySelectorAll(".clip-card").forEach((card) => {
          const matches =
            !query || card.dataset.search.includes(query);
          const seconds = Number(card.dataset.duration || 0);
          const score = Number(card.dataset.score || 0);
          card.hidden = !(
            matches &&
            (active === "all" ||
              (active === "long" && seconds > 30) ||
              (active === "high" && score >= 70))
          );
        });
      }

      function clipTimestamp(clip) {
        const timestamp = Number(clip?.timestamp);
        if (Number.isFinite(timestamp) && timestamp > 0) {
          return timestamp;
        }

        const name = String(clip?.nombre || "");
        const compact = name.match(
          /(\d{8})-(\d{6})(?:-\d+)?(?:\.mp4)?$/i
        );
        if (compact) {
          return Number(compact[1] + compact[2]);
        }

        const separated = name.match(
          /(\d{4})-(\d{2})-(\d{2})-(\d{6})(?:-\d+)?(?:\.mp4)?$/i
        );
        return separated
          ? Number(
              separated[1] +
                separated[2] +
                separated[3] +
                separated[4]
            )
          : 0;
      }

      function summary() {
        const listos = state.clips.listos || [];
        const revisar = state.clips.revisar || [];
        const raw = state.clips.raw || [];
        const all = listos.concat(revisar, raw);
        const channels = new Set(
          all.map((clip) => clip.canal).filter(Boolean)
        );
        const latest = all.reduce(
          (current, clip) =>
            !current || clipTimestamp(clip) > clipTimestamp(current)
              ? clip
              : current,
          null
        );

        setText("#countListos", listos.length);
        setText("#tabCountListos", listos.length);
        setText("#countRevisar", revisar.length);
        setText("#tabCountRevisar", revisar.length);
        setText("#tabCountRaw", raw.length);
        setText("#reviewTotal", revisar.length);
        setText("#countCanales", channels.size);
        setText(
          "#lastClipTime",
          latest ? clipTime(latest.nombre, latest.timestamp) : "--:--"
        );
        setText(
          "#lastClipNote",
          latest ? latest.canal || "último archivo" : "aún sin datos"
        );
        setText(
          "#countListosNote",
          listos.length === 1 ? "publicable" : "publicables"
        );
        setText(
          "#countRevisarNote",
          revisar.length === 1 ? "pendiente" : "pendientes"
        );
        setText("#systemState", all.length ? "Operativo" : "En espera");
        setText(
          "#systemDetail",
          all.length
            ? channels.size + " canales · " + all.length + " cortes disponibles"
            : "Esperando el primer candidato"
        );
        setText(
          "#listosNote",
          listos.length + " archivos · actualización automática"
        );
        setText(
          "#revisarNote",
          revisar.length + " archivos · revisión humana"
        );
        setText("#rawNote", raw.length + " candidatos · pausa tras Whisper");
      }

      function cargarClips(force) {
        fetch("/api/clips", { cache: "no-store" })
          .then((response) => {
            if (!response.ok) {
              throw Error("HTTP " + response.status);
            }
            return response.json();
          })
          .then((data) => {
            const clean = {
              listos: Array.isArray(data.listos) ? data.listos : [],
              revisar: Array.isArray(data.revisar) ? data.revisar : [],
              raw: Array.isArray(data.raw) ? data.raw : []
            };
            const signature = JSON.stringify(clean);
            const changed = force || signature !== state.signature;

            state.clips = clean;
            state.signature = signature;
            summary();

            if (changed) {
              render("listos");
              render("revisar");
              render("raw");
            }
            connection(
              "live",
              "SINCRONIZADO",
              new Date().toLocaleTimeString("es-ES", {
                hour: "2-digit",
                minute: "2-digit"
              })
            );
          })
          .catch((error) => {
            state.signature = "";
            connection("error", "SIN CONEXIÓN", "error");
            setText("#systemState", "Revisar conexión");
            setText("#systemDetail", "La galería no responde ahora");
            $("#listosGrid").setAttribute("aria-busy", "false");
            $("#revisarGrid").setAttribute("aria-busy", "false");
            $("#rawGrid").setAttribute("aria-busy", "false");
            $("#listosGrid").replaceChildren(errorState(error.message));
            $("#revisarGrid").replaceChildren(errorState(error.message));
            $("#rawGrid").replaceChildren(errorState(error.message));
            notify("No se pudieron cargar los clips.", true);
          });
      }

      function switchTab(tab) {
        state.tab = tab;
        $$(".tab").forEach((button) => {
          button.setAttribute(
            "aria-selected",
            button.dataset.tab === tab ? "true" : "false"
          );
        });
        $$(".panel").forEach((panel) => {
          panel.hidden = panel.id !== "panel-" + tab;
        });

        if (tab === "explorador") {
          cargarArchivosSeguro(state.path);
        }
        if (tab === "logs") {
          cargarLogs();
        }
      }

      function breadcrumbs(path) {
        const root = $("#breadcrumbs");
        root.replaceChildren();

        const add = (label, target) => {
          const button = text("button", "", label);
          button.type = "button";
          button.addEventListener("click", () => {
            cargarArchivosSeguro(target);
          });
          root.appendChild(button);
        };

        add("DATA", "");

        let accumulated = "";
        String(path || "")
          .split("/")
          .filter(Boolean)
          .forEach((part) => {
            root.appendChild(text("span", "", "/"));
            accumulated = accumulated
              ? accumulated + "/" + part
              : part;
            add(part, accumulated);
          });
      }

      function cargarArchivosSeguro(path) {
        state.path = String(path || "").replace(/^[/]+|[/]+$/g, "");

        fetch(
          "/api/browse?path=" + encodeURIComponent(state.path),
          { cache: "no-store" }
        )
          .then((response) => {
            if (!response.ok) {
              throw Error("HTTP " + response.status);
            }
            return response.json();
          })
          .then((data) => {
            state.path = String(data.path || "").replace(
              /^[/]+|[/]+$/g,
              ""
            );
            breadcrumbs(state.path);

            const grid = $("#fileGrid");
            grid.replaceChildren();
            const items = Array.isArray(data.items) ? data.items : [];

            if (!items.length) {
              grid.appendChild(emptyState("listos"));
              setText("#explorerNote", "Esta carpeta está vacía");
              return;
            }

            setText("#explorerNote", items.length + " elementos");
            items.forEach((item) => {
              const target = state.path
                ? state.path + "/" + item.name
                : item.name;

              if (item.is_dir) {
                const button = text("button", "file-item");
                button.type = "button";
                button.innerHTML = icon("folder");

                const copy = text("span", "file-copy");
                copy.append(
                  text("span", "file-name", item.name),
                  text("span", "file-size", "Carpeta")
                );
                button.appendChild(copy);
                button.addEventListener("click", () => {
                  cargarArchivosSeguro(target);
                });
                grid.appendChild(button);
                return;
              }

              const isVideo = /[.]mp4$/i.test(item.name);
              const isText = /[.](txt|log|csv|json)$/i.test(item.name);
              const card = text("div", "file-item");

              card.appendChild(iconNode(isVideo ? "activity" : "file"));

              const copy = text("span", "file-copy");
              copy.append(
                text("span", "file-name", item.name),
                text("span", "file-size", item.size || "Archivo")
              );
              card.appendChild(copy);

              const actions = text("span", "file-actions");
              if (isVideo || isText) {
                const preview = text("button", "icon-button");
                preview.type = "button";
                preview.setAttribute(
                  "aria-label",
                  "Previsualizar " + item.name
                );
                preview.title = "Previsualizar";
                preview.innerHTML = icon("play");
                preview.addEventListener("click", () => {
                  abrirPrevisualizacionSeguro(
                    target,
                    item.name,
                    isVideo ? "video" : "text"
                  );
                });
                actions.appendChild(preview);
              }

              const download = document.createElement("a");
              download.className = "icon-button";
              download.href = safeFileUrl(target);
              download.download = "";
              download.setAttribute(
                "aria-label",
                "Descargar " + item.name
              );
              download.title = "Descargar";
              download.innerHTML = icon("download");
              actions.appendChild(download);

              card.appendChild(actions);
              grid.appendChild(card);
            });
          })
          .catch((error) => {
            $("#fileGrid").replaceChildren(errorState(error.message));
            notify("No se pudo abrir el explorador.", true);
          });
      }

      function cargarLogs() {
        fetch("/api/logs", { cache: "no-store" })
          .then((response) => {
            if (!response.ok) {
              throw Error("HTTP " + response.status);
            }
            return response.text();
          })
          .then((body) => {
            $("#logsBox").textContent = body;
            setText(
              "#logsNote",
              "Actualizado " +
                new Date().toLocaleTimeString("es-ES", {
                  hour: "2-digit",
                  minute: "2-digit"
                })
            );
          })
          .catch((error) => {
            $("#logsBox").textContent =
              "No se pudieron cargar los registros: " + error.message;
            notify("No se pudieron cargar los registros.", true);
          });
      }

      function abrirPrevisualizacionSeguro(url, title, type) {
        state.modalFocus = document.activeElement;
        $("#previewTitle").textContent = title || "Previsualización";

        const body = $("#previewBody");
        const safe = safeFileUrl(url);
        body.replaceChildren();

        if (type === "text") {
          const pre = document.createElement("pre");
          pre.textContent = "Cargando archivo…";
          body.appendChild(pre);

          fetch(safe, { cache: "no-store" })
            .then((response) => {
              if (!response.ok) {
                throw Error("HTTP " + response.status);
              }
              return response.text();
            })
            .then((value) => {
              pre.textContent = value || "(Archivo vacío)";
            })
            .catch(() => {
              pre.textContent = "No se pudo cargar el contenido del archivo.";
            });
        } else {
          const video = document.createElement("video");
          video.controls = true;
          video.autoplay = true;
          video.playsInline = true;
          video.preload = "metadata";
          video.src = safe;
          video.setAttribute(
            "aria-label",
            "Previsualización de " + (title || "vídeo")
          );
          body.appendChild(video);
        }

        $("#previewModal").hidden = false;
        document.body.style.overflow = "hidden";
        $("#closePreview").focus();
      }

      function cerrarPrevisualizacion() {
        const modal = $("#previewModal");
        $("#previewBody").replaceChildren();
        modal.hidden = true;
        document.body.style.overflow = "";

        if (
          state.modalFocus &&
          typeof state.modalFocus.focus === "function"
        ) {
          state.modalFocus.focus();
        }
      }

      $$(".tab").forEach((button) => {
        button.addEventListener("click", () => {
          switchTab(button.dataset.tab);
        });
      });

      $$(".filter-chip").forEach((button) => {
        button.addEventListener("click", () => {
          $$('.filter-chip[data-scope="' + button.dataset.scope + '"]')
            .forEach((item) => {
              item.classList.toggle("active", item === button);
            });
          filter(button.dataset.scope);
        });
      });

      $("#searchListos").addEventListener("input", () => filter("listos"));
      $("#searchRevisar").addEventListener("input", () => filter("revisar"));
      $("#searchRaw").addEventListener("input", () => filter("raw"));

      $("#refreshButton").addEventListener("click", () => {
        cargarClips(true);
        if (state.tab === "explorador") {
          cargarArchivosSeguro(state.path);
        }
        if (state.tab === "logs") {
          cargarLogs();
        }
      });

      $("#closePreview").addEventListener("click", cerrarPrevisualizacion);
      $("#previewModal").addEventListener("click", (event) => {
        if (event.target.id === "previewModal") {
          cerrarPrevisualizacion();
        }
      });

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !$("#previewModal").hidden) {
          cerrarPrevisualizacion();
        }
      });

      window.abrirPrevisualizacionSeguro = abrirPrevisualizacionSeguro;
      window.cerrarPrevisualizacion = cerrarPrevisualizacion;
      window.filtrarClips = () => {
        filter("listos");
        filter("revisar");
      };

      summary();
      cargarClips(true);
      window.setInterval(() => {
        if (document.visibilityState !== "hidden") {
          cargarClips(false);
        }
      }, 15000);
    }());
  </script>
</body>
</html>
"""

class Handler(SimpleHTTPRequestHandler):
    usuario = ""
    clave = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DATA), **kwargs)

    def _pedir_credenciales(self):
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Clipper Studio"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _autorizado(self) -> bool:
        cabecera = self.headers.get("Authorization", "")
        if not cabecera.startswith("Basic "):
            return False
        try:
            texto = base64.b64decode(cabecera[6:]).decode("utf-8")
            usuario, _, clave = texto.partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return (hmac.compare_digest(usuario, self.usuario)
                and hmac.compare_digest(clave, self.clave))

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path == "/salud":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Length", "2")
            self.end_headers()
            return
        if not self._autorizado():
            return self._pedir_credenciales()
        if path.startswith("/files/"):
            target = self._archivo_publico(unquote(path[7:]))
            if target is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.path = "/" + target.relative_to(DATA.resolve()).as_posix()
        super().do_HEAD()

    def _responder_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _responder_html(self, html_str):
        body = html_str.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _archivo_publico(self, rel_path: str) -> Path | None:
        root = DATA.resolve()
        target = (root / rel_path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        try:
            out_root = OUT.resolve()
            if not target.is_relative_to(out_root):
                return None
            relative = target.relative_to(out_root)
        except (OSError, ValueError):
            return None
        if not relative.parts or relative.parts[0] not in {"LISTOS", "REVISAR", "RAW"}:
            return None
        if target.suffix.lower() not in {".mp4", ".txt"}:
            return None
        return target

    def do_GET(self):
        if self.path == "/salud":
            cuerpo = b"ok"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return

        if not self._autorizado():
            return self._pedir_credenciales()

        url_parsed = urlparse(self.path)
        path = url_parsed.path

        if path in ["/", "/index.html"]:
            return self._responder_html(HTML_TEMPLATE)

        if path == "/api/clips":
            return self._handle_api_clips()

        if path == "/api/browse":
            query = parse_qs(url_parsed.query)
            subpath = query.get("path", [""])[0]
            return self._handle_api_browse(subpath)

        if path == "/api/logs":
            return self._handle_api_logs()

        if path.startswith("/files/"):
            target = self._archivo_publico(unquote(path[7:]))
            if target is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.path = "/" + target.relative_to(DATA.resolve()).as_posix()

        super().do_GET()

    def _handle_api_clips(self):
        listos_dir = OUT / "LISTOS"
        revisar_dir = OUT / "REVISAR"

        listos = self._obtener_clips_dir(listos_dir, es_revisar=False)
        revisar = self._obtener_clips_dir(revisar_dir, es_revisar=True)

        datos = {"listos": listos, "revisar": revisar, "raw": raw.listar_api()}
        # Al terminar el barrido, no en cada fichero: asi un arranque en frio
        # escribe el indice una vez en vez de 87 veces.
        guardar_duraciones()
        self._responder_json(datos)

    def _obtener_clips_dir(self, dir_path: Path, es_revisar: bool) -> list:
        clips = []
        if not dir_path.exists():
            return clips

        for mp4 in sorted(dir_path.glob("*.mp4"), reverse=True):
            canal = clipper.canal_desde_nombre(mp4.name)

            try:
                timestamp = mp4.stat().st_mtime
            except OSError:
                timestamp = 0

            gancho = ""
            motivo = ""
            llm = {}
            txt_file = mp4.with_suffix(".txt")
            motivos_file = mp4.with_suffix(".motivos.txt")

            if motivos_file.exists():
                motivo, llm, gancho = _leer_motivos(motivos_file)

            if txt_file.exists():
                contenido = txt_file.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"gancho en pantalla:\s*(.*)", contenido)
                if m:
                    gancho = m.group(1).strip()

            descripcion, hashtags = _leer_ficha_publicable(txt_file) if txt_file.exists() else ("", [])
            if llm:
                descripcion = llm.get("social_description") or descripcion
                hashtags = llm.get("hashtags") or hashtags

            rel_url = f"/files/out/{'REVISAR' if es_revisar else 'LISTOS'}/{quote(mp4.name, safe='')}"
            txt_url = (f"/files/out/{'REVISAR' if es_revisar else 'LISTOS'}/{quote(txt_file.name, safe='')}"
                       if txt_file.exists() else "")
            try:
                txt_size = txt_file.stat().st_size if txt_file.exists() else None
            except OSError:
                txt_size = None
            duracion = _duracion_video(mp4)

            clips.append({
                "nombre": mp4.name,
                "timestamp": timestamp,
                "canal": canal,
                "duracion": round(duracion),
                "gancho": gancho or mp4.stem,
                "motivo": motivo,
                "llm": llm,
                "description": descripcion,
                "hashtags": hashtags,
                "url": rel_url,
                "txt_url": txt_url,
                "txt_size": txt_size,
            })
        return clips

    def _handle_api_browse(self, subpath: str):
        root = DATA.resolve()
        target = (root / subpath).resolve()
        if not target.is_relative_to(root):
            target = root

        items = []
        if target.exists() and target.is_dir():
            for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                size_str = "-"
                if not p.is_dir():
                    bytes_sz = p.stat().st_size
                    size_str = f"{bytes_sz / (1024*1024):.1f} MB" if bytes_sz >= 1024*1024 else f"{bytes_sz / 1024:.1f} KB"

                items.append({
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size": size_str,
                })

        self._responder_json({"path": subpath, "items": items})

    def _handle_api_logs(self):
        logs_dir = DATA / "logs"
        log_text = ""
        if logs_dir.exists():
            archivos = list(logs_dir.glob("*.log")) + list(logs_dir.glob("*.jsonl"))
            for log_file in sorted(archivos, reverse=True):
                try:
                    contenido = log_file.read_text(encoding="utf-8", errors="replace")
                    lineas = contenido.splitlines()[-60:]
                    log_text += f"=== Log: {log_file.name} ===\n" + "\n".join(lineas) + "\n\n"
                except Exception:
                    pass
        body = (log_text or "No hay archivos de registro en /app/clips/logs").encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def log_message(self, formato, *args):
        pass

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def arrancar(puerto: int = None, en_hilo: bool = False):
    usuario = os.environ.get("CLIPPER_WEB_USUARIO", "clips")
    clave = os.environ.get("CLIPPER_WEB_CLAVE", "").strip()
    if not clave or clave.casefold() == "pon-aqui-una-clave-larga":
        raise RuntimeError("CLIPPER_WEB_CLAVE debe ser una clave real y no el marcador del ejemplo")
    puerto = puerto or int(os.environ.get("CLIPPER_WEB_PUERTO", "8080"))

    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    raw.recuperar_huerfanos()

    Handler.usuario, Handler.clave = usuario, clave
    handler = partial(Handler)
    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), handler)
    servidor.daemon_threads = True

    print(f"🌐 GALERÍA WEB ACTIVA\n   URL: http://0.0.0.0:{puerto}\n   USUARIO: {usuario}")
    if en_hilo:
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        return servidor
    servidor.serve_forever()


if __name__ == "__main__":
    arrancar()
