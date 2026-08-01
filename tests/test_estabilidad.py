import json
import io
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import clipper
import calidad
import kick
import live
import notify
import publicar_todo
import web


def _incrementar_contador(contadores, bloqueo_path, repeticiones):
    live.CONTADORES = Path(contadores)
    live.CONTADORES_LOCK = Path(bloqueo_path)
    for _ in range(repeticiones):
        live.elegir_duracion("canal")


class _RespuestaOpenAI:
    def __init__(self, cuerpo):
        self.cuerpo = cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.cuerpo).encode("utf-8")


def _respuesta_editorial(titulo="Título sólido"):
    resultado = {
        "decision": "publicar",
        "score": 78,
        "confidence": 0.86,
        "reason": "El momento tiene una reacción clara.",
        "screen_title": titulo,
    }
    return {
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": json.dumps(resultado)}],
        }],
        "usage": {"input_tokens": 123, "output_tokens": 22},
    }


class EstabilidadTests(unittest.TestCase):
    def test_run_expone_fallos_del_comando(self):
        with self.assertRaises(FileNotFoundError):
            clipper.run(["__binario_que_no_existe__"])

        with self.assertRaises(subprocess.CalledProcessError):
            clipper.run([sys.executable, "-c", "import sys; sys.exit(3)"])

    def test_whisper_cache_se_vacia(self):
        clipper._MODELO_CACHE["test"] = object()
        clipper.liberar_whisper_model()
        self.assertEqual(clipper._MODELO_CACHE, {})

    def test_nombres_antiguos_y_nuevos(self):
        self.assertEqual(clipper.canal_desde_nombre("elcalvolol-193235-01.mp4"), "elcalvolol")
        self.assertEqual(
            clipper.canal_desde_nombre("elcalvolol-20260801-193235-01.mp4"),
            "elcalvolol",
        )
        self.assertEqual(clipper.canal_desde_nombre("001_mi_canal_2026-08-01.mp4"), "mi_canal")
        self.assertEqual(
            clipper.canal_desde_nombre("001_mi_canal_2026-08-01_193235.mp4"),
            "mi_canal",
        )

    def test_contador_de_duraciones_es_atomico(self):
        with tempfile.TemporaryDirectory() as tmp:
            anterior, anterior_lock = live.CONTADORES, live.CONTADORES_LOCK
            try:
                live.CONTADORES = Path(tmp) / "_contadores.json"
                live.CONTADORES_LOCK = Path(tmp) / "_contadores.lock"
                live.elegir_duracion("canal")
                live.elegir_duracion("canal")
                self.assertEqual(json.loads(live.CONTADORES.read_text())["canal"], 2)
            finally:
                live.CONTADORES, live.CONTADORES_LOCK = anterior, anterior_lock

    def test_contador_de_duraciones_entre_procesos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contadores, bloqueo_path = root / "contadores.json", root / "contadores.lock"
            ctx = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
            procesos = [ctx.Process(target=_incrementar_contador,
                                     args=(str(contadores), str(bloqueo_path), 6))
                         for _ in range(2)]
            for proceso in procesos:
                proceso.start()
            for proceso in procesos:
                proceso.join(30)
                self.assertEqual(proceso.exitcode, 0)
            self.assertEqual(json.loads(contadores.read_text())["canal"], 12)

    def test_registrar_listo_propag_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            anterior = notify.LISTOS, notify.CONTADOR, notify.SALIDA_LOCK
            try:
                notify.LISTOS = root / "LISTOS"
                notify.CONTADOR = notify.LISTOS / ".contador"
                notify.SALIDA_LOCK = root / ".salida.lock"
                origen = root / "clip.mp4"
                origen.write_bytes(b"video")
                meta = {"canal": "canal"}
                with patch.object(notify, "_sincronizar"):
                    destino = notify.registrar_listo(origen, meta)
                self.assertEqual(meta["n"], 1)
                self.assertTrue(destino.exists())
            finally:
                notify.LISTOS, notify.CONTADOR, notify.SALIDA_LOCK = anterior

    def test_kick_entrega_texto_real(self):
        payload = json.dumps({"message": {"content": "jajaja esto es real"}})
        self.assertEqual(kick._texto_mensaje(payload), "jajaja esto es real")

    def test_web_muestra_canal_y_duracion_reales(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            mp4 = carpeta / "elcalvolol-20260801-193235-01.mp4"
            mp4.write_bytes(b"video")
            handler = object.__new__(web.Handler)
            web._duracion_video_cache.cache_clear()
            with patch.object(clipper, "run", return_value=SimpleNamespace(stdout="33.7")) as ejecutar:
                clips = handler._obtener_clips_dir(carpeta, es_revisar=True)
                handler._obtener_clips_dir(carpeta, es_revisar=True)
            self.assertEqual(clips[0]["canal"], "elcalvolol")
            self.assertEqual(clips[0]["duracion"], 34)
            self.assertEqual(ejecutar.call_count, 1)

    def test_web_expone_timestamp_real_en_listos(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            mp4 = carpeta / "001_canal_2026-08-02.mp4"
            mp4.write_bytes(b"video")
            esperado = mp4.stat().st_mtime
            handler = object.__new__(web.Handler)
            web._duracion_video_cache.cache_clear()
            with patch.object(clipper, "run", return_value=SimpleNamespace(stdout="30")):
                clips = handler._obtener_clips_dir(carpeta, es_revisar=False)
        self.assertEqual(clips[0]["nombre"], mp4.name)
        self.assertEqual(clips[0]["timestamp"], esperado)

    def test_web_reintento_fuerza_render_tras_error(self):
        cargar = web.HTML_TEMPLATE.split("function cargarClips", 1)[1]
        cargar = cargar.split("function switchTab", 1)[0]
        self.assertIn('state.signature = "";', cargar)
        self.assertLess(
            cargar.index('state.signature = "";'),
            cargar.index('replaceChildren(errorState')
        )

    def test_web_muestra_evaluacion_llm_en_revisar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origen = root / "canal-20260801-193235-01.mp4"
            origen.write_bytes(b"video")
            anteriores = clipper.OUT, web.OUT
            try:
                clipper.OUT = root / "out"
                web.OUT = clipper.OUT
                destino = calidad.apartar(origen, ["LLM en modo prueba"], {
                    "canal": "canal",
                    "hook": "Título de prueba",
                    "llm": {
                        "model": "gpt-5.6-luna",
                        "mode": "prueba",
                        "decision": "descartar",
                        "score": 12,
                        "confidence": 0.91,
                        "reason": "No hay un momento claro.",
                    },
                })
                handler = object.__new__(web.Handler)
                web._duracion_video_cache.cache_clear()
                with patch.object(clipper, "run", return_value=SimpleNamespace(stdout="30")):
                    clips = handler._obtener_clips_dir(destino.parent, es_revisar=True)
            finally:
                clipper.OUT, web.OUT = anteriores
            self.assertEqual(clips[0]["llm"]["decision"], "descartar")
            self.assertEqual(clips[0]["llm"]["score"], 12)
            self.assertIn("No hay un momento claro", clips[0]["llm"]["reason"])

    def test_web_rechaza_clave_de_ejemplo(self):
        with patch.dict(os.environ, {"CLIPPER_WEB_CLAVE": "pon-aqui-una-clave-larga"}):
            with self.assertRaises(RuntimeError):
                web.arrancar()

    def test_web_silencia_desconexion_de_video(self):
        handler = object.__new__(web.Handler)

        for error in (BrokenPipeError, ConnectionResetError):
            class ClienteCierra:
                def write(self, _datos):
                    raise error

            with self.subTest(error=error):
                handler.copyfile(io.BytesIO(b"video"), ClienteCierra())

    def test_publicar_todo_es_idempotente(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = root / "REVISAR"
            review.mkdir()
            pendiente = review / "canal-20260801-193235-01.mp4"
            pendiente.write_bytes(b"video")
            anteriores = (
                publicar_todo.REVISAR,
                notify.LISTOS,
                notify.CONTADOR,
                notify.SALIDA_LOCK,
            )
            try:
                publicar_todo.REVISAR = review
                notify.LISTOS = root / "LISTOS"
                notify.CONTADOR = notify.LISTOS / ".contador"
                notify.SALIDA_LOCK = root / ".salida.lock"
                with patch.object(notify, "avisar"):
                    with patch("sys.argv", ["publicar_todo.py"]):
                        publicar_todo.main()
                        self.assertFalse(pendiente.exists())
                        with self.assertRaises(SystemExit):
                            publicar_todo.main()
            finally:
                (publicar_todo.REVISAR, notify.LISTOS,
                 notify.CONTADOR, notify.SALIDA_LOCK) = anteriores

    def test_llm_valido_reemplaza_gancho_sin_mover_tiempos(self):
        segmentos = [{"start": 10.5, "end": 13.0, "text": "Esto fue inesperado"}]
        copia = [dict(segmento) for segmento in segmentos]
        entorno = {
            "CLIPPER_LLM_ACTIVO": "1",
            "OPENAI_API_KEY": "sk-test-no-log",
            "CLIPPER_LLM_MODELO": "gpt-5.6-luna",
            "CLIPPER_LLM_MODO": "prueba",
        }
        with patch.dict(os.environ, entorno):
            with patch.object(
                clipper.urllib.request,
                "urlopen",
                return_value=_RespuestaOpenAI(_respuesta_editorial()),
            ) as llamada:
                gancho, meta = clipper.evaluar_editorial(
                    "canal", "pico de reacción", segmentos, ["qué ha pasado"],
                    30.0, 12.0, "Gancho heurístico",
                )

        self.assertEqual(gancho, "Título sólido")
        self.assertEqual(segmentos, copia)
        self.assertEqual(meta["decision"], "publicar")
        self.assertEqual(meta["input_tokens"], 123)
        self.assertEqual(meta["output_tokens"], 22)
        self.assertGreaterEqual(meta["latency_ms"], 0)
        cuerpo = json.loads(llamada.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("sk-test-no-log", json.dumps(cuerpo))
        self.assertEqual(cuerpo["model"], "gpt-5.6-luna")

    def test_contexto_llm_es_relativo_y_acotado(self):
        segmentos = [
            {"start": 100.0, "end": 136.0, "text": "entra y sale del clip"},
            {"start": 125.0, "end": 150.0, "text": "el pico queda al final"},
        ]
        relativos, pico = live.contexto_editorial(segmentos, 100.0, 134.0, 150.0)
        with patch.dict(os.environ, {
            "CLIPPER_LLM_ACTIVO": "1",
            "OPENAI_API_KEY": "sk-test-no-log",
            "CLIPPER_LLM_MODO": "prueba",
        }):
            with patch.object(
                clipper.urllib.request,
                "urlopen",
                return_value=_RespuestaOpenAI(_respuesta_editorial()),
            ) as llamada:
                clipper.evaluar_editorial(
                    "canal", "reacción", relativos, [], 34.0, pico,
                    "Gancho heurístico",
                )
        cuerpo = json.loads(llamada.call_args.args[0].data.decode("utf-8"))
        prompt = cuerpo["input"]

        self.assertEqual(pico, 34.0)
        self.assertIn("POSICIÓN DEL PICO: 34.0s", prompt)
        self.assertNotIn("136.0s", prompt)
        self.assertNotIn("150.0s", prompt)
        for segmento in relativos:
            self.assertGreaterEqual(segmento["start"], 0.0)
            self.assertLessEqual(segmento["start"], 34.0)
            self.assertGreaterEqual(segmento["end"], 0.0)
            self.assertLessEqual(segmento["end"], 34.0)
        self.assertEqual(segmentos[0]["start"], 100.0)

    def test_llm_modo_desconocido_se_fuerza_a_prueba(self):
        with patch.dict(os.environ, {
            "CLIPPER_LLM_ACTIVO": "1",
            "OPENAI_API_KEY": "sk-test-no-log",
            "CLIPPER_LLM_MODO": "produccion",
        }):
            with patch.object(
                clipper.urllib.request,
                "urlopen",
                return_value=_RespuestaOpenAI(_respuesta_editorial()),
            ):
                _, meta = clipper.evaluar_editorial(
                    "canal", "motivo", [], [], 34.0, 12.0, "Gancho heurístico",
                )
        self.assertEqual(meta["mode"], "prueba")

    def test_llm_falla_sin_perder_candidato_ni_reintentar(self):
        entorno = {
            "CLIPPER_LLM_ACTIVO": "1",
            "OPENAI_API_KEY": "sk-test-no-log",
            "CLIPPER_LLM_MODELO": "gpt-5.6-luna",
            "CLIPPER_LLM_MODO": "prueba",
        }
        casos = (
            ("título vacío", _RespuestaOpenAI(_respuesta_editorial("")), "título vacío"),
            ("JSON inválido", _RespuestaOpenAI({
                "output": [{"content": [{"type": "output_text", "text": "no-json"}]}],
            }), "JSON inválida"),
            ("timeout", TimeoutError(), "timeout"),
            ("HTTP 429", clipper.urllib.error.HTTPError(
                clipper.LLM_ENDPOINT, 429, "rate limit", None, None), "HTTP 429"),
            ("HTTP 500", clipper.urllib.error.HTTPError(
                clipper.LLM_ENDPOINT, 500, "server error", None, None), "HTTP 500"),
        )
        for nombre, resultado, esperado in casos:
            with self.subTest(nombre=nombre):
                with patch.dict(os.environ, entorno):
                    opciones = (
                        {"side_effect": resultado}
                        if isinstance(resultado, BaseException)
                        else {"return_value": resultado}
                    )
                    with patch.object(clipper.urllib.request, "urlopen", **opciones) as llamada:
                        gancho, meta = clipper.evaluar_editorial(
                            "canal", "motivo", [], [], 30.0, 12.0, "Gancho heurístico",
                        )
                self.assertEqual(gancho, "Gancho heurístico")
                self.assertIn(esperado, meta["reason"])
                self.assertEqual(llamada.call_count, 1)

    def test_llm_sin_clave_no_hace_peticion(self):
        with patch.dict(os.environ, {
            "CLIPPER_LLM_ACTIVO": "1",
            "OPENAI_API_KEY": "",
        }):
            with patch.object(clipper.urllib.request, "urlopen") as llamada:
                gancho, meta = clipper.evaluar_editorial(
                    "canal", "motivo", [], [], 30.0, 12.0, "Gancho heurístico",
                )
        self.assertEqual(gancho, "Gancho heurístico")
        self.assertIn("OPENAI_API_KEY", meta["reason"])
        self.assertEqual(llamada.call_count, 0)

    def test_modo_prueba_no_descarta_recomendacion_de_luna(self):
        clip = {
            "start": 0,
            "end": 30,
            "hook": "Nadie esperaba esta reacción brutal",
            "hook_auto": True,
            "llm": {"decision": "descartar", "mode": "prueba"},
        }
        segmentos = [{"start": 0, "end": 30, "text": "Una frase de prueba"}]
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"video")
            with patch.dict(calidad.CONFIG["calidad"], {
                "publicar_con_gancho_automatico": True,
                "palabras_por_segundo_min": 0,
            }):
                with patch.object(calidad, "_analizar_calidad_av", return_value=(-10, 0)):
                    apto, motivos = calidad.evaluar(video, clip, segmentos, (26, 34))
        self.assertFalse(apto)
        self.assertTrue(any("LLM en modo prueba" in motivo for motivo in motivos))
        self.assertEqual(clip["llm"]["decision"], "descartar")


if __name__ == "__main__":
    unittest.main()
