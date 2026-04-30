<#
Validate Windows host prerequisites for installing PawFlow Server.
Run from PowerShell before the Bash installer when WSL is not available yet.
#>

param(
    [int]$Port = 9090,
    [switch]$Source,
    [switch]$RequireSocket
)

$Failures = 0
$Warnings = 0

function Ok($msg) { Write-Host "OK    $msg" -ForegroundColor Green }
function Warn($msg) { $script:Warnings = $script:Warnings + 1; Write-Host "WARN  $msg" -ForegroundColor Yellow }
function Fail($msg) { $script:Failures = $script:Failures + 1; Write-Host "FAIL  $msg" -ForegroundColor Red }
function Info($msg) { Write-Host "INFO  $msg" -ForegroundColor Cyan }
function Has-Command($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Wsl-Command-Ok($command) {
    if (-not (Has-Command "wsl.exe")) { return $false }
    try {
        & wsl.exe sh -lc $command *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}
function Wsl-Command-Output($command) {
    if (-not (Has-Command "wsl.exe")) { return "" }
    try {
        return (& wsl.exe sh -lc $command 2>&1 | Out-String)
    } catch {
        return ""
    }
}

Info "Detected host: Windows / PowerShell"
Info "Recommended install path: WSL2 + Docker Desktop with WSL integration enabled, then run bash scripts/install-pawflow.sh inside the WSL distro."

if (Has-Command "wsl.exe") {
    Ok "wsl.exe found"
    try {
        $wslStatus = & wsl.exe --status 2>&1 | Out-String
        Ok "wsl.exe --status works"
    } catch {
        Warn "wsl.exe exists but 'wsl.exe --status' failed. Run 'wsl --install' or repair WSL from Windows Features."
    }
    try {
        $wslListRaw = & wsl.exe --list --verbose 2>&1 | Out-String
        $wslListExitCode = $LASTEXITCODE
        $wslList = $wslListRaw -replace "`0", ""
        $hasWsl2Distro = $false
        $wslList.TrimEnd().Split("`n") | ForEach-Object {
            $line = ($_ -replace "\s+", " ").Trim()
            if ($line -match "^(\*\s*)?\S+\s+\S+\s+2$") { $hasWsl2Distro = $true }
        }
        if ($wslListExitCode -eq 0 -and $hasWsl2Distro) {
            Ok "At least one WSL2 distro is installed"
        } elseif ($wslStatus -match "Default Version:\s*2" -or $wslStatus -match "WSL 2") {
            Ok "WSL2 default/version detected"
        } else {
            Warn "No WSL2 distro was clearly detected. Run: wsl --list --verbose ; then convert with: wsl --set-version <DistroName> 2"
        }
    } catch {
        Warn "Could not inspect WSL distro versions. Run: wsl --list --verbose"
    }
} else {
    Fail "WSL is not installed. Install it with: wsl --install ; reboot if requested; then install Ubuntu from Microsoft Store if needed."
}

$DockerReachable = $false
$DockerReachableFromWsl = $false
if (Has-Command "docker") {
    Ok "docker command found"
    try {
        $dockerInfo = & docker info 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) {
            $DockerReachable = $true
            Ok "Docker daemon reachable from Windows"
            if ($dockerInfo -match "OSType:\s*linux") {
                Ok "Docker is using Linux containers"
            } else {
                Warn "Docker may not be using Linux containers. Switch Docker Desktop to Linux containers."
            }
        } else {
            Warn "Windows docker CLI exists but daemon is not reachable from native PowerShell; checking WSL Docker integration."
        }
    } catch {
        Warn "Windows docker CLI exists but 'docker info' failed; checking WSL Docker integration."
    }
} else {
    Warn "Docker CLI not found in native PowerShell; checking WSL Docker integration."
}

if (-not $DockerReachable -and (Wsl-Command-Ok "docker info >/dev/null 2>&1")) {
    $DockerReachable = $true
    $DockerReachableFromWsl = $true
    Ok "Docker daemon reachable from WSL"
}

if (-not $DockerReachable) {
    Fail "Docker daemon is not reachable from Windows or WSL. Start Docker Desktop and enable WSL integration for your distro."
}

$dockerDesktopPath = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
if (Test-Path $dockerDesktopPath) {
    Ok "Docker Desktop installed at $dockerDesktopPath"
} else {
    Warn "Docker Desktop executable not found in Program Files. If Docker is installed elsewhere, this warning is harmless."
}

if ($Source) {
    if (Has-Command "git") {
        Ok "git command found"
    } else {
        Fail "git command not found. Required for source installs. Install Git for Windows or run source install from WSL."
    }
} elseif (Has-Command "git") {
    Ok "git command found (optional for image install)"
} else {
    Warn "git not found. Image install can continue, but source install will not work."
}

try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
    $listener.Start()
    $listener.Stop()
    Ok "Port $Port appears available"
} catch {
    Fail "Port $Port is already in use on 127.0.0.1. Choose another port or stop the conflicting service."
}

if ($DockerReachable) {
    try {
        if ($DockerReachableFromWsl) {
            $df = Wsl-Command-Output "docker system df"
            $dfExitOk = ($df.Trim().Length -gt 0)
        } else {
            $df = & docker system df 2>&1 | Out-String
            $dfExitOk = ($LASTEXITCODE -eq 0)
        }
        if ($dfExitOk) {
            Info "Docker disk usage:"
            $df.TrimEnd().Split("`n") | ForEach-Object { Info "  $_" }
        } else {
            Warn "Could not inspect Docker disk usage. Ensure Docker Desktop has enough disk space."
        }
    } catch {
        Warn "Could not inspect Docker disk usage. Ensure Docker Desktop has enough disk space."
    }
}

if ($RequireSocket) {
    if (Wsl-Command-Ok "test -S /var/run/docker.sock") {
        Ok "Docker socket is available inside WSL at /var/run/docker.sock"
    } else {
        Fail "Docker socket is not available inside WSL at /var/run/docker.sock. Enable Docker Desktop WSL integration for your distro."
    }
}

if ($Failures -gt 0) {
    Write-Host ""
    Fail "Doctor found $Failures blocking issue(s) and $Warnings warning(s)."
    exit 1
}

Write-Host ""
Ok "Doctor passed with $Warnings warning(s). Next: open WSL and run bash scripts/install-pawflow.sh."
