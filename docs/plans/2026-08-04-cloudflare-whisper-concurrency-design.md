# Concurrencia de Cloudflare Whisper

## Objetivo

Permitir hasta tres transcripciones simultáneas con Cloudflare Workers AI sin
saturar el servidor cuando sea necesario usar Whisper local.

## Arquitectura

Cada canal ya se ejecuta en un proceso independiente. No se introduce una cola
`asyncio`: los procesos existentes aportan la concurrencia y un semáforo de
archivos compartido limita las peticiones remotas a tres.

El semáforo tendrá tres plazas. Una transcripción Cloudflare espera hasta poder
bloquear una de ellas y la libera automáticamente al terminar, fallar o morir el
proceso. Whisper local y el render conservan el cerrojo exclusivo de CPU. Luna
mantiene su cola serial actual.

## Flujo

1. El canal prepara el audio del candidato.
2. Si Cloudflare está configurado, espera una de las tres plazas compartidas.
3. Ejecuta la petición y libera la plaza.
4. Si Cloudflare falla, espera el cerrojo de CPU y transcribe localmente.
5. El resto del pipeline continúa sin cambios.

## Observabilidad y errores

Los logs registrarán cuándo una petición espera plaza, cuándo la consigue y su
latencia. Una excepción no puede dejar una plaza ocupada. El fallback local
nunca se ejecutará en paralelo con otra tarea pesada protegida por el cerrojo de
CPU.

## Comprobaciones

- Nunca hay más de tres plazas Cloudflare ocupadas.
- Una plaza se libera aunque la petición falle.
- El fallback local sigue pasando por el cerrojo exclusivo de CPU.
- Las transcripciones y manifiestos mantienen el formato actual.
