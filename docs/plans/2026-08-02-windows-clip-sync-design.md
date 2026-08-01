# Sincronización automática de clips en Windows

## Objetivo

Entregar a otro usuario un paquete pequeño que instale, sin Python ni dependencias externas, una sincronización de todos los clips de `LISTOS` y `REVISAR` desde Clipper hacia una carpeta elegida por él. Windows ejecutará la sincronización cada diez minutos.

## Decisiones aprobadas

- Se descargan ambas bandejas en subcarpetas separadas: `LISTOS` y `REVISAR`.
- El instalador pregunta la carpeta de destino.
- Se usan el usuario y la contraseña actuales de la web de Clipper.
- La tarea funciona bajo el usuario actual de Windows, sin administrador y mientras tenga una sesión iniciada.
- Los clips locales nunca se eliminan porque desaparezcan del servidor.
- No se introducen Python, ejecutables empaquetados ni dependencias nuevas.

## Paquete entregable

Crear `windows-sync/` con solo estos archivos:

- `Instalar.bat`: abre Windows PowerShell 5.1 y ejecuta el modo de instalación.
- `Sincronizar-Clips.ps1`: contiene instalación, sincronización, desinstalación y una autocomprobación pequeña mediante parámetros.
- `Desinstalar.bat`: ejecuta el modo de desinstalación.

El instalador copia el script a `%LOCALAPPDATA%\ClipperSync\`. Así, el ZIP original se puede borrar o mover después de instalarlo.

## Instalación

`Instalar.bat` llama a `Sincronizar-Clips.ps1 -Install`. El proceso:

1. Solicita la URL HTTPS base de Clipper y elimina la barra final.
2. Rechaza HTTP salvo `localhost`, para no enviar Basic Auth sin cifrado.
3. Solicita el usuario y la contraseña mediante `Read-Host -AsSecureString`.
4. Abre un selector nativo de carpetas; si no está disponible, solicita la ruta por consola.
5. Crea `<destino>\LISTOS` y `<destino>\REVISAR`.
6. Guarda URL, usuario y destino en `%LOCALAPPDATA%\ClipperSync\config.json`.
7. Guarda la contraseña con `ConvertFrom-SecureString`, cifrada por DPAPI para ese usuario de Windows. Nunca se incluye en el BAT, repositorio, argumentos ni logs.
8. Copia el script a `%LOCALAPPDATA%\ClipperSync\Sincronizar-Clips.ps1`.
9. Valida las credenciales consultando `/api/clips` antes de registrar nada. Si falla, no crea la tarea.
10. Ejecuta una sincronización inicial.
11. Registra `Clipper - Sincronizar clips` en el Programador de tareas cada diez minutos, con nivel limitado, usuario interactivo actual y política `IgnoreNew` para impedir ejecuciones solapadas.

Reinstalar actualiza configuración, script y tarea en lugar de duplicarlos.

## Flujo de sincronización

En cada ejecución:

1. Carga la configuración y descifra la contraseña solo en memoria.
2. Adquiere un mutex local; si ya hay otra ejecución, termina correctamente.
3. Consulta `GET /api/clips` con Basic Auth y `Cache-Control: no-store`.
4. Recorre `listos` y `revisar`.
5. Obtiene cada nombre con `Path.GetFileName` y rechaza rutas, nombres vacíos o caracteres inválidos.
6. Construye la descarga desde la URL base, la bandeja y el nombre escapado. No acepta una URL absoluta enviada por la respuesta, evitando reenviar credenciales a otro host.
7. Consulta el tamaño remoto mediante `HEAD`, que el servidor actual ya soporta.
8. Si existe un archivo local con el mismo nombre y tamaño, lo omite.
9. Si falta o el tamaño no coincide, descarga a un temporal único dentro de la misma carpeta.
10. Comprueba el tamaño y renombra el temporal al nombre definitivo de forma atómica.
11. Libera el mutex y registra un resumen.

Un archivo local borrado se descargará de nuevo. Un archivo local adicional se conserva. No se requiere una base de datos de estado: nombre y tamaño son suficientes porque Clipper genera nombres únicos e inmutables.

## Errores y registro

- Un 401/403 detiene el ciclo y registra `credenciales rechazadas`, sin imprimirlas.
- Un error de red conserva todo lo ya descargado y se reintenta diez minutos después.
- El fallo de un clip no impide intentar los demás.
- Los temporales incompletos no se consideran clips y se reemplazan en el siguiente intento.
- El log vive en `%LOCALAPPDATA%\ClipperSync\sync.log`, rota al superar 1 MB y conserva solo una copia anterior.
- La salida de cada ejecución debe indicar descargados, omitidos y fallidos.

## Desinstalación

`Desinstalar.bat` ejecuta `-Uninstall`, elimina la tarea programada y `%LOCALAPPDATA%\ClipperSync\`. Pregunta antes de actuar y nunca borra la carpeta de clips elegida.

## Seguridad asumida

Basic Auth solo es aceptable mediante HTTPS. Como se usarán las credenciales web actuales, el amigo también podrá acceder a la galería, explorador y logs si conoce la URL. DPAPI evita dejar la contraseña en texto plano, pero no reduce esos permisos.

## Comprobaciones y aceptación

El mismo script ofrece `-SelfTest`, sin frameworks, para validar construcción segura de rutas, rechazo de traversal y decisión de descarga por tamaño.

Antes de entregar:

1. Ejecutar `-SelfTest` en Windows PowerShell 5.1.
2. Instalar usando una carpeta temporal con espacios.
3. Confirmar que la primera ejecución descarga ambas bandejas.
4. Repetir y confirmar cero descargas.
5. Truncar una copia local y confirmar que se repara.
6. Simular credenciales erróneas y confirmar que no se corrompe ningún archivo.
7. Confirmar que existe una sola tarea, intervalo de diez minutos y `IgnoreNew`.
8. Desinstalar y comprobar que la tarea/configuración desaparecen y los clips permanecen.

## Plan de implementación

1. Crear `windows-sync/Sincronizar-Clips.ps1` con parámetros `-Install`, `-Uninstall` y `-SelfTest`; sin parámetro ejecuta una sincronización.
2. Implementar configuración local, selector de carpeta, DPAPI y validación HTTPS/credenciales.
3. Implementar consulta del manifiesto, validación de nombres, comparación por tamaño y descarga temporal atómica.
4. Implementar mutex, log con rotación y manejo de errores por ciclo/archivo.
5. Implementar registro idempotente de la tarea programada y desinstalación conservadora.
6. Añadir los dos BAT como envoltorios mínimos del script.
7. Añadir al README instrucciones de entrega, instalación, funcionamiento, actualización y desinstalación.
8. Ejecutar la autocomprobación y el checklist de aceptación sin usar credenciales reales en pruebas o logs.

## Fuera de alcance

- Interfaz gráfica propia.
- Sincronización bidireccional o borrado remoto/local.
- Actualizaciones automáticas del sincronizador.
- Cuentas o tokens con permisos separados.
- Ejecución cuando ningún usuario ha iniciado sesión.
