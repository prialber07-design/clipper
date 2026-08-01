"""
Galeria web y explorador de archivos intuitivo para /app/clips.

Diseñado con interfaz ultra-moderna (Shadcn/UI + GSAP + Lucide Icons).
Interfaz muy limpia, accesible e intuitiva (apta para todo tipo de usuarios).
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


def _duracion_video(path: Path) -> int:
    try:
        stat = path.stat()
    except OSError:
        return 0
    return _duracion_video_cache(path, stat.st_mtime_ns, stat.st_size)


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

HTML_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Clipper Studio · Gestor de Clips y Archivos</title>

  <!-- Fuentes Tipográficas Pro -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Space+Grotesk:wght@600;700&display=swap" rel="stylesheet">

  <!-- Lucide Icons -->
  <script src="https://unpkg.com/lucide@latest"></script>

  <!-- GSAP Animation Engine -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>

  <style>
    :root {
      --bg-dark: #090d16;
      --bg-card: #131927;
      --bg-card-hover: #1c253b;
      --bg-glass: rgba(19, 25, 39, 0.75);
      
      --accent: #6366f1;
      --accent-gradient: linear-gradient(135deg, #6366f1, #8b5cf6);
      --accent-hover: #4f46e5;
      
      --success: #10b981;
      --success-gradient: linear-gradient(135deg, #10b981, #059669);
      
      --warning: #f59e0b;
      --danger: #ef4444;
      
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      
      --border: rgba(255, 255, 255, 0.08);
      --border-bright: rgba(255, 255, 255, 0.18);
      --shadow-card: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
      --shadow-glow: 0 0 25px rgba(99, 102, 241, 0.25);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
      background-color: var(--bg-dark);
      color: var(--text-main);
      min-height: 100vh;
      padding-bottom: 4rem;
      line-height: 1.5;
      overflow-x: hidden;
    }

    /* Fondo animado sutil */
    .bg-glow {
      position: fixed;
      top: -200px;
      left: 50%;
      transform: translateX(-50%);
      width: 800px;
      height: 400px;
      background: radial-gradient(circle, rgba(99, 102, 241, 0.15) 0%, rgba(9, 13, 22, 0) 70%);
      pointer-events: none;
      z-index: 0;
    }

    /* Header Nav */
    header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: var(--bg-glass);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--border);
      padding: 1.1rem 1.75rem;
    }

    .header-container {
      max-width: 1350px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }

    .logo-area {
      display: flex;
      align-items: center;
      gap: 0.85rem;
    }

    .logo-badge {
      width: 44px;
      height: 44px;
      background: var(--accent-gradient);
      border-radius: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      box-shadow: var(--shadow-glow);
    }

    .logo-text h1 {
      font-family: 'Space Grotesk', sans-serif;
      font-size: 1.4rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }

    .logo-text p {
      font-size: 0.8rem;
      color: var(--text-muted);
      font-weight: 500;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.6rem;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #34d399;
      padding: 0.5rem 1.1rem;
      border-radius: 999px;
      font-size: 0.85rem;
      font-weight: 700;
    }

    .status-dot {
      width: 9px;
      height: 9px;
      background-color: #34d399;
      border-radius: 50%;
      box-shadow: 0 0 12px #34d399;
    }

    main {
      max-width: 1350px;
      margin: 2rem auto 0;
      padding: 0 1.75rem;
      position: relative;
      z-index: 1;
    }

    /* Pestañas de Navegación */
    .tabs-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 1.1rem;
      margin-bottom: 2.25rem;
    }

    .tab-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 1.25rem 1.25rem;
      border-radius: 18px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.9rem;
      font-family: inherit;
      font-weight: 700;
      font-size: 1.05rem;
      transition: border-color 0.2s, background 0.2s;
      box-shadow: var(--shadow-card);
    }

    .tab-card:hover {
      background: var(--bg-card-hover);
      color: var(--text-main);
      border-color: var(--border-bright);
    }

    .tab-card.active {
      background: var(--accent-gradient);
      color: #ffffff;
      border-color: transparent;
      box-shadow: var(--shadow-glow);
    }

    .tab-card .tab-badge {
      margin-left: auto;
      background: rgba(255, 255, 255, 0.18);
      padding: 0.25rem 0.7rem;
      border-radius: 999px;
      font-size: 0.85rem;
    }

    /* Search Bar */
    .search-container {
      margin-bottom: 2.25rem;
      position: relative;
    }

    .search-field {
      width: 100%;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.1rem 1.25rem 1.1rem 3.2rem;
      color: var(--text-main);
      font-size: 1.05rem;
      font-family: inherit;
      outline: none;
      transition: border-color 0.2s, box-shadow 0.2s;
    }

    .search-field:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.2);
    }

    .search-icon-svg {
      position: absolute;
      left: 1.1rem;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
    }

    /* Content Views */
    .view-pane {
      display: none;
    }

    .view-pane.active {
      display: block;
    }

    /* Clips Cards Grid */
    .clips-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 2rem;
    }

    .clip-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 22px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      box-shadow: var(--shadow-card);
      transition: border-color 0.3s;
    }

    .clip-card:hover {
      border-color: var(--border-bright);
    }

    .video-thumb-container {
      position: relative;
      width: 100%;
      background: #000;
      aspect-ratio: 9 / 16;
      max-height: 480px;
      cursor: pointer;
      overflow: hidden;
    }

    .video-thumb-container video {
      width: 100%;
      height: 100%;
      object-fit: contain;
    }

    .thumb-play-overlay {
      position: absolute;
      inset: 0;
      background: rgba(0, 0, 0, 0.35);
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: 0;
      transition: opacity 0.25s ease;
    }

    .video-thumb-container:hover .thumb-play-overlay {
      opacity: 1;
    }

    .play-circle-icon {
      width: 64px;
      height: 64px;
      background: var(--accent-gradient);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      box-shadow: 0 0 30px rgba(99, 102, 241, 0.6);
      transform: scale(0.85);
      transition: transform 0.25s ease;
    }

    .video-thumb-container:hover .play-circle-icon {
      transform: scale(1);
    }

    .clip-body {
      padding: 1.4rem;
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
      flex-grow: 1;
    }

    .tags-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
    }

    .streamer-pill {
      background: rgba(99, 102, 241, 0.18);
      color: #a5b4fc;
      font-weight: 800;
      font-size: 0.85rem;
      padding: 0.35rem 0.85rem;
      border-radius: 10px;
    }

    .dur-pill {
      background: rgba(255, 255, 255, 0.08);
      color: var(--text-muted);
      font-weight: 700;
      font-size: 0.8rem;
      padding: 0.35rem 0.75rem;
      border-radius: 10px;
    }

    .clip-heading {
      font-size: 1.1rem;
      font-weight: 800;
      line-height: 1.4;
      color: var(--text-main);
    }

    .clip-alert {
      font-size: 0.85rem;
      color: #fca5a5;
      background: rgba(239, 68, 68, 0.14);
      border-left: 4px solid var(--danger);
      padding: 0.6rem 0.85rem;
      border-radius: 8px;
      font-weight: 600;
    }

    /* Action Buttons (Para Señor Mayor: Claros y Grandes) */
    .btn-group {
      display: flex;
      gap: 0.75rem;
      margin-top: auto;
      padding-top: 0.5rem;
    }

    .btn-action-primary {
      flex: 1.3;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.6rem;
      background: var(--success-gradient);
      color: #ffffff;
      text-decoration: none;
      font-weight: 800;
      font-size: 1rem;
      padding: 1rem;
      border-radius: 14px;
      box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3);
      border: none;
      cursor: pointer;
      transition: opacity 0.2s;
    }

    .btn-action-primary:hover {
      opacity: 0.92;
    }

    .btn-action-secondary {
      flex: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      background: rgba(255, 255, 255, 0.08);
      color: var(--text-main);
      font-weight: 700;
      font-size: 0.95rem;
      padding: 1rem;
      border-radius: 14px;
      border: 1px solid var(--border);
      cursor: pointer;
      transition: background 0.2s, border-color 0.2s;
    }

    .btn-action-secondary:hover {
      background: var(--bg-card-hover);
      border-color: var(--border-bright);
    }

    /* Explorador de Archivos */
    .breadcrumbs-box {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
      background: var(--bg-card);
      padding: 1.1rem 1.5rem;
      border-radius: 18px;
      margin-bottom: 1.75rem;
      border: 1px solid var(--border);
      font-weight: 700;
      font-size: 1.05rem;
    }

    .crumb-link {
      color: var(--accent);
      cursor: pointer;
    }

    .crumb-link:hover {
      text-decoration: underline;
    }

    .crumb-divider {
      color: var(--text-dim);
    }

    .files-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1.35rem;
    }

    .file-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 1.35rem;
      display: flex;
      align-items: center;
      gap: 1.1rem;
      cursor: pointer;
      box-shadow: var(--shadow-card);
      transition: border-color 0.2s, background 0.2s;
    }

    .file-card:hover {
      background: var(--bg-card-hover);
      border-color: var(--border-bright);
    }

    .file-card-icon {
      width: 48px;
      height: 48px;
      background: rgba(99, 102, 241, 0.12);
      border-radius: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--accent);
      flex-shrink: 0;
    }

    .file-card-meta {
      overflow: hidden;
      flex-grow: 1;
    }

    .file-card-name {
      font-weight: 800;
      font-size: 1rem;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .file-card-sub {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-top: 0.2rem;
    }

    /* Visor de Logs */
    .terminal-box {
      background: #040711;
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 1.75rem;
      font-family: 'Space Grotesk', monospace;
      font-size: 0.95rem;
      color: #38bdf8;
      height: 520px;
      overflow-y: auto;
      white-space: pre-wrap;
      box-shadow: inset 0 4px 20px rgba(0,0,0,0.6);
      line-height: 1.6;
    }

    .btn-refresh {
      margin-bottom: 1.25rem;
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-main);
      padding: 0.9rem 1.4rem;
      border-radius: 14px;
      font-weight: 700;
      font-size: 1rem;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.6rem;
      transition: background 0.2s;
    }

    .btn-refresh:hover {
      background: var(--accent);
    }

    /* Modal Emergente de Previsualización */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.85);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      z-index: 1000;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
    }

    .modal-backdrop.active {
      display: flex;
    }

    .modal-window {
      background: var(--bg-card);
      border: 1px solid var(--border-bright);
      border-radius: 26px;
      width: 100%;
      max-width: 580px;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: 0 30px 60px rgba(0,0,0,0.8);
    }

    .modal-top {
      padding: 1.35rem 1.6rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
    }

    .modal-title-text {
      font-size: 1.15rem;
      font-weight: 800;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .btn-close-modal {
      background: rgba(255, 255, 255, 0.1);
      border: none;
      color: var(--text-main);
      width: 40px;
      height: 40px;
      border-radius: 50%;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s;
    }

    .btn-close-modal:hover {
      background: var(--danger);
    }

    .modal-center {
      padding: 1.5rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
    }

    .video-player-modal {
      width: 100%;
      max-height: 58vh;
      border-radius: 18px;
      background: #000;
      object-fit: contain;
      aspect-ratio: 9 / 16;
    }

    .modal-bottom {
      padding: 1.35rem 1.6rem;
      border-top: 1px solid var(--border);
    }

    .empty-box {
      text-align: center;
      padding: 4.5rem 2rem;
      background: var(--bg-card);
      border-radius: 24px;
      border: 1px dashed var(--border);
      grid-column: 1 / -1;
    }

    .empty-icon-svg {
      margin-bottom: 1rem;
      color: var(--text-muted);
    }
  </style>
</head>
<body>

  <div class="bg-glow"></div>

  <!-- Header Navbar -->
  <header>
    <div class="header-container">
      <div class="logo-area">
        <div class="logo-badge">
          <i data-lucide="film" style="width:24px;height:24px;"></i>
        </div>
        <div class="logo-text">
          <h1>Clipper Studio</h1>
          <p>Gestor de Vídeos y Archivos</p>
        </div>
      </div>
      <div class="status-pill">
        <span class="status-dot"></span>
        <span>Servidor en Línea · /app/clips</span>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main>
    <!-- Navigation Tabs -->
    <nav class="tabs-grid">
      <button class="tab-card active" onclick="switchTab('listos', this)">
        <i data-lucide="play-circle" style="width:24px;height:24px;"></i>
        <span>Clips Listos</span>
        <span class="tab-badge" id="count-listos">0</span>
      </button>
      <button class="tab-card" onclick="switchTab('revisar', this)">
        <i data-lucide="alert-triangle" style="width:24px;height:24px;"></i>
        <span>En Revisión</span>
        <span class="tab-badge" id="count-revisar">0</span>
      </button>
      <button class="tab-card" onclick="switchTab('explorador', this)">
        <i data-lucide="folder" style="width:24px;height:24px;"></i>
        <span>Explorador /app/clips</span>
      </button>
      <button class="tab-card" onclick="switchTab('logs', this)">
        <i data-lucide="terminal" style="width:24px;height:24px;"></i>
        <span>Logs del Servidor</span>
      </button>
    </nav>

    <!-- Search Input -->
    <div class="search-container">
      <i data-lucide="search" class="search-icon-svg" style="width:22px;height:22px;"></i>
      <input type="text" id="searchInput" class="search-field" placeholder="Buscar clip por streamer o título del gancho..." onkeyup="filtrarClips()">
    </div>

    <!-- Tab 1: Clips Listos -->
    <section id="tab-listos" class="view-pane active">
      <div class="clips-grid" id="grid-listos"></div>
    </section>

    <!-- Tab 2: Clips en Revisión -->
    <section id="tab-revisar" class="view-pane">
      <div class="clips-grid" id="grid-revisar"></div>
    </section>

    <!-- Tab 3: Explorador de Archivos -->
    <section id="tab-explorador" class="view-pane">
      <div class="breadcrumbs-box" id="breadcrumbs"></div>
      <div class="files-grid" id="files-grid"></div>
    </section>

    <!-- Tab 4: Logs del Servidor -->
    <section id="tab-logs" class="view-pane">
      <button class="btn-refresh" onclick="cargarLogs()">
        <i data-lucide="refresh-cw" style="width:20px;height:20px;"></i>
        <span>Actualizar Registros</span>
      </button>
      <div class="terminal-box" id="logs-box">Cargando registros...</div>
    </section>
  </main>

  <!-- Modal Previsualizador de Vídeos y Archivos -->
  <div class="modal-backdrop" id="previewModal" onclick="cerrarPrevisualizacion(event)">
    <div class="modal-window" onclick="event.stopPropagation()">
      <div class="modal-top">
        <h3 class="modal-title-text" id="modalTitle">Previsualización</h3>
        <button class="btn-close-modal" onclick="cerrarPrevisualizacion()">
          <i data-lucide="x" style="width:20px;height:20px;"></i>
        </button>
      </div>
      <div class="modal-center" id="modalBody"></div>
      <div class="modal-bottom">
        <a id="modalDownloadBtn" href="#" download class="btn-action-primary">
          <i data-lucide="download" style="width:20px;height:20px;"></i>
          <span>DESCARGAR VÍDEO AHORA</span>
        </a>
      </div>
    </div>
  </div>

  <script>
    let currentPath = '';
    let firmaClips = '';

    document.addEventListener('DOMContentLoaded', () => {
      lucide.createIcons();
      animateHeader();
      cargarClips();
      cargarArchivosSeguro('');
      cargarLogs();
      setInterval(() => {
        cargarClips();
        cargarLogs();
      }, 15000);

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') cerrarPrevisualizacion();
      });
    });

    function animateHeader() {
      gsap.from('header', { y: -30, opacity: 0, duration: 0.6, ease: 'power3.out' });
      gsap.from('.tab-card', { y: 20, opacity: 0, duration: 0.5, stagger: 0.08, ease: 'power2.out' });
    }

    function animateGrid(selector) {
      gsap.from(selector, { y: 25, opacity: 0, duration: 0.4, stagger: 0.05, ease: 'power2.out' });
    }

    function switchTab(tabId, btn) {
      document.querySelectorAll('.tab-card').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.view-pane').forEach(c => c.classList.remove('active'));

      btn.classList.add('active');
      const view = document.getElementById('tab-' + tabId);
      view.classList.add('active');

      if (tabId === 'listos') animateGrid('#grid-listos .clip-card');
      if (tabId === 'revisar') animateGrid('#grid-revisar .clip-card');
      if (tabId === 'explorador') animateGrid('#files-grid .file-card');
    }

    async function cargarClips() {
      try {
        const res = await fetch('/api/clips');
        const data = await res.json();

        document.getElementById('count-listos').textContent = data.listos.length;
        document.getElementById('count-revisar').textContent = data.revisar.length;

        const nuevaFirma = JSON.stringify(data);
        if (nuevaFirma === firmaClips) return;
        firmaClips = nuevaFirma;
        renderGridSeguro('grid-listos', data.listos, false);
        renderGridSeguro('grid-revisar', data.revisar, true);
        lucide.createIcons();
      } catch (e) {
        console.error("Error cargando clips:", e);
      }
    }

    function safeFileUrl(value) {
      try {
        const url = new URL(value, window.location.origin);
        if (url.origin !== window.location.origin || !url.pathname.startsWith('/files/')) return '#';
        return url.href;
      } catch (_) {
        return '#';
      }
    }

    function textNode(tag, className, text) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      node.textContent = text || '';
      return node;
    }

    function icon(name, size = 18) {
      const node = document.createElement('i');
      node.dataset.lucide = name;
      node.style.width = `${size}px`;
      node.style.height = `${size}px`;
      return node;
    }

    function renderGridSeguro(containerId, clips, esRevisar) {
      const container = document.getElementById(containerId);
      container.replaceChildren();
      if (!clips || clips.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty-box';
        empty.append(icon(esRevisar ? 'sparkles' : 'coffee', 48));
        empty.append(textNode('h3', '', esRevisar ? 'No hay clips pendientes de revisión' : 'No hay clips listos para subir todavía'));
        empty.append(textNode('p', '', esRevisar ? 'Todos los clips generados han superado el filtro de calidad.' : 'Los clips aparecerán aquí de forma automática en cuanto salten picos en los directos.'));
        container.append(empty);
        lucide.createIcons();
        return;
      }

      clips.forEach((clip) => {
        const url = safeFileUrl(clip.url);
        const article = document.createElement('article');
        article.className = 'clip-card';
        article.dataset.search = `${clip.canal || ''} ${clip.gancho || ''}`.toLowerCase();

        const thumb = document.createElement('div');
        thumb.className = 'video-thumb-container';
        const video = document.createElement('video');
        video.src = url;
        video.controls = true;
        video.preload = 'none';
        video.playsInline = true;
        thumb.append(video);
        thumb.addEventListener('click', () => abrirPrevisualizacionSeguro(url, clip.gancho || clip.nombre, 'video'));
        article.append(thumb);

        const body = document.createElement('div');
        body.className = 'clip-body';
        const tags = document.createElement('div');
        tags.className = 'tags-row';
        tags.append(textNode('span', 'streamer-pill', `#${clip.canal || 'desconocido'}`));
        tags.append(textNode('span', 'dur-pill', `⏱️ ${clip.duracion || 0}s`));
        body.append(tags, textNode('h2', 'clip-heading', clip.gancho || '(Sin título)'));
        if (esRevisar && clip.motivo) body.append(textNode('div', 'clip-alert', `⚠️ ${clip.motivo}`));
        if (esRevisar && clip.llm && typeof clip.llm === 'object') {
          const decision = String(clip.llm.decision || 'revisar').toUpperCase();
          const score = Number.isFinite(Number(clip.llm.score)) ? Number(clip.llm.score) : 0;
          const confidence = Number.isFinite(Number(clip.llm.confidence))
            ? ` · confianza ${(Number(clip.llm.confidence) * 100).toFixed(0)}%` : '';
          const panel = document.createElement('div');
          panel.className = 'clip-alert';
          panel.append(
            textNode('div', '', `🤖 LUNA · ${decision} · ${score}/100${confidence}`),
            textNode('div', '', clip.llm.reason || 'Sin motivo registrado')
          );
          body.append(panel);
        }

        const buttons = document.createElement('div');
        buttons.className = 'btn-group';
        const view = document.createElement('button');
        view.className = 'btn-action-secondary';
        view.type = 'button';
        view.append(icon('eye'), textNode('span', '', 'Ver'));
        view.addEventListener('click', () => abrirPrevisualizacionSeguro(url, clip.gancho || clip.nombre, 'video'));
        const download = document.createElement('a');
        download.className = 'btn-action-primary';
        download.href = url;
        download.download = '';
        download.append(icon('download'), textNode('span', '', 'Descargar'));
        buttons.append(view, download);
        body.append(buttons);
        article.append(body);
        container.append(article);
      });
      lucide.createIcons();
      animateGrid('#' + containerId + ' .clip-card');
    }

    function filtrarClips() {
      const query = document.getElementById('searchInput').value.toLowerCase();
      document.querySelectorAll('.clip-card').forEach(card => {
        const text = card.getAttribute('data-search') || '';
        card.style.display = text.includes(query) ? 'flex' : 'none';
      });
    }

    async function cargarArchivosSeguro(subpath) {
      currentPath = subpath;
      try {
        const res = await fetch('/api/browse?path=' + encodeURIComponent(subpath));
        const data = await res.json();
        const breadcrumbs = document.getElementById('breadcrumbs');
        breadcrumbs.replaceChildren();
        const home = textNode('span', 'crumb-link', '🏠 Inicio (/app/clips)');
        home.addEventListener('click', () => cargarArchivosSeguro(''));
        breadcrumbs.append(home);
        let acc = '';
        for (const part of (subpath ? subpath.split('/').filter(Boolean) : [])) {
          acc += (acc ? '/' : '') + part;
          breadcrumbs.append(textNode('span', 'crumb-divider', '/'));
          const crumb = textNode('span', 'crumb-link', part);
          const target = acc;
          crumb.addEventListener('click', () => cargarArchivosSeguro(target));
          breadcrumbs.append(crumb);
        }

        const grid = document.getElementById('files-grid');
        grid.replaceChildren();
        if (!data.items || data.items.length === 0) {
          grid.append(textNode('div', 'empty-box', 'Esta carpeta está vacía.'));
          return;
        }
        for (const item of data.items) {
          const itemPath = subpath ? `${subpath}/${item.name}` : item.name;
          const fileUrl = safeFileUrl('/files/' + itemPath.split('/').map(encodeURIComponent).join('/'));
          const card = document.createElement('div');
          card.className = 'file-card';
          const isVideo = item.name.toLowerCase().endsWith('.mp4');
          const isText = /[.](txt|log|csv|json)$/i.test(item.name);
          card.append(icon(item.is_dir ? 'folder' : (isVideo ? 'film' : 'file-text'), 24));
          const meta = document.createElement('div');
          meta.className = 'file-card-meta';
          meta.append(textNode('div', 'file-card-name', item.name), textNode('div', 'file-card-sub', item.is_dir ? 'Carpeta' : item.size));
          card.append(meta);
          if (item.is_dir) {
            card.addEventListener('click', () => cargarArchivosSeguro(itemPath));
          } else if (isVideo || isText) {
            const preview = document.createElement('button');
            preview.type = 'button';
            preview.className = 'btn-action-secondary';
            preview.append(icon('eye', 16));
            preview.addEventListener('click', () => abrirPrevisualizacionSeguro(fileUrl, item.name, isVideo ? 'video' : 'text'));
            const download = document.createElement('a');
            download.className = 'btn-action-secondary';
            download.href = fileUrl;
            download.download = '';
            download.append(icon('download', 16));
            const actions = document.createElement('div');
            actions.style.cssText = 'display:flex;gap:.4rem';
            actions.append(preview, download);
            card.append(actions);
          }
          grid.append(card);
        }
        lucide.createIcons();
        animateGrid('#files-grid .file-card');
      } catch (e) {
        console.error('Error explorando archivos:', e);
      }
    }

    async function abrirPrevisualizacionSeguro(url, title, type) {
      const modal = document.getElementById('previewModal');
      const modalTitle = document.getElementById('modalTitle');
      const modalBody = document.getElementById('modalBody');
      const modalDownloadBtn = document.getElementById('modalDownloadBtn');
      const safeUrl = safeFileUrl(url);
      modalTitle.textContent = title || 'Previsualización';
      modalDownloadBtn.href = safeUrl;
      modalBody.replaceChildren();
      if (type === 'video') {
        const video = document.createElement('video');
        video.className = 'video-player-modal';
        video.src = safeUrl;
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        modalBody.append(video);
      } else if (type === 'text') {
        try {
          const res = await fetch(safeUrl);
          const text = await res.text();
          modalBody.append(textNode('div', '', text || '(Archivo vacío)'));
        } catch (_) {
          modalBody.append(textNode('p', '', 'No se pudo cargar el contenido del archivo.'));
        }
      }
      modal.classList.add('active');
      gsap.from('.modal-window', { scale: 0.9, opacity: 0, duration: 0.3, ease: 'back.out(1.5)' });
      lucide.createIcons();
    }

    function cerrarPrevisualizacion(e) {
      if (e && e.target !== document.getElementById('previewModal') && !e.target.classList.contains('btn-close-modal')) return;
      const modal = document.getElementById('previewModal');
      const modalBody = document.getElementById('modalBody');
      gsap.to('.modal-window', { scale: 0.9, opacity: 0, duration: 0.2, onComplete: () => {
        modal.classList.remove('active');
        modalBody.replaceChildren();
      }});
    }

    async function cargarLogs() {
      const box = document.getElementById('logs-box');
      box.textContent = "Cargando registros del servidor...";
      try {
        const res = await fetch('/api/logs');
        const data = await res.text();
        box.textContent = data || "No hay registros de actividad todavía.";
        box.scrollTop = box.scrollHeight;
      } catch (e) {
        box.textContent = "Error obteniendo los logs del servidor.";
      }
    }
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
            root = DATA.resolve()
            target = (root / unquote(path[7:])).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.path = "/" + target.relative_to(root).as_posix()
        super().do_HEAD()

    def _responder_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
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
            rel_path = unquote(path[7:])
            root = DATA.resolve()
            target = (root / rel_path).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.path = "/" + target.relative_to(root).as_posix()

        super().do_GET()

    def _handle_api_clips(self):
        listos_dir = OUT / "LISTOS"
        revisar_dir = OUT / "REVISAR"

        listos = self._obtener_clips_dir(listos_dir, es_revisar=False)
        revisar = self._obtener_clips_dir(revisar_dir, es_revisar=True)

        self._responder_json({"listos": listos, "revisar": revisar})

    def _obtener_clips_dir(self, dir_path: Path, es_revisar: bool) -> list:
        clips = []
        if not dir_path.exists():
            return clips

        for mp4 in sorted(dir_path.glob("*.mp4"), reverse=True):
            canal = clipper.canal_desde_nombre(mp4.name)

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

            rel_url = f"/files/out/{'REVISAR' if es_revisar else 'LISTOS'}/{quote(mp4.name, safe='')}"
            duracion = _duracion_video(mp4)

            clips.append({
                "nombre": mp4.name,
                "canal": canal,
                "duracion": round(duracion),
                "gancho": gancho or mp4.stem,
                "motivo": motivo,
                "llm": llm,
                "url": rel_url
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
            for log_file in sorted(logs_dir.glob("*.log"), reverse=True):
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
