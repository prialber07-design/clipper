"""Pruebas de los arreglos de mantenimiento: redaccion, limpieza y cerrojo."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import antigravity
import bloqueo
import clipper
import raw
import servidor


class RedaccionTests(unittest.TestCase):
    """Un secreto corto no puede convertir cada '1' del log en [REDACTED]."""

    def test_secreto_corto_no_destroza_el_identificador(self):
        with patch.dict(os.environ, {"UN_AUTH": "1"}, clear=True):
            self.assertEqual(raw._texto_log("canal-01"), "canal-01")

    def test_secreto_corto_no_muerde_el_texto_de_gemini(self):
        with patch.dict(os.environ, {"UN_TOKEN": "1"}, clear=True):
            self.assertEqual(antigravity._texto("Gane 1 millon", 100),
                             "Gane 1 millon")

    def test_secreto_largo_sigue_tachado_en_el_log(self):
        with patch.dict(os.environ,
                        {"OPENAI_API_KEY": "sk-secreto-muy-largo"}, clear=True):
            texto = raw._texto_log("clave=sk-secreto-muy-largo")
        self.assertNotIn("sk-secreto-muy-largo", texto)
        self.assertIn("[REDACTED]", texto)

    def test_secreto_largo_sigue_tachado_hacia_gemini(self):
        with patch.dict(os.environ,
                        {"MI_TOKEN": "token-secreto-largo"}, clear=True):
            texto = antigravity._texto("valor token-secreto-largo", 100)
        self.assertNotIn("token-secreto-largo", texto)
        self.assertIn("[REDACTED]", texto)

    def test_el_umbral_es_el_mismo_en_los_dos_modulos(self):
        self.assertEqual(raw.LARGO_MINIMO_SECRETO,
                         antigravity.LARGO_MINIMO_SECRETO)


class LimpiezaRawTests(unittest.TestCase):
    """RAW solo caduca cuando ya dio clip; lo pendiente se conserva."""

    VIEJO = 30 * 86400

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = (servidor.DATA, servidor.OUT, raw.RAW,
                         clipper.DATA, clipper.OUT)
        servidor.DATA = self.root
        servidor.OUT = self.root / "out"
        clipper.DATA = self.root
        clipper.OUT = self.root / "out"
        raw.RAW = self.root / "out" / "RAW"
        raw.RAW.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        (servidor.DATA, servidor.OUT, raw.RAW,
         clipper.DATA, clipper.OUT) = self.previous
        self.temp.cleanup()

    def _candidato(self, raw_id, status, *, antiguo=True, con_gemini=True):
        carpeta = raw.RAW
        mp4 = carpeta / f"{raw_id}.mp4"
        manifest = carpeta / f"{raw_id}.json"
        mp4.write_bytes(b"raw")
        manifest.write_text(json.dumps({"id": raw_id, "status": status}),
                            encoding="utf-8")
        rutas = [mp4, manifest]
        if con_gemini:
            gemini = carpeta / "_gemini" / f"{raw_id}.json"
            gemini.parent.mkdir(parents=True, exist_ok=True)
            gemini.write_text("{}", encoding="utf-8")
            rutas.append(gemini)
        if antiguo:
            viejo = time.time() - self.VIEJO
            for ruta in rutas:
                os.utime(ruta, (viejo, viejo))
        return mp4, manifest

    def _limpiar(self):
        servidor.limpiar_archivos_antiguos(dias=7)

    def test_completado_antiguo_se_borra_entero(self):
        mp4, manifest = self._candidato("completado-01", "completado")
        gemini = raw.RAW / "_gemini" / "completado-01.json"
        self._limpiar()
        self.assertFalse(mp4.exists())
        self.assertFalse(manifest.exists())
        self.assertFalse(gemini.exists())

    def test_pendiente_antiguo_se_conserva(self):
        mp4, manifest = self._candidato("pendiente-01", "pendiente")
        self._limpiar()
        self.assertTrue(mp4.exists(), "un candidato sin analizar no debe borrarse")
        self.assertTrue(manifest.exists())

    def test_error_antiguo_se_conserva(self):
        mp4, manifest = self._candidato("error-01", "error_luna")
        self._limpiar()
        self.assertTrue(mp4.exists())
        self.assertTrue(manifest.exists())

    def test_completado_reciente_se_conserva(self):
        mp4, manifest = self._candidato("reciente-01", "completado", antiguo=False)
        self._limpiar()
        self.assertTrue(mp4.exists())
        self.assertTrue(manifest.exists())

    def test_manifiesto_con_id_tramposo_no_borra_fuera(self):
        intruso = self.root / "intruso.mp4"
        intruso.write_bytes(b"no tocar")
        manifest = raw.RAW / "trampa.json"
        manifest.write_text(
            json.dumps({"id": "../../intruso", "status": "completado"}),
            encoding="utf-8")
        viejo = time.time() - self.VIEJO
        os.utime(manifest, (viejo, viejo))
        self._limpiar()
        self.assertTrue(intruso.exists())
        self.assertIsNone(
            servidor._raw_caducado(manifest, 7 * 86400, time.time()))

    def test_manifiesto_ilegible_no_rompe_la_limpieza(self):
        roto = raw.RAW / "roto.json"
        roto.write_text("{no es json", encoding="utf-8")
        viejo = time.time() - self.VIEJO
        os.utime(roto, (viejo, viejo))
        self._limpiar()
        self.assertTrue(roto.exists())

    def test_listos_antiguos_si_se_borran(self):
        listos = servidor.OUT / "LISTOS"
        listos.mkdir(parents=True, exist_ok=True)
        clip = listos / "001_canal_2026-07-01.mp4"
        clip.write_bytes(b"clip")
        viejo = time.time() - self.VIEJO
        os.utime(clip, (viejo, viejo))
        self._limpiar()
        self.assertFalse(clip.exists(), "la caducidad normal sigue funcionando")


class CerrojoCpuTests(unittest.TestCase):
    """Transcribir y renderizar comparten un unico cerrojo."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.temp.name) / "cpu.lock"

    def tearDown(self):
        self.temp.cleanup()

    def test_desactivado_no_toma_el_cerrojo(self):
        with bloqueo.exclusivo_si(False, self.ruta):
            pass
        self.assertFalse(self.ruta.exists())

    def test_activado_toma_el_cerrojo(self):
        with bloqueo.exclusivo_si(True, self.ruta):
            self.assertTrue(self.ruta.exists())

    def test_config_manda_sobre_la_serializacion(self):
        with patch.dict(clipper.CONFIG, {"cpu": {"una_tarea_pesada_a_la_vez": False}}):
            self.assertFalse(clipper.serializar_cpu())
        with patch.dict(clipper.CONFIG, {"cpu": {"una_tarea_pesada_a_la_vez": True}}):
            self.assertTrue(clipper.serializar_cpu())

    def test_sin_clave_se_serializa_por_defecto(self):
        with patch.dict(clipper.CONFIG, {"cpu": {}}):
            self.assertTrue(clipper.serializar_cpu())

    def test_transcripcion_y_render_usan_el_mismo_cerrojo(self):
        """Guarda contra volver a separarlos: es el bug que se arreglo."""
        fuentes = {
            nombre: (clipper.ROOT / nombre).read_text(encoding="utf-8")
            for nombre in ("live.py", "raw.py")
        }
        for nombre, texto in fuentes.items():
            self.assertIn("clipper.CPU_LOCK", texto, f"{nombre} no usa el cerrojo comun")
            self.assertNotIn(".whisper.lock", texto, f"{nombre} conserva un cerrojo propio")


if __name__ == "__main__":
    unittest.main()
