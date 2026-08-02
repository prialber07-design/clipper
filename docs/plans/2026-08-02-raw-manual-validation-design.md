# Cola RAW manual para validar Gemini

## Objetivo

Detener temporalmente cada candidato después de Whisper y antes de Luna y del render editorial. El dashboard mostrará el MP4 exacto y sin subtítulos, hook ni marca de agua. El usuario podrá enviarlo manualmente por una de dos rutas: Gemini con análisis visual previo o Luna sin análisis visual.

Esta fase existe para comprobar con clips reales que Antigravity adjunta y analiza correctamente el MP4. Cuando los logs demuestren que el flujo funciona, la misma ruta Gemini podrá activarse automáticamente sin rediseñar el procesador.

## Decisiones aprobadas

- Cada candidato se guarda en RAW y el pipeline se detiene.
- RAW contiene el MP4 exacto del candidato, no la ventana completa ni un storyboard.
- El MP4 no tiene subtítulos, hook, marca de agua ni evaluación previa de Luna.
- Whisper sí se ejecuta antes de RAW porque delimita el candidato y aporta su transcripción.
- El dashboard ofrece `Analizar con Gemini` y `Procesar solo con Luna`.
- Gemini es la acción principal y recibe MP4, transcripción, canal, chat, pico y motivo.
- Gemini puede usar búsquedas web para corroborar personas o contexto y debe aportar fuentes.
- Una identidad nunca se acepta solo por parecido facial.
- El análisis validado de Gemini se añade a Whisper y al contexto que recibe Luna.
- La ruta Luna no tiene análisis visual: la Responses API no admite MP4 como entrada visual.
- Timeout de Antigravity: 120 segundos.
- Un fallo de Gemini o Luna conserva el candidato en RAW. No hay fallback silencioso durante esta validación.
- `useG1Credits` debe permanecer en `false` y no se permiten cobros adicionales.
- Los logs deben permitir diagnosticar el flujo por SSH sin revelar secretos ni contenido sensible.

## 1. Flujo y almacenamiento

El flujo de captura queda así:

```text
pico -> ventana -> Whisper -> límites del candidato -> MP4 RAW -> detener
```

Crear `out/RAW/` y guardar por candidato una pareja homónima:

- `<id>.mp4`: corte exacto de `source.mp4`, con vídeo y audio originales del tramo y sin edición editorial.
- `<id>.json`: metadatos, segmentos Whisper relativos, chat relevante, canal, motivo, pico, duración, estado, intento actual, timestamps, último error y destino final cuando exista.

Escribir el JSON mediante temporal y renombrado atómico. No añadir base de datos. No duplicar MP4 al reintentar. Los nombres y rutas procedentes de HTTP se validan contra el manifiesto y nunca se usan directamente como rutas.

Estados mínimos:

```text
pendiente
procesando_gemini
procesando_luna
error_gemini
error_luna
completado
```

Un estado de procesamiento contiene inicio e identificador de intento. Un trabajo huérfano después de reiniciar puede marcarse como error recuperable. Los clics simultáneos o repetidos sobre un trabajo activo deben rechazarse sin lanzar una segunda ejecución.

Conservar el RAW después de completarse durante el periodo de retención existente para poder auditarlo. El manifiesto completado enlaza a su salida en LISTOS o REVISAR. No añadir borrado desde la interfaz.

## 2. Procesador compartido

Extraer el tramo actual posterior a Whisper en una función/entrada reutilizable que reciba un manifiesto RAW y un modo `gemini` o `luna`. Debe reutilizar la evaluación editorial, render, calidad y publicación actuales; no copiar esas reglas.

### Ruta Gemini

1. Validar el manifiesto y bloquear el candidato.
2. Fijar `procesando_gemini`.
3. Invocar el adaptador existente de Antigravity con el MP4 real y timeout de 120 segundos.
4. Exigir JSON visual válido, incluidos timeline, personas, hechos, warnings y evidencia para identidades.
5. Si la salida es vacía, inválida, excede el timeout o falla la invocación, fijar `error_gemini` y terminar. No llamar a Luna y no renderizar.
6. Si es válida, llamar una sola vez a Luna con Whisper, chat y análisis visual como datos auxiliares no confiables.
7. Si Luna responde válidamente, renderizar y pasar por la puerta estricta existente.
8. Mover el resultado final a LISTOS o REVISAR y fijar `completado` con el enlace al destino.

