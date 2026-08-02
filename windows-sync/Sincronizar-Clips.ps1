[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$SelfTest
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$TaskName = "Clipper - Sincronizar clips"
$AppDirectory = Join-Path $env:LOCALAPPDATA "ClipperSync"
$ConfigPath = Join-Path $AppDirectory "config.json"
$PasswordPath = Join-Path $AppDirectory "password.dpapi"
$InstalledScriptPath = Join-Path $AppDirectory "Sincronizar-Clips.ps1"
$LogPath = Join-Path $AppDirectory "sync.log"
$RotatedLogPath = Join-Path $AppDirectory "sync.log.1"
$PowerShellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$SourceScriptPath = $MyInvocation.MyCommand.Path
$MaxLogBytes = 1MB

function ConvertTo-SafeLogText {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value) {
        return ""
    }
    return [regex]::Replace($Value, '[\x00-\x1F\x7F]', ' ')
}

function Write-SyncLog {
    param(
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level = "INFO",
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    try {
        if (-not (Test-Path -LiteralPath $AppDirectory)) {
            New-Item -ItemType Directory -Path $AppDirectory -Force | Out-Null
        }

        $existing = Get-Item -LiteralPath $LogPath -ErrorAction SilentlyContinue
        if ($null -ne $existing -and $existing.Length -gt $MaxLogBytes) {
            if (Test-Path -LiteralPath $RotatedLogPath) {
                Remove-Item -LiteralPath $RotatedLogPath -Force -ErrorAction SilentlyContinue
            }
            Move-Item -LiteralPath $LogPath -Destination $RotatedLogPath -Force
        }

        $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, (ConvertTo-SafeLogText $Message)
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    }
    catch {
        # Logging must never stop a sync or expose an exception to the caller.
    }
}

function Normalize-BaseUrl {
    param([Parameter(Mandatory = $true)][string]$Value)

    $candidate = $Value.Trim()
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        throw "La URL es obligatoria."
    }

    $parsed = $null
    if (-not [Uri]::TryCreate($candidate, [UriKind]::Absolute, [ref]$parsed)) {
        throw "La URL no es valida."
    }
    if ($parsed.UserInfo -or $parsed.Query -or $parsed.Fragment) {
        throw "La URL no puede incluir credenciales, consulta ni fragmento."
    }
    if ($parsed.Scheme -ieq "http") {
        if ($parsed.Host -ine "localhost") {
            throw "Usa HTTPS; HTTP solo se permite para localhost."
        }
    }
    elseif ($parsed.Scheme -ine "https") {
        throw "La URL debe usar HTTPS."
    }

    return $candidate.TrimEnd('/')
}

function Get-SafeClipName {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value) {
        return $null
    }

    $raw = [string]$Value
    try {
        $leaf = [IO.Path]::GetFileName($raw)
        $rooted = [IO.Path]::IsPathRooted($raw)
        $invalid = [IO.Path]::GetInvalidFileNameChars()
    }
    catch {
        return $null
    }

    if ([string]::IsNullOrWhiteSpace($leaf) -or $leaf -ne $raw -or $rooted) {
        return $null
    }
    if ($raw.IndexOfAny($invalid) -ge 0 -or $leaf -notmatch '(?i)\.mp4$') {
        return $null
    }
    if ($leaf -eq "." -or $leaf -eq "..") {
        return $null
    }
    return $leaf
}

function Test-NeedsDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$RemoteLength
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $true
    }
    $local = Get-Item -LiteralPath $Path
    return ([long]$local.Length -ne $RemoteLength)
}

function Get-BasicAuthHeader {
    param(
        [Parameter(Mandatory = $true)][string]$UserName,
        [Parameter(Mandatory = $true)][Security.SecureString]$Password
    )

    $credential = New-Object -TypeName System.Net.NetworkCredential -ArgumentList @($UserName, $Password)
    $plainPair = "{0}:{1}" -f $credential.UserName, $credential.Password
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($plainPair)
        return "Basic " + [Convert]::ToBase64String($bytes)
    }
    finally {
        $plainPair = $null
        $credential = $null
    }
}

