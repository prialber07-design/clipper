# Selección editorial, hook TikTok y entrega automática V2

## Objetivo

Convertir la recomendación de Luna en una decisión editorial real y conservadora. Solo los clips que Luna y los controles locales consideren publicables pasarán a `LISTOS`. Cada clip listo tendrá un hook permanente con aspecto TikTok y un TXT listo para copiar en TikTok, Instagram o YouTube Shorts. El sincronizador de Windows descargará únicamente esas parejas MP4+TXT a la carpeta elegida, sin subcarpetas.

Este documento sustituye el diseño anterior de sincronización del mismo día.

## Decisiones aprobadas

- Publicación automática estricta: `decision=publicar`, `score >= 80` y `confidence >= 0.75`, además de todos los controles técnicos locales.
- `revisar`, `descartar` y cualquier fallo de Luna van a `REVISAR`; nunca se elimina automáticamente un candidato.
- Una sola llamada a Luna genera decisión, hook, descripción y hashtags.
- Hook permanente, sin fundidos ni animaciones, centrado al 18% de un vídeo 1080x1920.
- TikTok Sans Bold oficial, texto negro y caja blanca opaca.
- Los emojis son opcionales: Luna puede añadir entre cero y dos, solo al final del hook y solo si aportan.
- Descripción de una o dos frases, seguida de una línea en blanco y entre cuatro y seis hashtags.
- El sincronizador descarga solo `LISTOS`, directamente a la carpeta elegida, con dos archivos homónimos por clip: `.mp4` y `.txt`.

## 1. Decisión editorial real

Eliminar `CLIPPER_LLM_MODO` y toda la condición especial `modo prueba`. `CLIPPER_LLM_ACTIVO` y `OPENAI_API_KEY` ya determinan si Luna está disponible; no hace falta un segundo interruptor.

Mantener una única llamada por candidato. Ampliar su esquema estricto con:

- `decision`: `publicar`, `revisar` o `descartar`.
- `score`: entero de 0 a 100.
- `confidence`: número de 0 a 1.
- `reason`: explicación editorial breve.
- `screen_title`: hook superior.
- `social_description`: una o dos frases listas para publicar.
- `hashtags`: entre cuatro y seis etiquetas.

Las instrucciones deben exigir fidelidad a la transcripción, impedir clickbait inventado y permitir cero, uno o dos emojis relevantes al final de `screen_title`. No se debe forzar un emoji en contenido serio.

Validar y sanear todos los campos. El hook conserva mayúsculas/minúsculas naturales, no contiene saltos arbitrarios ni comandos ASS y respeta el límite de longitud. La descripción tiene un límite razonable y no incluye encabezados técnicos. Los hashtags se normalizan con `#`, se deduplican y no admiten espacios.

## 2. Puerta estricta hacia LISTOS

Un candidato solo es publicable cuando se cumplen simultáneamente:

1. Respuesta válida de Luna.
2. `decision == "publicar"`.
3. `score >= 80`.
4. `confidence >= 0.75`.
5. Hook, descripción y cuatro a seis hashtags válidos.
6. Duración válida.
7. Densidad de diálogo suficiente.
8. Audio audible.
9. Pantalla negra dentro del límite.
10. Controles locales de calidad del hook superados.

Los umbrales 80 y 0.75 deben vivir en `config.json` para poder calibrarlos sin tocar código, manteniendo esos valores como predeterminados.

Un hook heurístico ya no bloquea por ser automático, sino por no venir acompañado de una evaluación válida que supere la puerta estricta. Si Luna está desactivada, falta la clave, hay timeout, HTTP error, JSON inválido, título vacío o contenido editorial incompleto, el clip va a `REVISAR` con el motivo concreto.

`decision=revisar` y `decision=descartar` también van a `REVISAR`; el motivo debe conservar la decisión, score, confianza y explicación para la interfaz.

## 3. Formato visual del hook

Usar TikTok Sans Bold oficial, publicada bajo SIL Open Font License 1.1. Incluir en el repositorio únicamente los archivos de fuente necesarios y su licencia, obtenidos del repositorio oficial enlazado por TikTok. El render no debe depender de que el host tenga la fuente instalada.

Configurar ASS para:

- fuente `TikTok Sans` en peso negrita;
- texto negro;
- fondo blanco opaco con padding;
- máximo dos líneas dentro de márgenes seguros;
- alineación centrada;
- posición `x=540`, `y=346` para el 18% de 1920;
- duración desde `0` hasta el final exacto del clip;
- sin `fad`, alpha animado, entrada, salida ni movimiento.

Eliminar las opciones de duración/fundido del hook que dejan de tener efecto. Añadir una fuente emoji libre como fallback reproducible para que los emojis elegidos por Luna no aparezcan como cuadrados. El render debe preservar los emojis y la capitalización del hook.

Fuente oficial: https://developers.tiktok.com/blog/tiktok-sans-open-source

## 4. TXT listo para publicar

Guardar en el clip la descripción y hashtags devueltos por Luna. `_ficha_texto` debe producir únicamente:

