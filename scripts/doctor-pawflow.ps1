<#
Validate Windows host prerequisites for installing PawFlow and its Docker runtimes.
Run from PowerShell before the Bash installer when using native Windows or WSL.
#>

param(
    [int]$Port = 19990,
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
Info "Supported install paths: native Windows with Docker Desktop Linux containers, or WSL2 with Docker Desktop WSL integration."

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
    Warn "WSL is not installed. Native Windows install can continue if Docker Desktop is reachable from this shell. Install WSL only for WSL-based installs."
}

$DockerReachable = $false
$DockerReachableFromWsl = $false
if (Wsl-Command-Ok "docker info >/dev/null 2>&1") {
    $DockerReachable = $true
    $DockerReachableFromWsl = $true
    Ok "Docker daemon reachable from WSL"
} elseif (Has-Command "docker") {
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
            Fail "Docker command exists but daemon is not reachable from Windows or WSL. Start Docker Desktop. Enable WSL integration only for WSL-based installs."
        }
    } catch {
        Fail "Docker command exists but 'docker info' failed and WSL Docker is not reachable. Start Docker Desktop."
    }
} else {
    Fail "Docker CLI not found in native PowerShell or WSL. Install Docker Desktop for Windows."
}

if (-not $DockerReachable) {
    Fail "Docker daemon is not reachable from Windows or WSL. Start Docker Desktop."
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

$PortInUseFromWindows = $false
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    try {
        $tcpListeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        if ($tcpListeners.Count -gt 0) { $PortInUseFromWindows = $true }
    } catch {
        Warn "Could not inspect Windows TCP listeners with Get-NetTCPConnection; falling back to bind probe."
    }
}

$PortInUseFromWsl = $false
if (Has-Command "wsl.exe") {
    $PortInUseFromWsl = Wsl-Command-Ok "python3 - <<'PY'
import socket, sys
port = int('$Port')
for host in ('127.0.0.1', '0.0.0.0'):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
    except OSError:
        sys.exit(0)
    finally:
        s.close()
sys.exit(1)
PY"
}

if ($PortInUseFromWindows) {
    Fail "Port $Port is already in use on Windows. Choose another port or stop the conflicting PawFlow/Docker service."
} elseif ($PortInUseFromWsl) {
    Fail "Port $Port is already in use inside WSL. Choose another port or stop the conflicting PawFlow/Docker service."
} else {
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("0.0.0.0"), $Port)
        $listener.Server.ExclusiveAddressUse = $true
        $listener.Start()
        $listener.Stop()
        Ok "Port $Port appears available"
    } catch {
        Fail "Port $Port is already in use or unavailable on Windows. Choose another port or stop the conflicting service."
    }
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
Ok "Doctor passed with $Warnings warning(s). Next: run bash scripts/install-pawflow.sh from Git Bash/native Bash or from WSL."
