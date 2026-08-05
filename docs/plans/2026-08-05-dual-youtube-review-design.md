# Publicación dual en YouTube con revisión del amigo

## Objetivo

Mantener un único dashboard y un único pipeline, pero tratar cada variante como
un destino editorial independiente:

- `yo`: variante azul, publicación automática al entrar en `LISTOS`.
- `amigo`: variante amarilla, siempre requiere revisión manual aunque el clip
  haya superado el filtro de calidad.

## Flujo

Los clips continúan generándose como pareja azul/amarilla en las carpetas
actuales. La separación es lógica, no se crean carpetas duplicadas.

La cuenta propia conserva el comportamiento actual. Solo la variante azul
aprobada se encola automáticamente. Para el amigo, cualquier amarillo no
publicado aparece en su vista de revisión, proceda de `LISTOS` o `REVISAR`. El
botón de publicación usa exclusivamente sus credenciales de YouTube. Tras una
publicación correcta aparece como listo; descartarlo solo elimina el amarillo.

## Configuración

Las credenciales actuales siguen perteneciendo a `yo`. Se añaden:

```env
CLIPPER_YOUTUBE_AMIGO_CLIENT_ID=
CLIPPER_YOUTUBE_AMIGO_CLIENT_SECRET=
CLIPPER_YOUTUBE_AMIGO_REFRESH_TOKEN=
CLIPPER_YOUTUBE_AMIGO_PRIVACY=public
```

## Estado de publicación

Cada trabajo queda asociado a su ruta concreta y a una cuenta (`yo` o
`amigo`). La variante azul mantiene compatibilidad con el estado existente. La
amarilla crea un registro separado, por lo que reintentos, errores e ID remoto
no se mezclan entre cuentas.

Los logs de publicación incluyen la cuenta y la variante sin mostrar secretos.

## Interfaz

Se añaden dos filtros globales combinables:

- Persona: `Yo · azul` y `Mi amigo · amarillo`.
- Streamer: `Todos` y cada canal disponible.

La selección se conserva durante la sesión del navegador. En la vista del
amigo, `REVISAR` reúne todos los amarillos pendientes y `LISTOS` muestra los ya
publicados. En la vista propia se mantienen las secciones actuales filtradas a
azul.

## Errores y aislamiento

- Un error de la cuenta del amigo no bloquea la propia.
- Un descarte amarillo no toca el azul ni su estado de publicación.
- Una cuenta sin credenciales se muestra como no configurada y no se intenta
  publicar.
- Las credenciales nunca se incluyen en respuestas web ni logs.

## Verificación

Las pruebas cubren publicación automática azul, ausencia de automatismo
amarillo, selección de credenciales por cuenta, descarte aislado, compatibilidad
del estado previo y combinación de filtros.
