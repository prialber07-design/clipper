"""Pruebas de los arreglos de mantenimiento: redaccion, limpieza y cerrojo."""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest.mock
from unittest.mock import patch

import antigravity
import bloqueo
import clipper
import live
import raw
import web
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

    def test_el_render_deja_marca_de_prioridad_mientras_espera(self):
        """Sin esto el render se quedaba 16 minutos sin conseguir turno."""
        marca = bloqueo._marca_prioridad(self.ruta)
        with bloqueo.exclusivo(self.ruta, prioritario=True):
            self.assertFalse(marca.exists(),
                             "con el turno ya en la mano, la marca debe levantarse")
        self.assertFalse(marca.exists())

    def test_una_marca_fresca_hace_ceder_el_turno(self):
        marca = bloqueo._marca_prioridad(self.ruta)
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.touch()
        self.assertTrue(bloqueo._hay_prioridad(self.ruta))

    def test_una_marca_caducada_se_ignora(self):
        """Si el proceso prioritario muere, nadie puede quedarse esperandolo."""
        marca = bloqueo._marca_prioridad(self.ruta)
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.touch()
        viejo = time.time() - bloqueo.PRIORIDAD_MAX_S - 60
        os.utime(marca, (viejo, viejo))
        self.assertFalse(bloqueo._hay_prioridad(self.ruta))
        # Y el turno se consigue igualmente, sin quedarse colgado.
        with bloqueo.exclusivo(self.ruta, etiqueta="normal"):
            pass

    def test_sin_marca_no_hay_prioridad(self):
        self.assertFalse(bloqueo._hay_prioridad(self.ruta))

    def test_el_render_pide_prioridad(self):
        """Guarda contra quitarla sin darse cuenta."""
        fuente = (clipper.ROOT / "raw.py").read_text(encoding="utf-8")
        self.assertIn("prioritario=True", fuente,
                      "el render debe pedir prioridad sobre las transcripciones")

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


