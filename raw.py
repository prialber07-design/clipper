"""Cola RAW y procesamiento editorial de candidatos analizados por Gemini."""

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import antigravity
import bloqueo
import calidad
import clipper
import notify
from registro import obtener


RAW = clipper.OUT / "RAW"
RAW_LOG = clipper.DATA / "logs" / "raw-processing.jsonl"
RAW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
MODOS = {"gemini", "luna"}
ESTADOS_ACTIVOS = {"procesando_gemini", "procesando_luna"}
ESTADOS_REINTENTABLES = {"pendiente", "error_gemini", "error_luna", "error_render"}
RETRY_DELAYS_S = (60, 300, 900, 3600)
# Un valor de uno o dos caracteres no es un secreto que merezca proteccion, y
# tacharlo destroza el log entero: con un ALGO_AUTH=1 en el entorno, cada "1"
# del texto se convierte en [REDACTED] y hasta los identificadores quedan
# ilegibles ("canal-0[REDACTED]"). Por debajo de este largo no se sustituye.
LARGO_MINIMO_SECRETO = 8
_MANIFEST_LOCK = clipper.DATA / ".raw-manifest.lock"
_PROCESS_LOCK = clipper.DATA / ".raw-process.lock"
_THREADS_LOCK = threading.Lock()
_THREADS = {}
LOG = obtener("raw")


class RawError(Exception):
    """Error seguro para devolver al endpoint sin exponer trazas."""


class RawActivo(RawError):
    pass


def modo() -> str:
    valor = os.environ.get("CLIPPER_RAW_MODO", "manual").strip().lower()
    if valor != "manual":
        LOG.warning("⚠️ RAW_CONFIG_INVALIDA · VALOR: %s · SE USA: MANUAL", valor[:40])
    return "manual"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validar_id(raw_id: str) -> str:
    raw_id = str(raw_id or "")
    if not RAW_ID.fullmatch(raw_id):
        raise RawError("identificador RAW inválido")
    return raw_id


def _manifest_path(raw_id: str) -> Path:
    return RAW / f"{validar_id(raw_id)}.json"


def _mp4_path(raw_id: str) -> Path:
    return RAW / f"{validar_id(raw_id)}.mp4"


def _gemini_path(raw_id: str) -> Path:
    return RAW / "_gemini" / f"{validar_id(raw_id)}.json"


def _atomic_write(path: Path, data: dict):
    temporal = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporal.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporal, path)


def _read(raw_id: str) -> dict:
    path = _manifest_path(raw_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RawError("manifiesto RAW no disponible") from error
    if not isinstance(data, dict) or data.get("id") != raw_id:
        raise RawError("manifiesto RAW inválido")
    return data


def _update(raw_id: str, **changes) -> dict:
    with bloqueo.exclusivo(_MANIFEST_LOCK, etiqueta="manifiesto RAW"):
        data = _read(raw_id)
        data.update(changes)
        _atomic_write(_manifest_path(raw_id), data)
        return data


def _texto_log(value, limit=240) -> str:
    value = str(value or "")
    palabras_secretas = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CLAVE",
                         "COOKIE", "AUTH", "PRIVATE", "CREDENTIAL", "DSN")
    for nombre, secreto in os.environ.items():
        if (len(secreto or "") >= LARGO_MINIMO_SECRETO
                and any(palabra in nombre.upper() for palabra in palabras_secretas)):
            value = value.replace(secreto, "[REDACTED]")
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return " ".join(value.split())[:limit]


