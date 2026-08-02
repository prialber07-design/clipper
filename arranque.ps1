# Hace que el clipper arranque al ENCENDER el PC, sin esperar a que inicies
# sesion. Requiere administrador (cambiar el principal de una tarea lo exige).
#
#   powershell -ExecutionPolicy Bypass -File arranque.ps1
#
# Se ejecuta como el usuario Alber con LogonType S4U: mantiene tu perfil (hace
# falta para encontrar ffmpeg y streamlink de WinGet, y el modelo de Whisper
# cacheado) y NO guarda tu contraseña en ninguna parte. Como SYSTEM no valdria:
# ese perfil no ve nada de eso y arrancaria en vacio.

$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $admin) {
    Write-Host "[!] Hace falta administrador. Pidiendo permiso..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy", "Bypass", "-NoExit", "-File", "`"$PSCommandPath`""
    )
    return
}

$usuario = "Alber"
$tarea = "clipper"

# Al encender, con un minuto de margen para que la red este levantada. Se deja
# ademas el disparador de inicio de sesion como red de seguridad: si por lo que
# sea el de arranque no prende, el otro lo recoge. MultipleInstances=IgnoreNew
# ya evita que se dupliquen.
$alEncender = New-ScheduledTaskTrigger -AtStartup
$alEncender.Delay = "PT1M"
$alEntrar = New-ScheduledTaskTrigger -AtLogOn -User $usuario

$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType S4U -RunLevel Limited

Set-ScheduledTask -TaskName $tarea -Trigger @($alEncender, $alEntrar) -Principal $principal | Out-Null

$t = Get-ScheduledTask -TaskName $tarea
Write-Host ""
Write-Host "[ok] Tarea actualizada" -ForegroundColor Green
Write-Host "  usuario   : $($t.Principal.UserId)"
Write-Host "  logontype : $($t.Principal.LogonType)   (S4U = sin contraseña guardada)"
foreach ($tr in $t.Triggers) {
    $d = if ($tr.Delay) { "  espera $($tr.Delay)" } else { "" }
    Write-Host "  disparo   : $($tr.CimClass.CimClassName)$d"
}
Write-Host "  estado    : $($t.State)"
Write-Host ""
Write-Host "Compruebalo apagando y encendiendo sin iniciar sesion: los" -ForegroundColor Cyan
Write-Host "vigilantes deben estar vivos igualmente." -ForegroundColor Cyan
