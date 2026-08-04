"""Regresiones operativas que no pertenecen al flujo editorial."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import clipper
import raw
import servidor
import web


class MantenimientoTests(unittest.TestCase):
    def test_un_secreto_corto_no_destroza_logs(self):
        with patch.dict(os.environ, {"UN_AUTH": "1"}, clear=True):
            self.assertEqual(raw._texto_log("canal-01"), "canal-01")

    def test_un_secreto_real_se_tacha(self):
        with patch.dict(os.environ, {"CODEX_ACCESS_TOKEN": "secreto-muy-largo"}, clear=True):
            texto = raw._texto_log("clave=sk-secreto-muy-largo")
        self.assertNotIn("sk-secreto-muy-largo", texto)
        self.assertIn("[REDACTED]", texto)

    def test_preparar_volumen_solo_crea_cache_interna(self):
        with tempfile.TemporaryDirectory() as carpeta:
            anterior = servidor.DATA
            servidor.DATA = Path(carpeta)
            interna = servidor.DATA / "modelos"
            externa = servidor.DATA.parent / f"{servidor.DATA.name}-fuera"
            try:
                with patch.dict(os.environ, {"HF_HOME": str(interna)}, clear=True):
                    servidor.preparar_volumen()
                self.assertTrue(interna.is_dir())
                with patch.dict(os.environ, {"HF_HOME": str(externa)}, clear=True):
                    servidor.preparar_volumen()
                self.assertFalse(externa.exists())
            finally:
                servidor.DATA = anterior

    def test_limpieza_conserva_raw_pendiente_y_borra_completado(self):
        with tempfile.TemporaryDirectory() as carpeta:
            root = Path(carpeta)
            anteriores = (servidor.DATA, servidor.OUT, raw.RAW, clipper.DATA, clipper.OUT)
            servidor.DATA = clipper.DATA = root
            servidor.OUT = clipper.OUT = root / "out"
            raw.RAW = root / "out" / "RAW"
            raw.RAW.mkdir(parents=True)
            try:
                for raw_id, estado in (("pendiente", "pendiente"), ("hecho", "completado")):
                    (raw.RAW / f"{raw_id}.mp4").write_bytes(b"raw")
                    (raw.RAW / f"{raw_id}.json").write_text(
                        json.dumps({"id": raw_id, "status": estado}), encoding="utf-8")
                    viejo = time.time() - 30 * 86400
                    os.utime(raw.RAW / f"{raw_id}.mp4", (viejo, viejo))
                    os.utime(raw.RAW / f"{raw_id}.json", (viejo, viejo))
                servidor.limpiar_archivos_antiguos(dias=7)
                self.assertTrue((raw.RAW / "pendiente.mp4").exists())
                self.assertFalse((raw.RAW / "hecho.mp4").exists())
                self.assertFalse((raw.RAW / "hecho.json").exists())
            finally:
                (servidor.DATA, servidor.OUT, raw.RAW,
                 clipper.DATA, clipper.OUT) = anteriores

    def test_cache_raw_no_reparsea_manifiestos_estables(self):
        with tempfile.TemporaryDirectory() as carpeta:
            anteriores = (raw.RAW, clipper.OUT)
            clipper.OUT = Path(carpeta) / "out"
            raw.RAW = clipper.OUT / "RAW"
            raw.RAW.mkdir(parents=True)
            (raw.RAW / "uno.mp4").write_bytes(b"raw")
            raw._atomic_write(raw.RAW / "uno.json", {
                "id": "uno", "status": "pendiente", "duracion": 30,
                "segments": [{"text": "x"}] * 1000,
            })
            raw._LISTA_CACHE.clear()
            try:
                raw.listar_api()
                with patch.object(raw, "_read", wraps=raw._read) as leer:
                    raw.listar_api()
                leer.assert_not_called()
            finally:
                raw.RAW, clipper.OUT = anteriores
                raw._LISTA_CACHE.clear()

    def test_interfaz_no_expone_transcripcion_raw(self):
        self.assertNotIn("clip.segments", web.HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
