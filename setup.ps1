$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $taskPython = (& py -3 -c 'import sys; print(sys.executable)')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $taskPython = (Get-Command python).Source
}
if (-not $taskPython) { throw 'Install Python 3.10+ (64-bit) from python.org, then run again.' }
& $taskPython -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
$envPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $envPython install_deps.py
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the error above.' }
Write-Host 'Setup complete. Open start.cmd.'
