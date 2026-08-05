"""Publica la variante azul en redes sociales sin frenar el pipeline."""

import hashlib
import hmac
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import clipper
from registro import obtener


DATA = clipper.DATA
OUT = clipper.OUT
STATE = DATA / "publicaciones.json"
PLATAFORMAS = ("youtube", "instagram", "tiktok")
AUTOMATICAS = ("youtube", "instagram")
REINTENTOS = (60, 300, 900)
LOG = obtener("publicacion")

_STATE_MUTEX = threading.RLock()
_OPERACION_MUTEX = threading.Lock()
_WORKER = None


class PublicacionError(RuntimeError):
    pass


def _cargar() -> dict:
    try:
        datos = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        datos = {}
    if not isinstance(datos, dict):
        datos = {}
    datos.setdefault("items", {})
    return datos


def _guardar(datos: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporal = STATE.with_name(f".{STATE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporal, STATE)


def _relativa(path: Path) -> str:
    try:
        return path.resolve().relative_to(DATA.resolve()).as_posix()
    except (OSError, ValueError) as error:
        raise PublicacionError("archivo fuera del volumen de Clipper") from error


def _es_azul(path: Path) -> bool:
    return bool(re.search(r"[-_]azul$", path.stem, re.IGNORECASE))


def _azul_desde_variante(path: Path) -> Path:
    stem = re.sub(r"([-_])amarillo$", r"\1azul", path.stem, flags=re.IGNORECASE)
    return path.with_name(stem + path.suffix)


def _amarillo_desde_azul(path: Path) -> Path:
    stem = re.sub(r"([-_])azul$", r"\1amarillo", path.stem, flags=re.IGNORECASE)
    return path.with_name(stem + path.suffix)


def _validar_clip(nombre: str, origen: str) -> Path:
    if not nombre or Path(nombre).name != nombre or not nombre.lower().endswith(".mp4"):
        raise PublicacionError("nombre de clip inválido")
    if origen not in {"LISTOS", "REVISAR"}:
        raise PublicacionError("origen de clip inválido")
    path = OUT / origen / nombre
    path = _azul_desde_variante(path)
    try:
        if not path.resolve().is_relative_to((OUT / origen).resolve()):
            raise PublicacionError("ruta de clip inválida")
    except OSError as error:
        raise PublicacionError("ruta de clip inválida") from error
    if not _es_azul(path) or not path.is_file():
        raise PublicacionError("la variante azul no existe")
    return path


def _validar_revision(nombre: str) -> Path:
    return _validar_clip(nombre, "REVISAR")


def _caption(path: Path) -> str:
    try:
        caption = path.with_suffix(".txt").read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PublicacionError("falta el TXT publicable") from error
    if not caption:
        raise PublicacionError("el TXT publicable está vacío")
    return caption


def _hook_revision(path: Path) -> str:
    try:
        texto = path.with_suffix(".motivos.txt").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        texto = ""
    encontrado = re.search(r"(?im)^gancho:\s*(.+)$", texto)
    return encontrado.group(1).strip() if encontrado else _caption(path).splitlines()[0]


def _crear_item(path: Path, titulo: str, caption: str, origen: str) -> dict:
    return {
        "path": _relativa(path),
        "title": titulo.strip()[:100] or caption.splitlines()[0][:100],
        "caption": caption,
        "source": origen,
        "created_at": time.time(),
        "platforms": {},
    }


def _encolar(path: Path, titulo: str, caption: str, origen: str,
             plataformas: tuple[str, ...]):
    clave = _relativa(path)
    titulo = titulo.strip()[:100] or caption.splitlines()[0][:100]
    with _STATE_MUTEX:
        datos = _cargar()
        item = datos["items"].setdefault(clave, _crear_item(path, titulo, caption, origen))
        item.update({"path": clave, "title": titulo, "caption": caption,
                     "source": origen})
        for plataforma in plataformas:
            actual = item["platforms"].get(plataforma, {})
            if actual.get("status") not in {"published", "submitted"}:
                item["platforms"][plataforma] = {
                    **actual,
                    "status": "pending",
                    "next_retry_at": 0,
                    "last_error": "",
                }
        _guardar(datos)
    return clave


def encolar_listos(destinos: list[Path], items: list[tuple[Path, dict]]):
    """Encola solo el destino azul recién creado; nunca barre clips antiguos."""
    for destino, (_, meta) in zip(destinos, items):
        if str(meta.get("variante", "")).lower() != "azul":
            continue
        _encolar(destino, str(meta.get("hook", "")), _caption(destino),
                 "LISTOS", AUTOMATICAS)
        LOG.info("📤 PUBLICACIÓN AUTOMÁTICA ENCOLADA\n   ARCHIVO: %s", destino.name)


def encolar_revision(nombre: str, plataforma: str) -> dict:
    return encolar_manual(nombre, plataforma, "REVISAR")


def encolar_manual(nombre: str, plataforma: str, origen: str) -> dict:
    if plataforma not in PLATAFORMAS:
        raise PublicacionError("plataforma no válida")
    path = _validar_clip(nombre, origen)
    _encolar(path, _hook_revision(path), _caption(path), origen, (plataforma,))
    return estado(path)


def estado(path: Path) -> dict:
    path = _azul_desde_variante(path)
    try:
        clave = _relativa(path)
    except PublicacionError:
        return {}
    with _STATE_MUTEX:
        item = _cargar()["items"].get(clave, {})
    plataformas = item.get("platforms", {}) if isinstance(item, dict) else {}
    return {
        **{p: plataformas.get(p, {}) for p in PLATAFORMAS},
        "complete": all(plataformas.get(p, {}).get("status") == "published"
                        for p in AUTOMATICAS),
        "configured": {p: configurada(p) for p in PLATAFORMAS},
    }


def revision_completada(path: Path) -> bool:
    return bool(estado(path).get("complete"))


def descartar_revision(nombre: str) -> list[str]:
    path = _validar_revision(nombre)
    borrados = []
    with _OPERACION_MUTEX:
        for mp4 in (path, _amarillo_desde_azul(path)):
            for candidato in (mp4, mp4.with_suffix(".txt"),
                              mp4.with_suffix(".motivos.txt")):
                if candidato.is_file():
                    candidato.unlink()
                    borrados.append(candidato.name)
        with _STATE_MUTEX:
            datos = _cargar()
            datos["items"].pop(_relativa(path), None)
            _guardar(datos)
    LOG.info("🗑️ CANDIDATO DESCARTADO\n   ARCHIVOS: %s", ", ".join(borrados))
    return borrados


def configurada(plataforma: str) -> bool:
    if plataforma == "youtube":
        claves = ("CLIPPER_YOUTUBE_CLIENT_ID", "CLIPPER_YOUTUBE_CLIENT_SECRET",
                  "CLIPPER_YOUTUBE_REFRESH_TOKEN")
    elif plataforma == "instagram":
        claves = ("CLIPPER_INSTAGRAM_ACCOUNT_ID", "CLIPPER_INSTAGRAM_ACCESS_TOKEN",
                  "CLIPPER_URL_PUBLICA", "CLIPPER_WEB_CLAVE")
    elif plataforma == "tiktok":
        claves = ("CLIPPER_TIKTOK_CLIENT_KEY", "CLIPPER_TIKTOK_CLIENT_SECRET",
                  "CLIPPER_TIKTOK_REFRESH_TOKEN")
    else:
        return False
    return all(os.environ.get(clave, "").strip() for clave in claves)


def _json_request(url: str, *, method="GET", form=None, payload=None,
                  headers=None, timeout=120) -> dict:
    cabeceras = {"Accept": "application/json", **(headers or {})}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        cabeceras["Content-Type"] = "application/x-www-form-urlencoded"
    elif payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        cabeceras["Content-Type"] = "application/json; charset=utf-8"
    peticion = urllib.request.Request(url, data=data, headers=cabeceras, method=method)
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            cuerpo = respuesta.read()
    except urllib.error.HTTPError as error:
        detalle = error.read(4096).decode("utf-8", "replace")
        raise PublicacionError(f"HTTP {error.code}: {detalle}") from error
    except urllib.error.URLError as error:
        raise PublicacionError(f"red no disponible: {error.reason}") from error
    try:
        datos = json.loads(cuerpo)
    except json.JSONDecodeError as error:
        raise PublicacionError("respuesta JSON inválida") from error
    if isinstance(datos, dict) and datos.get("error"):
        detalle = datos["error"]
        if not (isinstance(detalle, dict)
                and str(detalle.get("code", "")).lower() == "ok"):
            raise PublicacionError(str(detalle))
    if not isinstance(datos, dict):
        raise PublicacionError("respuesta inesperada")
    return datos


def _youtube_token() -> str:
    datos = _json_request("https://oauth2.googleapis.com/token", method="POST", form={
        "client_id": os.environ["CLIPPER_YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["CLIPPER_YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["CLIPPER_YOUTUBE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    token = datos.get("access_token")
    if not token:
        raise PublicacionError("Google no devolvió access_token")
    return token


def _youtube_iniciar(path: Path, item: dict, token: str) -> str:
    privacidad = os.environ.get("CLIPPER_YOUTUBE_PRIVACY", "public").lower()
    if privacidad not in {"public", "private", "unlisted"}:
        raise PublicacionError("CLIPPER_YOUTUBE_PRIVACY no es válida")
    tags = [tag[1:] for tag in re.findall(r"#[\wáéíóúüñÁÉÍÓÚÜÑ]+", item["caption"])]
    payload = {
        "snippet": {"title": item["title"][:100], "description": item["caption"],
                    "tags": tags, "categoryId": "22"},
        "status": {"privacyStatus": privacidad, "selfDeclaredMadeForKids": False},
    }
    cuerpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = ("https://www.googleapis.com/upload/youtube/v3/videos"
           "?uploadType=resumable&part=snippet,status&notifySubscribers=false")
    peticion = urllib.request.Request(url, data=cuerpo, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
        "X-Upload-Content-Length": str(path.stat().st_size),
        "X-Upload-Content-Type": "video/mp4",
    })
    try:
        with urllib.request.urlopen(peticion, timeout=30) as respuesta:
            location = respuesta.headers.get("Location", "")
    except urllib.error.HTTPError as error:
        raise PublicacionError(
            f"YouTube HTTP {error.code}: {error.read(4096).decode('utf-8', 'replace')}") from error
    if not location:
        raise PublicacionError("YouTube no devolvió la sesión de subida")
    return location


def _youtube_put(url: str, token: str, *, body=None, headers=None) -> tuple[int, dict, bytes]:
    parsed = urllib.parse.urlsplit(url)
    conexion = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=120)
    ruta = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    cabeceras = {"Authorization": f"Bearer {token}", **(headers or {})}
    try:
        conexion.request("PUT", ruta, body=body, headers=cabeceras)
        respuesta = conexion.getresponse()
        contenido = respuesta.read()
        return respuesta.status, dict(respuesta.getheaders()), contenido
    except OSError as error:
        raise PublicacionError(f"subida a YouTube interrumpida: {error}") from error
    finally:
        conexion.close()


def _youtube_publicar(path: Path, item: dict, session_url: str,
                      guardar_session) -> str:
    token = _youtube_token()
    total = path.stat().st_size
    if not session_url:
        session_url = _youtube_iniciar(path, item, token)
        guardar_session(session_url)

    status, headers, cuerpo = _youtube_put(session_url, token, headers={
        "Content-Length": "0", "Content-Range": f"bytes */{total}"})
    if status == 404:
        session_url = _youtube_iniciar(path, item, token)
        guardar_session(session_url)
        status, headers, cuerpo = 308, {}, b""
    if status in {200, 201}:
        return str(json.loads(cuerpo).get("id", ""))
    if status != 308:
        raise PublicacionError(f"YouTube rechazó la sesión con HTTP {status}")

    rango = headers.get("Range") or headers.get("range") or ""
    encontrado = re.search(r"(\d+)$", rango)
    inicio = int(encontrado.group(1)) + 1 if encontrado else 0
    with path.open("rb") as video:
        video.seek(inicio)
        cabeceras = {"Content-Type": "video/mp4", "Content-Length": str(total - inicio)}
        if inicio:
            cabeceras["Content-Range"] = f"bytes {inicio}-{total - 1}/{total}"
        status, _, cuerpo = _youtube_put(session_url, token, body=video, headers=cabeceras)
    if status not in {200, 201}:
        raise PublicacionError(f"YouTube no completó la subida (HTTP {status})")
    try:
        video_id = json.loads(cuerpo).get("id", "")
    except json.JSONDecodeError as error:
        raise PublicacionError("YouTube devolvió una respuesta inválida") from error
    if not video_id:
        raise PublicacionError("YouTube no devolvió el ID del vídeo")
    return str(video_id)


def _firma_url(relativa: str, expira: int) -> str:
    secreto = os.environ.get("CLIPPER_WEB_CLAVE", "").encode("utf-8")
    if not secreto:
        raise PublicacionError("CLIPPER_WEB_CLAVE no está configurada")
    return hmac.new(secreto, f"{relativa}|{expira}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


def url_temporal(path: Path, duracion_s: int = 3600) -> str:
    relativa = path.resolve().relative_to(OUT.resolve()).as_posix()
    expira = int(time.time()) + duracion_s
    firma = _firma_url(relativa, expira)
    base = os.environ["CLIPPER_URL_PUBLICA"].rstrip("/")
    ruta = urllib.parse.quote(relativa, safe="/")
    return f"{base}/social-media/{ruta}?expires={expira}&signature={firma}"


def validar_url_temporal(relativa: str, expira: str, firma: str) -> Path | None:
    try:
        limite = int(expira)
    except (TypeError, ValueError):
        return None
    if limite < int(time.time()) or limite > int(time.time()) + 7200:
        return None
    try:
        esperada = _firma_url(relativa, limite)
    except PublicacionError:
        return None
    if not hmac.compare_digest(esperada, firma or ""):
        return None
    try:
        path = (OUT / relativa).resolve()
        if not path.is_relative_to(OUT.resolve()) or not path.is_file():
            return None
        sub = path.relative_to(OUT.resolve())
    except (OSError, ValueError):
        return None
    if len(sub.parts) != 2 or sub.parts[0] not in {"LISTOS", "REVISAR"}:
        return None
    return path if path.suffix.lower() == ".mp4" and _es_azul(path) else None


def _instagram_publicar(path: Path, item: dict, container_id: str,
                         guardar_container) -> str:
    version = os.environ.get("CLIPPER_META_API_VERSION", "v25.0").strip()
    account = os.environ["CLIPPER_INSTAGRAM_ACCOUNT_ID"]
    token = os.environ["CLIPPER_INSTAGRAM_ACCESS_TOKEN"]
    base = f"https://graph.facebook.com/{version}"
    auth = {"Authorization": f"Bearer {token}"}
    if not container_id:
        creado = _json_request(f"{base}/{account}/media", method="POST", headers=auth,
                               form={"media_type": "REELS", "video_url": url_temporal(path),
                                     "caption": item["caption"], "share_to_feed": "true"})
        container_id = str(creado.get("id", ""))
        if not container_id:
            raise PublicacionError("Instagram no devolvió container_id")
        guardar_container(container_id)

    limite = time.time() + 120
    while True:
        consulta = _json_request(
            f"{base}/{container_id}?fields=status_code,status", headers=auth, timeout=30)
        status = str(consulta.get("status_code", "")).upper()
        if status == "FINISHED":
            break
        if status == "PUBLISHED":
            return container_id
        if status in {"ERROR", "EXPIRED"}:
            raise PublicacionError(f"Instagram no procesó el Reel: {consulta.get('status', status)}")
        if time.time() >= limite:
            raise PublicacionError("Instagram sigue procesando el Reel tras 120 s")
        time.sleep(5)

    publicado = _json_request(f"{base}/{account}/media_publish", method="POST",
                              headers=auth, form={"creation_id": container_id})
    media_id = str(publicado.get("id", ""))
    if not media_id:
        raise PublicacionError("Instagram no devolvió el ID del Reel")
    return media_id


def _tiktok_refresh_token() -> str:
    inicial = os.environ["CLIPPER_TIKTOK_REFRESH_TOKEN"].strip()
    archivo = DATA / ".tiktok-oauth.json"
    huella = hashlib.sha256(inicial.encode("utf-8")).hexdigest()
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        if datos.get("seed_hash") == huella and datos.get("refresh_token"):
            return str(datos["refresh_token"])
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return inicial


def _guardar_tiktok_refresh(token: str):
    inicial = os.environ["CLIPPER_TIKTOK_REFRESH_TOKEN"].strip()
    archivo = DATA / ".tiktok-oauth.json"
    temporal = archivo.with_name(f".{archivo.name}.{os.getpid()}.tmp")
    archivo.parent.mkdir(parents=True, exist_ok=True)
    temporal.write_text(json.dumps({
        "seed_hash": hashlib.sha256(inicial.encode("utf-8")).hexdigest(),
        "refresh_token": token,
    }), encoding="utf-8")
    try:
        temporal.chmod(0o600)
    except OSError:
        pass
    os.replace(temporal, archivo)


def _tiktok_token() -> str:
    datos = _json_request("https://open.tiktokapis.com/v2/oauth/token/", method="POST",
                          form={
                              "client_key": os.environ["CLIPPER_TIKTOK_CLIENT_KEY"],
                              "client_secret": os.environ["CLIPPER_TIKTOK_CLIENT_SECRET"],
                              "grant_type": "refresh_token",
                              "refresh_token": _tiktok_refresh_token(),
                          }, timeout=30)
    token = str(datos.get("access_token", ""))
    refresh = str(datos.get("refresh_token", ""))
    if not token or not refresh:
        raise PublicacionError("TikTok no devolvió los tokens OAuth")
    _guardar_tiktok_refresh(refresh)
    return token


def _tiktok_data(datos: dict) -> dict:
    contenido = datos.get("data", {})
    if not isinstance(contenido, dict):
        raise PublicacionError("TikTok devolvió una respuesta inesperada")
    return contenido


def _tiktok_status(token: str, publish_id: str) -> dict:
    return _tiktok_data(_json_request(
        "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
        method="POST", headers={"Authorization": f"Bearer {token}"},
        payload={"publish_id": publish_id}, timeout=30))


def _tiktok_iniciar(path: Path, token: str) -> tuple[str, str, int, int]:
    total = path.stat().st_size
    if total <= 0:
        raise PublicacionError("el MP4 está vacío")
    chunk = min(total, 64 * 1024 * 1024)
    cantidad = max(1, total // chunk)
    datos = _tiktok_data(_json_request(
        "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
        method="POST", headers={"Authorization": f"Bearer {token}"}, payload={
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": total,
                "chunk_size": chunk,
                "total_chunk_count": cantidad,
            }
        }, timeout=30))
    publish_id = str(datos.get("publish_id", ""))
    upload_url = str(datos.get("upload_url", ""))
    if not publish_id or not upload_url:
        raise PublicacionError("TikTok no devolvió la sesión de subida")
    return publish_id, upload_url, chunk, cantidad


def _tiktok_subir(path: Path, upload_url: str, chunk: int, cantidad: int):
    parsed = urllib.parse.urlsplit(upload_url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host.endswith(".tiktokapis.com"):
        raise PublicacionError("TikTok devolvió una URL de subida no válida")
    ruta = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    total = path.stat().st_size
    inicio = 0
    with path.open("rb") as video:
        for indice in range(cantidad):
            longitud = total - inicio if indice == cantidad - 1 else chunk
            cuerpo = video.read(longitud)
            if len(cuerpo) != longitud:
                raise PublicacionError("no se pudo leer el MP4 completo")
            conexion = http.client.HTTPSConnection(host, parsed.port or 443, timeout=120)
            try:
                conexion.request("PUT", ruta, body=cuerpo, headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(longitud),
                    "Content-Range": f"bytes {inicio}-{inicio + longitud - 1}/{total}",
                })
                respuesta = conexion.getresponse()
                respuesta.read()
            except OSError as error:
                raise PublicacionError(f"subida a TikTok interrumpida: {error}") from error
            finally:
                conexion.close()
            esperado = 201 if indice == cantidad - 1 else 206
            if respuesta.status != esperado:
                raise PublicacionError(
                    f"TikTok no aceptó el bloque {indice + 1} (HTTP {respuesta.status})")
            inicio += longitud


def _tiktok_publicar(path: Path, publish_id: str, guardar_intermedio) -> str:
    token = _tiktok_token()
    total = path.stat().st_size
    if publish_id:
        consulta = _tiktok_status(token, publish_id)
        if str(consulta.get("status", "")).upper() == "FAILED":
            raise PublicacionError(
                f"TikTok rechazó el vídeo: {consulta.get('fail_reason', 'sin detalle')}")
        if int(consulta.get("uploaded_bytes", 0) or 0) >= total:
            return publish_id

    publish_id, upload_url, chunk, cantidad = _tiktok_iniciar(path, token)
    guardar_intermedio("publish_id", publish_id)
    guardar_intermedio("upload_url", upload_url)
    _tiktok_subir(path, upload_url, chunk, cantidad)
    return publish_id


def _guardar_intermedio(clave: str, plataforma: str, campo: str, valor: str):
    with _STATE_MUTEX:
        datos = _cargar()
        estado_plataforma = datos["items"][clave]["platforms"][plataforma]
        estado_plataforma[campo] = valor
        _guardar(datos)


def _siguiente_trabajo():
    ahora = time.time()
    with _STATE_MUTEX:
        datos = _cargar()
        for clave, item in datos["items"].items():
            for plataforma, estado_plataforma in item.get("platforms", {}).items():
                if plataforma not in PLATAFORMAS or not configurada(plataforma):
                    continue
                status = estado_plataforma.get("status", "pending")
                colgado = status == "publishing" and ahora - estado_plataforma.get(
                    "started_at", 0) > 600
                if status not in {"pending", "error"} and not colgado:
                    continue
                if estado_plataforma.get("next_retry_at", 0) > ahora:
                    continue
                estado_plataforma["status"] = "publishing"
                estado_plataforma["started_at"] = ahora
                estado_plataforma["attempts"] = int(estado_plataforma.get("attempts", 0)) + 1
                _guardar(datos)
                return clave, json.loads(json.dumps(item)), plataforma
    return None


def procesar_una() -> bool:
    trabajo = _siguiente_trabajo()
    if not trabajo:
        return False
    clave, item, plataforma = trabajo
    path = DATA / item["path"]
    with _OPERACION_MUTEX:
        try:
            if not path.is_file():
                raise PublicacionError("el MP4 ya no existe")
            intermedio = item["platforms"].get(plataforma, {})
            if plataforma == "youtube":
                remoto = _youtube_publicar(
                    path, item, intermedio.get("upload_url", ""),
                    lambda valor: _guardar_intermedio(
                        clave, plataforma, "upload_url", valor))
            elif plataforma == "instagram":
                remoto = _instagram_publicar(
                    path, item, intermedio.get("container_id", ""),
                    lambda valor: _guardar_intermedio(
                        clave, plataforma, "container_id", valor))
            else:
                remoto = _tiktok_publicar(
                    path, intermedio.get("publish_id", ""),
                    lambda campo, valor: _guardar_intermedio(
                        clave, plataforma, campo, valor))
        except Exception as error:
            with _STATE_MUTEX:
                datos = _cargar()
                estado_plataforma = datos["items"][clave]["platforms"][plataforma]
                intentos = int(estado_plataforma.get("attempts", 1))
                espera = REINTENTOS[intentos - 1] if intentos <= len(REINTENTOS) else 3600
                estado_plataforma.update({"status": "error", "last_error": str(error)[:500],
                                          "next_retry_at": time.time() + espera})
                _guardar(datos)
            LOG.warning("⚠️ PUBLICACIÓN FALLIDA\n   RED: %s\n   ARCHIVO: %s\n   MOTIVO: %s",
                        plataforma.upper(), path.name, error)
            return True

        with _STATE_MUTEX:
            datos = _cargar()
            final = "submitted" if plataforma == "tiktok" else "published"
            datos["items"][clave]["platforms"][plataforma].update({
                "status": final, "remote_id": remoto,
                "published_at": time.time(), "last_error": "", "next_retry_at": 0,
            })
            _guardar(datos)
        LOG.info("✅ %s\n   RED: %s\n   ARCHIVO: %s\n   ID: %s",
                 "ENVIADO AL INBOX" if plataforma == "tiktok" else "PUBLICADO",
                 plataforma.upper(), path.name, remoto)
        return True


def _bucle_worker():
    while True:
        try:
            while procesar_una():
                pass
        except Exception as error:
            LOG.exception("💥 WORKER DE PUBLICACIÓN\n   MOTIVO: %s", error)
        time.sleep(15)


def arrancar_worker():
    global _WORKER
    if _WORKER and _WORKER.is_alive():
        return _WORKER
    _WORKER = threading.Thread(target=_bucle_worker, name="social-publisher", daemon=True)
    _WORKER.start()
    LOG.info("📡 WORKER DE PUBLICACIÓN ACTIVO\n   YOUTUBE: %s\n   INSTAGRAM: %s\n   TIKTOK: %s",
             "CONFIGURADO" if configurada("youtube") else "SIN CREDENCIALES",
             "CONFIGURADO" if configurada("instagram") else "SIN CREDENCIALES",
             "CONFIGURADO" if configurada("tiktok") else "SIN CREDENCIALES")
    return _WORKER