class ProcesarPendientesTests(unittest.TestCase):
    """La ruta Gemini integrada vuelve a dispararse, pero con freno."""

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
        raw.RAW.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        (raw.RAW, raw.RAW_LOG, raw._MANIFEST_LOCK,
         raw._PROCESS_LOCK, clipper.DATA, clipper.OUT) = self.previous
        self.temp.cleanup()

    def _candidato(self, raw_id, status="pendiente", creado="2026-08-02T05:00:00+00:00",
                   con_gemini=False, next_retry_at=""):
        (raw.RAW / f"{raw_id}.mp4").write_bytes(b"raw")
        raw._atomic_write(raw.RAW / f"{raw_id}.json", {
            "id": raw_id, "status": status, "created_at": creado,
            "duracion": 30.0, "next_retry_at": next_retry_at,
            # _claim siempre la escribe: un activo sin fecha no existe en
            # produccion, y sin ella el trabajo se daria por muerto.
            "last_attempt_at": datetime.now(timezone.utc).isoformat(),
        })
        if con_gemini:
            destino = raw.RAW / "_gemini" / f"{raw_id}.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text("{}", encoding="utf-8")

    def _encolar(self, activo=True, limite=1):
        preparado = (True, "") if activo else (False, "disabled")
        raw._AVISO_CONFIG.update(motivo="", ts=0.0)
        with patch.object(raw.antigravity, "preparado", return_value=preparado), \
             patch.object(raw, "enqueue") as enqueue:
            encolados = raw.procesar_pendientes(limite=limite)
        return encolados, [call.args[0] for call in enqueue.call_args_list]

    def test_encola_el_candidato_pendiente(self):
        self._candidato("uno-01")
        encolados, ids = self._encolar()
        self.assertEqual(encolados, 1)
        self.assertEqual(ids, ["uno-01"])

    def test_el_interruptor_lo_apaga(self):
        self._candidato("uno-01")
        encolados, ids = self._encolar(activo=False)
        self.assertEqual((encolados, ids), (0, []),
                         "CLIPPER_ANTIGRAVITY_ACTIVO=0 debe frenarlo todo")

    def test_empieza_por_el_mas_antiguo(self):
        self._candidato("nuevo-01", creado="2026-08-02T20:00:00+00:00")
        self._candidato("viejo-01", creado="2026-08-01T05:00:00+00:00")
        _, ids = self._encolar()
        self.assertEqual(ids, ["viejo-01"], "la cola debe drenarse en orden")

    def test_no_encola_si_hay_uno_en_marcha(self):
        self._candidato("activo-01", status="procesando_gemini")
        self._candidato("espera-01")
        encolados, ids = self._encolar()
        self.assertEqual((encolados, ids), (0, []),
                         "el analisis se serializa: uno cada vez")

    def test_ignora_los_que_ya_tienen_analisis(self):
        self._candidato("hecho-01", con_gemini=True)
        encolados, ids = self._encolar()
        self.assertEqual((encolados, ids), (0, []),
                         "de esos ya se ocupa procesar_analizados")

    def test_respeta_la_espera_creciente(self):
        futuro = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self._candidato("fallado-01", status="error_gemini", next_retry_at=futuro)
        encolados, ids = self._encolar()
        self.assertEqual((encolados, ids), (0, []),
                         "un agy que falla no puede reintentarse cada 15s")

    def test_reintenta_cuando_vence_la_espera(self):
        pasado = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self._candidato("fallado-01", status="error_gemini", next_retry_at=pasado)
        encolados, _ = self._encolar()
        self.assertEqual(encolados, 1)

    def test_no_toca_los_completados(self):
        self._candidato("listo-01", status="completado")
        encolados, ids = self._encolar()
        self.assertEqual((encolados, ids), (0, []))

    def test_un_fallo_de_gemini_programa_la_espera(self):
        """Lo contrario seria machacar agy cada 15 segundos con cuota agotada."""
        self._candidato("falla-01")
        _, intento = raw._claim("falla-01", "gemini")
        with patch.object(raw.antigravity, "analizar",
                          return_value=(None, {"status": "quota", "latency_ms": 5})):
            raw._run("falla-01", "gemini", intento)
        actual = raw._read("falla-01")
        self.assertEqual(actual["status"], "error_gemini")
        self.assertEqual(actual["retry_count"], 1)
        self.assertTrue(actual["next_retry_at"], "sin espera se reintenta en bucle")
        self.assertGreater(datetime.fromisoformat(actual["next_retry_at"]),
                           datetime.now(timezone.utc))

    def test_la_espera_crece_con_cada_fallo(self):
        self._candidato("falla-02")
        for esperado in (1, 2, 3):
            raw._update("falla-02", status="pendiente", next_retry_at="")
            _, intento = raw._claim("falla-02", "gemini")
            with patch.object(raw.antigravity, "analizar",
                              return_value=(None, {"status": "quota"})):
                raw._run("falla-02", "gemini", intento)
            self.assertEqual(raw._read("falla-02")["retry_count"], esperado)

    def test_un_zombi_no_bloquea_la_cola(self):
        """El fallo real: dos trabajos muertos pararon la cola doce horas."""
        viejo = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        self._candidato("zombi-01", status="procesando_luna")
        raw._update("zombi-01", last_attempt_at=viejo)
        self._candidato("espera-01")
        encolados, ids = self._encolar()
        self.assertEqual(ids, ["espera-01"],
                         "un trabajo muerto no puede frenar a los vivos")

    def test_un_trabajo_vivo_si_bloquea(self):
        reciente = datetime.now(timezone.utc).isoformat()
        self._candidato("vivo-01", status="procesando_luna")
        raw._update("vivo-01", last_attempt_at=reciente)
        self._candidato("espera-01")
        encolados, ids = self._encolar()
        self.assertEqual((encolados, ids), (0, []),
                         "el analisis va de uno en uno a proposito")

    def test_sin_fecha_de_intento_se_da_por_muerto(self):
        self._candidato("sinfecha-01", status="procesando_luna")
        raw._update("sinfecha-01", last_attempt_at="")
        self._candidato("espera-01")
        _, ids = self._encolar()
        self.assertEqual(ids, ["espera-01"])

    def test_recuperar_en_caliente_solo_toca_los_zombis(self):
        viejo = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        self._candidato("zombi-01", status="procesando_luna")
        raw._update("zombi-01", last_attempt_at=viejo)
        self._candidato("vivo-01", status="procesando_luna")
        raw._update("vivo-01", last_attempt_at=datetime.now(timezone.utc).isoformat())

        raw.recuperar_huerfanos(max_edad_s=raw.EDAD_ZOMBI_S)

        self.assertEqual(raw._read("zombi-01")["status"], "error_luna")
        self.assertEqual(raw._read("vivo-01")["status"], "procesando_luna",
                         "no puede tumbar un trabajo que si esta corriendo")

    def test_al_arrancar_se_recupera_todo(self):
        """Ningun hilo sobrevive al reinicio, por reciente que sea."""
        self._candidato("reciente-01", status="procesando_gemini")
        raw._update("reciente-01", last_attempt_at=datetime.now(timezone.utc).isoformat())
        raw.recuperar_huerfanos()
        self.assertEqual(raw._read("reciente-01")["status"], "error_gemini")

    def test_el_limite_acota_cuantos_entran(self):
        for i in range(5):
            self._candidato(f"cand-{i:02d}", creado=f"2026-08-0{i + 1}T05:00:00+00:00")
        encolados, ids = self._encolar(limite=2)
        self.assertEqual(encolados, 2)
        self.assertEqual(ids, ["cand-00", "cand-01"])


