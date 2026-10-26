<#
.SYNOPSIS
Stops the supervisor recorded in -Logs, then stops the resource monitor only when this launcher owns its live PID
record. A monitor started independently is left running.

.PARAMETER Logs
The supervisor log directory. Defaults to logs\.
#>
param([string]$Logs = "")
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$Logs = if ($Logs) { [System.IO.Path]::GetFullPath($Logs) } else { Join-Path $root "logs" }
$monitorLogs = Join-Path $root "logs"
$monitorPidPath = Join-Path $monitorLogs "resource_monitor.pid"
$monitorOwnershipPath = Join-Path $monitorLogs "launcher.monitor.json"
. (Join-Path $PSScriptRoot "common.ps1")

function Test-MatchingRecord($left, $right) {
    return $null -ne $left -and $null -ne $right -and $left.pid -eq $right.pid -and $left.created -eq $right.created
}

& $python (Join-Path $root "launcher\supervisor.py") --stop --logs $Logs
if ($LASTEXITCODE -ne 0) {
    Write-Host "The supervisor did not stop; see $(Join-Path $Logs 'supervisor.status.json')."
    exit $LASTEXITCODE
}

$owned = Get-PidRecord $monitorOwnershipPath
$live = Get-PidRecord $monitorPidPath
if ((Test-MatchingRecord $owned $live) -and (Test-SameProcess $live)) {
    & $python (Join-Path $root "tools\resource_monitor.py") --stop
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The launcher-owned resource monitor did not stop; see $(Join-Path $monitorLogs 'resource_monitor.jsonl')."
        exit $LASTEXITCODE
    }
    Remove-Item -LiteralPath $monitorOwnershipPath
    Write-Host "Stopped launcher-owned resource monitor (PID $($live.pid))."
} else {
    Write-Host "Resource monitor was not started by this launcher; leaving it running."
}
