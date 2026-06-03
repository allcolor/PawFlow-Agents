<#
Install or update PawFlow on Windows with Docker Desktop Linux containers.

Examples:
  powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -Port 19990 -PullImages
  powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -Version 1.0.0.prealpha.2 -Port 19990 -PullImages
  powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -CheckUpdates
  powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -SelfUpdate
#>

param(
    [string]$Version = $env:PAWFLOW_VERSION,
    [int]$Port = $(if ($env:PAWFLOW_PORT) { [int]$env:PAWFLOW_PORT } else { 0 }),
    [string]$HostName = $(if ($env:PAWFLOW_HOST) { $env:PAWFLOW_HOST } else { "0.0.0.0" }),
    [string]$PawFlowHome = $(if ($env:PAWFLOW_HOME) { $env:PAWFLOW_HOME } else { Join-Path $HOME "pawflow" }),
    [string]$Container = $(if ($env:PAWFLOW_CONTAINER) { $env:PAWFLOW_CONTAINER } else { "pawflow-server" }),
    [string]$Image = $env:PAWFLOW_IMAGE,
    [string]$ImageRepo = $(if ($env:PAWFLOW_IMAGE_REPO) { $env:PAWFLOW_IMAGE_REPO } else { "ghcr.io/allcolor/pawflow" }),
    [string]$RelayMinimalImage = $(if ($env:PAWFLOW_RELAY_MINIMAL_IMAGE) { $env:PAWFLOW_RELAY_MINIMAL_IMAGE } elseif ($env:PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE) { $env:PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE } else { "" }),
    [string]$RelayMinimalImageRepo = $(if ($env:PAWFLOW_RELAY_MINIMAL_IMAGE_REPO) { $env:PAWFLOW_RELAY_MINIMAL_IMAGE_REPO } else { "ghcr.io/allcolor/pawflow-relay-minimal" }),
    [string]$RelayDevImage = $(if ($env:PAWFLOW_RELAY_DEV_IMAGE) { $env:PAWFLOW_RELAY_DEV_IMAGE } else { "" }),
    [string]$RelayDevImageRepo = $(if ($env:PAWFLOW_RELAY_DEV_IMAGE_REPO) { $env:PAWFLOW_RELAY_DEV_IMAGE_REPO } else { "ghcr.io/allcolor/pawflow-relay-dev" }),
    [string]$RuntimeDir = $env:PAWFLOW_RUNTIME_DIR,
    [string]$DockerPlatform = $env:PAWFLOW_DOCKER_PLATFORM,
    [string]$CliLlmImage = $(if ($env:PAWFLOW_CLI_LLM_IMAGE) { $env:PAWFLOW_CLI_LLM_IMAGE } else { "pawflow-claude-code:latest" }),
    [switch]$PullImages,
    [switch]$NoStart,
    [switch]$SkipDoctor,
    [switch]$CheckUpdates,
    [switch]$SelfUpdate,
    [switch]$KeepOldImages
)

$ErrorActionPreference = "Stop"
$OldPawFlowImageIds = @()

function Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Ok($msg) { Write-Host $msg -ForegroundColor Green }
function Warn($msg) { Write-Host $msg -ForegroundColor Yellow }
function Fail($msg) { Write-Error $msg; exit 1 }
function Has-Command($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Normalize-Version($value) {
    if (-not $value) { return "" }
    return ($value -replace '^v', '')
}
function Image-Tag($image) {
    if (-not $image -or -not $image.Contains(':')) { return "" }
    return Normalize-Version($image.Substring($image.LastIndexOf(':') + 1))
}
function Resolve-LatestVersion {
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/allcolor/PawFlow-Agents/releases?per_page=20" -Headers @{ "User-Agent" = "pawflow-installer" }
    $release = @($releases | Where-Object { -not $_.draft } | Select-Object -First 1)[0]
    if (-not $release) { Fail "Could not find a published PawFlow release on GitHub." }
    return Normalize-Version($release.tag_name)
}
function Get-InstalledServerImage {
    if (-not (Has-Command "docker")) { return "" }
    $out = & docker inspect -f '{{.Config.Image}}' $Container 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return (($out | Out-String).Trim())
}
function Check-Updates {
    $latest = Resolve-LatestVersion
    $installedImage = Get-InstalledServerImage
    $installedVersion = Image-Tag $installedImage
    $selectedPort = if ($Port -gt 0) { [string]$Port } else { "PORT" }
    Write-Host "Latest PawFlow release: $latest"
    if ($installedImage) {
        Write-Host "Installed server image: $installedImage"
        Write-Host "Installed version: $installedVersion"
    } else {
        Write-Host "Installed server image: none detected for container '$Container'"
    }
    if ($installedVersion -and $installedVersion -eq $latest) {
        Write-Host "Server update: already on the latest release."
    } else {
        Write-Host "Server update available. Recommended command:"
        Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -Version $latest -Port $selectedPort -PullImages"
    }
    Write-Host "Installer refresh command:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/install-pawflow.ps1 -SelfUpdate"
}
function Self-UpdateInstaller {
    $latest = Resolve-LatestVersion
    $url = "https://github.com/allcolor/PawFlow-Agents/releases/download/$latest/pawflow-install-$latest.zip"
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("pawflow-installer-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp "pawflow-install.zip"
        Info "Downloading PawFlow installer $latest: $url"
        Invoke-WebRequest -Uri $url -OutFile $zip -Headers @{ "User-Agent" = "pawflow-installer" }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $scriptDir = Split-Path -Parent $MyInvocation.ScriptName
        foreach ($name in @("install-pawflow.sh", "install-pawflow.ps1")) {
            $src = Join-Path $tmp ("scripts\" + $name)
            if (Test-Path $src) {
                Copy-Item $src (Join-Path $scriptDir $name) -Force
                Ok "Updated $(Join-Path $scriptDir $name)"
            }
        }
        Ok "Installer scripts updated to release $latest. Rerun the installer command you wanted to execute."
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
function Maybe-LoginGhcr {
    $user = if ($env:PAWFLOW_GHCR_USER) { $env:PAWFLOW_GHCR_USER } else { $env:GHCR_USER }
    $token = if ($env:PAWFLOW_GHCR_TOKEN) { $env:PAWFLOW_GHCR_TOKEN } else { $env:GHCR_TOKEN }
    if (-not $token) { return }
    if (-not $user) { Fail "PAWFLOW_GHCR_TOKEN is set but PAWFLOW_GHCR_USER is missing." }
    Info "Logging in to GHCR as $user"
    $token | & docker login ghcr.io -u $user --password-stdin | Out-Null
}
function Pull-Image($image) {
    $args = @("pull")
    if ($DockerPlatform) { $args += @("--platform", $DockerPlatform) }
    $args += $image
    Info "Pulling image: $image"
    & docker @args
    if ($LASTEXITCODE -ne 0) { Fail "Failed to pull image: $image" }
}
function Runtime-DirForImage($image) {
    if ($RuntimeDir) { return $RuntimeDir }
    $tag = Image-Tag $image
    if (-not $tag) { $tag = "latest" }
    $safe = $tag -replace '[^A-Za-z0-9._-]', '_'
    return Join-Path $HOME ".pawflow\runtime\$safe"
}
function Extract-ImageArtifacts($image, $outDir) {
    Info "Extracting PawFlow runtime artifacts from image: $image -> $outDir"
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    $cid = (& docker create $image true).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $cid) { Fail "Could not create temporary container from $image" }
    try {
        foreach ($rel in @(
            "scripts/run-pawflow-docker.sh",
            "scripts/doctor-pawflow.sh",
            "scripts/doctor-pawflow.ps1",
            "scripts/install-pawflow.ps1",
            "docker/claude-code",
            "docker/pawflow_sdk",
            "tools/mcp_bridge.py",
            "pawflow_relay"
        )) {
            $dest = Join-Path $outDir ($rel -replace '/', [System.IO.Path]::DirectorySeparatorChar)
            New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
            if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
            & docker cp ("$cid:/app/$rel") $dest
            if ($LASTEXITCODE -ne 0) { Fail "Failed to extract $rel from $image" }
        }
    } finally {
        & docker rm -f $cid *> $null
    }
}
function Build-CliImage($repoDir) {
    $context = Join-Path $repoDir "docker\claude-code"
    $args = @("build")
    if ($DockerPlatform) { $args += @("--platform", $DockerPlatform) }
    $args += @("-t", $CliLlmImage, $context)
    Info "Building PawFlow CLI LLM image locally: $CliLlmImage"
    & docker @args
    if ($LASTEXITCODE -ne 0) { Fail "Failed to build CLI LLM image: $CliLlmImage" }
}
function Ensure-Dirs {
    foreach ($rel in @("data", "config", "certs", "logs")) {
        New-Item -ItemType Directory -Path (Join-Path $PawFlowHome $rel) -Force | Out-Null
    }
}
function Remove-ManagedRelayContainers {
    $names = @(& docker ps -a --format '{{.Names}}' | Where-Object { $_ -match '^(pawflow-relay-srv|pawflow-relay-min)' })
    if ($names.Count -eq 0) { return }
    Info "Removing managed PawFlow relay containers so they restart with current runtime code: $($names -join ', ')"
    Info "Relay home volumes and workspace directories are preserved."
    & docker rm -f @names | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Could not remove managed PawFlow relay containers" }
}
function Sync-PersistentRelayRuntime($repoDir) {
    $runtimeDir = Join-Path $PawFlowHome "data\runtime\relay_runtime\current"
    $tools = Join-Path $repoDir "tools"
    $relayPkg = Join-Path $repoDir "pawflow_relay"
    $sdk = Join-Path $repoDir "docker\pawflow_sdk\pawflow.py"
    if (-not (Test-Path $tools) -or -not (Test-Path $relayPkg) -or -not (Test-Path $sdk)) {
        Write-Warning "Cannot sync relay runtime from $repoDir; required runtime sources are missing."
        return
    }
    Info "Syncing relay runtime code into persistent data: $runtimeDir"
    if (Test-Path $runtimeDir) { Remove-Item $runtimeDir -Recurse -Force }
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    Copy-Item -Recurse -Force -Path (Join-Path $tools '*') -Destination $runtimeDir
    Copy-Item -Recurse -Force -Path $relayPkg -Destination (Join-Path $runtimeDir 'pawflow_relay')
    Copy-Item -Force -Path $sdk -Destination (Join-Path $runtimeDir 'pawflow.py')
}
function Run-ServerContainer($repoDir) {
    Ensure-Dirs
    Sync-PersistentRelayRuntime $repoDir
    $existing = & docker ps -a --format '{{.Names}}' | Where-Object { $_ -eq $Container }
    if ($existing) {
        Info "Container '$Container' already exists; recreating it with image $Image while keeping persistent volumes."
        Remove-ManagedRelayContainers
        & docker rm -f $Container | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "Could not remove existing container: $Container" }
    }
    $cliCheck = & docker run --rm --entrypoint sh $Image -lc 'command -v docker && docker --version' 2>&1 | Out-String
    if ($cliCheck -notmatch "Docker version") { Fail "Server image '$Image' does not contain a working Docker CLI.`n$cliCheck" }
    $bootstrapKey = if ($env:PAWFLOW_BOOTSTRAP_GATEWAY_KEY) { $env:PAWFLOW_BOOTSTRAP_GATEWAY_KEY } else { "RoyBetty" }
    $bootstrapLabel = if ($env:PAWFLOW_BOOTSTRAP_GATEWAY_KEY) { "custom value from PAWFLOW_BOOTSTRAP_GATEWAY_KEY" } else { "RoyBetty" }
    $args = @(
        "run", "-d",
        "--name", $Container,
        "--restart", "unless-stopped",
        "-p", "${HostName}:${Port}:${Port}",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", "$(Join-Path $PawFlowHome 'data'):/app/data",
        "-v", "$(Join-Path $PawFlowHome 'config'):/app/config",
        "-v", "$(Join-Path $PawFlowHome 'certs'):/app/certs",
        "-v", "$(Join-Path $PawFlowHome 'logs'):/app/logs",
        "-e", "PAWFLOW_APP_DIR=/app",
        "-e", "PAWFLOW_HOST_APP_DIR=$repoDir",
        "-e", "PAWFLOW_DATA_DIR=/app/data",
        "-e", "PAWFLOW_HOST_DATA_DIR=$(Join-Path $PawFlowHome 'data')",
        "-e", "PAWFLOW_SERVER_RELAY_IMAGE=$RelayDevImage",
        "-e", "PAWFLOW_SERVER_RELAY_MINIMAL_IMAGE=$RelayMinimalImage",
        "-e", "PAWFLOW_RUN_UID=1000",
        "-e", "PAWFLOW_RUN_GID=1000",
        "-e", "PAWFLOW_BOOTSTRAP_GATEWAY_KEY=$bootstrapKey",
        $Image,
        "python", "cli.py", "start", "--host", "0.0.0.0", "--port", [string]$Port
    )
    Info "Starting $Container from $Image"
    & docker @args
    if ($LASTEXITCODE -ne 0) { Fail "Failed to start PawFlow container." }
    Ok "PawFlow is starting at https://localhost:$Port"
    Write-Host "Initial bootstrap Private Gateway key: $bootstrapLabel"
    Write-Host "Follow logs: docker logs -f $Container"
}
function Cleanup-OldImages {
    if ($KeepOldImages) { return }
    $current = @($Image, $RelayMinimalImage, $RelayDevImage)
    $repos = @($ImageRepo, $RelayMinimalImageRepo, $RelayDevImageRepo)
    Info "Cleaning older PawFlow GHCR image tags not used by this install."
    $rows = & docker images --format '{{.Repository}}|{{.Tag}}'
    foreach ($row in $rows) {
        $parts = $row.Split('|')
        if ($parts.Count -ne 2) { continue }
        $repo = $parts[0]
        $tag = $parts[1]
        if (-not $repo -or $repo -eq '<none>' -or $tag -eq '<none>') { continue }
        if ($repos -notcontains $repo) { continue }
        $ref = "${repo}:${tag}"
        if ($current -contains $ref) { continue }
        Info "Removing old image tag: $ref"
        & docker rmi $ref *> $null
    }
}
function Capture-ExistingPawFlowImageIds {
    $script:OldPawFlowImageIds = @()
    $cliRepo = if ($CliLlmImage.Contains(':')) { $CliLlmImage.Substring(0, $CliLlmImage.LastIndexOf(':')) } else { $CliLlmImage }
    $repos = @($ImageRepo, $RelayMinimalImageRepo, $RelayDevImageRepo, $cliRepo)
    $rows = & docker images --format '{{.Repository}}|{{.ID}}'
    foreach ($row in $rows) {
        $parts = $row.Split('|')
        if ($parts.Count -ne 2) { continue }
        if ($repos -contains $parts[0]) { $script:OldPawFlowImageIds += $parts[1] }
    }
    $script:OldPawFlowImageIds = @($script:OldPawFlowImageIds | Sort-Object -Unique)
}
function Cleanup-RetaggedPawFlowImages {
    if ($KeepOldImages) { return }
    foreach ($oldId in $script:OldPawFlowImageIds) {
        if (-not $oldId) { continue }
        $exists = & docker image inspect $oldId 2>$null
        if ($LASTEXITCODE -ne 0) { continue }
        $tags = & docker image inspect -f '{{range .RepoTags}}{{.}}{{end}}' $oldId 2>$null
        if ($tags -and $tags -ne '<none>:<none>') { continue }
        Info "Removing old untagged PawFlow image id: $oldId"
        & docker rmi $oldId *> $null
    }
}


if ($SelfUpdate) { Self-UpdateInstaller; exit 0 }
if ($CheckUpdates) { Check-Updates; exit 0 }
if ($Port -le 0) { Fail "Choose a port with -Port PORT or PAWFLOW_PORT=PORT." }
if (-not (Has-Command "docker")) { Fail "Docker CLI not found. Install Docker Desktop for Windows." }
& docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker is installed but the daemon is not reachable. Start Docker Desktop, then rerun the installer." }

Maybe-LoginGhcr

$tag = if ($Version) { $Version } else { "latest" }
if (-not $Image) { $Image = "${ImageRepo}:${tag}" }
if (-not $RelayMinimalImage) { $RelayMinimalImage = "${RelayMinimalImageRepo}:${tag}" }
if (-not $RelayDevImage) { $RelayDevImage = "${RelayDevImageRepo}:${tag}" }

Capture-ExistingPawFlowImageIds

Info "Host: Windows / PowerShell"
Info "Version: $Version"
Info "Server image: $Image"
Info "Minimal relay image: $RelayMinimalImage"
Info "Full relay image: $RelayDevImage"
Info "CLI LLM image: $CliLlmImage (local build)"
if ($DockerPlatform) { Info "Docker platform: $DockerPlatform" }

Pull-Image $Image
Pull-Image $RelayMinimalImage
Pull-Image $RelayDevImage

$repoDir = Runtime-DirForImage $Image
Extract-ImageArtifacts $Image $repoDir
if (-not $SkipDoctor) {
    $doctor = Join-Path $repoDir "scripts\doctor-pawflow.ps1"
    if (Test-Path $doctor) { & $doctor -Port $Port }
}
Build-CliImage $repoDir

if ($NoStart) {
    Cleanup-OldImages
    Cleanup-RetaggedPawFlowImages
    Ok "Image preparation complete. Server start skipped because -NoStart was set."
    exit 0
}

Run-ServerContainer $repoDir
Cleanup-OldImages
Cleanup-RetaggedPawFlowImages