function New-ClipperRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][ValidateSet("GET", "HEAD")][string]$Method,
        [Parameter(Mandatory = $true)][string]$Authorization
    )

    $request = [Net.HttpWebRequest]::Create($Uri)
    $request.Method = $Method
    $request.AllowAutoRedirect = $false
    $request.Timeout = 30000
    $request.ReadWriteTimeout = 30000
    $request.UserAgent = "ClipperSync/1.0"
    $request.Headers.Add("Authorization", $Authorization)
    $request.Headers.Add("Cache-Control", "no-store")
    return $request
}

function Get-ResponseCode {
    param([AllowNull()][object]$Response)

    if ($null -eq $Response) {
        return 0
    }
    try {
        return [int]$Response.StatusCode
    }
    catch {
        return 0
    }
}

function Get-ExceptionResponse {
    param([Parameter(Mandatory = $true)][System.Management.Automation.ErrorRecord]$ErrorRecord)

    $exception = $ErrorRecord.Exception
    while ($null -ne $exception) {
        $property = $exception.PSObject.Properties["Response"]
        if ($null -ne $property -and $null -ne $property.Value) {
            return $property.Value
        }
        $exception = $exception.InnerException
    }
    return $null
}

function Throw-HttpFailure {
    param(
        [AllowNull()][object]$Response,
        [Parameter(Mandatory = $true)][string]$Context
    )

    $code = Get-ResponseCode $Response
    if ($code -eq 401 -or $code -eq 403) {
        throw (New-Object System.UnauthorizedAccessException("credenciales rechazadas"))
    }
    if ($code -gt 0) {
        throw ("{0} HTTP {1}" -f $Context, $code)
    }
    throw ("{0} sin respuesta" -f $Context)
}

function Get-ClipManifest {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$Authorization
    )

    $response = $null
    $reader = $null
    try {
        $request = New-ClipperRequest -Uri ($BaseUrl + "/api/clips") -Method GET -Authorization $Authorization
        $response = $request.GetResponse()
        $code = Get-ResponseCode $response
        if ($code -lt 200 -or $code -ge 300) {
            Throw-HttpFailure -Response $response -Context "manifiesto"
        }

        $reader = New-Object IO.StreamReader($response.GetResponseStream(), [Text.Encoding]::UTF8)
        $data = ConvertFrom-Json $reader.ReadToEnd()
        $listosProperty = $null
        $revisarProperty = $null
        if ($null -ne $data) {
            $listosProperty = $data.PSObject.Properties["listos"]
            $revisarProperty = $data.PSObject.Properties["revisar"]
        }
        if ($null -eq $listosProperty) {
            throw "manifiesto invalido"
        }
        return [pscustomobject]@{
            listos = @($listosProperty.Value)
            revisar = if ($null -ne $revisarProperty) { @($revisarProperty.Value) } else { @() }
        }
    }
    catch {
        if ($_.Exception -is [UnauthorizedAccessException]) {
            throw
        }
        $failureResponse = $response
        if ($null -eq $failureResponse) {
            $failureResponse = Get-ExceptionResponse $_
        }
        $code = Get-ResponseCode $failureResponse
        if ($code -eq 401 -or $code -eq 403) {
            throw (New-Object System.UnauthorizedAccessException("credenciales rechazadas"))
        }
        throw "no se pudo consultar el manifiesto"
    }
    finally {
        if ($null -ne $reader) {
            $reader.Dispose()
        }
        if ($null -ne $response) {
            $response.Close()
        }
    }
}

function Get-RemoteLength {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Authorization
    )

    $response = $null
    try {
        $request = New-ClipperRequest -Uri $Uri -Method HEAD -Authorization $Authorization
        $response = $request.GetResponse()
        $code = Get-ResponseCode $response
        if ($code -lt 200 -or $code -ge 300) {
            Throw-HttpFailure -Response $response -Context "HEAD"
        }
        if ($response.ContentLength -lt 0) {
            throw "HEAD sin tamano"
        }
        return [long]$response.ContentLength
    }
    catch {
        if ($_.Exception -is [UnauthorizedAccessException]) {
            throw
        }
        $failureResponse = $response
        if ($null -eq $failureResponse) {
            $failureResponse = Get-ExceptionResponse $_
        }
        $code = Get-ResponseCode $failureResponse
        if ($code -eq 401 -or $code -eq 403) {
            throw (New-Object System.UnauthorizedAccessException("credenciales rechazadas"))
        }
        throw "HEAD no disponible"
    }
    finally {
        if ($null -ne $response) {
            $response.Close()
        }
    }
}

