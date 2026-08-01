import json
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
        self.assertEqual(clipper.canal_desde_nombre("001_elcalvolol_2026-08-01.mp4"), "elcalvolol")

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
            with patch.object(clipper, "run", return_value=SimpleNamespace(stdout="33.7")):
                clips = handler._obtener_clips_dir(carpeta, es_revisar=True)
            self.assertEqual(clips[0]["canal"], "elcalvolol")
            self.assertEqual(clips[0]["duracion"], 34)

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
