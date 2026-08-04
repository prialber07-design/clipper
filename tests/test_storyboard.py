import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import clipper
import storyboard


class StoryboardTests(unittest.TestCase):
    def test_muestreo_conserva_principio_y_final(self):
        self.assertEqual(storyboard._muestrear(list(range(20)), 3), [0, 10, 19])

    def test_extrae_en_orden_y_limpia_temporales(self):
        with tempfile.TemporaryDirectory() as carpeta:
            video = Path(carpeta) / "clip.mp4"
            video.write_bytes(b"video")

            def ejecutar(args):
                salida = Path(args[-1])
                if "%04d" in salida.name:
                    for nombre in ("base-0001.jpg", "base-0002.jpg"):
                        (salida.parent / nombre).write_bytes(b"jpg")
                else:
                    salida.write_bytes(b"jpg")

            with patch.object(storyboard.clipper, "run", side_effect=ejecutar):
                with storyboard.extraer(video, 10, 4.5) as frames:
                    paths = [path for _, path in frames]
                    self.assertEqual([t for t, _ in frames], sorted(t for t, _ in frames))
                    self.assertTrue(all(path.exists() for path in paths))
                self.assertTrue(all(not path.exists() for path in paths))

    def test_codex_exec_usa_oauth_imagenes_y_json_estricto(self):
        with tempfile.TemporaryDirectory() as carpeta:
            frame = Path(carpeta) / "f.jpg"
            frame.write_bytes(b"jpeg")
            resultado = {
                "decision": "publicar", "score": 90, "confidence": 0.9,
                "reason": "claro", "screen_title": "Momento increíble",
                "social_description": "Una escena lista para compartir.",
                "hashtags": ["#uno", "#dos", "#tres", "#cuatro"],
                "visual_summary": "Una persona habla.", "visual_timeline": [],
                "people": [], "visible_text": [], "visual_warnings": [],
            }
            def ejecutar(cmd, **kwargs):
                Path(cmd[cmd.index("-o") + 1]).write_text(
                    json.dumps(resultado), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch.dict("os.environ", {"CLIPPER_LLM_ACTIVO": "1"}, clear=False), \
                 patch.object(clipper.subprocess, "run", side_effect=ejecutar) as ejecutar_mock:
                _, meta = clipper.evaluar_editorial(
                    "canal", "pico", [], [], 10, 4, "", [(0.0, frame)], True)
            cmd = ejecutar_mock.call_args.args[0]
            self.assertEqual(ejecutar_mock.call_args.kwargs["timeout"], 120)
            self.assertEqual(cmd[cmd.index("--image") + 1], str(frame))
            self.assertIn("--output-schema", cmd)
            self.assertIn("--ephemeral", cmd)
            self.assertNotIn("OPENAI_API_KEY", " ".join(cmd))
            self.assertNotIn("OPENAI_API_KEY", ejecutar_mock.call_args.kwargs["env"])
            self.assertNotIn("CODEX_API_KEY", ejecutar_mock.call_args.kwargs["env"])
            self.assertEqual(meta["image_count"], 1)


if __name__ == "__main__":
    unittest.main()