function Get-ClipUrl {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$Queue,
        [Parameter(Mandatory = $true)][string]$Name
    )

    return "{0}/files/out/{1}/{2}" -f $BaseUrl, $Queue, [Uri]::EscapeDataString($Name)
}

function Save-RemotePart {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Authorization,
        [Parameter(Mandatory = $true)][string]$Part,
        [Parameter(Mandatory = $true)][long]$RemoteLength
    )

    $response = $null
    $inputStream = $null
    $outputStream = $null
    $completed = $false
    try {
        $request = New-ClipperRequest -Uri $Uri -Method GET -Authorization $Authorization
        $response = $request.GetResponse()
        $code = Get-ResponseCode $response
        if ($code -lt 200 -or $code -ge 300) {
            Throw-HttpFailure -Response $response -Context "descarga"
        }

        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Open(
            $Part,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None
        )
        $inputStream.CopyTo($outputStream)
        $outputStream.Flush()
        $outputStream.Dispose()
        $outputStream = $null
        $inputStream.Dispose()
        $inputStream = $null
        $response.Close()
        $response = $null

        if ([long](Get-Item -LiteralPath $Part).Length -ne $RemoteLength) {
            throw "tamano descargado incorrecto"
        }
        $completed = $true
    }
    catch {
        if ($_.Exception -is [UnauthorizedAccessException]) {
            throw
        }
        $failureResponse = $response
        if ($null -eq $failureResponse) {
            $failureResponse = Get-ExceptionResponse $_
        }
        $code = Get-ResponseCode $failureResponse
        if ($code -eq 401 -or $code -eq 403) {
            throw (New-Object System.UnauthorizedAccessException("credenciales rechazadas"))
        }
        throw "descarga fallida"
    }
    finally {
        if ($null -ne $outputStream) {
            $outputStream.Dispose()
        }
        if ($null -ne $inputStream) {
            $inputStream.Dispose()
        }
        if ($null -ne $response) {
            $response.Close()
        }
        if (-not $completed -and (Test-Path -LiteralPath $Part)) {
            Remove-Item -LiteralPath $Part -Force -ErrorAction SilentlyContinue
        }
    }
}

