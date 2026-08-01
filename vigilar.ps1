# Lanza y para vigilantes de varios canales a la vez.
#
#   .\vigilar.ps1 davooxeneize lopezfnx elcalvolol  -> arranca esos
#   .\vigilar.ps1 -Estado                            -> que hay corriendo de verdad
#   .\vigilar.ps1 -Parar                             -> para todo y limpia huerfanos
#
# El estado se saca del sistema operativo (linea de comandos de cada proceso),
# no de un fichero de registro: un registro se queda obsoleto en cuanto un
# proceso muere o alguien lanza otra tanda, y entonces miente.

param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Canales,
    [switch]$Parar,
    [switch]$Estado
)

$raiz = $PSScriptRoot
$py   = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

function Get-Vigilantes {
    # Devuelve @{ canal = ...; pid = ... } por cada live.py watch en marcha.
    # pythonw.exe tambien cuenta: es el interprete que usa la tarea programada,
    # y buscar solo python.exe hacia parecer que no habia nada corriendo.
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') }
    $salida = @()
    foreach ($p in $procs) {
        $cmd = $p.CommandLine
        if (-not $cmd -or $cmd -notmatch 'live\.py') { continue }
        if ($cmd -notmatch 'watch\s+([A-Za-z0-9_]+)') { continue }
        $salida += [pscustomobject]@{ canal = $Matches[1]; pid = $p.ProcessId }
    }
    return $salida
}

if ($Estado) {
    $vig = Get-Vigilantes
    if (-not $vig) { "No hay ningun vigilante en marcha."; return }
    foreach ($v in $vig) {
        $log = "$raiz\logs\$($v.canal).log"
        $ultimo = ""
        if (Test-Path $log) {
            $ultimo = ((Get-Content $log -Raw) -split "`r|`n" |
                       Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()
        }
        $clips = 0
        if (Test-Path "$raiz\out\LISTOS") {
            # Los clips van numerados delante: 004_elcalvolol_2026-08-01.mp4
            $clips = @(Get-ChildItem "$raiz\out\LISTOS" -Filter "*_$($v.canal)_*.mp4" -EA SilentlyContinue).Count
        }
        "{0,-20} PID {1,-7} {2} clips | {3}" -f $v.canal, $v.pid, $clips, $ultimo
    }
    return
}

if ($Parar) {
    foreach ($v in Get-Vigilantes) { taskkill /PID $v.pid /T /F 2>&1 | Out-Null }
    # Los hijos de streamlink sobreviven al padre y dejan el buffer bloqueado.
    Get-Process ffmpeg, streamlink -ErrorAction SilentlyContinue | ForEach-Object {
        taskkill /PID $_.Id /T /F 2>&1 | Out-Null
    }
    Start-Sleep -Seconds 2
    Remove-Item "$raiz\buffer" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$raiz\vigilantes.json" -Force -ErrorAction SilentlyContinue
    "Todos parados. Buffer limpio."
    return
}

if (-not $Canales) { "Uso: .\vigilar.ps1 <canal> [canal2 ...] | -Estado | -Parar"; return }
if (-not (Test-Path $py)) { Write-Error "No encuentro Python en $py"; return }

$cfg = Get-Content (Join-Path $raiz "config.json") -Raw | ConvertFrom-Json
if ($Canales.Count -gt 3) {
    Write-Warning "$($Canales.Count) canales a la vez. Cada uno descarga un directo continuo (3-6 Mbps) y comparte GPU. Recomendado: 3."
}

$yaVivos = @(Get-Vigilantes)
$lanzados = @()

foreach ($canal in $Canales) {
    if ($yaVivos.canal -contains $canal) {
        "Ya vigilando $canal, lo salto (dos procesos se pelearian por el mismo buffer)."
        continue
    }

    $info = $cfg.canales | Where-Object { $_.canal -eq $canal } | Select-Object -First 1
    if (-not $info)            { Write-Warning "$canal no esta en config.json. Lo salto."; continue }
    if (-not $info.verificado) { Write-Warning "$canal no esta verificado. Lo salto."; continue }

    $argumentos = @("$raiz\live.py", "watch", $canal, "--plataforma", $info.plataforma)
    if ($info.plataforma -ne "twitch") { $argumentos += "--solo-audio" }

    New-Item -ItemType Directory -Force "$raiz\logs" | Out-Null

    # Start-Process trunca el fichero de salida. Sin rotar, cada reinicio borra
    # el historial y con el la unica forma de medir que tal va el detector.
    $log = "$raiz\logs\$canal.log"
    if (Test-Path $log) {
        Move-Item $log "$raiz\logs\$canal.$(Get-Date -Format yyyyMMdd-HHmmss).log" -Force
    }
    Get-ChildItem "$raiz\logs" -Filter "$canal.*.log" -EA SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 15 | Remove-Item -Force -EA SilentlyContinue

    # Sin esto el log sale con mojibake ("Â¿CuÃ¡ntas?") y los ganchos con tildes
    # se vuelven ilegibles justo donde mas falta hace leerlos.
    $env:PYTHONIOENCODING = "utf-8"
    $p = Start-Process -FilePath $py -ArgumentList $argumentos -WorkingDirectory $raiz `
        -RedirectStandardOutput "$raiz\logs\$canal.log" `
        -RedirectStandardError  "$raiz\logs\$canal.err.log" `
        -PassThru -WindowStyle Hidden
    $lanzados += [pscustomobject]@{ canal = $canal; pid = $p.Id }
    "Lanzado {0,-20} PID {1}  ({2})" -f $canal, $p.Id, $info.plataforma
}

if (-not $lanzados) { return }

# 20s: lo que tarda en fallar por buffer bloqueado o canal inexistente.
Start-Sleep -Seconds 20
"`nComprobacion real:"
$vivos = @(Get-Vigilantes)
foreach ($v in $lanzados) {
    if ($vivos.pid -contains $v.pid) {
        "{0,-20} VIVO" -f $v.canal
    } else {
        $err = "$raiz\logs\$($v.canal).err.log"
        $motivo = if ((Test-Path $err) -and (Get-Item $err).Length -gt 0) {
            ((Get-Content $err -Raw) -split "`r|`n" | Where-Object { $_.Trim() } | Select-Object -Last 1).Trim()
        } else { "sin traza; mira logs\$($v.canal).log" }
        "{0,-20} MUERTO -> {1}" -f $v.canal, $motivo
    }
}
