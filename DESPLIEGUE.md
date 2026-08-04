# Despliegue de Clipper

## Flujo actual

Cada candidato se conserva primero como MP4 en `/app/clips/out/RAW`. El
supervisor extrae un JPEG por segundo y cinco alrededor del pico, y envía con
`codex exec` las imágenes, la transcripción, el chat y el canal.
La respuesta decide `LISTOS` o `REVISAR` y aporta hook, descripción y hashtags.

Si falla la extracción, la red o Luna, el MP4 permanece en RAW y se reintenta
con espera creciente. Los JPEG viven en un directorio temporal y siempre se
eliminan.

## EasyPanel

El servicio necesita un volumen persistente montado en `/app/clips` y estas
variables:

```env
CLIPPER_DATA=/app/clips
CLIPPER_LLM_ACTIVO=1
CLIPPER_CODEX_MODELO=
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_AI_TOKEN=...
CLIPPER_WEB_USUARIO=...
CLIPPER_WEB_PASSWORD=...
```

Cloudflare usa `@cf/openai/whisper` como transcriptor principal. Si sus
credenciales faltan, se agota la cuota o la petición falla, el mismo trabajo
continúa automáticamente con `faster-whisper` local.

Tras el primer despliegue, inicia la sesión OAuth desde una terminal de
EasyPanel con `codex login --device-auth`. `CODEX_HOME` vive en el volumen, por
lo que la sesión sobrevive a los redespliegues. El servicio web solo debe
publicarse detrás de HTTPS.

## Comprobación tras redesplegar

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker logs --tail 150 <contenedor>
docker exec <contenedor> codex login status
docker exec <contenedor> python -m py_compile clipper.py raw.py storyboard.py servidor.py
docker exec <contenedor> sh -lc 'find /app/clips/out/RAW -maxdepth 1 -type f | sort | tail -30'
docker exec <contenedor> sh -lc 'tail -100 /app/clips/logs/raw-processing.jsonl'
```

La secuencia normal del log es:

```text
RAW_CREATED
RAW_QUEUED
LUNA_VISUAL_STARTED
LUNA_VISUAL_FINISHED
RENDER_STARTED
RENDER_FINISHED
MOVED_TO_LISTOS | MOVED_TO_REVISAR
RAW_COMPLETED
```

Un `LUNA_FAILED` conserva el candidato y registra `last_error`, número de
reintentos y próximo intento en su manifiesto JSON.

## Directorios persistentes

```text
/app/clips/out/RAW       candidatos originales y manifiestos
/app/clips/out/LISTOS    clips publicables
/app/clips/out/REVISAR   clips que requieren revisión
/app/clips/logs          logs operativos
/app/clips/modelos       caché de Whisper
/app/clips/codex-home    sesión OAuth de Codex (privada)
```

La limpieza automática solo elimina RAW completados que hayan superado la
retención configurada; nunca borra pendientes o errores.
