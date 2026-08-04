# Cloudflare Whisper y subtítulos sincronizados

## Objetivo

Usar `@cf/openai/whisper` como transcriptor principal y corregir el desfase de
subtítulos observado en los clips.

## Diseño aprobado

- El RAW se recodifica desde el instante exacto. Se elimina el corte por
  keyframe producido por `-ss` junto con `-c copy`.
- Si existen `CLOUDFLARE_ACCOUNT_ID` y `CLOUDFLARE_AI_TOKEN`, la transcripción
  se envía a Workers AI y se conservan sus timestamps por palabra.
- Ante cuota agotada, timeout, red o respuesta inválida, el proceso continúa
  con `faster-whisper` local.
- Los logs indican proveedor y latencia, pero nunca credenciales.

## Verificación

- Prueba del comando FFmpeg de recorte exacto.
- Prueba del parser de la respuesta de Cloudflare.
- Transcripción y render de un clip real en el servidor.
