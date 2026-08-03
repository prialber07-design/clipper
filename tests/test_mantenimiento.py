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
import live
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


class VentanaSinRecodificarTests(unittest.TestCase):
    """Montar la ventana copia los .ts; recodificar era el mayor coste de CPU."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous_work = live.WORK
        live.WORK = self.root / "work"
        self.buffer = self.root / "buffer"
        self.buffer.mkdir(parents=True, exist_ok=True)
        for i in range(12):
            (self.buffer / f"{i:06d}.ts").write_bytes(b"ts")

    def tearDown(self):
        live.WORK = self.previous_work
        self.temp.cleanup()

    def _montar(self):
        cap = live.BufferExistente(self.buffer)
        with patch.object(live.clipper, "run") as run:
            live.montar_ventana(cap, t_video=80.0, slug="prueba", antes=40.0)
        return [call.args[0] for call in run.call_args_list]

    def test_la_ventana_se_concatena_sin_recodificar(self):
        concat = self._montar()[0]
        self.assertIn("copy", concat)
        self.assertNotIn("libx264", concat, "volver a recodificar la ventana")

    def test_el_audio_se_sigue_extrayendo(self):
        comandos = self._montar()
        self.assertTrue(any("pcm_s16le" in cmd for cmd in comandos),
                        "Whisper necesita el wav de 16 kHz")


class RmsSegmentoTests(unittest.TestCase):
    """El RMS lo calcula ffmpeg, pero la escala debe seguir siendo la misma."""

    def _con_stderr(self, stderr):
        salida = type("Proc", (), {"stderr": stderr})()
        with patch.object(live.subprocess, "run", return_value=salida):
            return live.rms_segmento(Path("da-igual.ts"))

    def test_convierte_dbfs_a_amplitud_lineal(self):
        # -20 dBFS es la decima parte de la escala completa.
        self.assertAlmostEqual(self._con_stderr("RMS level dB: -20.000000"),
                               3276.8, places=1)

    def test_silencio_absoluto_no_revienta(self):
        self.assertEqual(self._con_stderr("RMS level dB: -inf"), 0.0)

    def test_sin_lectura_devuelve_cero(self):
        self.assertEqual(self._con_stderr("ffmpeg no pudo abrir el fichero"), 0.0)

    def test_se_queda_con_el_valor_global(self):
        stderr = "RMS level dB: -30.000000\nRMS level dB: -20.000000\n"
        self.assertAlmostEqual(self._con_stderr(stderr), 3276.8, places=1)


class PrepararVolumenTests(unittest.TestCase):
    """HOME vive en el volumen y un bind mount lo deja sin crear."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = servidor.DATA
        servidor.DATA = self.root

    def tearDown(self):
        servidor.DATA = self.previous
        self.temp.cleanup()

    def test_crea_el_home_de_agy_dentro_del_volumen(self):
        home = self.root / "antigravity"
        with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
            servidor.preparar_volumen()
        self.assertTrue(home.is_dir(), "sin HOME, agy no puede guardar la sesion")

    def test_crea_tambien_la_carpeta_del_modelo(self):
        modelos = self.root / "modelos"
        with patch.dict(os.environ, {"HF_HOME": str(modelos)}, clear=True):
            servidor.preparar_volumen()
        self.assertTrue(modelos.is_dir())

    def test_no_toca_un_home_fuera_del_volumen(self):
        fuera = Path(self.temp.name).parent / "home-de-verdad-no-crear"
        with patch.dict(os.environ, {"HOME": str(fuera)}, clear=True):
            servidor.preparar_volumen()
        self.assertFalse(fuera.exists(), "solo debe crear rutas dentro del volumen")

    def test_respeta_una_carpeta_que_ya_existe(self):
        home = self.root / "antigravity"
        home.mkdir()
        (home / "sesion.json").write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
            servidor.preparar_volumen()
        self.assertTrue((home / "sesion.json").exists(), "no debe pisar lo que hay")

    def test_sin_variables_no_hace_nada(self):
        with patch.dict(os.environ, {}, clear=True):
            servidor.preparar_volumen()
        self.assertEqual(list(self.root.iterdir()), [])


class AdjuntoNtfyTests(unittest.TestCase):
    def test_el_adjunto_viene_desactivado(self):
        """Con ntfy anonimo el adjunto nunca llegaba a enviarse."""
        self.assertFalse(
            clipper.CONFIG.get("notificaciones", {}).get("adjuntar_video", False))


if __name__ == "__main__":
    unittest.main()