function Publish-Part {
    param(
        [Parameter(Mandatory = $true)][string]$Part,
        [Parameter(Mandatory = $true)][string]$Target
    )

    $backup = Join-Path (Split-Path -Parent $Target) (".{0}.{1}.bak" -f
        (Split-Path -Leaf $Target), [Guid]::NewGuid().ToString("N"))
    try {
        if (Test-Path -LiteralPath $Target -PathType Leaf) {
            [IO.File]::Replace($Part, $Target, $backup, $true)
        }
        else {
            [IO.File]::Move($Part, $Target)
        }
    }
    finally {
        if (Test-Path -LiteralPath $backup) {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
    }
}

function Sync-OneClip {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$Authorization,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Queue,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if ($Queue -ne "LISTOS") {
        throw "solo se sincroniza LISTOS"
    }

    if (-not (Test-Path -LiteralPath $Destination)) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    }
    $target = Join-Path $Destination $Name
    $txtName = ([IO.Path]::GetFileNameWithoutExtension($Name) + ".txt")
    $txtTarget = Join-Path $Destination $txtName
    $remoteUri = Get-ClipUrl -BaseUrl $BaseUrl -Queue $Queue -Name $Name
    $remoteLength = Get-RemoteLength -Uri $remoteUri -Authorization $Authorization
    $txtUri = Get-ClipUrl -BaseUrl $BaseUrl -Queue $Queue -Name $txtName
    $txtLength = Get-RemoteLength -Uri $txtUri -Authorization $Authorization
    $needsMp4 = Test-NeedsDownload -Path $target -RemoteLength $remoteLength
    $needsTxt = Test-NeedsDownload -Path $txtTarget -RemoteLength $txtLength
    if (-not $needsMp4 -and -not $needsTxt) {
        return "omitted"
    }

    $mp4Part = Join-Path $Destination (".{0}.{1}.part" -f $Name, [Guid]::NewGuid().ToString("N"))
    $txtPart = Join-Path $Destination (".{0}.{1}.part" -f $txtName, [Guid]::NewGuid().ToString("N"))
    try {
        if ($needsTxt) {
            Save-RemotePart -Uri $txtUri -Authorization $Authorization -Part $txtPart -RemoteLength $txtLength
        }
        if ($needsMp4) {
            Save-RemotePart -Uri $remoteUri -Authorization $Authorization -Part $mp4Part -RemoteLength $remoteLength
        }
        # El TXT se publica primero: nunca queda un MP4 definitivo sin su ficha.
        if ($needsTxt) {
            Publish-Part -Part $txtPart -Target $txtTarget
        }
        if ($needsMp4) {
            Publish-Part -Part $mp4Part -Target $target
        }
        return "downloaded"
    }
    finally {
        foreach ($part in @($mp4Part, $txtPart)) {
            if (Test-Path -LiteralPath $part) {
                Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function New-SyncTaskDefinition {
    $service = New-Object -ComObject "Schedule.Service"
    $service.Connect()
    $definition = $service.NewTask(0)
    $definition.RegistrationInfo.Description = "Sincroniza los clips de Clipper cada 10 minutos."
    $definition.Settings.MultipleInstances = 2
    $definition.Settings.StartWhenAvailable = $true
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $definition.Principal.UserId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $definition.Principal.LogonType = 3
    $definition.Principal.RunLevel = 0

    $trigger = $definition.Triggers.Create(1)
    $trigger.StartBoundary = (Get-Date).AddMinutes(1).ToString("s")
    $trigger.Enabled = $true
    $trigger.Repetition.Interval = "PT10M"
    $trigger.Repetition.StopAtDurationEnd = $false

    $action = $definition.Actions.Create(0)
    $action.Path = $PowerShellPath
    $action.Arguments = '-NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $InstalledScriptPath
    $action.WorkingDirectory = $AppDirectory

    return $definition
}

function Register-SyncTask {
    $service = New-Object -ComObject "Schedule.Service"
    $service.Connect()
    $root = $service.GetFolder("\")
    $definition = New-SyncTaskDefinition
    $root.RegisterTaskDefinition($TaskName, $definition, 6, $null, $null, 3, $null) | Out-Null
}

function Remove-SyncTask {
    $service = New-Object -ComObject "Schedule.Service"
    $service.Connect()
    $root = $service.GetFolder("\")
    $existing = $null
    foreach ($task in $root.GetTasks(0)) {
        if ($task.Name -eq $TaskName) {
            $existing = $task
            break
        }
    }
    if ($null -ne $existing) {
        $root.DeleteTask($TaskName, 0)
    }
}

function Save-Configuration {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][string]$UserName,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][Security.SecureString]$Password
    )

    if (-not (Test-Path -LiteralPath $AppDirectory)) {
        New-Item -ItemType Directory -Path $AppDirectory -Force | Out-Null
    }
    $config = [ordered]@{
        url = $BaseUrl
        username = $UserName
        destination = $Destination
    }
    $config | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
    ConvertFrom-SecureString -SecureString $Password | Set-Content -LiteralPath $PasswordPath -Encoding ASCII
}

function Get-Configuration {
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $PasswordPath -PathType Leaf)) {
        throw "ClipperSync no esta instalado."
    }

    try {
        $config = ConvertFrom-Json (Get-Content -LiteralPath $ConfigPath -Raw)
    }
    catch {
        throw "configuracion invalida"
    }
    if ($null -eq $config.url -or $null -eq $config.username -or $null -eq $config.destination) {
        throw "configuracion incompleta"
    }

    return [pscustomobject]@{
        url = Normalize-BaseUrl ([string]$config.url)
        username = [string]$config.username
        destination = [IO.Path]::GetFullPath([string]$config.destination)
    }
}

function Get-ConfigurationPassword {
    try {
        $encrypted = (Get-Content -LiteralPath $PasswordPath -Raw).Trim()
        return ConvertTo-SecureString $encrypted
    }
    catch {
        throw "contrasena DPAPI invalida para este usuario de Windows"
    }
}

function Ensure-Destination {
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        $fullPath = [IO.Path]::GetFullPath($Path)
    }
    catch {
        throw "carpeta de destino invalida"
    }
    if (-not (Test-Path -LiteralPath $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
    }
    if (-not (Get-Item -LiteralPath $fullPath).PSIsContainer) {
        throw "la carpeta de destino no es una carpeta"
    }
    return $fullPath
}

function Select-Destination {
    $selected = $null
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object Windows.Forms.FolderBrowserDialog
        $dialog.Description = "Elige la carpeta donde guardar los clips de Clipper"
        $dialog.ShowNewFolderButton = $true
        if ($dialog.ShowDialog() -eq [Windows.Forms.DialogResult]::OK) {
            $selected = $dialog.SelectedPath
        }
        $dialog.Dispose()
    }
    catch {
        $selected = $null
    }

    if ([string]::IsNullOrWhiteSpace($selected)) {
        $selected = Read-Host "Ruta de la carpeta de clips"
    }
    return Ensure-Destination $selected
}

