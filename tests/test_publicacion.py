import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, unquote, urlparse

import publicacion


class PublicacionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.out = self.data / "out"
        self.anteriores = (publicacion.DATA, publicacion.OUT, publicacion.STATE)
        publicacion.DATA = self.data
        publicacion.OUT = self.out
        publicacion.STATE = self.data / "publicaciones.json"

    def tearDown(self):
        (publicacion.DATA, publicacion.OUT, publicacion.STATE) = self.anteriores
        self.tmp.cleanup()

    def _pareja(self, carpeta="LISTOS"):
        raiz = self.out / carpeta
        raiz.mkdir(parents=True)
        salida = []
        for variante in ("amarillo", "azul"):
            mp4 = raiz / f"clip-01-{variante}.mp4"
            mp4.write_bytes(b"video")
            mp4.with_suffix(".txt").write_text(
                "Una descripción.\n\n#clip #canal\n", encoding="utf-8")
            salida.append(mp4)
        return salida

    def test_listos_encola_solo_la_variante_azul(self):
        amarillo, azul = self._pareja()
        items = [
            (amarillo, {"variante": "amarillo", "hook": "Gancho"}),
            (azul, {"variante": "azul", "hook": "Gancho"}),
        ]
        publicacion.encolar_listos([amarillo, azul], items)
        datos = json.loads(publicacion.STATE.read_text(encoding="utf-8"))
        self.assertEqual(list(datos["items"]), ["out/LISTOS/clip-01-azul.mp4"])
        item = next(iter(datos["items"].values()))
        self.assertEqual(set(item["platforms"]), {"youtube", "instagram"})
        self.assertEqual(item["title"], "Gancho")

    def test_publicado_no_se_envia_dos_veces(self):
        amarillo, azul = self._pareja()
        publicacion.encolar_listos(
            [amarillo, azul],
            [(amarillo, {"variante": "amarillo"}),
             (azul, {"variante": "azul", "hook": "Gancho"})],
        )
        entorno = {
            "CLIPPER_YOUTUBE_CLIENT_ID": "id",
            "CLIPPER_YOUTUBE_CLIENT_SECRET": "secret",
            "CLIPPER_YOUTUBE_REFRESH_TOKEN": "refresh",
            "CLIPPER_INSTAGRAM_ACCOUNT_ID": "",
            "CLIPPER_INSTAGRAM_ACCESS_TOKEN": "",
        }
        with patch.dict(os.environ, entorno, clear=False), \
             patch.object(publicacion, "_youtube_publicar", return_value="yt-123") as subir:
            self.assertTrue(publicacion.procesar_una())
            self.assertFalse(publicacion.procesar_una())
        subir.assert_called_once()
        self.assertEqual(publicacion.estado(azul)["youtube"]["remote_id"], "yt-123")

    def test_revision_completa_y_descarta_la_pareja(self):
        amarillo, azul = self._pareja("REVISAR")
        azul.with_suffix(".motivos.txt").write_text(
            "gancho: El mejor momento", encoding="utf-8")
        publicacion.encolar_revision(azul.name, "youtube")
        publicacion.encolar_revision(azul.name, "instagram")
        datos = json.loads(publicacion.STATE.read_text(encoding="utf-8"))
        item = next(iter(datos["items"].values()))
        for plataforma in publicacion.AUTOMATICAS:
            item["platforms"][plataforma]["status"] = "published"
        publicacion.STATE.write_text(json.dumps(datos), encoding="utf-8")
        self.assertTrue(publicacion.revision_completada(azul))
        borrados = publicacion.descartar_revision(azul.name)
        self.assertIn(azul.name, borrados)
        self.assertFalse(azul.exists())
        self.assertFalse(amarillo.exists())

    def test_amigo_revisa_amarillo_y_descartarlo_conserva_azul(self):
        amarillo, azul = self._pareja("LISTOS")
        publicacion.encolar_manual(
            amarillo.name, "youtube", "LISTOS", cuenta="amigo")

        datos = json.loads(publicacion.STATE.read_text(encoding="utf-8"))
        self.assertEqual(list(datos["items"]), ["out/LISTOS/clip-01-amarillo.mp4"])
        item = next(iter(datos["items"].values()))
        self.assertEqual(item["account"], "amigo")
        self.assertEqual(set(item["platforms"]), {"youtube"})

        borrados = publicacion.descartar_revision(
            amarillo.name, cuenta="amigo", origen="LISTOS")
        self.assertIn(amarillo.name, borrados)
        self.assertFalse(amarillo.exists())
        self.assertTrue(azul.exists())

    def test_amigo_usa_sus_credenciales_de_youtube(self):
        entorno = {
            "CLIPPER_YOUTUBE_CLIENT_ID": "id-yo",
            "CLIPPER_YOUTUBE_CLIENT_SECRET": "secret-yo",
            "CLIPPER_YOUTUBE_REFRESH_TOKEN": "refresh-yo",
            "CLIPPER_YOUTUBE_AMIGO_CLIENT_ID": "id-amigo",
            "CLIPPER_YOUTUBE_AMIGO_CLIENT_SECRET": "secret-amigo",
            "CLIPPER_YOUTUBE_AMIGO_REFRESH_TOKEN": "refresh-amigo",
        }
        with patch.dict(os.environ, entorno, clear=False), \
             patch.object(publicacion, "_json_request",
                          return_value={"access_token": "token"}) as request:
            self.assertEqual(publicacion._youtube_token("amigo"), "token")
        form = request.call_args.kwargs["form"]
        self.assertEqual(form["client_id"], "id-amigo")
        self.assertEqual(form["client_secret"], "secret-amigo")
        self.assertEqual(form["refresh_token"], "refresh-amigo")

    def test_url_temporal_solo_da_acceso_al_mp4_azul(self):
        _, azul = self._pareja()
        with patch.dict(os.environ, {
            "CLIPPER_WEB_CLAVE": "clave-larga",
            "CLIPPER_URL_PUBLICA": "https://clips.example",
        }):
            firmada = publicacion.url_temporal(azul)
            parsed = urlparse(firmada)
            query = parse_qs(parsed.query)
            relativa = unquote(parsed.path.removeprefix("/social-media/"))
            self.assertEqual(publicacion.validar_url_temporal(
                relativa, query["expires"][0], query["signature"][0]), azul)
            self.assertIsNone(publicacion.validar_url_temporal(
                relativa, query["expires"][0], "0" * 64))
        with patch.dict(os.environ, {"CLIPPER_WEB_CLAVE": ""}):
            self.assertIsNone(publicacion.validar_url_temporal(
                relativa, query["expires"][0], query["signature"][0]))

    def test_tiktok_solo_se_encola_al_pulsar_y_no_se_duplica(self):
        _, azul = self._pareja()
        publicacion.encolar_listos(
            [azul], [(azul, {"variante": "azul", "hook": "Gancho"})])
        datos = json.loads(publicacion.STATE.read_text(encoding="utf-8"))
        item = next(iter(datos["items"].values()))
        self.assertNotIn("tiktok", item["platforms"])

        publicacion.encolar_manual(azul.name, "tiktok", "LISTOS")
        entorno = {
            "CLIPPER_TIKTOK_CLIENT_KEY": "key",
            "CLIPPER_TIKTOK_CLIENT_SECRET": "secret",
            "CLIPPER_TIKTOK_REFRESH_TOKEN": "refresh",
        }
        with patch.dict(os.environ, entorno, clear=False), \
             patch.object(publicacion, "_tiktok_publicar",
                          return_value="v_inbox_file~123") as subir:
            self.assertTrue(publicacion.procesar_una())
            self.assertFalse(publicacion.procesar_una())
        subir.assert_called_once()
        estado = publicacion.estado(azul)["tiktok"]
        self.assertEqual(estado["status"], "submitted")
        self.assertEqual(estado["remote_id"], "v_inbox_file~123")

    def test_tiktok_renueva_y_guarda_el_refresh_token_rotado(self):
        entorno = {
            "CLIPPER_TIKTOK_CLIENT_KEY": "key",
            "CLIPPER_TIKTOK_CLIENT_SECRET": "secret",
            "CLIPPER_TIKTOK_REFRESH_TOKEN": "refresh-inicial",
        }
        respuesta = {"access_token": "access", "refresh_token": "refresh-nuevo"}
        with patch.dict(os.environ, entorno, clear=False), \
             patch.object(publicacion, "_json_request", return_value=respuesta):
            self.assertEqual(publicacion._tiktok_token(), "access")
            self.assertEqual(publicacion._tiktok_refresh_token(), "refresh-nuevo")
        guardado = json.loads(
            (self.data / ".tiktok-oauth.json").read_text(encoding="utf-8"))
        self.assertNotIn("access_token", guardado)

    def test_tiktok_sube_el_mp4_con_content_range(self):
        _, azul = self._pareja()
        respuesta = MagicMock(status=201)
        respuesta.read.return_value = b""
        conexion = MagicMock()
        conexion.getresponse.return_value = respuesta
        with patch.object(publicacion.http.client, "HTTPSConnection",
                          return_value=conexion):
            publicacion._tiktok_subir(
                azul,
                "https://open-upload.tiktokapis.com/video/?upload_id=1",
                azul.stat().st_size,
                1,
            )
        _, ruta, kwargs = conexion.request.mock_calls[0]
        self.assertEqual(ruta[0], "PUT")
        self.assertEqual(kwargs["body"], b"video")
        self.assertEqual(kwargs["headers"]["Content-Range"], "bytes 0-4/5")


if __name__ == "__main__":
    unittest.main()
