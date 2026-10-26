<#
.SYNOPSIS
Stops JARVIS: asks the running supervisor to stop every program (the last started first), then stops the resource
monitor that start_all.ps1 requested.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

& $python (Join-Path $root "launcher\supervisor.py") --stop
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $python (Join-Path $root "tools\resource_monitor.py") --stop
exit $LASTEXITCODE
