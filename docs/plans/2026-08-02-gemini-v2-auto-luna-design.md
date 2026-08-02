# Consumo automático de análisis Gemini v2

## Objetivo

Procesar un candidato RAW únicamente cuando exista un análisis visual Gemini v2
válido con el mismo identificador. Después, Clipper enviará automáticamente ese
análisis junto con Whisper y chat a Luna, aplicará la puerta editorial estricta
y moverá el render a `LISTOS` o `REVISAR`.

No existirá una ruta manual que permita ejecutar Gemini desde Clipper ni enviar
un RAW directamente a Luna sin análisis visual.

## Archivos y contrato

Para `<id>` deben existir:

```text
out/RAW/<id>.mp4
out/RAW/<id>.json
out/RAW/_gemini/<id>.json
```

El primer JSON es el manifiesto privado de Clipper. El segundo es la respuesta
externa y debe cumplir:

- `schema == 2`;
- `identity_policy_version == 2`;
- `raw_id == <id>`;
- `status == "ok"`;
- `result` supera el validador estricto existente de `antigravity.py` para la
  duración real del candidato.

Un archivo parcial, inválido o de otro candidato nunca llega a Luna.

## Flujo

El ciclo existente de `servidor.py`, que ya se ejecuta cada 15 segundos, llama a
un escáner pequeño de `raw.py`. No se crea otro servicio ni cron.

```text
RAW pendiente
  └─ sin JSON v2 válido → permanece esperando
  └─ con JSON v2 válido → reclamar trabajo
       → guardar resultado Gemini validado en el manifiesto
       → Luna estricta
       → render y controles locales
       → LISTOS o REVISAR
```

El bloqueo y la cola RAW existentes garantizan una sola ejecución simultánea y
evitan procesar dos veces el mismo candidato.

## Reintentos

Los fallos de Luna o render se reintentan automáticamente hasta completarse. El
manifiesto guarda contador y próximo intento. Las esperas son 1, 5 y 15 minutos;
después quedan limitadas a una hora entre intentos.

Los errores del análisis v2 no generan llamadas: el RAW permanece esperando a
que el archivo sea reemplazado por uno válido.

## Interfaz y API

La tarjeta RAW conserva previsualización y descarga. Se eliminan los botones
`Analizar con Gemini`, `Procesar con Luna` y sus reintentos manuales. También se
retira `POST /api/raw/process` para que ninguna ruta pública pueda saltarse el
análisis v2.

La interfaz muestra estados descriptivos: esperando análisis Gemini, procesando
con Luna, esperando reintento, error del análisis o completado.

## Migración

- Los cuatro análisis v2 ya existentes serán detectados después del despliegue.
- Los RAW sin v2 permanecerán intactos.
- Antes de activar el nuevo flujo se borrarán los dos MP4 de `LISTOS`, los 85 de
  `REVISAR` y todos sus archivos auxiliares, con verificación posterior.
- `CLIPPER_RAW_MODO` permanece en `manual`; `agy` continúa siendo el responsable
  externo de crear `_gemini/<id>.json`.

## Comprobaciones

1. Un v2 válido llega a Luna exactamente una vez y termina en una bandeja.
2. Falta de v2, `raw_id` distinto, esquema antiguo o resultado inválido no llama
   a Luna.
3. Dos escaneos no duplican el trabajo.
4. Luna o render fallidos programan reintentos con espera creciente.
5. Un reinicio recupera un trabajo interrumpido.
6. La UI y el servidor ya no contienen la acción POST manual.
7. La suite completa, `py_compile`, JavaScript embebido y `git diff --check`
   pasan antes del despliegue.
