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

## Publicación en YouTube, Instagram y TikTok

La variante azul se publica automáticamente en la cuenta propia. La amarilla
usa una segunda cuenta de YouTube y siempre exige revisión manual. Las redes
cuyas credenciales estén incompletas quedan desactivadas y no interfieren con
el resto de Clipper.

### YouTube

1. Crea un proyecto en Google Cloud y habilita **YouTube Data API v3**.
2. Configura la pantalla de consentimiento y añade tu cuenta como usuario de
   prueba mientras la aplicación siga en modo de pruebas.
3. Crea un cliente OAuth y autoriza el scope
   `https://www.googleapis.com/auth/youtube.upload` con acceso offline.
4. Guarda en EasyPanel:

```env
CLIPPER_YOUTUBE_CLIENT_ID=...
CLIPPER_YOUTUBE_CLIENT_SECRET=...
CLIPPER_YOUTUBE_REFRESH_TOKEN=...
CLIPPER_YOUTUBE_PRIVACY=public
```

Para la cuenta del amigo, repite el OAuth con su canal y añade:

```env
CLIPPER_YOUTUBE_AMIGO_CLIENT_ID=...
CLIPPER_YOUTUBE_AMIGO_CLIENT_SECRET=...
CLIPPER_YOUTUBE_AMIGO_REFRESH_TOKEN=...
CLIPPER_YOUTUBE_AMIGO_PRIVACY=public
```

Estos clips nunca se encolan automáticamente. Se publican desde el filtro
`Mi amigo · amarillo` del dashboard.

Google restringe a `private` las subidas de proyectos no auditados creados
después del 28 de julio de 2020. Haz primero una prueba con `private` y solicita
la auditoría antes de activar `public`.

### Instagram

1. Usa una cuenta de Instagram profesional enlazada a una página de Facebook.
2. Crea una app de Meta con `instagram_basic`, `instagram_content_publish`,
   `pages_show_list` y `pages_read_engagement`.
3. Obtén un Page Access Token de larga duración y el ID de
   `instagram_business_account`.
4. Configura:

```env
CLIPPER_INSTAGRAM_ACCOUNT_ID=...
CLIPPER_INSTAGRAM_ACCESS_TOKEN=...
CLIPPER_META_API_VERSION=v25.0
CLIPPER_URL_PUBLICA=https://tu-dominio
```

Meta descarga el MP4 mediante una URL firmada de una hora. El dominio debe ser
HTTPS y accesible desde Internet. El token nunca aparece en esa URL ni en los
logs. Los reintentos y resultados quedan en
`/app/clips/publicaciones.json`.

### TikTok

TikTok no es automático: el botón del dashboard envía el MP4 azul al inbox y
la publicación se termina desde la aplicación móvil.

1. Crea una app en [TikTok for Developers](https://developers.tiktok.com/).
2. Añade Login Kit y Content Posting API y solicita el scope `video.upload`.
3. Autoriza tu cuenta con OAuth y cambia el código por un refresh token.
4. Configura:

```env
CLIPPER_TIKTOK_CLIENT_KEY=...
CLIPPER_TIKTOK_CLIENT_SECRET=...
CLIPPER_TIKTOK_REFRESH_TOKEN=...
```

El access token se renueva automáticamente. Si TikTok rota el refresh token,
Clipper conserva el nuevo valor en `/app/clips/.tiktok-oauth.json`. TikTok no
permite prellenar descripción o hashtags en el flujo de inbox: se copian desde
el TXT del clip al completar la publicación.
