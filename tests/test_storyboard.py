import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import clipper
import storyboard


class _Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        return self.data


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

    def test_payload_multimodal_usa_timeout_visual_y_json_estricto(self):
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
            api = {"output_text": json.dumps(resultado),
                   "usage": {"input_tokens": 10, "output_tokens": 5}}
            with patch.dict("os.environ", {"CLIPPER_LLM_ACTIVO": "1", "OPENAI_API_KEY": "secreto-largo"}, clear=False), \
                 patch.object(clipper.urllib.request, "urlopen", return_value=_Response(api)) as abrir:
                _, meta = clipper.evaluar_editorial(
                    "canal", "pico", [], [], 10, 4, "", [(0.0, frame)], True)
            request = abrir.call_args.args[0]
            payload = json.loads(request.data)
            imagen = payload["input"][0]["content"][2]
            self.assertEqual(abrir.call_args.kwargs["timeout"], 120)
            self.assertEqual(imagen["detail"], "high")
            self.assertEqual(base64.b64decode(imagen["image_url"].split(",", 1)[1]), b"jpeg")
            self.assertEqual(meta["image_count"], 1)
            self.assertTrue(payload["text"]["format"]["strict"])


if __name__ == "__main__":
    unittest.main()