class CacheGaleriaTests(unittest.TestCase):
    """La galería no puede volver a medir 87 vídeos en cada reinicio."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = (web.DATA, web._DURACIONES, web._duraciones,
                         web._duraciones_sucio)
        web.DATA = self.root
        web._DURACIONES = self.root / ".duraciones.json"
        web._duraciones = None
        web._duraciones_sucio = False
        web._duracion_video_cache.cache_clear()
        self.clip = self.root / "clip.mp4"
        self.clip.write_bytes(b"video")

    def tearDown(self):
        (web.DATA, web._DURACIONES, web._duraciones,
         web._duraciones_sucio) = self.previous
        web._duracion_video_cache.cache_clear()
        self.temp.cleanup()

    def _medir(self, segundos=31):
        """Cuenta cuántas veces se llama de verdad a ffprobe."""
        return patch.object(web, "_duracion_video_cache", return_value=segundos)

    def test_el_indice_sobrevive_al_reinicio(self):
        with self._medir() as ffprobe:
            self.assertEqual(web._duracion_video(self.clip), 31)
            web.guardar_duraciones()
            self.assertEqual(ffprobe.call_count, 1)

        self.assertTrue(web._DURACIONES.is_file(), "no se escribió el índice")

        # Reinicio del contenedor: se pierde la memoria, queda el disco.
        web._duraciones = None
        web._duracion_video_cache.cache_clear()
        with self._medir() as ffprobe:
            self.assertEqual(web._duracion_video(self.clip), 31)
            self.assertEqual(ffprobe.call_count, 0,
                             "tras reiniciar no debe volver a medir")

    def test_un_video_modificado_se_vuelve_a_medir(self):
        with self._medir(31):
            web._duracion_video(self.clip)
            web.guardar_duraciones()
        web._duraciones = None
        web._duracion_video_cache.cache_clear()
        self.clip.write_bytes(b"video mas largo")  # cambia tamaño y mtime
        with self._medir(64) as ffprobe:
            self.assertEqual(web._duracion_video(self.clip), 64)
            self.assertEqual(ffprobe.call_count, 1)

    def test_el_indice_poda_lo_que_ya_no_existe(self):
        with self._medir():
            web._duracion_video(self.clip)
        web.guardar_duraciones()
        self.clip.unlink()
        web._duraciones_sucio = True
        web.guardar_duraciones()
        self.assertEqual(json.loads(web._DURACIONES.read_text(encoding="utf-8")), {},
                         "el índice crecería para siempre")

    def test_un_indice_corrupto_no_tumba_la_galeria(self):
        web._DURACIONES.write_text("{esto no es json", encoding="utf-8")
        web._duraciones = None
        with self._medir(31):
            self.assertEqual(web._duracion_video(self.clip), 31)


class CacheListadoRawTests(unittest.TestCase):
    """El listado no puede reparsear los manifiestos enteros cada 15 s."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = (raw.RAW, clipper.OUT)
        raw.RAW = self.root / "out" / "RAW"
        clipper.OUT = self.root / "out"
        raw.RAW.mkdir(parents=True, exist_ok=True)
        raw._LISTA_CACHE.clear()
        (raw.RAW / "uno-01.mp4").write_bytes(b"raw")
        raw._atomic_write(raw.RAW / "uno-01.json", {
            "id": "uno-01", "status": "pendiente", "canal": "canal",
            "duracion": 30.0,
            # Lo caro de verdad: transcripcion y palabras con sus tiempos.
            "segments": [{"start": i, "end": i + 1, "text": "palabra"} for i in range(400)],
            "words": [{"start": i, "end": i + 1, "word": "x"} for i in range(2000)],
        })

    def tearDown(self):
        (raw.RAW, clipper.OUT) = self.previous
        raw._LISTA_CACHE.clear()
        self.temp.cleanup()

    def test_no_reparsea_un_manifiesto_que_no_cambio(self):
        raw.listar_api()
        with patch.object(raw, "_read", wraps=raw._read) as leer:
            raw.listar_api()
            raw.listar_api()
        self.assertEqual(leer.call_count, 0, "estaba releyendo lo mismo cada vez")

    def test_un_manifiesto_modificado_si_se_relee(self):
        raw.listar_api()
        raw._update("uno-01", status="completado")
        item = raw.listar_api()[0]
        self.assertEqual(item["status"], "completado")

    def test_detecta_el_analisis_nuevo_sin_tocar_el_manifiesto(self):
        self.assertFalse(raw.listar_api()[0]["gemini_ready"])
        destino = raw.RAW / "_gemini" / "uno-01.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text("{}", encoding="utf-8")
        self.assertTrue(raw.listar_api()[0]["gemini_ready"],
                        "el JSON de Gemini aparece sin modificar el manifiesto")

    def test_el_listado_no_expone_la_transcripcion(self):
        item = raw.listar_api()[0]
        for campo in ("segments", "words", "chat", "_queue", "_name"):
            self.assertNotIn(campo, item)


