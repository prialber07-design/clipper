import json
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import clipper
import raw
import web


def _llm():
    return {
        "decision": "publicar", "score": 90, "confidence": 0.9,
        "reason": "momento claro", "social_description": "Descripción.",
        "hashtags": ["#uno", "#dos", "#tres", "#cuatro"],
        "visual_summary": "Dos personas conversan.", "visual_timeline": [],
        "people": [], "visible_text": [], "visual_warnings": [],
        "image_count": 3, "latency_ms": 10,
    }


class RawTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = (raw.RAW, raw.RAW_LOG, raw._MANIFEST_LOCK,
                         raw._PROCESS_LOCK, clipper.DATA, clipper.OUT)
        raw.RAW = self.root / "out" / "RAW"
        raw.RAW_LOG = self.root / "logs" / "raw.jsonl"
        raw._MANIFEST_LOCK = self.root / "manifest.lock"
        raw._PROCESS_LOCK = self.root / "process.lock"
        clipper.DATA = self.root
        clipper.OUT = self.root / "out"
        raw.RAW.mkdir(parents=True)
        raw._LISTA_CACHE.clear()

    def tearDown(self):
        (raw.RAW, raw.RAW_LOG, raw._MANIFEST_LOCK,
         raw._PROCESS_LOCK, clipper.DATA, clipper.OUT) = self.previous
        raw._LISTA_CACHE.clear()
        self.temp.cleanup()

    def candidato(self, raw_id="uno-01", status="pendiente", creado="2026-08-01T00:00:00+00:00"):
        (raw.RAW / f"{raw_id}.mp4").write_bytes(b"video")
        raw._atomic_write(raw.RAW / f"{raw_id}.json", {
            "id": raw_id, "status": status, "created_at": creado,
            "last_attempt_at": "", "last_error": "", "attempt": None,
            "retry_count": 0, "next_retry_at": "", "duracion": 30.0,
            "pico": 12.0, "canal": "canal", "motivo": "risa",
            "segments": [{"start": 0, "end": 2, "text": "hola"}],
            "words": [], "chat": [], "luna": None, "destination": None,
        })
        return raw_id

    def test_un_candidato_solo_se_reclama_una_vez(self):
        raw_id = self.candidato()
        raw._claim(raw_id)
        with self.assertRaises(raw.RawActivo):
            raw._claim(raw_id)

    def test_luna_visual_completa_y_registra_fotogramas(self):
        raw_id = self.candidato()
        _, intento = raw._claim(raw_id)
        frame = self.root / "frame.jpg"
        frame.write_bytes(b"jpg")
        with patch.object(raw.storyboard, "extraer", return_value=nullcontext([(0.0, frame)])), \
             patch.object(raw.clipper, "evaluar_editorial", return_value=("Hook", _llm())), \
             patch.object(raw, "_render_and_publish", return_value={"queue": "LISTOS", "name": "clip.mp4"}):
            raw._run(raw_id, intento)
        manifest = raw._read(raw_id)
        self.assertEqual(manifest["status"], "completado")
        self.assertEqual(manifest["luna"]["image_count"], 3)
        self.assertIn("LUNA_VISUAL_FINISHED", raw.RAW_LOG.read_text(encoding="utf-8"))

    def test_fallo_visual_conserva_raw_y_programa_reintento(self):
        raw_id = self.candidato()
        _, intento = raw._claim(raw_id)
        with patch.object(raw.storyboard, "extraer", side_effect=RuntimeError("fallo ffmpeg")):
            raw._run(raw_id, intento)
        manifest = raw._read(raw_id)
        self.assertEqual(manifest["status"], "error_luna")
        self.assertEqual(manifest["retry_count"], 1)
        self.assertTrue((raw.RAW / f"{raw_id}.mp4").exists())
        self.assertGreater(datetime.fromisoformat(manifest["next_retry_at"]),
                           datetime.now(timezone.utc))

    def test_supervisor_empieza_por_el_mas_antiguo(self):
        self.candidato("nuevo", creado="2026-08-02T00:00:00+00:00")
        self.candidato("viejo", creado="2026-08-01T00:00:00+00:00")
        with patch.object(raw, "enqueue") as enqueue:
            self.assertEqual(raw.procesar_pendientes(), 1)
        enqueue.assert_called_once_with("viejo")

    def test_supervisor_respeta_backoff(self):
        raw_id = self.candidato(status="error_luna")
        raw._update(raw_id, next_retry_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        with patch.object(raw, "enqueue") as enqueue:
            self.assertEqual(raw.procesar_pendientes(), 0)
        enqueue.assert_not_called()

    def test_api_no_expone_transcripcion(self):
        raw_id = self.candidato()
        raw._update(raw_id, luna={"latency_ms": 12, "image_count": 30})
        item = raw.listar_api()[0]
        self.assertEqual(item["image_count"], 30)
        for campo in ("segments", "words", "chat"):
            self.assertNotIn(campo, item)

    def test_interfaz_describe_el_flujo_automatico(self):
        self.assertIn("Esperando turno de Luna", web.HTML_TEMPLATE)
        self.assertNotIn("Analizar con ", web.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
