$ErrorActionPreference = "Stop"
$Installer = Join-Path $PSScriptRoot "install.py"
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $Installer @args
    exit $LASTEXITCODE
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $Installer @args
    exit $LASTEXITCODE
}
Write-Error "Python 3.10 or newer is required."
exit 2