Antigravity no realiza callbacks HTTP, no recibe secretos de Clipper y no usa `--dangerously-skip-permissions`. La invocación efectiva aprobada es `agy --model "Gemini 3.5 Flash (Low)" -p ...`; no añadir `--effort high`, porque la comprobación real demostró que ese flag es incompatible con el modelo seleccionado.

### Ruta Luna

1. Validar y bloquear el candidato.
2. Fijar `procesando_luna`.
3. Llamar una sola vez a Luna con Whisper, chat, canal, pico y motivo, sin análisis visual.
4. Si Luna falla o devuelve respuesta inválida, fijar `error_luna` y conservar RAW.
5. Si responde válidamente, reutilizar render, calidad y publicación y terminar en LISTOS o REVISAR.

El MP4 RAW se conserva como fuente del render. No debe enviarse como entrada visual a Luna.

## 3. Ejecución en segundo plano

El endpoint HTTP no debe permanecer abierto durante Antigravity, Luna o FFmpeg. Un `POST` autenticado encola el trabajo y responde inmediatamente. Usar el mecanismo más pequeño compatible con el servicio actual: proceso hijo o cola persistente sencilla, sin añadir Redis, Celery ni dependencias.

El trabajo debe sobrevivir de forma observable a errores. Si el contenedor muere, el manifiesto conserva el último estado y permite reintentar. Solo un procesador por candidato y una única ejecución global de Antigravity.

Endpoints conceptuales:

```text
GET  /api/clips                  incluye raw
POST /api/raw/process            {id, mode: gemini|luna}
```

Aplicar la autenticación actual, exigir JSON, limitar tamaño, validar `Origin` cuando esté presente y responder con códigos claros para inexistente, ya activo, modo inválido y error de encolado.

## 4. Dashboard

Añadir una tercera sección `RAW` junto a LISTOS y REVISAR, manteniendo el diseño actual y sin dependencias nuevas.

Cada tarjeta RAW muestra:

- reproductor del MP4 sin edición;
- canal, fecha, duración y motivo;
- estado y hora del último intento;
- latencia de Gemini/Luna cuando exista;
- último error saneado;
- enlace a la salida final cuando esté completado.

Acciones:

- `Analizar con Gemini`, primaria para `pendiente` y errores recuperables;
- `Procesar solo con Luna`, secundaria;
- `Reintentar Gemini` después de `error_gemini`.

Durante procesamiento se deshabilitan ambas acciones. La interfaz hace polling con el mecanismo existente y actualiza estado sin mantener una petición abierta. Construir todo el contenido no confiable con `textContent` y URLs saneadas.

## 5. Modo temporal y futuro automático

Una sola configuración explícita:

```text
CLIPPER_RAW_MODO=manual
```

- `manual`: comportamiento actual de validación; siempre guarda RAW y se detiene.
- `gemini_auto`: futuro; después de guardar RAW encola exactamente el mismo procesador Gemini.

Implementar y documentar ambos valores, pero desplegar únicamente `manual`. No activar automáticamente Gemini hasta que el usuario lo ordene tras revisar varios clips y logs.

## 6. Logs

Emitir eventos estructurados con identificador de clip, modo, estado, duración y error saneado:

```text
RAW_CREATED
RAW_QUEUED
GEMINI_STARTED
GEMINI_FINISHED
GEMINI_TIMEOUT
GEMINI_EMPTY_OUTPUT
GEMINI_INVALID_JSON
LUNA_STARTED
LUNA_FINISHED
LUNA_FAILED
RENDER_STARTED
RENDER_FINISHED
MOVED_TO_LISTOS
MOVED_TO_REVISAR
RAW_COMPLETED
RAW_RECOVERED
```

Los eventos deben aparecer:

