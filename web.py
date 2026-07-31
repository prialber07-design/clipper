"""
Galeria web y explorador de archivos intuitivo para /app/clips.

Diseñado con interfaz moderna, intuitiva y accesible (apta para todo tipo de usuarios).
Permite explorar todas las carpetas (/app/clips), reproducir vídeos, ver logs y descargar clips.
"""

import base64
import hmac
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import clipper

DATA = clipper.DATA
OUT = clipper.OUT

HTML_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Clipper Studio · Gestor de Clips y Archivos</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-dark: #0b0f19;
      --bg-card: #151c2c;
      --bg-card-hover: #1e293b;
      --accent: #6366f1;
      --accent-hover: #4f46e5;
      --success: #10b981;
      --success-hover: #059669;
      --warning: #f59e0b;
      --danger: #ef4444;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --border: rgba(255, 255, 255, 0.08);
      --glass: rgba(21, 28, 44, 0.75);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    body {
      font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
      background-color: var(--bg-dark);
      color: var(--text-main);
      min-height: 100vh;
      padding-bottom: 3rem;
      line-height: 1.5;
    }

    /* --- Navbar Superior --- */
    header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: var(--glass);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--border);
      padding: 1rem 1.5rem;
    }

    .header-container {
      max-width: 1300px;
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
      gap: 0.75rem;
    }

    .logo-icon {
      font-size: 2rem;
      background: linear-gradient(135deg, #818cf8, #c084fc);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .logo-text h1 {
      font-size: 1.35rem;
      font-weight: 800;
      letter-spacing: -0.02em;
    }

    .logo-text p {
      font-size: 0.8rem;
      color: var(--text-muted);
      font-weight: 500;
    }

    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #34d399;
      padding: 0.4rem 0.9rem;
      border-radius: 999px;
      font-size: 0.85rem;
      font-weight: 600;
    }

    .status-dot {
      width: 8px;
      height: 8px;
      background-color: #34d399;
      border-radius: 50%;
      box-shadow: 0 0 10px #34d399;
    }

    /* --- Contenedor Principal --- */
    main {
      max-width: 1300px;
      margin: 2rem auto 0;
      padding: 0 1.5rem;
    }

    /* --- Pestañas de Navegación Grandes --- */
    .tabs-nav {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }

    .tab-btn {
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 1.25rem 1rem;
      border-radius: 16px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.85rem;
      font-family: inherit;
      font-weight: 700;
      font-size: 1.05rem;
      transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }

    .tab-btn:hover {
      background: var(--bg-card-hover);
      color: var(--text-main);
      transform: translateY(-2px);
      border-color: rgba(255,255,255,0.2);
    }

    .tab-btn.active {
      background: linear-gradient(135deg, var(--accent), var(--accent-hover));
      color: #ffffff;
      border-color: transparent;
      box-shadow: 0 8px 20px rgba(99, 102, 241, 0.35);
    }

    .tab-btn .tab-icon {
      font-size: 1.6rem;
    }

    .tab-btn .tab-count {
      margin-left: auto;
      background: rgba(255, 255, 255, 0.18);
      padding: 0.2rem 0.6rem;
      border-radius: 999px;
      font-size: 0.85rem;
    }

    /* --- Barra de Búsqueda --- */
    .search-bar {
      margin-bottom: 2rem;
      position: relative;
    }

    .search-input {
      width: 100%;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1rem 1.25rem 1rem 3rem;
      color: var(--text-main);
      font-size: 1rem;
      font-family: inherit;
      outline: none;
      transition: border-color 0.2s;
    }

    .search-input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
    }

    .search-icon {
      position: absolute;
      left: 1rem;
      top: 50%;
      transform: translateY(-50%);
      font-size: 1.2rem;
      color: var(--text-muted);
    }

    /* --- Secciones de Contenido --- */
    .tab-content {
      display: none;
    }

    .tab-content.active {
      display: block;
      animation: fadeIn 0.3s ease-out;
    }

    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* --- Rejilla de Clips (Tarjetas Grandes) --- */
    .clips-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
      gap: 1.75rem;
    }

    .clip-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 20px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: all 0.3s ease;
      box-shadow: 0 10px 25px rgba(0,0,0,0.2);
    }

    .clip-card:hover {
      transform: translateY(-5px);
      box-shadow: 0 16px 35px rgba(0,0,0,0.35);
      border-color: rgba(255,255,255,0.2);
    }

    .video-wrapper {
      position: relative;
      width: 100%;
      background: #000;
      aspect-ratio: 9 / 16;
      max-height: 480px;
    }

    .video-wrapper video {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }

    .clip-details {
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      flex-grow: 1;
    }

    .clip-badge-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
    }

    .channel-tag {
      background: rgba(99, 102, 241, 0.15);
      color: #a5b4fc;
      font-weight: 700;
      font-size: 0.85rem;
      padding: 0.3rem 0.75rem;
      border-radius: 8px;
    }

    .duration-tag {
      background: rgba(255,255,255,0.08);
      color: var(--text-muted);
      font-weight: 600;
      font-size: 0.8rem;
      padding: 0.3rem 0.6rem;
      border-radius: 8px;
    }

    .clip-title {
      font-size: 1.05rem;
      font-weight: 700;
      line-height: 1.4;
      color: var(--text-main);
    }

    .clip-reason {
      font-size: 0.85rem;
      color: #fca5a5;
      background: rgba(239, 68, 68, 0.12);
      border-left: 3px solid var(--danger);
      padding: 0.5rem 0.75rem;
      border-radius: 6px;
    }

    /* --- Botón Descargar Gigante --- */
    .download-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.6rem;
      width: 100%;
      background: linear-gradient(135deg, var(--success), var(--success-hover));
      color: #ffffff;
      text-decoration: none;
      font-weight: 800;
      font-size: 1.05rem;
      padding: 1rem;
      border-radius: 14px;
      margin-top: auto;
      transition: all 0.2s ease;
      box-shadow: 0 4px 14px rgba(16, 185, 129, 0.3);
      border: none;
      cursor: pointer;
    }

    .download-btn:hover {
      transform: scale(1.02);
      box-shadow: 0 6px 20px rgba(16, 185, 129, 0.45);
    }

    /* --- Explorador de Archivos --- */
    .breadcrumbs {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
      background: var(--bg-card);
      padding: 1rem 1.25rem;
      border-radius: 14px;
      margin-bottom: 1.5rem;
      border: 1px solid var(--border);
      font-weight: 600;
    }

    .crumb-item {
      color: var(--accent);
      cursor: pointer;
      text-decoration: none;
    }

    .crumb-item:hover {
      text-decoration: underline;
    }

    .crumb-separator {
      color: var(--text-muted);
    }

    .files-list {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 1.25rem;
    }

    .file-item-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.25rem;
      display: flex;
      align-items: center;
      gap: 1rem;
      transition: all 0.2s ease;
      cursor: pointer;
    }

    .file-item-card:hover {
      background: var(--bg-card-hover);
      border-color: rgba(255,255,255,0.2);
      transform: translateY(-2px);
    }

    .file-icon {
      font-size: 2.2rem;
    }

    .file-info {
      overflow: hidden;
      flex-grow: 1;
    }

    .file-name {
      font-weight: 700;
      font-size: 0.95rem;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      color: var(--text-main);
    }

    .file-meta {
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-top: 0.2rem;
    }

    .file-actions {
      display: flex;
      gap: 0.5rem;
    }

    .action-btn-sm {
      background: rgba(255,255,255,0.08);
      color: var(--text-main);
      border: none;
      padding: 0.5rem 0.75rem;
      border-radius: 8px;
      font-weight: 600;
      font-size: 0.85rem;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
    }

    .action-btn-sm:hover {
      background: var(--accent);
      color: #fff;
    }

    /* --- Visor de Logs --- */
    .logs-container {
      background: #050811;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.5rem;
      font-family: monospace;
      font-size: 0.9rem;
      color: #38bdf8;
      height: 500px;
      overflow-y: auto;
      white-space: pre-wrap;
      box-shadow: inset 0 2px 8px rgba(0,0,0,0.5);
    }

    .refresh-logs-btn {
      margin-bottom: 1rem;
      background: var(--bg-card);
      border: 1px solid var(--border);
      color: var(--text-main);
      padding: 0.75rem 1.25rem;
      border-radius: 12px;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
    }

    .refresh-logs-btn:hover {
      background: var(--accent);
    }

    .empty-state {
      text-align: center;
      padding: 4rem 2rem;
      background: var(--bg-card);
      border-radius: 20px;
      border: 1px dashed var(--border);
    }

    .empty-state-icon {
      font-size: 3.5rem;
      margin-bottom: 1rem;
    }

    .empty-state h3 {
      font-size: 1.3rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
    }

    /* --- Modal de Previsualización de Vídeos y Archivos --- */
    .modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      background: rgba(0, 0, 0, 0.85);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      z-index: 1000;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
    }

    .modal-overlay.active {
      display: flex;
      animation: fadeIn 0.25s ease-out;
    }

    .modal-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 24px;
      width: 100%;
      max-width: 600px;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
    }

    .modal-header {
      padding: 1.25rem 1.5rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
    }

    .modal-title {
      font-size: 1.1rem;
      font-weight: 700;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .modal-close-btn {
      background: rgba(255,255,255,0.1);
      border: none;
      color: var(--text-main);
      font-size: 1.2rem;
      width: 36px;
      height: 36px;
      border-radius: 50%;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s;
    }

    .modal-close-btn:hover {
      background: var(--danger);
      color: #fff;
    }

    .modal-body {
      padding: 1.5rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 1rem;
    }

    .modal-video-player {
      width: 100%;
      max-height: 60vh;
      border-radius: 16px;
      background: #000;
      object-fit: contain;
      aspect-ratio: 9 / 16;
    }

    .modal-text-viewer {
      width: 100%;
      background: #050811;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1rem;
      font-family: monospace;
      font-size: 0.85rem;
      color: #38bdf8;
      max-height: 50vh;
      overflow-y: auto;
      white-space: pre-wrap;
    }

    .modal-footer {
      padding: 1.25rem 1.5rem;
      border-top: 1px solid var(--border);
      display: flex;
      gap: 1rem;
    }
  </style>
</head>
<body>

  <header>
    <div class="header-container">
      <div class="logo-area">
        <span class="logo-icon">🎬</span>
        <div class="logo-text">
          <h1>Clipper Studio</h1>
          <p>Gestor de Vídeos y Archivos</p>
        </div>
      </div>
      <div class="status-badge">
        <span class="status-dot"></span>
        <span>Servidor Activo · /app/clips</span>
      </div>
    </div>
  </header>

  <main>
    <!-- Pestañas Principales para Navegación Intuitiva -->
    <nav class="tabs-nav">
      <button class="tab-btn active" onclick="switchTab('listos', this)">
        <span class="tab-icon">🎬</span>
        <span>Clips Listos</span>
        <span class="tab-count" id="count-listos">0</span>
      </button>
      <button class="tab-btn" onclick="switchTab('revisar', this)">
        <span class="tab-icon">⚠️</span>
        <span>En Revisión</span>
        <span class="tab-count" id="count-revisar">0</span>
      </button>
      <button class="tab-btn" onclick="switchTab('explorador', this)">
        <span class="tab-icon">📁</span>
        <span>Explorador /app/clips</span>
      </button>
      <button class="tab-btn" onclick="switchTab('logs', this)">
        <span class="tab-icon">📋</span>
        <span>Logs del Servidor</span>
      </button>
    </nav>

    <!-- Buscador de Vídeos -->
    <div class="search-bar">
      <span class="search-icon">🔍</span>
      <input type="text" id="searchInput" class="search-input" placeholder="Buscar clip por streamer o por título..." onkeyup="filtrarClips()">
    </div>

    <!-- Pestaña 1: Clips Listos para Subir -->
    <section id="tab-listos" class="tab-content active">
      <div class="clips-grid" id="grid-listos"></div>
    </section>

    <!-- Pestaña 2: Clips en Revisión -->
    <section id="tab-revisar" class="tab-content">
      <div class="clips-grid" id="grid-revisar"></div>
    </section>

    <!-- Pestaña 3: Explorador de Archivos y Carpetas -->
    <section id="tab-explorador" class="tab-content">
      <div class="breadcrumbs" id="breadcrumbs"></div>
      <div class="files-list" id="files-grid"></div>
    </section>

    <!-- Pestaña 4: Registros / Logs del Servidor -->
    <section id="tab-logs" class="tab-content">
      <button class="refresh-logs-btn" onclick="cargarLogs()">🔄 Actualizar Registros</button>
      <div class="logs-container" id="logs-box">Cargando registros del servidor...</div>
    </section>
  </main>

  <!-- Modal de Previsualización -->
  <div class="modal-overlay" id="previewModal" onclick="cerrarPrevisualizacion(event)">
    <div class="modal-card" onclick="event.stopPropagation()">
      <div class="modal-header">
        <h3 class="modal-title" id="modalTitle">Previsualización</h3>
        <button class="modal-close-btn" onclick="cerrarPrevisualizacion()">✕</button>
      </div>
      <div class="modal-body" id="modalBody"></div>
      <div class="modal-footer">
        <a id="modalDownloadBtn" href="#" download class="download-btn">
          <span>⬇️ DESCARGAR AHORA</span>
        </a>
      </div>
    </div>
  </div>

  <script>
    let currentPath = '';

    document.addEventListener('DOMContentLoaded', () => {
      cargarClips();
      cargarArchivos('');
      cargarLogs();

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') cerrarPrevisualizacion();
      });
    });

    function switchTab(tabId, btn) {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      
      btn.classList.add('active');
      document.getElementById('tab-' + tabId).classList.add('active');
    }

    async function cargarClips() {
      try {
        const res = await fetch('/api/clips');
        const data = await res.json();
        
        document.getElementById('count-listos').textContent = data.listos.length;
        document.getElementById('count-revisar').textContent = data.revisar.length;

        renderGrid('grid-listos', data.listos, false);
        renderGrid('grid-revisar', data.revisar, true);
      } catch (e) {
        console.error("Error cargando clips:", e);
      }
    }

    function renderGrid(containerId, clips, esRevisar) {
      const container = document.getElementById(containerId);
      if (!clips || clips.length === 0) {
        container.innerHTML = `
          <div class="empty-state" style="grid-column: 1 / -1;">
            <div class="empty-state-icon">${esRevisar ? '✨' : '☕'}</div>
            <h3>${esRevisar ? 'No hay clips pendientes de revisión' : 'No hay clips listos para subir todavía'}</h3>
            <p>${esRevisar ? 'Todos los clips generados cumplen con la calidad recomendada.' : 'Los clips aparecerán aquí automáticamente cuando los streamers emitan en directo.'}</p>
          </div>`;
        return;
      }

      container.innerHTML = clips.map(c => `
        <article class="clip-card" data-search="${(c.canal + ' ' + c.gancho).toLowerCase()}">
          <div class="video-wrapper" onclick="abrirPrevisualizacion('${c.url}', '${c.gancho || c.nombre}', 'video')">
            <video src="${c.url}" controls preload="metadata" playsinline></video>
          </div>
          <div class="clip-details">
            <div class="clip-badge-row">
              <span class="channel-tag">#${c.canal}</span>
              <span class="duration-tag">⏱️ ${c.duracion}s</span>
            </div>
            <h2 class="clip-title">${c.gancho || '(Sin título)'}</h2>
            ${esRevisar && c.motivo ? `<div class="clip-reason">⚠️ ${c.motivo}</div>` : ''}
            <div style="display:flex; gap:0.5rem; margin-top:auto;">
              <button onclick="abrirPrevisualizacion('${c.url}', '${c.gancho || c.nombre}', 'video')" class="action-btn-sm" style="flex:1; justify-content:center; padding:0.8rem; font-size:0.95rem;">
                👁️ Previsualizar
              </button>
              <a href="${c.url}" download class="download-btn" style="flex:1.2;">
                <span>⬇️ Descargar</span>
              </a>
            </div>
          </div>
        </article>
      `).join('');
    }

    function filtrarClips() {
      const query = document.getElementById('searchInput').value.toLowerCase();
      document.querySelectorAll('.clip-card').forEach(card => {
        const text = card.getAttribute('data-search') || '';
        card.style.display = text.includes(query) ? 'flex' : 'none';
      });
    }

    async function cargarArchivos(subpath) {
      currentPath = subpath;
      try {
        const res = await fetch('/api/browse?path=' + encodeURIComponent(subpath));
        const data = await res.json();
        
        // Render Migas de Pan (Breadcrumbs)
        const parts = subpath ? subpath.split('/').filter(Boolean) : [];
        let breadHTML = `<span class="crumb-item" onclick="cargarArchivos('')">🏠 Inicio (/app/clips)</span>`;
        let acc = '';
        parts.forEach((p, idx) => {
          acc += (acc ? '/' : '') + p;
          const target = acc;
          breadHTML += ` <span class="crumb-separator">/</span> <span class="crumb-item" onclick="cargarArchivos('${target}')">${p}</span>`;
        });
        document.getElementById('breadcrumbs').innerHTML = breadHTML;

        // Render Archivos y Carpetas
        const grid = document.getElementById('files-grid');
        if (!data.items || data.items.length === 0) {
          grid.innerHTML = `<div class="empty-state" style="grid-column: 1 / -1;"><p>Esta carpeta está vacía.</p></div>`;
          return;
        }

        grid.innerHTML = data.items.map(item => {
          const icon = item.is_dir ? '📂' : (item.name.endsWith('.mp4') ? '🎬' : (item.name.endsWith('.log') || item.name.endsWith('.txt') ? '📄' : '📁'));
          const itemPath = subpath ? (subpath + '/' + item.name) : item.name;
          const fileUrl = '/files/' + itemPath;
          const isVideo = item.name.endsWith('.mp4');
          const isText = item.name.endsWith('.txt') || item.name.endsWith('.log') || item.name.endsWith('.csv') || item.name.endsWith('.json');

          if (item.is_dir) {
            return `
              <div class="file-item-card" onclick="cargarArchivos('${itemPath}')">
                <span class="file-icon">${icon}</span>
                <div class="file-info">
                  <div class="file-name">${item.name}</div>
                  <div class="file-meta">Carpeta</div>
                </div>
                <button class="action-btn-sm">Abrir ➔</button>
              </div>`;
          } else {
            return `
              <div class="file-item-card">
                <span class="file-icon" onclick="${isVideo ? `abrirPrevisualizacion('${fileUrl}', '${item.name}', 'video')` : (isText ? `abrirPrevisualizacion('${fileUrl}', '${item.name}', 'text')` : '')}">${icon}</span>
                <div class="file-info" onclick="${isVideo ? `abrirPrevisualizacion('${fileUrl}', '${item.name}', 'video')` : (isText ? `abrirPrevisualizacion('${fileUrl}', '${item.name}', 'text')` : '')}">
                  <div class="file-name" title="${item.name}">${item.name}</div>
                  <div class="file-meta">${item.size}</div>
                </div>
                <div class="file-actions">
                  ${isVideo || isText ? `<button onclick="abrirPrevisualizacion('${fileUrl}', '${item.name}', '${isVideo ? 'video' : 'text'}')" class="action-btn-sm">👁️ Ver</button>` : ''}
                  <a href="${fileUrl}" download class="action-btn-sm">⬇️</a>
                </div>
              </div>`;
          }
        }).join('');

      } catch (e) {
        console.error("Error explorando archivos:", e);
      }
    }

    async function abrirPrevisualizacion(url, title, type) {
      const modal = document.getElementById('previewModal');
      const modalTitle = document.getElementById('modalTitle');
      const modalBody = document.getElementById('modalBody');
      const modalDownloadBtn = document.getElementById('modalDownloadBtn');

      modalTitle.textContent = title || 'Previsualización';
      modalDownloadBtn.href = url;
      modalBody.innerHTML = 'Cargando contenido...';

      if (type === 'video') {
        modalBody.innerHTML = `<video src="${url}" class="modal-video-player" controls autoplay playsinline></video>`;
      } else if (type === 'text') {
        try {
          const res = await fetch(url);
          const text = await res.text();
          modalBody.innerHTML = `<div class="modal-text-viewer">${text || '(Archivo vacío)'}</div>`;
        } catch (e) {
          modalBody.innerHTML = `<p style="color:var(--danger)">No se pudo cargar el archivo de texto.</p>`;
        }
      } else {
        modalBody.innerHTML = `<p>Archivo sin vista previa disponible.</p>`;
      }

      modal.classList.add('active');
    }

    function cerrarPrevisualizacion(e) {
      if (e && e.target !== document.getElementById('previewModal') && !e.target.classList.contains('modal-close-btn')) return;
      const modal = document.getElementById('previewModal');
      const modalBody = document.getElementById('modalBody');
      modal.classList.remove('active');
      modalBody.innerHTML = '';
    }

    async function cargarLogs() {
      const box = document.getElementById('logs-box');
      box.textContent = "Cargando registros recientes...";
      try {
        const res = await fetch('/api/logs');
        const data = await res.text();
        box.textContent = data || "No hay registros disponibles en este momento.";
        box.scrollTop = box.scrollHeight;
      } catch (e) {
        box.textContent = "Error al obtener los logs del servidor.";
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
        # Endpoint de salud público
        if self.path == "/salud":
            cuerpo = b"ok"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return

        # Verificar credenciales para todo el resto
        if not self._autorizado():
            return self._pedir_credenciales()

        url_parsed = urlparse(self.path)
        path = url_parsed.path

        # 1. Página principal de la interfaz
        if path in ["/", "/index.html"]:
            return self._responder_html(HTML_TEMPLATE)

        # 2. API: Lista de clips (Listos y Revisar)
        if path == "/api/clips":
            return self._handle_api_clips()

        # 3. API: Explorador de carpetas (/app/clips)
        if path == "/api/browse":
            query = parse_qs(url_parsed.query)
            subpath = query.get("path", [""])[0]
            return self._handle_api_browse(subpath)

        # 4. API: Logs del servidor
        if path == "/api/logs":
            return self._handle_api_logs()

        # 5. Descargas / visualización de archivos de /app/clips
        if path.startswith("/files/"):
            rel_path = unquote(path[7:])
            self.path = "/" + rel_path

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
            partes = mp4.stem.split("_")
            canal = partes[1] if len(partes) >= 2 else "clip"
            
            gancho = ""
            motivo = ""
            txt_file = mp4.with_suffix(".txt")
            motivos_file = mp4.with_suffix(".motivos.txt")

            if motivos_file.exists():
                motivo = motivos_file.read_text(encoding="utf-8", errors="replace").strip()
            
            if txt_file.exists():
                contenido = txt_file.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"gancho en pantalla:\s*(.*)", contenido)
                if m:
                    gancho = m.group(1).strip()

            rel_url = f"/files/out/{'REVISAR' if es_revisar else 'LISTOS'}/{mp4.name}"
            
            clips.append({
                "nombre": mp4.name,
                "canal": canal,
                "duracion": 30,  # estimado o extraíble
                "gancho": gancho or mp4.stem,
                "motivo": motivo,
                "url": rel_url
            })
        return clips

    def _handle_api_browse(self, subpath: str):
        target = (DATA / subpath).resolve()
        # Seguridad para evitar que salgan de /app/clips
        if not str(target).startswith(str(DATA.resolve())):
            target = DATA.resolve()

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
        super().end_headers()

    def log_message(self, formato, *args):
        pass


def arrancar(puerto: int = None, en_hilo: bool = False):
    usuario = os.environ.get("CLIPPER_WEB_USUARIO", "clips")
    clave = os.environ.get("CLIPPER_WEB_CLAVE", "clips")
    puerto = puerto or int(os.environ.get("CLIPPER_WEB_PUERTO", "8080"))

    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    Handler.usuario, Handler.clave = usuario, clave
    handler = partial(Handler)
    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), handler)
    servidor.daemon_threads = True

    print(f"[>] Galería Web e interfaz en http://0.0.0.0:{puerto} (usuario: {usuario})")
    if en_hilo:
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        return servidor
    servidor.serve_forever()


if __name__ == "__main__":
    arrancar()
