# Análisis visual de RAW mediante Luna

## Objetivo

Usar una única llamada multimodal a GPT-5.6 Luna. Cada candidato RAW se
analiza visual y editorialmente antes de
renderizarse.

## Flujo

1. Clipper conserva el MP4 RAW y su transcripción.
2. FFmpeg extrae un JPEG 768x432 por segundo y cinco capturas adicionales
   alrededor del pico conocido.
3. Clipper envía en una sola petición a Luna los fotogramas ordenados con su
   timestamp, la transcripción, el chat, el canal y el motivo del pico.
4. Luna devuelve un JSON estricto con análisis visual, decisión editorial,
   hook, descripción y hashtags.
5. Clipper valida la respuesta, renderiza y mueve el resultado a LISTOS o
   REVISAR. Si falla la API, conserva el RAW y aplica el backoff existente.
6. Los JPEG son temporales y se eliminan siempre.

## Decisiones

- Una única llamada evita duplicar coste, latencia y estados intermedios.
- Se reutiliza la integración HTTP actual de Responses API; no se añade el SDK.
- Las imágenes se redimensionan antes de enviarlas y usan `detail: high` para
  contener coste sin perder demasiado detalle facial o texto visible.
- No se activa búsqueda web automática. Una identidad solo se afirma cuando la
  sostienen el canal, la transcripción o texto visible; de lo contrario se
  describe sin nombre.
- No quedan binarios, OAuth, variables, estados ni carpetas del sistema
  anterior.

## Verificación

- Prueba de extracción y limpieza de fotogramas.
- Prueba del payload multimodal ordenado y del esquema visual.
- Pruebas de éxito, error, reintento y recuperación de la cola RAW.
- Suite completa, compilación y `git diff --check`.
