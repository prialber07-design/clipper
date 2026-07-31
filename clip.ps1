# Wrapper: usa el Python 3.12 instalado sin depender del PATH.
#   .\clip.ps1 fetch <url>        .\clip.ps1 transcribe <slug>     .\clip.ps1 render <slug>
#   .\clip.ps1 live watch <canal> --plataforma twitch
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { Write-Error "No encuentro Python 3.12 en $py"; exit 1 }
if ($args.Count -gt 0 -and $args[0] -eq "live") {
    & $py (Join-Path $PSScriptRoot "live.py") @($args[1..($args.Count-1)])
} else {
    & $py (Join-Path $PSScriptRoot "clipper.py") @args
}
