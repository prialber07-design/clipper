# Recorte flexible, tono brainrot y doble render

## Objetivo

Dejar que Luna seleccione el momento completo más corto dentro de cada candidato,
sin rellenarlo artificialmente hasta unos 30 segundos, y entregar dos versiones
visuales independientes de cada clip.

## Selección temporal

- Cada candidato ofrece a Luna hasta 40 segundos de contexto.
- La respuesta estructurada añade `clip_start_s` y `clip_end_s`.
- El tramo elegido debe durar entre 8 y 40 segundos, quedar dentro del candidato
  e incluir el pico que originó la captura.
- Luna debe terminar después del remate y eliminar introducciones, silencios y
  explicaciones posteriores que no aporten.
- Un intervalo inválido no se corrige ni se rellena: el candidato pasa a revisión.
- Se elimina la alternancia artificial entre clips cortos y clips largos.

## Estilo editorial de Luna

- Hook de unas 4-6 palabras y un máximo de 32 caracteres.
- Descripción de una sola frase corta.
- Lenguaje joven, informal, curioso, clickbait y estilo brainrot.
- El tono nunca permite inventar hechos, identidades o consecuencias que no
  sostengan el vídeo, la transcripción o el chat.
- Los emojis siguen siendo opcionales y solo aparecen al final del hook.

## Render y publicación

Una única evaluación editorial genera dos MP4 a partir del mismo corte:

1. `amarillo`: palabra activa amarilla y marca obtenida de `CLIPPER_MARCA`.
2. `azul`: palabra activa azul TikTok `#25F4EE` y ninguna marca.

Los subtítulos bajan de tamaño para ocupar menos pantalla. Cada MP4 recibe un
nombre que identifica su variante y un TXT propio con la misma descripción y
hashtags. Las dos versiones pasan juntas a `LISTOS` o juntas a `REVISAR`; no se
publica una pareja incompleta.

El audio de ambas salidas se normaliza mediante `loudnorm` a -16 LUFS, LRA 11 y
pico máximo -1.5 dB.

## Errores y comprobación

- Validar tipos, límites, duración e inclusión del pico antes de renderizar.
- Renderizar las dos variantes antes de mover cualquiera a la bandeja final.
- Probar el contrato estructurado, el saneamiento temporal, colores y marca por
  variante, tamaño de subtítulos, normalización y entrega de dos parejas MP4+TXT.
- Ejecutar la suite existente y una comprobación real corta con ffmpeg.