```text
Una o dos frases de descripción listas para publicar.

#hashtag1 #hashtag2 #hashtag3 #hashtag4
```

No incluir títulos como `DESCRIPCIÓN`, datos de duración, origen, tiempos ni instrucciones internas. El usuario debe poder seleccionar todo el archivo y pegarlo directamente.

Para candidatos que acaben en `REVISAR`, conservar también el TXT generado si es válido, pero el sincronizador no lo descargará.

## 5. Interfaz web

La API debe exponer por clip la descripción recomendada, los hashtags y, cuando exista, la URL y tamaño del TXT homónimo. La interfaz mostrará un bloque `Descripción recomendada` usando `textContent`, tanto en LISTOS como en REVISAR. Puede incluir un botón nativo de copiar con confirmación accesible, sin añadir dependencias.

Los clips antiguos sin el nuevo formato deben seguir mostrándose sin romper la página; el bloque puede indicar `Sin descripción recomendada`.

## 6. Sincronizador Windows V2

Adaptar la implementación existente en `windows-sync/`; no crear un segundo sincronizador.

- Consultar solo `data.listos`; ignorar por completo `data.revisar`.
- No crear subcarpetas `LISTOS` o `REVISAR`.
- Usar directamente la carpeta elegida como destino.
- Por cada clip, exigir el MP4 y su TXT homónimo.
- Descargar ambos a temporales únicos y comprobar sus tamaños.
- Publicar primero el TXT y después el MP4, de modo que nunca aparezca un vídeo definitivo sin su descripción.
- Si falta el TXT remoto, no publicar localmente el MP4; registrar el problema y reintentar en el siguiente ciclo.
- Omitir cada archivo local que ya tenga el tamaño remoto correcto y reparar cualquiera truncado.
- No borrar archivos locales ni sincronizar en sentido inverso.
- Mantener DPAPI, HTTPS obligatorio, tarea cada diez minutos, `IgnoreNew`, mutex, log rotado e instalación idempotente del diseño anterior.

El instalador debe describir claramente que la carpeta recibirá parejas como:

```text
001_canal_2026-08-02.mp4
001_canal_2026-08-02.txt
```

La desinstalación elimina tarea, script y configuración, pero nunca la carpeta elegida ni sus MP4/TXT.

## 7. Compatibilidad y migración

- Los clips ya presentes en LISTOS se siguen sirviendo. Si tienen un TXT antiguo, se descargará tal cual; no se inventará una descripción retroactiva.
- La web tolera TXT antiguos y nuevos.
- Reinstalar el sincronizador actual migra la configuración y deja de crear/usar las subcarpetas anteriores. No mueve ni borra archivos ya descargados en ellas.
- Actualizar README y `.env.ejemplo` para retirar `CLIPPER_LLM_MODO` y explicar la puerta estricta.

## 8. Pruebas y aceptación

Añadir pruebas pequeñas que fallen ante estas regresiones:

1. `publicar`, score 80, confianza 0.75 y controles técnicos válidos pasa a LISTOS.
2. Score 79 o confianza 0.74 va a REVISAR.
3. `revisar`, `descartar`, timeout, título o descripción inválidos van a REVISAR.
4. El TXT contiene solo descripción, línea vacía y cuatro a seis hashtags.
5. El ASS usa TikTok Sans, negro sobre blanco, posición 540/346, duración completa y no contiene `fad`.
6. Un hook sin emoji y otro con emoji se renderizan sin perder caracteres.
7. La API y la web muestran `Descripción recomendada` sin inyección HTML.
8. `-SelfTest` del sincronizador demuestra que solo procesa LISTOS, escribe en la raíz y exige MP4+TXT.
9. Una segunda sincronización no redescarga parejas completas; una pareja truncada se repara.
10. La tarea conserva el intervalo de diez minutos y la desinstalación conserva los archivos entregados.

Ejecutar además la suite Python completa, `py_compile`, validación del JavaScript embebido, `git diff --check`, autocomprobación con Windows PowerShell 5.1 y un render real corto inspeccionando al menos el primer y último fotograma.

## Plan de implementación

1. Ampliar y validar el esquema/respuesta editorial en `clipper.py`; retirar el modo prueba.
2. Propagar descripción, hashtags y metadatos de Luna desde `live.py` hasta render, calidad y publicación.
3. Implementar la puerta estricta en un único punto compartido de `calidad.py`.
4. Cambiar `_ficha_texto` al formato directamente publicable.
5. Incorporar TikTok Sans y fallback emoji; actualizar ASS y Docker CPU/GPU.
6. Exponer y mostrar la descripción/TXT en `web.py`.
7. Adaptar `windows-sync/` para LISTOS, raíz plana y parejas MP4+TXT.
8. Actualizar configuración, ejemplos y README, eliminando referencias obsoletas al modo prueba.
9. Añadir las pruebas de regresión y ejecutar todas las verificaciones antes de entregar.

## Fuera de alcance

- Segunda llamada de IA para revisar la primera.
- Eliminación automática de descartados.
- Generar descripciones nuevas para clips históricos.
- Subida automática a redes sociales.
- Sincronización de REVISAR al ordenador del amigo.