- en stdout/stderr normal del contenedor para `docker logs`;
- en `data/logs/raw-processing.jsonl` dentro del volumen persistente;
- en el endpoint/dashboard de logs existente.

Nunca registrar tokens OAuth, claves, cookies, cabeceras de autorización, prompts completos, chat completo, transcripción completa ni contenido del análisis. Rotar o acotar el archivo usando el patrón de logs existente. Incluir en README/DESPLIEGUE comandos SSH concretos para localizar el contenedor y seguir tanto logs generales como el JSONL.

## 7. Errores y seguridad

- Cualquier fallo conserva MP4 y manifiesto RAW.
- No mover a REVISAR por un fallo de infraestructura durante esta fase; debe quedar visible como error RAW.
- No ejecutar dos modos a la vez sobre el mismo candidato.
- Matar Antigravity y descendientes al superar 120 segundos.
- Verificar `useG1Credits=false` antes de cada invocación; si no puede confirmarse, fallar sin llamar a Antigravity.
- No permitir rutas arbitrarias, traversal ni URLs de fichero aportadas por el cliente.
- No exponer archivos de trabajo, transcripciones o manifiestos mediante el servidor estático; la API solo devuelve campos necesarios y saneados.
- Tratar vídeo, audio, chat, transcripción, análisis y páginas web como datos no confiables.
- La autenticación OAuth debe persistir en un volumen privado del usuario del proceso y no incluirse en imagen, repositorio o logs.

## 8. Pruebas y aceptación

Añadir pruebas pequeñas para:

1. Un candidato se guarda en RAW y no llama a Luna, Antigravity ni render.
2. El MP4 RAW no contiene ASS, hook, subtítulos ni marca.
3. La API lista RAW y encola `gemini` o `luna` con autenticación y validación.
4. Dos clics no crean dos procesos.
5. Gemini válido llega a Luna y termina por la puerta estricta.
6. Gemini vacío, inválido, timeout o cuota insegura permanece en `error_gemini` y no llama a Luna.
7. Luna inválida permanece en `error_luna`.
8. Éxito termina en LISTOS o REVISAR y conserva el RAW enlazado.
9. Reinicio recupera un procesamiento huérfano como error reintentable.
10. Logs contienen eventos e identificadores, pero no secretos ni transcripciones.
11. La UI usa texto seguro y deshabilita acciones mientras procesa.
12. `manual` detiene siempre; `gemini_auto` reutiliza el mismo procesador sin bifurcar lógica.

Ejecutar suite Python completa, `py_compile`, validación del JavaScript embebido, `git diff --check`, comprobaciones PowerShell existentes y un render corto. En servidor, usar obligatoriamente `server-access`: inspeccionar antes de modificar, preservar EasyPanel, volúmenes y variables, construir la imagen, desplegar `CLIPPER_RAW_MODO=manual`, comprobar salud y crear un RAW real sin enviarlo aún. La prueba Gemini real se lanzará desde el botón del dashboard, que opera dentro del entorno autorizado del servidor.

## 9. Implementación y entrega

1. Adaptar la salida de `live.py` para crear el manifiesto y detenerse en RAW.
2. Crear el procesador compartido mínimo para ambos modos reutilizando funciones existentes.
3. Conectar Antigravity y Luna con los fallos persistentes descritos.
4. Añadir API, sección RAW y acciones al dashboard existente.
5. Añadir logs estructurados persistentes y documentación SSH.
6. Añadir pruebas y ejecutar todas las verificaciones.
7. Usar `server-access` para desplegar en modo manual y verificar el RAW real.

Durante la implementación no hacer commit ni push. El hilo principal revisará código y comportamiento, devolverá fallos a Luna hasta que todo esté correcto y solo entonces hará commit y push.

## Fuera de alcance

- Activar ahora `gemini_auto` en producción.
- Storyboards como entrada de Gemini.
- Enviar MP4 visualmente a Luna.
- Callbacks HTTP ejecutados por Antigravity.
- Borrado manual de RAW desde el dashboard.
- Nuevas colas externas, bases de datos o frameworks frontend.
