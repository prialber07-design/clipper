# Análisis automático con Codex OAuth

## Objetivo

Eliminar `OPENAI_API_KEY` del análisis editorial y visual. La transcripción se
mantiene local con `faster-whisper`; Codex recibe esa transcripción y los
fotogramas mediante la sesión OAuth de ChatGPT guardada por Codex CLI.

## Flujo

1. Clipper genera el RAW y lo transcribe localmente.
2. El storyboard existente extrae un fotograma por segundo y los del pico.
3. `codex exec` recibe el prompt, las imágenes y el esquema JSON.
4. Clipper valida la respuesta con sus controles actuales y renderiza o deja el
   RAW para reintento.

Codex se ejecuta dentro del contenedor actual, sin cron ni segundo trabajador.
`CODEX_HOME=/app/clips/codex-home` conserva `auth.json` en el volumen. El agente
se ejecuta sin permisos de escritura, sin herramientas externas y con sesión
efímera. Los errores conservan el RAW y quedan en los logs existentes.

## Autenticación y operación

Tras desplegar se ejecuta una vez `codex login --device-auth` dentro del
contenedor. La sesión OAuth se renueva durante el uso; si deja de ser válida, la
cola se detiene de forma recuperable hasta repetir el login.

No se usa Whisper API: `faster-whisper` ya es local y gratuito. “Whisper por
OAuth” significa que su transcripción forma parte del análisis autenticado por
OAuth junto con las imágenes.

## Verificación

- Prueba unitaria de comando, imágenes y JSON estructurado.
- `codex login status` dentro del contenedor.
- Procesamiento de un RAW real y comprobación de `LUNA_VISUAL_FINISHED`.