class TranscripcionPorLotesTests(unittest.TestCase):
    """El batching existe pero viene apagado: rompe timestamps en clips cortos."""

    def setUp(self):
        self.model = unittest.mock.Mock()
        self.model.transcribe.return_value = ([], None)
        self.opciones = {"language": "es", "word_timestamps": True}

    def _llamar(self, batch_size):
        return clipper._transcribir(self.model, Path("audio.wav"),
                                    {"batch_size": batch_size}, self.opciones)

    def test_apagado_usa_la_via_normal(self):
        for valor in (0, 1, None, ""):
            with self.subTest(batch_size=valor):
                self.model.reset_mock()
                self._llamar(valor)
                self.model.transcribe.assert_called_once()

    def test_viene_apagado_en_la_configuracion(self):
        """Activarlo sin medir desincroniza los subtitulos quemados."""
        self.assertLessEqual(
            int(clipper.CONFIG["whisper"].get("batch_size", 0) or 0), 1)

    def test_encendido_usa_la_tuberia_por_lotes(self):
        tuberia = unittest.mock.Mock()
        tuberia.transcribe.return_value = ([], None)
        fabrica = unittest.mock.Mock(return_value=tuberia)
        with patch.dict("sys.modules", {"faster_whisper": unittest.mock.Mock(
                BatchedInferencePipeline=fabrica)}):
            self._llamar(16)
        fabrica.assert_called_once_with(model=self.model)
        self.assertEqual(tuberia.transcribe.call_args.kwargs["batch_size"], 16)
        self.model.transcribe.assert_not_called()

    def test_si_la_version_no_lo_trae_no_revienta(self):
        modulo = unittest.mock.Mock()
        del modulo.BatchedInferencePipeline
        with patch.dict("sys.modules", {"faster_whisper": modulo}):
            self._llamar(16)
        self.model.transcribe.assert_called_once()

    def test_las_opciones_llegan_intactas_en_los_dos_caminos(self):
        self._llamar(0)
        normales = self.model.transcribe.call_args.kwargs
        self.assertTrue(normales["word_timestamps"], "sin esto no hay subtitulos")
        self.assertEqual(normales["language"], "es")


