import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import clipper
import live
import raw
import web


def _visual():
    return {
        "summary": "Una escena factual.",
        "timeline": [],
        "people": [],
        "visible_text": [],
        "setting": "Directo",
        "key_moment": "El momento central.",
        "editorial_facts": ["Hecho comprobable."],
        "warnings": [],
    }


def _llm():
    return {
        "model": "gpt-5.6-luna",
        "decision": "publicar",
        "score": 90,
        "confidence": 0.9,
        "reason": "Momento claro.",
        "social_description": "Una reacción inesperada.",
        "hashtags": ["#clips", "#viral", "#directo", "#fyp"],
        "input_tokens": 10,
        "output_tokens": 10,
        "latency_ms": 20,
    }


class RawTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = (
            raw.RAW, raw.RAW_LOG, raw._MANIFEST_LOCK, raw._PROCESS_LOCK,
            clipper.DATA, clipper.OUT, clipper.WORK,
        )
        raw.RAW = self.root / "out" / "RAW"
        raw.RAW_LOG = self.root / "logs" / "raw-processing.jsonl"
        raw._MANIFEST_LOCK = self.root / "raw-manifest.lock"
        raw._PROCESS_LOCK = self.root / "raw-process.lock"
        clipper.DATA = self.root
        clipper.OUT = self.root / "out"
        clipper.WORK = self.root / "work"
        raw._THREADS.clear()

    def tearDown(self):
        raw._THREADS.clear()
        raw.RAW_LOG.unlink(missing_ok=True)
        (raw.RAW, raw.RAW_LOG, raw._MANIFEST_LOCK, raw._PROCESS_LOCK,
         clipper.DATA, clipper.OUT, clipper.WORK) = self.previous
        self.temp.cleanup()

    def _manifest(self, status="pendiente", raw_id="canal-20260802-030000-01"):
        raw.RAW.mkdir(parents=True, exist_ok=True)
        (raw.RAW / f"{raw_id}.mp4").write_bytes(b"raw")
        data = {
            "schema": 1, "id": raw_id, "nombre": f"{raw_id}.mp4",
            "canal": "canal", "motivo": "pico", "pico": 4.0,
            "start": 0.0, "end": 34.0, "duracion": 34.0,
            "segments": [{"start": 0.0, "end": 34.0, "text": "Momento increíble"}],
            "words": [], "chat": ["clipealo"], "status": status,
            "created_at": "2026-08-02T03:00:00+00:00", "last_attempt_at": "",
            "last_error": "", "attempt": None, "gemini": None, "luna": None,
            "destination": None,
        }
        raw._atomic_write(raw.RAW / f"{raw_id}.json", data)
        return raw_id

    def test_api_raw_no_expone_manifesta(self):
        self._manifest()
        item = raw.listar_api()[0]
        self.assertEqual(item["status"], "pendiente")
        self.assertIn("/files/out/RAW/", item["url"])
        self.assertNotIn("segments", item)
        self.assertNotIn("words", item)

    def test_dos_clics_no_crean_dos_procesos(self):
        raw_id = self._manifest()

        class FakeThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self):
                return None

        with patch.object(raw.threading, "Thread", FakeThread):
            raw.enqueue(raw_id, "gemini")
            with self.assertRaises(raw.RawActivo):
                raw.enqueue(raw_id, "luna")

    def test_gemini_valido_llega_a_luna_y_completa(self):
        raw_id = self._manifest("procesando_gemini")
        manifest = raw._read(raw_id)
        manifest["attempt"] = {"id": "attempt1", "mode": "gemini", "started_at": "now"}
        raw._atomic_write(raw.RAW / f"{raw_id}.json", manifest)
        with patch.object(raw.antigravity, "analizar", return_value=(_visual(), {"status": "ok", "latency_ms": 7})), \
                patch.object(raw.clipper, "evaluar_editorial", return_value=("Giro inesperado", _llm())) as luna, \
                patch.object(raw, "_render_and_publish", return_value={"queue": "LISTOS", "name": "001_canal_2026-08-02.mp4"}):
            raw._run(raw_id, "gemini", "attempt1")
        resultado = raw._read(raw_id)
        self.assertEqual(resultado["status"], "completado")
        self.assertEqual(resultado["destination"]["queue"], "LISTOS")
        self.assertEqual(luna.call_args.kwargs["analisis_visual"]["summary"], "Una escena factual.")
        self.assertTrue(luna.call_args.kwargs["estricto"])

    def test_gemini_invalido_se_queda_en_raw_y_no_llama_a_luna(self):
        raw_id = self._manifest("procesando_gemini")
        manifest = raw._read(raw_id)
        manifest["attempt"] = {"id": "attempt2", "mode": "gemini", "started_at": "now"}
        raw._atomic_write(raw.RAW / f"{raw_id}.json", manifest)
        with patch.object(raw.antigravity, "analizar", return_value=(None, {"status": "invalid_json", "latency_ms": 10})), \
                patch.object(raw.clipper, "evaluar_editorial") as luna:
            raw._run(raw_id, "gemini", "attempt2")
        resultado = raw._read(raw_id)
        self.assertEqual(resultado["status"], "error_gemini")
        luna.assert_not_called()
        self.assertTrue((raw.RAW / f"{raw_id}.mp4").exists())

    def test_excepcion_de_gemini_es_error_gemini_y_no_llama_a_luna(self):
        raw_id = self._manifest("procesando_gemini")
        manifest = raw._read(raw_id)
        manifest["attempt"] = {"id": "attempt-exception", "mode": "gemini", "started_at": "now"}
        raw._atomic_write(raw.RAW / f"{raw_id}.json", manifest)
        with patch.object(raw.antigravity, "analizar",
                          side_effect=ValueError("prompt inválido")), \
                patch.object(raw.clipper, "evaluar_editorial") as luna:
            raw._run(raw_id, "gemini", "attempt-exception")
        resultado = raw._read(raw_id)
        self.assertEqual(resultado["status"], "error_gemini")
        self.assertIn("prompt inválido", resultado["last_error"])
        luna.assert_not_called()
        log = raw.RAW_LOG.read_text(encoding="utf-8")
        self.assertIn("GEMINI_FAILED", log)
        self.assertNotIn("RAW_FAILED", log)

    def test_fallo_al_arrancar_hilo_conserva_la_fase_gemini(self):
        raw_id = self._manifest()

        class HiloFallido:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                raise RuntimeError("thread start failed")

        with patch.object(raw.threading, "Thread", HiloFallido), \
                self.assertRaises(raw.RawError):
            raw.enqueue(raw_id, "gemini")
        resultado = raw._read(raw_id)
        self.assertEqual(resultado["status"], "error_gemini")
        log = raw.RAW_LOG.read_text(encoding="utf-8")
        self.assertIn("GEMINI_FAILED", log)
        self.assertNotIn("RAW_FAILED", log)
        self.assertFalse(raw._THREADS)

    def test_creditos_no_confirmados_se_quedan_en_raw(self):
        raw_id = self._manifest("procesando_gemini")
        manifest = raw._read(raw_id)
        manifest["attempt"] = {"id": "attempt-creditos", "mode": "gemini", "started_at": "now"}
        raw._atomic_write(raw.RAW / f"{raw_id}.json", manifest)
        with patch.object(raw.antigravity, "analizar",
                          return_value=(None, {"status": "credits_unknown", "latency_ms": 0})), \
                patch.object(raw.clipper, "evaluar_editorial") as luna:
            raw._run(raw_id, "gemini", "attempt-creditos")
        self.assertEqual(raw._read(raw_id)["status"], "error_gemini")
        luna.assert_not_called()

    def test_luna_invalida_se_queda_en_raw(self):
        raw_id = self._manifest("procesando_luna")
        manifest = raw._read(raw_id)
        manifest["attempt"] = {"id": "attempt3", "mode": "luna", "started_at": "now"}
        raw._atomic_write(raw.RAW / f"{raw_id}.json", manifest)
        with patch.object(raw.clipper, "evaluar_editorial", side_effect=RuntimeError("JSON_INVALIDO")):
            raw._run(raw_id, "luna", "attempt3")
        self.assertEqual(raw._read(raw_id)["status"], "error_luna")

    def test_reinicio_recupera_proceso_huerfano(self):
        raw_id = self._manifest("procesando_luna")
        raw.recuperar_huerfanos()
        self.assertEqual(raw._read(raw_id)["status"], "error_luna")

    def test_logs_persistentes_no_guardan_secretos(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "super-secreto"}):
            raw._evento("LUNA_FAILED", "canal-01", modo_actual="luna",
                        error="OPENAI_API_KEY=super-secreto")
        contenido = raw.RAW_LOG.read_text(encoding="utf-8")
        self.assertIn("LUNA_FAILED", contenido)
        self.assertIn("canal-01", contenido)
        self.assertNotIn("super-secreto", contenido)

    def test_live_manual_guarda_raw_y_no_llama_a_luna_ni_render(self):
        anterior = live.WORK
        live.WORK = self.root / "work"
        slug = "canal-20260802-030000"
        trabajo = live.WORK / slug
        trabajo.mkdir(parents=True)
        (trabajo / "transcript.json").write_text(json.dumps({
            "segments": [{"start": 0, "end": 40, "text": "Esto es un momento increíble"}],
            "words": [{"start": 0, "end": 1, "word": "Esto"}],
        }), encoding="utf-8")
        try:
            manifest = {"id": slug + "-01", "nombre": slug + "-01.mp4", "status": "pendiente"}
            with patch.object(live, "recargar"), patch.object(live, "aplicar_ajustes_canal"), \
                    patch.object(live.time, "strftime", return_value="20260802-030000"), \
                    patch.object(live, "elegir_duracion", return_value=("corto", {"min": 26, "max": 34})), \
                    patch.object(live, "montar_ventana", return_value=(self.root / "source.mp4", 20)), \
                    patch.object(live.bloqueo, "exclusivo", return_value=nullcontext()), \
                    patch.object(live.clipper, "cmd_transcribe"), \
                    patch.object(live.raw, "crear", return_value=manifest) as crear, \
                    patch.object(live.raw, "modo", return_value="manual"), \
                    patch.object(live.raw, "enqueue") as enqueue, \
                    patch.object(live.clipper, "evaluar_editorial") as luna, \
                    patch.object(live.clipper, "cmd_render") as render:
                live.procesar(Mock(), 20, "canal", "pico", "cpu", [])
            crear.assert_called_once()
            enqueue.assert_not_called()
            luna.assert_not_called()
            render.assert_not_called()
        finally:
            live.WORK = anterior

    def test_gemini_auto_reutiliza_el_mismo_procesador(self):
        anterior = live.WORK
        live.WORK = self.root / "work"
        slug = "canal-20260802-030000"
        trabajo = live.WORK / slug
        trabajo.mkdir(parents=True)
        (trabajo / "transcript.json").write_text(json.dumps({
            "segments": [{"start": 0, "end": 40, "text": "Esto es un momento increíble"}],
            "words": [],
        }), encoding="utf-8")
        try:
            with patch.object(live, "recargar"), patch.object(live, "aplicar_ajustes_canal"), \
                    patch.object(live.time, "strftime", return_value="20260802-030000"), \
                    patch.object(live, "elegir_duracion", return_value=("corto", {"min": 26, "max": 34})), \
                    patch.object(live, "montar_ventana", return_value=(self.root / "source.mp4", 20)), \
                    patch.object(live.bloqueo, "exclusivo", return_value=nullcontext()), \
                    patch.object(live.clipper, "cmd_transcribe"), \
                    patch.object(live.raw, "crear", return_value={"id": slug + "-01", "nombre": "x.mp4", "status": "pendiente"}), \
                    patch.object(live.raw, "modo", return_value="gemini_auto"), \
                    patch.object(live.raw, "enqueue") as enqueue:
                live.procesar(Mock(), 20, "canal", "pico", "cpu", [])
            enqueue.assert_called_once_with(slug + "-01", "gemini")
        finally:
            live.WORK = anterior

    def test_ui_raw_tiene_acciones_y_api_post(self):
        self.assertIn("panel-raw", web.HTML_TEMPLATE)
        self.assertIn("processRaw", web.HTML_TEMPLATE)
        self.assertIn("/api/raw/process", web.HTML_TEMPLATE)
        self.assertIn("textContent", web.HTML_TEMPLATE)
        self.assertIn("disabled", web.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
