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
from unittest.mock import MagicMock, patch

import clipper
import calidad
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


def _respuesta_editorial(titulo="Título sólido", descripcion="Una reacción clara.",
                         hashtags=None, decision="publicar", score=80,
                         confidence=0.75):
    hashtags = hashtags or ["#canal", "#clips", "#directo", "#reaccion"]
    return {
        "decision": decision,
        "score": score,
        "confidence": confidence,
        "reason": "El momento tiene una reacción clara.",
        "screen_title": titulo,
        "social_description": descripcion,
        "hashtags": hashtags,
        "visual_summary": "Una persona reacciona.",
        "visual_timeline": [], "people": [], "visible_text": [],
        "visual_warnings": [],
    }


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

    def test_cloudflare_conserva_timestamps_por_palabra(self):
        payload = json.dumps({"success": True, "result": {
            "words": [{"word": "hola", "start": 0.2, "end": 0.6}],
            "vtt": "WEBVTT\n\n00.200 --> 00.600\nhola\n",
        }}).encode()
        respuesta = MagicMock()
        respuesta.__enter__.return_value.read.return_value = payload

        def comprimir(comando):
            Path(comando[-1]).write_bytes(b"mp3")

        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT": "cuenta",
                                      "CLOUDFLARE_AI_TOKEN": "token"}), \
             patch.object(clipper, "run", side_effect=comprimir), \
             patch.object(clipper, "urlopen", return_value=respuesta) as abrir:
            audio = Path(tmp) / "audio.wav"
            audio.write_bytes(b"wav")
            segmentos, words = clipper._transcribir_cloudflare(audio)
        self.assertEqual(segmentos, [{"start": 0.2, "end": 0.6, "text": "hola"}])
        self.assertEqual(words, [{"start": 0.2, "end": 0.6, "word": "hola"}])
        peticion = abrir.call_args.args[0]
        self.assertTrue(peticion.full_url.endswith("?language=es"))
        self.assertEqual(peticion.headers["Content-type"], "audio/mpeg")
        self.assertEqual(peticion.data, b"mp3")

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
            mp4.with_suffix(".txt").write_text(
                "<script>alert(1)</script>\n\n#uno #dos #tres #cuatro\n",
                encoding="utf-8",
            )
            handler = object.__new__(web.Handler)
            web._duracion_video_cache.cache_clear()
            with patch.object(clipper, "run", return_value=SimpleNamespace(stdout="33.7")) as ejecutar:
                clips = handler._obtener_clips_dir(carpeta, es_revisar=True)
                handler._obtener_clips_dir(carpeta, es_revisar=True)
            self.assertEqual(clips[0]["canal"], "elcalvolol")
            self.assertEqual(clips[0]["duracion"], 34)
            self.assertEqual(clips[0]["description"], "<script>alert(1)</script>")
            self.assertEqual(clips[0]["txt_size"], mp4.with_suffix(".txt").stat().st_size)
            self.assertEqual(ejecutar.call_count, 1)
        self.assertIn("function descriptionPanel", web.HTML_TEMPLATE)
        self.assertIn("textContent", web.HTML_TEMPLATE)

    def test_web_expone_timestamp_real_en_listos(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            mp4 = carpeta / "001_canal_2026-08-02.mp4"
            mp4.write_bytes(b"video")
            esperado = mp4.stat().st_mtime
            handler = object.__new__(web.Handler)
            web._duracion_video_cache.cache_clear()
            with patch.object(clipper, "run", return_value=SimpleNamespace(stdout="30")):
                clips = handler._obtener_clips_dir(carpeta, es_revisar=False)
        self.assertEqual(clips[0]["nombre"], mp4.name)
        self.assertEqual(clips[0]["timestamp"], esperado)

    def test_web_tolera_txt_legacy_sin_mostrarlo_como_descripcion(self):
        with tempfile.TemporaryDirectory() as tmp:
            ficha = Path(tmp) / "clip.txt"
            ficha.write_text(
                "TITULO / PRIMERA LINEA\nHook antiguo\n\nHASHTAGS\n#clips\n",
                encoding="utf-8",
            )
            self.assertEqual(web._leer_ficha_publicable(ficha), ("", []))

    def test_web_reintento_fuerza_render_tras_error(self):
        cargar = web.HTML_TEMPLATE.split("function cargarClips", 1)[1]
        cargar = cargar.split("function switchTab", 1)[0]
        self.assertIn('state.signature = "";', cargar)
        self.assertLess(
            cargar.index('state.signature = "";'),
            cargar.index('replaceChildren(errorState')
        )

    def test_web_muestra_evaluacion_llm_en_revisar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origen = root / "canal-20260801-193235-01.mp4"
            origen.write_bytes(b"video")
            anteriores = clipper.OUT, web.OUT
            try:
                clipper.OUT = root / "out"
                web.OUT = clipper.OUT
                destino = calidad.apartar(origen, ["Luna decidió descartar"], {
                    "canal": "canal",
                    "hook": "Título de prueba",
                    "llm": {
                        "model": "gpt-5.6-luna",
                        "decision": "descartar",
                        "score": 12,
                        "confidence": 0.91,
                        "reason": "No hay un momento claro.",
                        "social_description": "Sin momento claro.",
                        "hashtags": ["#canal", "#clips", "#directo", "#reaccion"],
                    },
                })
                handler = object.__new__(web.Handler)
                web._duracion_video_cache.cache_clear()
                with patch.object(clipper, "run", return_value=SimpleNamespace(stdout="30")):
                    clips = handler._obtener_clips_dir(destino.parent, es_revisar=True)
            finally:
                clipper.OUT, web.OUT = anteriores
            self.assertEqual(clips[0]["llm"]["decision"], "descartar")
            self.assertEqual(clips[0]["llm"]["score"], 12)
            self.assertIn("No hay un momento claro", clips[0]["llm"]["reason"])

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

    def test_llm_valido_reemplaza_gancho_sin_mover_tiempos(self):
        segmentos = [{"start": 10.5, "end": 13.0, "text": "Esto fue inesperado"}]
        copia = [dict(segmento) for segmento in segmentos]
        with patch.dict(os.environ, {
            "CLIPPER_LLM_ACTIVO": "1", "CLIPPER_CODEX_MODELO": "gpt-5.6-terra",
        }), patch.object(clipper, "_codex_exec",
                         return_value=_respuesta_editorial()) as llamada:
            gancho, meta = clipper.evaluar_editorial(
                "canal", "pico de reacción", segmentos, ["qué ha pasado"],
                30.0, 12.0, "Gancho heurístico",
            )

        self.assertEqual(gancho, "Título sólido")
        self.assertEqual(segmentos, copia)
        self.assertEqual(meta["decision"], "publicar")
        self.assertEqual(meta["input_tokens"], 0)
        self.assertEqual(meta["output_tokens"], 0)
        self.assertEqual(llamada.call_args.args[2], "gpt-5.6-terra")

    def test_contexto_llm_es_relativo_y_acotado(self):
        segmentos = [
            {"start": 100.0, "end": 136.0, "text": "entra y sale del clip"},
            {"start": 125.0, "end": 150.0, "text": "el pico queda al final"},
        ]
        relativos, pico = live.contexto_editorial(segmentos, 100.0, 134.0, 150.0)
        with patch.dict(os.environ, {"CLIPPER_LLM_ACTIVO": "1"}), \
             patch.object(clipper, "_codex_exec",
                          return_value=_respuesta_editorial()) as llamada:
            clipper.evaluar_editorial(
                "canal", "reacción", relativos, [], 34.0, pico, "Gancho heurístico")
        prompt = llamada.call_args.args[0]

        self.assertEqual(pico, 34.0)
        self.assertIn("POSICIÓN DEL PICO: 34.0s", prompt)
        self.assertNotIn("136.0s", prompt)
        self.assertNotIn("150.0s", prompt)
        self.assertEqual(segmentos[0]["start"], 100.0)

    def test_llm_no_necesita_api_key(self):
        with patch.dict(os.environ, {
            "CLIPPER_LLM_ACTIVO": "1", "OPENAI_API_KEY": "",
        }), patch.object(clipper, "_codex_exec",
                         return_value=_respuesta_editorial()) as llamada:
            gancho, meta = clipper.evaluar_editorial(
                "canal", "motivo", [], [], 30.0, 12.0, "Gancho heurístico")
        self.assertEqual(gancho, "Título sólido")
        self.assertEqual(meta["decision"], "publicar")
        llamada.assert_called_once()

    def test_llm_falla_sin_perder_candidato_ni_reintentar(self):
        casos = (
            (_respuesta_editorial(""), "screen_title"),
            (ValueError("respuesta JSON inválida"), "JSON inválida"),
            (RuntimeError("CODEX_TIMEOUT"), "CODEX_TIMEOUT"),
            (RuntimeError("CODEX_ERROR: login required"), "login required"),
        )
        for resultado, esperado in casos:
            with self.subTest(esperado=esperado):
                opciones = ({"side_effect": resultado} if isinstance(resultado, BaseException)
                            else {"return_value": resultado})
                with patch.dict(os.environ, {"CLIPPER_LLM_ACTIVO": "1"}), \
                     patch.object(clipper, "_codex_exec", **opciones) as llamada:
                    gancho, meta = clipper.evaluar_editorial(
                        "canal", "motivo", [], [], 30.0, 12.0, "Gancho heurístico")
                self.assertEqual(gancho, "Gancho heurístico")
                self.assertIn(esperado, meta["reason"])
                llamada.assert_called_once()

    def test_llm_rechaza_descripcion_y_hashtags_invalidos(self):
        for respuesta, esperado in (
            (_respuesta_editorial(descripcion=""), "social_description"),
            (_respuesta_editorial(hashtags=["#uno"]), "hashtags"),
        ):
            with self.subTest(esperado=esperado), \
                 patch.dict(os.environ, {"CLIPPER_LLM_ACTIVO": "1"}), \
                 patch.object(clipper, "_codex_exec", return_value=respuesta):
                hook, meta = clipper.evaluar_editorial(
                    "canal", "motivo", [], [], 30.0, 12.0, "Gancho heurístico")
                self.assertEqual(hook, "Gancho heurístico")
                self.assertIn(esperado, meta["reason"])

    def test_puerta_estricta_publica_en_umbral(self):
        clip = {
            "start": 0,
            "end": 30,
            "hook": "Nadie esperaba esta reacción brutal",
            "hook_auto": True,
            "social_description": "Una reacción clara del directo.",
            "hashtags": ["#canal", "#clips", "#directo", "#reaccion"],
            "llm": {
                "decision": "publicar",
                "score": 80,
                "confidence": 0.75,
                "reason": "Momento claro.",
                "social_description": "Una reacción clara del directo.",
                "hashtags": ["#canal", "#clips", "#directo", "#reaccion"],
            },
        }
        segmentos = [{"start": 0, "end": 30, "text": "Una frase con bastante diálogo para superar el control"}]
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"video")
            with patch.dict(calidad.CONFIG["calidad"], {
                "palabras_por_segundo_min": 0,
            }):
                with patch.object(calidad, "_analizar_calidad_av", return_value=(-10, 0)):
                    apto, motivos = calidad.evaluar(video, clip, segmentos, (26, 34))
        self.assertTrue(apto, motivos)

    def test_puerta_estricta_rechaza_score_y_confianza_bajos(self):
        base = {
            "start": 0,
            "end": 30,
            "hook": "Nadie esperaba esta reacción brutal",
            "social_description": "Una reacción clara.",
            "hashtags": ["#canal", "#clips", "#directo", "#reaccion"],
            "llm": {
                "decision": "publicar",
                "score": 79,
                "confidence": 0.75,
                "reason": "Momento claro.",
                "social_description": "Una reacción clara.",
                "hashtags": ["#canal", "#clips", "#directo", "#reaccion"],
            },
        }
        with patch.object(calidad, "_analizar_calidad_av", return_value=(-10, 0)):
            with patch.dict(calidad.CONFIG["calidad"], {"palabras_por_segundo_min": 0}):
                apto, motivos = calidad.evaluar(
                    Path("clip.mp4"), base, [{"start": 0, "text": "palabras"}], (26, 34)
                )
        self.assertFalse(apto)
        self.assertTrue(any("puntuación" in motivo for motivo in motivos))

        base["llm"]["score"] = 80
        base["llm"]["confidence"] = 0.74
        with patch.object(calidad, "_analizar_calidad_av", return_value=(-10, 0)):
            with patch.dict(calidad.CONFIG["calidad"], {"palabras_por_segundo_min": 0}):
                apto, motivos = calidad.evaluar(
                    Path("clip.mp4"), base, [{"start": 0, "text": "palabras"}], (26, 34)
                )
        self.assertFalse(apto)
        self.assertTrue(any("confianza" in motivo for motivo in motivos))

    def test_revision_descartado_y_editorial_invalida_van_a_revision(self):
        for decision in ("revisar", "descartar"):
            with self.subTest(decision=decision):
                clip = {
                    "start": 0,
                    "end": 30,
                    "hook": "Nadie esperaba esta reacción brutal",
                    "social_description": "Una reacción clara.",
                    "hashtags": ["#canal", "#clips", "#directo", "#reaccion"],
                    "llm": {
                        "decision": decision,
                        "score": 99,
                        "confidence": 1.0,
                        "reason": "Requiere revisión.",
                        "social_description": "Una reacción clara.",
                        "hashtags": ["#canal", "#clips", "#directo", "#reaccion"],
                    },
                }
                with patch.object(calidad, "_analizar_calidad_av", return_value=(-10, 0)):
                    with patch.dict(calidad.CONFIG["calidad"], {"palabras_por_segundo_min": 0}):
                        apto, motivos = calidad.evaluar(
                            Path("clip.mp4"), clip, [{"start": 0, "text": "palabras"}], (26, 34)
                        )
                self.assertFalse(apto)
                self.assertTrue(any(decision in motivo for motivo in motivos))

        clip = {
            "start": 0,
            "end": 30,
            "hook": "Nadie esperaba esta reacción brutal",
            "llm": {"decision": "revisar", "score": 0, "confidence": 0,
                    "reason": "timeout"},
        }
        with patch.object(calidad, "_analizar_calidad_av", return_value=(-10, 0)):
            with patch.dict(calidad.CONFIG["calidad"], {"palabras_por_segundo_min": 0}):
                apto, motivos = calidad.evaluar(
                    Path("clip.mp4"), clip, [{"start": 0, "text": "palabras"}], (26, 34)
                )
        self.assertFalse(apto)
        self.assertTrue(any("descripción" in motivo for motivo in motivos))

    def test_ficha_txt_solo_contiene_publicacion(self):
        ficha = clipper._ficha_texto("canal-20260801-193235", {
            "social_description": "Una descripción lista.",
            "hashtags": ["#uno", "#dos", "#tres", "#cuatro"],
            "start": 1,
            "end": 31,
        })
        self.assertEqual(ficha, "Una descripción lista.\n\n#uno #dos #tres #cuatro\n")
        self.assertNotIn("origen", ficha.lower())
        self.assertNotIn("duración", ficha.lower())

    def test_ass_hook_permanente_tiktok_y_emoji(self):
        with tempfile.TemporaryDirectory() as tmp:
            ass = Path(tmp) / "subs.ass"
            ass_sin_emoji = Path(tmp) / "subs-sin-emoji.ass"
            anterior = clipper.CONFIG["render"]
            try:
                clipper.CONFIG["render"] = dict(anterior, hook_y=346)
                clipper._build_ass([], {
                    "start": 0,
                    "end": 30,
                    "hook": "Nadie esperaba esto 🔥",
                }, ass)
                clipper._build_ass([], {
                    "start": 0,
                    "end": 30,
                    "hook": "Nadie esperaba esto",
                }, ass_sin_emoji)
            finally:
                clipper.CONFIG["render"] = anterior
            texto = ass.read_text(encoding="utf-8")
            texto_sin_emoji = ass_sin_emoji.read_text(encoding="utf-8")
        self.assertIn("Hook,TikTok Sans", texto)
        self.assertIn("&H00000000", texto)
        self.assertIn("&H00FFFFFF", texto)
        self.assertIn(r"\pos(540,346)", texto)
        self.assertIn("0:30.00,Hook", texto)
        self.assertNotIn("\\fad", texto)
        self.assertIn("🔥", texto)
        self.assertIn(r"{\fnNoto Emoji}", texto)
        self.assertNotIn(r"{\fnNoto Emoji}", texto_sin_emoji)


if __name__ == "__main__":
    unittest.main()
