# Plan de implementación: concurrencia de Cloudflare Whisper

1. Añadir en `bloqueo.py` un contexto de tres cerrojos de archivo que adquiera
   una plaza disponible y la libere siempre al salir.
2. Usar ese límite únicamente alrededor de `_transcribir_cloudflare`; mover el
   cerrojo de CPU al fallback local para que Cloudflare pueda solaparse.
3. Añadir logs de espera y adquisición de plaza sin registrar audio ni secretos.
4. Probar el máximo de tres plazas, la liberación tras excepciones y que el
   fallback local conserva la exclusión de CPU.
5. Ejecutar la suite completa y revisar el diff.
