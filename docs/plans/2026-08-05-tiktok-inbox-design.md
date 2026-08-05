# Envío manual de clips al inbox de TikTok

## Objetivo

Permitir enviar manualmente la variante azul de un clip desde `LISTOS` o
`REVISAR` al inbox de la cuenta de TikTok. La publicación final se completa en
la aplicación de TikTok.

## Flujo

- Las tarjetas azules muestran `Enviar a TikTok` cuando OAuth está configurado.
- TikTok nunca se encola automáticamente al entrar un clip en `LISTOS`.
- El botón reutiliza la cola persistente y el worker de publicaciones.
- Los estados visibles son `Sin enviar`, `Pendiente`, `Enviando`, `Error` y
  `Enviado al inbox`.
- El TXT permanece junto al vídeo para copiar la descripción y los hashtags al
  completar la publicación en TikTok.

## API y autenticación

Se usa Content Posting API con el scope `video.upload` y transferencia
`FILE_UPLOAD`. Así no se necesita verificar un dominio ni exponer el vídeo con
una URL pública.

El servidor renueva el access token con el refresh token configurado. Si TikTok
rota el refresh token, el nuevo valor se guarda atómicamente en el volumen de
datos y no se expone en el dashboard ni en los logs.

Variables:

```env
CLIPPER_TIKTOK_CLIENT_KEY=
CLIPPER_TIKTOK_CLIENT_SECRET=
CLIPPER_TIKTOK_REFRESH_TOKEN=
```

## Estado e idempotencia

El `publish_id` y el estado final se guardan en `publicaciones.json`. Una segunda
pulsación no vuelve a subir un vídeo ya enviado. Los errores externos conservan
el MP4 y usan los reintentos existentes.

`Enviado al inbox` significa que TikTok recibió el archivo, no que el usuario lo
haya publicado. La API de inbox no admite prellenar caption o hashtags.

## Pruebas

Las pruebas usan respuestas HTTP simuladas y MP4 sintéticos para comprobar:

- que `LISTOS` no encola TikTok automáticamente;
- envío manual desde `LISTOS` y `REVISAR`;
- renovación y rotación del refresh token;
- subida binaria e idempotencia;
- errores sin pérdida del clip.

No se realizan publicaciones reales durante las pruebas.