def _evento(evento: str, raw_id: str, modo_actual: str = "", **extra):
    payload = {
        "ts": _ahora(),
        "evento": evento,
        "id": _texto_log(raw_id, 120),
    }
    if modo_actual:
        payload["modo"] = modo_actual
    for key, value in extra.items():
        if value is None:
            continue
        payload[key] = _texto_log(value) if isinstance(value, str) else value

    RAW_LOG.parent.mkdir(parents=True, exist_ok=True)
    with bloqueo.exclusivo(clipper.DATA / ".raw-log.lock", etiqueta="log RAW"):
        try:
            if RAW_LOG.exists() and RAW_LOG.stat().st_size > 5 * 1024 * 1024:
                os.replace(RAW_LOG, RAW_LOG.with_name("raw-processing.jsonl.1"))
        except OSError:
            pass
        with RAW_LOG.open("a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(payload, ensure_ascii=False) + "\n")

    detalle = " · ".join(f"{k.upper()}={v}" for k, v in payload.items()
                         if k not in {"ts", "evento", "id"})
    LOG.info("🧾 %s · ID: %s%s", evento, raw_id, f" · {detalle}" if detalle else "")


def _normalizar_words(words: list, inicio: float, fin: float) -> list:
    resultado = []
    for word in words or []:
        try:
            start = float(word.get("start", 0))
            end = float(word.get("end", start))
        except (AttributeError, TypeError, ValueError):
            continue
        if end <= inicio or start >= fin:
            continue
        copia = dict(word)
        copia["start"] = round(max(0.0, start - inicio), 3)
        copia["end"] = round(min(fin, end) - inicio, 3)
        resultado.append(copia)
    return resultado


def crear(fuente: Path, inicio: float, fin: float, raw_id: str, *, canal: str,
          motivo: str, pico: float, segmentos: list, words: list, chat: list,
          limites: tuple[float, float] | None = None) -> dict:
    """Recorta solo el tramo RAW, sin filtros editoriales ni recodificación."""
    raw_id = validar_id(raw_id)
    duracion = float(fin) - float(inicio)
    if duracion <= 0:
        raise RawError("duración RAW inválida")
    RAW.mkdir(parents=True, exist_ok=True)
    mp4 = _mp4_path(raw_id)
    manifest_path = _manifest_path(raw_id)
    if mp4.exists() or manifest_path.exists():
        raise RawError("el candidato RAW ya existe")

    fd, temporal_name = tempfile.mkstemp(prefix=f".{raw_id}-", suffix=".mp4", dir=RAW)
    os.close(fd)
    temporal = Path(temporal_name)
    try:
        clipper.run([
            clipper.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{float(inicio):.3f}", "-i", str(fuente),
            "-t", f"{duracion:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-avoid_negative_ts", "make_zero", str(temporal),
        ])
        os.replace(temporal, mp4)
    except Exception:
        temporal.unlink(missing_ok=True)
        raise

    manifest = {
        "schema": 1,
        "id": raw_id,
        "nombre": mp4.name,
        "canal": str(canal or "desconocido")[:120],
        "motivo": str(motivo or "")[:240],
        "pico": round(float(pico), 3),
        "start": 0.0,
        "end": round(duracion, 3),
        "duracion": round(duracion, 3),
        "limites": {
            "min": round(float(limites[0]), 3),
            "max": round(float(limites[1]), 3),
        } if limites else None,
        "segments": segmentos,
        "words": words,
        "chat": list(chat or [])[-20:],
        "status": "pendiente",
        "created_at": _ahora(),
        "last_attempt_at": "",
        "last_error": "",
        "attempt": None,
        "gemini": None,
        "luna": None,
        "destination": None,
        "retry_count": 0,
        "next_retry_at": "",
    }
    try:
        _atomic_write(manifest_path, manifest)
    except Exception:
        mp4.unlink(missing_ok=True)
        raise
    _evento("RAW_CREATED", raw_id, duracion=round(duracion, 3), status="pendiente")
    return manifest


def _error_text(error) -> str:
    return _texto_log(error, 300) or "error no especificado"


def _claim(raw_id: str, modo_actual: str) -> tuple[dict, str]:
    validar_id(raw_id)
    if modo_actual not in MODOS:
        raise RawError("modo inválido")
    with bloqueo.exclusivo(_MANIFEST_LOCK, etiqueta="cola RAW"):
        manifest = _read(raw_id)
        status = manifest.get("status")
        if status in ESTADOS_ACTIVOS:
            raise RawActivo("el candidato ya se está procesando")
        if status not in ESTADOS_REINTENTABLES:
            raise RawError("el candidato no admite otro procesamiento")
        intento = uuid.uuid4().hex[:12]
        manifest["status"] = f"procesando_{modo_actual}"
        manifest["last_attempt_at"] = _ahora()
        manifest["last_error"] = ""
        manifest["attempt"] = {"id": intento, "mode": modo_actual, "started_at": _ahora()}
        _atomic_write(_manifest_path(raw_id), manifest)
    _evento("RAW_QUEUED", raw_id, modo_actual=modo_actual,
            status=manifest["status"])
    return manifest, intento


def enqueue(raw_id: str, modo_actual: str) -> dict:
    manifest, intento = _claim(raw_id, modo_actual)
    try:
        hilo = threading.Thread(
            target=_run,
            args=(raw_id, modo_actual, intento),
            name=f"raw-{raw_id}",
            daemon=True,
        )
        with _THREADS_LOCK:
            _THREADS[(raw_id, intento)] = hilo
        hilo.start()
    except Exception as error:
        with _THREADS_LOCK:
            _THREADS.pop((raw_id, intento), None)
        estado = "error_gemini" if modo_actual == "gemini" else "error_luna"
        evento = "GEMINI_FAILED" if modo_actual == "gemini" else "LUNA_FAILED"
        _fallar(raw_id, modo_actual, estado, error, evento)
        raise RawError("no se pudo encolar el candidato") from error
    return manifest


def _leer_gemini_v2(raw_id: str, duracion: float) -> dict:
    try:
        data = json.loads(_gemini_path(raw_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RawError("análisis Gemini v2 no disponible") from error
    if (not isinstance(data, dict) or data.get("schema") != 2 or
            data.get("identity_policy_version") != 2 or
            data.get("raw_id") != raw_id or data.get("status") != "ok" or
            not isinstance(data.get("result"), dict)):
        raise RawError("análisis Gemini v2 inválido")
    try:
        result = antigravity.validar(
            json.dumps(data["result"], ensure_ascii=False), float(duracion))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RawError("resultado Gemini v2 inválido") from error
    for person in result["people"]:
        urls = [e for e in person["evidence"]
                if e.startswith(("http://", "https://"))]
        if person["name"] and len(urls) < 2:
            raise RawError("identidad Gemini v2 sin dos fuentes")
    return result


# Un trabajo vivo tarda minutos: analisis 2, Luna 20 segundos, y el render
# unos pocos mas aunque haya cola. Pasada media hora, quien sigue en
# 'procesando' es un zombi: su hilo murio y nadie lo va a terminar.
EDAD_ZOMBI_S = 1800


def _zombi(manifest: dict, max_edad_s: float) -> bool:
    """Un trabajo activo que lleva demasiado sin dar señales."""
    try:
        inicio = datetime.fromisoformat(manifest.get("last_attempt_at", ""))
    except (TypeError, ValueError):
        # Sin fecha de intento no hay forma de saber si vive: se da por muerto.
        return True
    return (datetime.now(timezone.utc) - inicio).total_seconds() > max_edad_s


def _reintento_pendiente(manifest: dict) -> bool:
    value = manifest.get("next_retry_at")
    if not value:
        return False
    try:
        return datetime.fromisoformat(value) > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def enqueue_analizado(raw_id: str, visual: dict) -> dict:
    manifest, intento = _claim(raw_id, "luna")
    try:
        hilo = threading.Thread(
            target=_run,
            args=(raw_id, "luna", intento, visual),
            name=f"raw-{raw_id}",
            daemon=True,
        )
        with _THREADS_LOCK:
            _THREADS[(raw_id, intento)] = hilo
        hilo.start()
    except Exception as error:
        with _THREADS_LOCK:
            _THREADS.pop((raw_id, intento), None)
        _fallar(raw_id, "luna", "error_luna", error, "LUNA_FAILED",
                reintentar=True)
        raise RawError("no se pudo encolar el candidato") from error
    return manifest


def procesar_analizados() -> int:
    """Encola RAW con un resultado externo v2 válido y reintentos vencidos."""
    if not RAW.exists():
        return 0
    encolados = 0
    for path in sorted(RAW.glob("*.json")):
        raw_id = path.stem
        try:
            manifest = _read(raw_id)
            if (manifest.get("status") == "completado" or
                    manifest.get("status") in ESTADOS_ACTIVOS or
                    _reintento_pendiente(manifest) or
                    not _mp4_path(raw_id).is_file() or
                    not _gemini_path(raw_id).is_file()):
                continue
            visual = _leer_gemini_v2(raw_id, manifest.get("duracion", 0))
            enqueue_analizado(raw_id, visual)
            encolados += 1
        except RawActivo:
            continue
        except RawError as error:
            mensaje = _error_text(error)
            try:
                actual = _read(raw_id)
                if actual.get("last_error") != mensaje:
                    _update(raw_id, status="error_gemini", last_error=mensaje)
                    _evento("GEMINI_V2_INVALID", raw_id, status="error_gemini",
                            error=mensaje)
            except RawError:
                pass
    return encolados


def procesar_pendientes(limite: int = 1) -> int:
    """Encola candidatos sin análisis para que Gemini los mire dentro del contenedor.

    El motivo por el que esta ruta estaba dormida era que el `agy` autenticado
    vivía en el host. Ya no: el token OAuth está en el volumen, así que el CLI
    del contenedor puede analizar por sí mismo.

    Se encola de uno en uno y el más antiguo primero. El análisis se serializa
    con su propio cerrojo y cada uno puede tardar hasta dos minutos, así que
    encolar la cola entera solo crearía cientos de hilos esperando turno.

    El interruptor es `CLIPPER_ANTIGRAVITY_ACTIVO`: en 0 esta función no hace
    nada y el comportamiento es el de antes, esperar análisis externos.
    """
    if not RAW.exists() or not antigravity.activo():
        return 0

    candidatos = []
    for path in RAW.glob("*.json"):
        raw_id = path.stem
        try:
            manifest = _read(raw_id)
        except RawError:
            continue
        if manifest.get("status") in ESTADOS_ACTIVOS:
            # Uno en marcha de verdad bloquea la cola a proposito, para que el
            # analisis vaya de uno en uno. Pero un zombi no: si contara, dos
            # trabajos muertos dejarian la cola parada para siempre, que es
            # exactamente lo que paso durante doce horas.
            if not _zombi(manifest, EDAD_ZOMBI_S):
                return 0
            continue
        if (manifest.get("status") not in ESTADOS_REINTENTABLES or
                _reintento_pendiente(manifest) or
                not _mp4_path(raw_id).is_file() or
                _gemini_path(raw_id).is_file()):
            continue
        candidatos.append((str(manifest.get("created_at", "")), raw_id))

    encolados = 0
    for _, raw_id in sorted(candidatos)[:max(0, int(limite))]:
        try:
            enqueue(raw_id, "gemini")
            encolados += 1
        except RawActivo:
            break
        except RawError:
            continue
    return encolados


def _fallar(raw_id: str, modo_actual: str, estado: str, error, evento: str = "",
            reintentar: bool = False):
    motivo = _error_text(error)
    try:
        cambios = {"status": estado, "last_error": motivo}
        if reintentar:
            manifest = _read(raw_id)
            count = max(0, int(manifest.get("retry_count", 0))) + 1
            delay = RETRY_DELAYS_S[min(count - 1, len(RETRY_DELAYS_S) - 1)]
            cambios.update({
                "retry_count": count,
                "next_retry_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).isoformat(timespec="seconds"),
            })
        _update(raw_id, **cambios)
    except RawError:
        return
    if evento:
        _evento(evento, raw_id, modo_actual=modo_actual, status=estado, error=motivo)


def _materializar_trabajo(manifest: dict, mp4: Path, clip: dict) -> tuple[Path, str]:
    slug = f"raw-{manifest['id']}"
    trabajo = clipper.WORK / slug
    trabajo.mkdir(parents=True, exist_ok=True)
    source = trabajo / "source.mp4"
    source.unlink(missing_ok=True)
    try:
        os.link(mp4, source)
    except OSError:
        shutil.copy2(mp4, source)
    (trabajo / "transcript.json").write_text(
        json.dumps({"segments": manifest.get("segments", []),
                    "words": manifest.get("words", [])}, ensure_ascii=False),
        encoding="utf-8",
    )
    (trabajo / "clips.json").write_text(
        json.dumps({"clips": [clip]}, ensure_ascii=False), encoding="utf-8")
    return trabajo, slug


def _render_and_publish(manifest: dict, mp4: Path, hook: str, llm: dict) -> dict:
    duracion = float(manifest["duracion"])
    clip = {
        "id": "01",
        "start": 0.0,
        "end": round(duracion, 3),
        "hook": hook,
        "hook_auto": False,
        "title": " ".join(s.get("text", "") for s in manifest.get("segments", []))[:90],
        "social_description": llm.get("social_description", ""),
        "hashtags": llm.get("hashtags", []),
        "llm": llm,
    }
    trabajo, slug = _materializar_trabajo(manifest, mp4, clip)
    salida = clipper.OUT / slug / f"{slug}-01.mp4"
    try:
        _evento("RENDER_STARTED", manifest["id"], status="procesando")
        args = type("RawRenderArgs", (), {
            "slug": slug,
            "only": None,
            "layout": None,
            "source_path": str(mp4),
        })()
        # Prioritario: un render termina un clip, mientras que una
        # transcripcion mas solo alarga la cola. Sin esto, con diez canales
        # transcribiendo el render no consigue el turno nunca.
        with bloqueo.exclusivo_si(clipper.serializar_cpu(), clipper.CPU_LOCK,
                                  etiqueta="render RAW", prioritario=True):
            clipper.cmd_render(args)
        if not salida.exists():
            raise RawError("render sin archivo de salida")
        _evento("RENDER_FINISHED", manifest["id"], status="ok")
        meta = {
            "canal": manifest.get("canal", "desconocido"),
            "motivo": manifest.get("motivo", ""),
            "hook": hook,
            "social_description": clip["social_description"],
            "hashtags": clip["hashtags"],
            "duracion": round(duracion),
            "llm": llm,
        }
        limites = manifest.get("limites") or {}
        if not isinstance(limites, dict):
            limites = {}
        apto, fallos = calidad.evaluar(
            salida, clip, manifest.get("segments", []),
            limites=(float(limites.get("min", clipper.CONFIG["render"]["duracion_min_s"])),
                     float(limites.get("max", clipper.CONFIG["render"]["duracion_max_s"]))),
        )
        if apto:
            destino = notify.publicar(salida, meta)
            cola = "LISTOS"
            _evento("MOVED_TO_LISTOS", manifest["id"], status="ok")
        else:
            destino = calidad.apartar(salida, fallos, meta)
            cola = "REVISAR"
            _evento("MOVED_TO_REVISAR", manifest["id"], status="ok")
        return {"queue": cola, "name": destino.name}
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)
        shutil.rmtree(clipper.OUT / slug, ignore_errors=True)


def _run(raw_id: str, modo_actual: str, intento: str,
         visual_prevalidado: dict | None = None):
    fase = "gemini" if modo_actual == "gemini" else "luna"
    automatico = visual_prevalidado is not None
    # Ambas entradas las dispara ahora el supervisor, no una persona: sin
    # espera creciente, un agy que falle (cuota, OAuth caducado) se
    # reintentaria cada 15 segundos para siempre.
    reintentable = automatico or modo_actual == "gemini"
    try:
        with bloqueo.exclusivo(_PROCESS_LOCK, etiqueta="procesamiento RAW"):
            manifest = _read(raw_id)
            if (manifest.get("attempt") or {}).get("id") != intento:
                return
            mp4 = _mp4_path(raw_id)
            if not mp4.is_file():
                raise RawError("MP4 RAW ausente")

            visual = visual_prevalidado
            if automatico:
                _update(raw_id, status="procesando_luna", gemini={
                    "model": "external-v2",
                    "status": "ok",
                    "latency_ms": 0,
                    "result": visual,
                })
                _evento("GEMINI_V2_ACCEPTED", raw_id, modo_actual="luna",
                        status="ok")
            if modo_actual == "gemini":
                _evento("GEMINI_STARTED", raw_id, modo_actual=modo_actual,
                        status="procesando_gemini")
                visual, visual_meta = antigravity.analizar(
                    candidato=mp4,
                    canal=manifest.get("canal", ""),
                    motivo=manifest.get("motivo", ""),
                    segmentos=manifest.get("segments", []),
                    chat=manifest.get("chat", []),
                    duracion=manifest.get("duracion", 0),
                    pico=manifest.get("pico", 0),
                    bloqueo_path=clipper.DATA / ".antigravity.lock",
                    permitir_manual=True,
                )
                _update(raw_id, gemini={**visual_meta, "result": visual} if visual else visual_meta)
                if visual is None:
                    estado = visual_meta.get("status", "error")
                    evento = ("GEMINI_TIMEOUT" if estado == "timeout" else
                              "GEMINI_EMPTY_OUTPUT" if estado == "empty_output" else
                              "GEMINI_INVALID_JSON" if estado == "invalid_json" else
                              "GEMINI_FAILED")
                    _fallar(raw_id, modo_actual, "error_gemini", estado, evento,
                            reintentar=reintentable)
                    return
                _evento("GEMINI_FINISHED", raw_id, modo_actual=modo_actual,
                        status="ok", latency_ms=visual_meta.get("latency_ms", 0))
                _update(raw_id, status="procesando_luna")
                fase = "luna"

            _evento("LUNA_STARTED", raw_id, modo_actual=modo_actual,
                    status="procesando_luna")
            try:
                hook, llm = clipper.evaluar_editorial(
                    canal=manifest.get("canal", ""),
                    motivo=manifest.get("motivo", ""),
                    segmentos=manifest.get("segments", []),
                    chat=manifest.get("chat", []),
                    duracion=manifest.get("duracion", 0),
                    pico=manifest.get("pico", 0),
                    fallback="",
                    analisis_visual=visual,
                    estricto=True,
                )
            except Exception as error:
                _fallar(raw_id, modo_actual, "error_luna", error, "LUNA_FAILED",
                        reintentar=reintentable)
                return
            _update(raw_id, luna=llm)
            _evento("LUNA_FINISHED", raw_id, modo_actual=modo_actual,
                    status="ok", latency_ms=llm.get("latency_ms", 0))

            fase = "render"
            try:
                destination = _render_and_publish(manifest, mp4, hook, llm)
            except Exception as error:
                _fallar(raw_id, modo_actual, "error_render", error, "RENDER_FAILED",
                        reintentar=reintentable)
                return
            _update(raw_id, status="completado", last_error="", destination=destination,
                    retry_count=0, next_retry_at="")
            _evento("RAW_COMPLETED", raw_id, modo_actual=modo_actual, status="completado")
    except Exception as error:
        estados = {
            "gemini": ("error_gemini", "GEMINI_FAILED"),
            "luna": ("error_luna", "LUNA_FAILED"),
            "render": ("error_render", "RENDER_FAILED"),
        }
        estado, evento = estados[fase]
        _fallar(raw_id, modo_actual, estado, error, evento,
                reintentar=reintentable)
    finally:
        with _THREADS_LOCK:
            _THREADS.pop((raw_id, intento), None)


def recuperar_huerfanos(max_edad_s: float | None = None):
    """Devuelve a la cola lo que quedo en 'procesando' sin nadie detras.

    Al arrancar se recupera todo (max_edad_s=None), porque ningun hilo
    sobrevive al reinicio. En caliente hay que llamarla con una edad maxima,
    para no tocar los trabajos que si estan corriendo ahora mismo.

    Hacia falta en caliente: dos trabajos quedaron en 'procesando_luna' tras un
    redespliegue y, como esta funcion solo corria al arrancar y el proceso ya
    no volvio a reiniciarse, bloquearon la cola entera durante doce horas.
    """
    if not RAW.exists():
        return
    for path in RAW.glob("*.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            estado = manifest.get("status", "")
            if estado not in ESTADOS_ACTIVOS:
                continue
            if max_edad_s is not None and not _zombi(manifest, max_edad_s):
                continue
            raw_id = validar_id(manifest.get("id", path.stem))
            nuevo = "error_gemini" if estado.endswith("gemini") else "error_luna"
            _update(raw_id, status=nuevo, last_error="proceso interrumpido; reintento disponible")
            _evento("RAW_RECOVERED", raw_id, status=nuevo, error="proceso interrumpido")
        except (OSError, json.JSONDecodeError, RawError):
            continue


_LISTA_CACHE = {}
_LISTA_CACHE_LOCK = threading.Lock()


def _campos_para_api(raw_id: str, mtime_ns: int) -> dict | None:
    """Extrae del manifiesto solo lo que la galería enseña, y lo recuerda.

    Un manifiesto lleva la transcripción y las palabras con sus tiempos: son
    cientos de KB de los que la lista usa seis campos. Con la cola en tres
    cifras eso era más de un MB de JSON reparseado cada quince segundos, en el
    mismo proceso que renderiza. Mientras el fichero no cambie, no hay nada
    nuevo que leer.
    """
    with _LISTA_CACHE_LOCK:
        guardado = _LISTA_CACHE.get(raw_id)
    if guardado and guardado[0] == mtime_ns:
        return guardado[1]

    try:
        manifest = _read(raw_id)
    except RawError:
        return None
    gemini = manifest.get("gemini") or {}
    luna = manifest.get("luna") or {}
    destino = manifest.get("destination") or {}
    campos = {
        "id": manifest["id"],
        "canal": manifest.get("canal", ""),
        "motivo": manifest.get("motivo", ""),
        "duracion": round(float(manifest.get("duracion", 0))),
        "status": manifest.get("status", "pendiente"),
        "last_attempt_at": manifest.get("last_attempt_at", ""),
        "last_error": manifest.get("last_error", ""),
        "next_retry_at": manifest.get("next_retry_at", ""),
        "gemini_latency_ms": gemini.get("latency_ms", 0) if isinstance(gemini, dict) else 0,
        "luna_latency_ms": luna.get("latency_ms", 0) if isinstance(luna, dict) else 0,
        "_queue": destino.get("queue") if isinstance(destino, dict) else "",
        "_name": destino.get("name") if isinstance(destino, dict) else "",
    }
    with _LISTA_CACHE_LOCK:
        if len(_LISTA_CACHE) > 500:
            _LISTA_CACHE.clear()
        _LISTA_CACHE[raw_id] = (mtime_ns, campos)
    return campos


def listar_api() -> list[dict]:
    salida = []
    if not RAW.exists():
        return salida
    paths = list(RAW.glob("*.json"))
    def mtime(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0

    paths.sort(key=mtime, reverse=True)
    for path in paths:
        try:
            mp4 = _mp4_path(path.stem)
            if not mp4.is_file():
                continue
            campos = _campos_para_api(path.stem, path.stat().st_mtime_ns)
            if campos is None:
                continue
            # Lo que no depende del manifiesto se recalcula siempre: el JSON de
            # Gemini aparece sin tocarlo, asi que cachearlo lo dejaria obsoleto.
            destination_url = ""
            if (campos["_queue"] in {"LISTOS", "REVISAR"} and
                    isinstance(campos["_name"], str) and campos["_name"]):
                destination_url = (f"/files/out/{campos['_queue']}"
                                   f"/{quote(campos['_name'], safe='')}")
            salida.append({
                **{k: v for k, v in campos.items() if not k.startswith("_")},
                "nombre": mp4.name,
                "timestamp": mp4.stat().st_mtime,
                "gemini_ready": _gemini_path(campos["id"]).is_file(),
                "destination": destination_url,
                "url": f"/files/out/RAW/{quote(mp4.name, safe='')}",
            })
        except (OSError, ValueError, TypeError, RawError):
            continue
    return salida