class MotivoHookRechazadoTests(unittest.TestCase):
    """Un rechazo de titulo debe decir que regla salto, sin filtrar el texto."""

    def _motivo(self, texto):
        hook, motivo = clipper._sanear_hook_detallado(texto)
        return hook, motivo

    def test_un_titulo_valido_pasa_sin_motivo(self):
        hook, motivo = self._motivo("Nunca había visto algo así")
        self.assertTrue(hook)
        self.assertEqual(motivo, "")

    def test_dice_que_son_las_llaves(self):
        _, motivo = self._motivo("Esto {rompe} los subtítulos")
        self.assertIn("llaves", motivo)

    def test_dice_que_el_emoji_va_en_medio(self):
        _, motivo = self._motivo("Mira 😱 lo que pasó aquí")
        self.assertIn("despues del primer emoji", motivo)

    def test_los_emojis_al_final_si_valen(self):
        hook, motivo = self._motivo("Se acabó la paciencia 😱")
        self.assertTrue(hook, motivo)
        self.assertEqual(motivo, "")

    def test_dice_que_sobran_emojis(self):
        _, motivo = self._motivo("Se acabó la paciencia 😱😱😱")
        self.assertIn("emojis", motivo)
        self.assertIn("2", motivo)

    def test_dice_que_ocupa_demasiadas_lineas(self):
        _, motivo = self._motivo("Palabra " * 9)
        self.assertTrue(motivo, "un titulo larguisimo debe explicar por que cae")

    def test_el_limite_cuadra_con_lo_que_cabe_en_pantalla(self):
        """66 caracteres no entraban en 2 lineas de 22: era rechazo seguro."""
        self.assertLessEqual(clipper.LLM_TITLE_MAX_CHARS, 2 * 22)

    def test_un_titulo_largo_se_rechaza_entero_sin_mutilarlo(self):
        largo = "Perdi tres mil euros en una sola noche y no me lo creo"
        hook, motivo = self._motivo(largo)
        self.assertEqual(hook, "", "un gancho cortado a medias no puede quemarse")
        self.assertIn("caracteres", motivo)
        self.assertIn(str(len(largo)), motivo, "debe decir cuanto se paso")

    def test_a_luna_se_le_dice_el_limite(self):
        """Rechazar por una regla que el modelo no conoce es un bucle tonto."""
        prompt = clipper._llm_prompt("canal", "pico", [], [], 30.0, 5.0)
        self.assertIn(str(clipper.LLM_TITLE_MAX_CHARS), prompt)

    def test_un_titulo_que_cabe_pasa(self):
        hook, motivo = self._motivo("Nunca vi nada igual en directo")
        self.assertTrue(hook, motivo)

    def test_dice_que_no_llego_texto(self):
        _, motivo = self._motivo(None)
        self.assertIn("no devolvio texto", motivo)

    def test_el_motivo_no_filtra_el_titulo(self):
        """Los logs no guardan respuestas del modelo, y esto va al log."""
        secreto = "Frase confidencial del modelo"
        _, motivo = self._motivo(secreto + " {roto}")
        self.assertNotIn("confidencial", motivo)
        self.assertNotIn(secreto, motivo)

    def test_la_funcion_simple_sigue_funcionando(self):
        self.assertEqual(clipper._sanear_hook("Esto {rompe} todo"), "")
        self.assertTrue(clipper._sanear_hook("Nunca había visto algo así"))


class RenderCalidadTests(unittest.TestCase):
    def test_el_crf_no_desperdicia_bitrate(self):
        """Las plataformas recodifican: un CRF bajo solo cuesta tiempo y megas."""
        self.assertGreaterEqual(int(clipper.CONFIG["render"]["crf"]), 23)


class ConfirmacionCreditosTests(unittest.TestCase):
    """agy borra useG1Credits de su settings.json; la confirmacion debe durar."""

    def _sin_fichero(self):
        return patch.object(antigravity, "_credits_del_fichero", return_value=None)

    def test_el_entorno_confirma_cuando_el_fichero_no_dice_nada(self):
        with self._sin_fichero(), patch.dict(os.environ, {"CLIPPER_G1_CREDITS": "0"}):
            self.assertIs(antigravity._credits_habilitados(), False)

    def test_sin_fichero_ni_entorno_sigue_siendo_desconocido(self):
        with self._sin_fichero(), patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(antigravity._credits_habilitados())

    def test_el_entorno_no_puede_tapar_un_si_del_fichero(self):
        """Si agy dice que gastara creditos, eso manda sobre cualquier variable."""
        with patch.object(antigravity, "_credits_del_fichero", return_value=True), \
             patch.dict(os.environ, {"CLIPPER_G1_CREDITS": "0"}):
            self.assertIs(antigravity._credits_habilitados(), True)

    def test_admite_las_formas_habituales(self):
        for texto, esperado in [("0", False), ("false", False), ("no", False),
                                ("1", True), ("true", True), ("si", True)]:
            with self.subTest(valor=texto):
                with self._sin_fichero(), patch.dict(os.environ,
                                                     {"CLIPPER_G1_CREDITS": texto}):
                    self.assertIs(antigravity._credits_habilitados(), esperado)

    def test_un_valor_raro_no_confirma_nada(self):
        with self._sin_fichero(), patch.dict(os.environ, {"CLIPPER_G1_CREDITS": "quizas"}):
            self.assertIsNone(antigravity._credits_habilitados())


