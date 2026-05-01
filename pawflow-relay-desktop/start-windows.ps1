param(
    [switch]$Install,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-NpmStart {
    $doInstall = $Install -or $Clean
    if ($doInstall) {
        if (Test-Path "node_modules") {
            Write-Host "Removing stale node_modules..." -ForegroundColor DarkCyan
            Remove-Item -Recurse -Force "node_modules"
        }
        npm install
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    npm start
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Get-WslUncInfo([string]$Path) {
    if ($Path -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.+)$') {
        return @{
            Distro = $Matches[1]
            LinuxPath = '/' + ($Matches[2] -replace '\\', '/')
        }
    }
    return $null
}

function Clear-WslNodeModules([string]$Path) {
    $wsl = Get-WslUncInfo $Path
    if ($null -eq $wsl) { return $false }
    Write-Host "Cleaning stale node_modules through WSL..." -ForegroundColor DarkCyan
    & wsl.exe -d $wsl.Distro -- rm -rf -- "$($wsl.LinuxPath)/node_modules"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    return $true
}

function Invoke-NpmStartFromUnc {
    $doInstall = $Install -or $Clean
    $cleanedWithWsl = $false
    if ($doInstall) {
        $cleanedWithWsl = Clear-WslNodeModules $scriptDir
    }

    $cmdPath = Join-Path $env:TEMP ("pawflow-relay-desktop-" + [Guid]::NewGuid().ToString("N") + ".cmd")
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("@echo off")
    $lines.Add("pushd `"$scriptDir`" || exit /b 1")
    if ($doInstall) {
        if (-not $cleanedWithWsl) {
            $lines.Add("if exist node_modules rmdir /s /q node_modules")
            $lines.Add("if exist node_modules (echo Failed to remove stale node_modules. Close shells/editors using it, then retry. & popd & exit /b 1)")
        }
        $lines.Add("npm install || (set _pf_rc=%errorlevel% & popd & exit /b %_pf_rc%)")
    }
    $lines.Add("npm start")
    $lines.Add("set _pf_rc=%errorlevel%")
    $lines.Add("popd")
    $lines.Add("exit /b %_pf_rc%")
    Set-Content -Path $cmdPath -Value $lines -Encoding ASCII
    Push-Location $env:TEMP
    try {
        & cmd.exe /d /c "`"$cmdPath`""
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
        Remove-Item -Force $cmdPath -ErrorAction SilentlyContinue
    }
}

if ($scriptDir.StartsWith("\\")) {
    Write-Host "PawFlow Relay Desktop: UNC path detected." -ForegroundColor Cyan
    Write-Host "Using WSL cleanup plus cmd.exe pushd because npm/electron scripts cannot run from UNC cwd." -ForegroundColor DarkCyan
    Invoke-NpmStartFromUnc
    exit 0
}

Push-Location $scriptDir
try {
    Invoke-NpmStart
} finally {
    Pop-Location
}
