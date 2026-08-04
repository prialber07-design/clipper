# Publicación automática en YouTube e Instagram

## Objetivo

Publicar automáticamente en las cuentas del propietario la variante azul de
cada clip que entre en `out/LISTOS`. Los candidatos de `out/REVISAR` permiten
publicar por separado en YouTube o Instagram, o descartar la pareja completa.

## Flujo

- Un worker interno comprueba periódicamente las publicaciones pendientes sin
  bloquear Whisper, Luna ni el render.
- Solo se publica `*_azul.mp4`; la variante amarilla queda disponible para la
  descarga del segundo editor.
- En `LISTOS`, YouTube e Instagram se encolan automáticamente.
- En `REVISAR`, cada tarjeta ofrece `Publicar en YouTube`, `Publicar en
  Instagram` y `Descartar`.
- Publicar en una red no cambia el estado de la otra. Tras publicar en ambas,
  la pareja deja de mostrarse en `REVISAR`, pero permanece en disco hasta la
  limpieza normal. Descartar sí elimina ambas variantes y sus archivos
  auxiliares.

## Estado e idempotencia

`/app/clips/publicaciones.json` guarda atómicamente, por clip y plataforma:

- estado e intentos;
- próxima fecha de reintento y último error;
- identificador remoto;
- título, caption y ruta local necesarios para reanudar tras un redeploy.

El worker registra el resultado remoto antes de considerar completada la
operación. Los reintentos se hacen a 1, 5 y 15 minutos y, después, cada hora.
Un fallo de publicación nunca mueve ni elimina el clip.

## YouTube

- Subida resumible del MP4 azul mediante OAuth.
- El hook de Luna se usa como título.
- El TXT completo se usa como descripción.
- La visibilidad inicial es configurable y, por defecto, pública.

Credenciales:

```env
CLIPPER_YOUTUBE_CLIENT_ID=
CLIPPER_YOUTUBE_CLIENT_SECRET=
CLIPPER_YOUTUBE_REFRESH_TOKEN=
```

## Instagram

- Creación de un contenedor Reel, espera hasta que termine el procesamiento y
  publicación del contenedor.
- El TXT completo se usa como caption y el Reel también aparece en el feed.
- Meta descarga el MP4 desde una URL HMAC temporal, limitada a un único
  archivo y sin acceso a la galería autenticada.

Credenciales:

```env
CLIPPER_INSTAGRAM_ACCOUNT_ID=
CLIPPER_INSTAGRAM_ACCESS_TOKEN=
```

También requiere `CLIPPER_URL_PUBLICA` con HTTPS.

## Interfaz y seguridad

Las acciones editoriales son peticiones `POST` protegidas por la autenticación
existente. Las URL temporales validan ruta, firma y caducidad. El descarte usa
el bloqueo de publicaciones para no competir con una subida en curso.

## Pruebas

Las pruebas automatizadas usan endpoints HTTP falsos y archivos sintéticos;
no publican vídeos reales. Antes de activar la visibilidad pública se hará una
prueba manual limitada con las cuentas reales.

## Decisiones deliberadas

No se construye una pantalla OAuth ni una abstracción genérica de redes
sociales. Las credenciales se obtienen una vez y se configuran mediante
variables de entorno. TikTok y más cuentas se incorporarán solo cuando exista
una necesidad real.
