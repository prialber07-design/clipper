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


if __name__ == "__main__":
    unittest.main()