class DiagnosticoAgyTests(unittest.TestCase):
    """agy sale con codigo 0 y sin salida: el stderr es la unica pista."""

    def test_recoge_el_mensaje_de_error(self):
        self.assertIn("token expired",
                      antigravity._diagnostico("Error: token expired"))

    def test_admite_lo_que__texto_rechazaria(self):
        """Un stderr con backticks o comandos no puede tumbar el diagnostico."""
        salida = antigravity._diagnostico("run `sudo agy login` to continue")
        self.assertIn("agy login", salida)

    def test_no_escupe_la_transcripcion_en_el_log(self):
        stderr = ("model unavailable\n<UNTRUSTED_CLIPPER_CONTEXT>\n"
                  '{"transcript":"lo que dijo el streamer"}')
        salida = antigravity._diagnostico(stderr)
        self.assertIn("model unavailable", salida)
        self.assertNotIn("streamer", salida)
        self.assertNotIn("UNTRUSTED", salida)

    def test_tacha_secretos(self):
        with patch.dict(os.environ, {"MI_TOKEN": "token-secreto-largo"}):
            salida = antigravity._diagnostico("fallo con token-secreto-largo")
        self.assertNotIn("token-secreto-largo", salida)
        self.assertIn("[REDACTED]", salida)

    def test_sin_stderr_lo_dice(self):
        self.assertEqual(antigravity._diagnostico(""), "(sin mensaje)")
        self.assertEqual(antigravity._diagnostico(None), "(sin mensaje)")

    def test_recorta(self):
        self.assertLessEqual(len(antigravity._diagnostico("x" * 2000)), 700)


class AnalisisNoConfiguradoTests(unittest.TestCase):
    """Un fallo de configuracion no puede ir quemando candidatos."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = (raw.RAW, clipper.OUT)
        raw.RAW = self.root / "out" / "RAW"
        clipper.OUT = self.root / "out"
        raw.RAW.mkdir(parents=True, exist_ok=True)
        (raw.RAW / "uno-01.mp4").write_bytes(b"raw")
        raw._atomic_write(raw.RAW / "uno-01.json", {
            "id": "uno-01", "status": "pendiente", "duracion": 30.0,
            "created_at": "2026-08-03T05:00:00+00:00", "next_retry_at": "",
        })
        raw._AVISO_CONFIG.update(motivo="", ts=0.0)

    def tearDown(self):
        (raw.RAW, clipper.OUT) = self.previous
        self.temp.cleanup()

    def test_no_encola_nada_si_falta_configuracion(self):
        with patch.object(raw.antigravity, "preparado",
                          return_value=(False, "credits_unknown")), \
             patch.object(raw, "enqueue") as enqueue:
            self.assertEqual(raw.procesar_pendientes(), 0)
        enqueue.assert_not_called()

    def test_el_candidato_se_queda_pendiente_intacto(self):
        with patch.object(raw.antigravity, "preparado",
                          return_value=(False, "credits_unknown")), \
             patch.object(raw, "enqueue"):
            raw.procesar_pendientes()
        self.assertEqual(raw._read("uno-01")["status"], "pendiente",
                         "no es culpa del clip; no puede quedar marcado como fallido")

    def test_el_aviso_no_se_repite_cada_15_segundos(self):
        with patch.object(raw.antigravity, "preparado",
                          return_value=(False, "credits_unknown")), \
             patch.object(raw.LOG, "warning") as aviso:
            for _ in range(20):
                raw.procesar_pendientes()
        self.assertEqual(aviso.call_count, 1, "spam de log en cada ciclo")

    def test_un_motivo_distinto_si_avisa(self):
        with patch.object(raw.LOG, "warning") as aviso:
            with patch.object(raw.antigravity, "preparado",
                              return_value=(False, "credits_unknown")):
                raw.procesar_pendientes()
            with patch.object(raw.antigravity, "preparado",
                              return_value=(False, "missing_binary")):
                raw.procesar_pendientes()
        self.assertEqual(aviso.call_count, 2)

    def test_configurado_del_todo_si_encola(self):
        with patch.object(raw.antigravity, "preparado", return_value=(True, "")), \
             patch.object(raw, "enqueue") as enqueue:
            self.assertEqual(raw.procesar_pendientes(), 1)
        enqueue.assert_called_once()


class AdjuntoNtfyTests(unittest.TestCase):
    def test_el_adjunto_viene_desactivado(self):
        """Con ntfy anonimo el adjunto nunca llegaba a enviarse."""
        self.assertFalse(
            clipper.CONFIG.get("notificaciones", {}).get("adjuntar_video", False))


if __name__ == "__main__":
    unittest.main()
