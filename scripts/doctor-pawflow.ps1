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

Info "Detected host: Windows / PowerShell"
Info "Recommended install path: WSL2 + Docker Desktop with WSL integration enabled, then run bash scripts/install-pawflow.sh inside the WSL distro."

if (Has-Command "wsl.exe") {
    Ok "wsl.exe found"
    try {
        $wslStatus = & wsl.exe --status 2>&1 | Out-String
        Ok "wsl.exe --status works"
        if ($wslStatus -notmatch "Default Version:\s*2" -and $wslStatus -notmatch "WSL 2") {
            Warn "WSL exists but WSL2 is not clearly the default. Run: wsl --set-default-version 2"
        }
    } catch {
        Warn "wsl.exe exists but 'wsl.exe --status' failed. Run 'wsl --install' or repair WSL from Windows Features."
    }
} else {
    Fail "WSL is not installed. Install it with: wsl --install ; reboot if requested; then install Ubuntu from Microsoft Store if needed."
}

if (Has-Command "docker") {
    Ok "docker command found"
    try {
        $dockerInfo = & docker info 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) {
            Ok "Docker daemon reachable"
            if ($dockerInfo -match "OSType:\s*linux") {
                Ok "Docker is using Linux containers"
            } else {
                Warn "Docker may not be using Linux containers. Switch Docker Desktop to Linux containers."
            }
        } else {
            Fail "Docker command exists but daemon is not reachable. Start Docker Desktop and wait until it is running."
        }
    } catch {
        Fail "Docker command exists but 'docker info' failed. Start Docker Desktop and verify WSL integration."
    }
} else {
    Fail "Docker CLI not found. Install Docker Desktop for Windows: https://www.docker.com/products/docker-desktop/"
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

if (Has-Command "docker") {
    try {
        $df = & docker system df 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) {
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
    Warn "--RequireSocket cannot validate /var/run/docker.sock from native Windows PowerShell. Run the Bash doctor inside WSL after enabling Docker Desktop WSL integration."
}

if ($Failures -gt 0) {
    Write-Host ""
    Fail "Doctor found $Failures blocking issue(s) and $Warnings warning(s)."
    exit 1
}

Write-Host ""
Ok "Doctor passed with $Warnings warning(s). Next: open WSL and run bash scripts/install-pawflow.sh."