function Read-InstallUrl {
    while ($true) {
        $value = Read-Host "URL HTTPS base de Clipper"
        try {
            return Normalize-BaseUrl $value
        }
        catch {
            Write-Host "URL no valida: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

function Invoke-Synchronization {
    param(
        [Parameter(Mandatory = $true)][object]$Config,
        [Parameter(Mandatory = $true)][Security.SecureString]$Password
    )

    $mutex = New-Object System.Threading.Mutex($false, "Local\ClipperSync")
    $ownsMutex = $false
    $downloaded = 0
    $omitted = 0
    $failed = 0
    try {
        try {
            $ownsMutex = $mutex.WaitOne(0)
        }
        catch [Threading.AbandonedMutexException] {
            $ownsMutex = $true
        }
        if (-not $ownsMutex) {
            Write-SyncLog -Message "ejecucion omitida: ya hay otra sincronizacion activa"
            Write-Host "Ya hay una sincronizacion activa; se omite esta ejecucion."
            return
        }

        $authorization = Get-BasicAuthHeader -UserName $Config.username -Password $Password
        $manifest = Get-ClipManifest -BaseUrl $Config.url -Authorization $authorization
        foreach ($item in @($manifest.listos)) {
                $rawName = $null
                if ($null -ne $item) {
                    $nameProperty = $item.PSObject.Properties["nombre"]
                    if ($null -ne $nameProperty) {
                        $rawName = $nameProperty.Value
                    }
                }
                $name = Get-SafeClipName $rawName
                if ($null -eq $name) {
                    $failed++
                    Write-SyncLog -Level "WARN" -Message "nombre rechazado en LISTOS"
                    continue
                }
                try {
                    $result = Sync-OneClip `
                        -BaseUrl $Config.url `
                        -Authorization $authorization `
                        -Destination $Config.destination `
                        -Queue "LISTOS" `
                        -Name $name
                    if ($result -eq "downloaded") {
                        $downloaded++
                    }
                    else {
                        $omitted++
                    }
                }
                catch [UnauthorizedAccessException] {
                    throw
                }
                catch {
                    $failed++
                    Write-SyncLog -Level "WARN" -Message "fallo en LISTOS"
                }
        }
    }
    catch [UnauthorizedAccessException] {
        $failed++
        Write-SyncLog -Level "ERROR" -Message "credenciales rechazadas"
        Write-Host "Credenciales rechazadas; no se continuara este ciclo." -ForegroundColor Red
    }
    catch {
        $failed++
        Write-SyncLog -Level "ERROR" -Message "error de red o manifiesto"
        Write-Host "No se pudo completar la sincronizacion; se reintentara en el proximo ciclo." -ForegroundColor Yellow
    }
    finally {
        Write-SyncLog -Message ("resumen: descargados={0}; omitidos={1}; fallidos={2}" -f $downloaded, $omitted, $failed)
        Write-Host ("Sincronizacion: descargados={0}; omitidos={1}; fallidos={2}" -f $downloaded, $omitted, $failed)
        if ($ownsMutex) {
            try {
                $mutex.ReleaseMutex()
            }
            catch {
            }
        }
        $mutex.Dispose()
    }
}

function Install-ClipperSync {
    $baseUrl = Read-InstallUrl
    $userName = Read-Host "Usuario de Clipper"
    if ([string]::IsNullOrWhiteSpace($userName)) {
        throw "El usuario es obligatorio."
    }
    $password = Read-Host "Contrasena de Clipper" -AsSecureString
    $destination = Select-Destination

    $authorization = Get-BasicAuthHeader -UserName $userName -Password $password
    try {
        Get-ClipManifest -BaseUrl $baseUrl -Authorization $authorization | Out-Null
    }
    catch [UnauthorizedAccessException] {
        throw "credenciales rechazadas; no se creo la tarea"
    }
    catch {
        throw "no se pudo validar la conexion; no se creo la tarea"
    }

    if (-not (Test-Path -LiteralPath $AppDirectory)) {
        New-Item -ItemType Directory -Path $AppDirectory -Force | Out-Null
    }
    $sourceScript = [IO.Path]::GetFullPath($SourceScriptPath)
    $targetScript = [IO.Path]::GetFullPath($InstalledScriptPath)
    if ($sourceScript -ine $targetScript) {
        Copy-Item -LiteralPath $sourceScript -Destination $targetScript -Force
    }
    Save-Configuration -BaseUrl $baseUrl -UserName $userName -Destination $destination -Password $password

    $config = Get-Configuration
    Invoke-Synchronization -Config $config -Password $password
    Register-SyncTask
    Write-Host "ClipperSync instalado. La tarea se ejecuta cada 10 minutos." -ForegroundColor Green
}

function Remove-AppDirectorySafely {
    $localAppData = [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\')
    $expected = Join-Path $localAppData "ClipperSync"
    $target = [IO.Path]::GetFullPath($AppDirectory).TrimEnd('\')
    if ($target -ine $expected.TrimEnd('\')) {
        throw "ruta de desinstalacion inesperada"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

function Uninstall-ClipperSync {
    $answer = Read-Host "Esto elimina la tarea y la configuracion, pero conserva tus clips. Continuar? (S/N)"
    if ($answer -notmatch '^(?i:s(?:i)?|y(?:es)?)$') {
        Write-Host "Desinstalacion cancelada."
        return
    }
    Remove-SyncTask
    Remove-AppDirectorySafely
    Write-Host "ClipperSync desinstalado. La carpeta elegida y sus clips no se han tocado." -ForegroundColor Green
}

function Assert-SelfTest {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )
    if (-not $Condition) {
        throw ("SelfTest: {0}" -f $Message)
    }
}

function Invoke-SelfTest {
    Assert-SelfTest ((Normalize-BaseUrl "https://clipper.example/" ) -eq "https://clipper.example") "normalizacion HTTPS"
    Assert-SelfTest ((Normalize-BaseUrl "http://localhost:8080/" ) -eq "http://localhost:8080") "excepcion localhost"
    $httpRejected = $false
    try {
        Normalize-BaseUrl "http://clipper.example" | Out-Null
    }
    catch {
        $httpRejected = $true
    }
    Assert-SelfTest $httpRejected "HTTP remoto rechazado"

    Assert-SelfTest ((Get-SafeClipName "clip.mp4") -eq "clip.mp4") "nombre valido"
    Assert-SelfTest ($null -eq (Get-SafeClipName "..\clip.mp4")) "traversal rechazado"
    Assert-SelfTest ($null -eq (Get-SafeClipName "C:\clip.mp4")) "ruta absoluta rechazada"
    Assert-SelfTest ($null -eq (Get-SafeClipName "clip|.mp4")) "caracter invalido rechazado"
    Assert-SelfTest ((Get-ClipUrl -BaseUrl "https://clipper.example" -Queue "LISTOS" -Name "clip.mp4") -match "/files/out/LISTOS/") "ruta remota LISTOS"
    $sourceText = Get-Content -LiteralPath $PSCommandPath -Raw
    Assert-SelfTest ($sourceText -match 'foreach \(\$item in @\(\$manifest\.listos\)\)') "solo procesa LISTOS"
    Assert-SelfTest ($sourceText -match '\$txtUri|\$txtTarget') "exige TXT homonimo"
    Assert-SelfTest ($sourceText -match 'Publish-Part -Part \$txtPart' -and
                     $sourceText -match 'Publish-Part -Part \$mp4Part') "publica TXT antes que MP4"

    $temporary = Join-Path ([IO.Path]::GetTempPath()) ("ClipperSync-SelfTest-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temporary -Force | Out-Null
    try {
        $sample = Join-Path $temporary "sample.mp4"
        [IO.File]::WriteAllBytes($sample, [byte[]](1, 2, 3, 4))
        $sampleTxt = Join-Path $temporary "sample.txt"
        [IO.File]::WriteAllBytes($sampleTxt, [byte[]](1, 2))
        $flat = Ensure-Destination $temporary
        Assert-SelfTest ($flat -eq [IO.Path]::GetFullPath($temporary)) "destino en la raiz"
        Assert-SelfTest (-not (Test-Path -LiteralPath (Join-Path $temporary "LISTOS"))) "no crea subcarpeta LISTOS"
        Assert-SelfTest (-not (Test-Path -LiteralPath (Join-Path $temporary "REVISAR"))) "no crea subcarpeta REVISAR"
        Assert-SelfTest (-not (Test-NeedsDownload -Path $sample -RemoteLength 4)) "omitir por tamano"
        Assert-SelfTest (Test-NeedsDownload -Path $sample -RemoteLength 3) "reparar tamano distinto"
        Assert-SelfTest (Test-NeedsDownload -Path (Join-Path $temporary "missing.mp4") -RemoteLength 4) "descargar archivo ausente"
    }
    finally {
        Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
    }

    $definition = New-SyncTaskDefinition
    $xml = $definition.XmlText
    Assert-SelfTest ($xml -match '<Interval>PT10M</Interval>') "intervalo de 10 minutos"
    Assert-SelfTest ($xml -match '<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>') "politica IgnoreNew"
    Assert-SelfTest ($xml -match '(?i)-WindowStyle Hidden') "tarea oculta"
    Assert-SelfTest ($xml -notmatch '(?i)password|authorization|credential') "secretos fuera de la tarea"
    Write-Host "SelfTest OK: rutas, traversal, tamanos, DPAPI-compatible y tarea PT10M/IgnoreNew." -ForegroundColor Green
}

$selectedModes = @(
    if ($Install) { $true }
    if ($Uninstall) { $true }
    if ($SelfTest) { $true }
).Count
if ($selectedModes -gt 1) {
    Write-Error "Usa solo uno de -Install, -Uninstall o -SelfTest."
    exit 1
}

try {
    if ($SelfTest) {
        Invoke-SelfTest
        exit 0
    }
    if ($Install) {
        Install-ClipperSync
        exit 0
    }
    if ($Uninstall) {
        Uninstall-ClipperSync
        exit 0
    }

    $configuration = Get-Configuration
    $configurationPassword = Get-ConfigurationPassword
    Invoke-Synchronization -Config $configuration -Password $configurationPassword
    exit 0
}
catch {
    Write-Host ("ERROR: {0}" -f (ConvertTo-SafeLogText $_.Exception.Message)) -ForegroundColor Red
    if (-not $SelfTest) {
        Write-SyncLog -Level "ERROR" -Message "operacion fallida"
    }
    exit 1
}
