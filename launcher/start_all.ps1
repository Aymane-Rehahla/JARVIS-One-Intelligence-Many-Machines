<#
.SYNOPSIS
Starts JARVIS with one click: the resource monitor (unless -NoMonitor) and supervisor run in the background. The
supervisor starts NATS, the LLM servers unless -NoLlm, the farm runtime, nodes, gateway and website unless -NoWebsite.

.PARAMETER NoLlm
Leave out the two llama.cpp servers. While the Phase 5 training runs, no LLM server may be started.

.PARAMETER Feed
The farm's brains: trained (default, the release's CTMs, decision 0021) or simulated (the snapshot's rule brains).

.PARAMETER NoWebsite
Do not start the website and do not open the browser.

.PARAMETER Ports
The ports.toml file for this run. Defaults to launcher\ports.toml.

.PARAMETER Logs
The supervisor log directory. Defaults to logs\.

.PARAMETER Only
A comma-separated list of supervisor programs to run, for example nats,farm,gateway.

.PARAMETER NoMonitor
Do not start or record a resource monitor. Intended for an isolated test while the real monitor is already running.

.PARAMETER VoiceWav
Rehearsals without a person: incubator-1's microphone is this recorded 16 kHz WAV file (its speakers stay real). The
supervisor is started through WMI, so an environment variable set in this shell would not reach the node.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File launcher\start_all.ps1 -NoLlm
#>
param(
    [switch]$NoLlm,
    [ValidateSet("trained", "simulated")]
    [string]$Feed = "trained",
    [switch]$NoWebsite,
    [string]$Ports = "",
    [string]$Logs = "",
    [string]$Only = "",
    [switch]$NoMonitor,
    [string]$VoiceWav = ""
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$supervisor = Join-Path $root "launcher\supervisor.py"
$resourceMonitor = Join-Path $root "tools\resource_monitor.py"
$Ports = if ($Ports) { [System.IO.Path]::GetFullPath($Ports) } else { Join-Path $root "launcher\ports.toml" }
$Logs = if ($Logs) { [System.IO.Path]::GetFullPath($Logs) } else { Join-Path $root "logs" }
$monitorLogs = Join-Path $root "logs"
$monitorPidPath = Join-Path $monitorLogs "resource_monitor.pid"
$monitorOwnershipPath = Join-Path $monitorLogs "launcher.monitor.json"
$portsText = Get-Content -LiteralPath $Ports -Raw
. (Join-Path $PSScriptRoot "common.ps1")

function Wait-LivePidRecord([string]$path, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $record = Get-PidRecord $path
        if (Test-SameProcess $record) { return $record }
        Start-Sleep -Milliseconds 100
    }
    return $null
}

function Start-ResourceMonitor {
    $existing = Get-PidRecord $monitorPidPath
    if (Test-SameProcess $existing) {
        Write-Host "Resource monitor already running (PID $($existing.pid)); leaving it unowned."
        return
    }
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = "`"$pythonw`" `"$resourceMonitor`""; CurrentDirectory = $root }
    if ($result.ReturnValue -ne 0) {
        throw "Could not start the resource monitor (WMI return value $($result.ReturnValue))."
    }
    $started = Wait-LivePidRecord $monitorPidPath 15
    if ($null -eq $started) {
        throw "The resource monitor did not write a live PID record; see $monitorLogs."
    }
    [ordered]@{ pid = $started.pid; created = $started.created } | ConvertTo-Json | Set-Content -LiteralPath $monitorOwnershipPath
    Write-Host "Started resource monitor (PID $($started.pid))."
}

function Get-Port([string]$name) {
    if ($portsText -match "(?m)^$name\s*=\s*(\d+)") { return [int]$Matches[1] }
    throw "$Ports has no '$name'"
}

function Show-SupervisorFailure([string]$logs) {
    Write-Host "The supervisor ended before readiness. Recent records:"
    foreach ($name in @("supervisor.jsonl", "supervisor.status.json")) {
        $path = Join-Path $logs $name
        Write-Host "--- $path (last 10 lines) ---"
        if (Test-Path -LiteralPath $path) {
            Get-Content -LiteralPath $path -Tail 10
        } else {
            Write-Host "not found"
        }
    }
}

function Wait-Http([string]$url, [int]$seconds, $supervisorRecord, [string]$logs) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-SameProcess $supervisorRecord)) {
            Show-SupervisorFailure $logs
            return $false
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $url
            if ($response.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pythonw)) {
    throw "Missing project Python: $pythonw"
}
if (-not (Test-Path -LiteralPath $resourceMonitor)) {
    throw "Missing resource monitor: $resourceMonitor"
}

$existingSupervisor = Get-PidRecord (Join-Path $Logs "supervisor.pid")
if (Test-SameProcess $existingSupervisor) {
    Write-Host "A supervisor is already running (PID $($existingSupervisor.pid)); not starting another one."
    exit 2
}

if ($NoMonitor) {
    Write-Host "Resource monitor left untouched (-NoMonitor)."
} else {
    Start-ResourceMonitor
}

$onlyNames = @($Only -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$arguments = @("`"$supervisor`"", "--ports", "`"$Ports`"", "--logs", "`"$Logs`"")
if ($NoLlm) { $arguments += "--no-llm" }
$arguments += "--feed"
$arguments += $Feed
if (-not $NoWebsite) { $arguments += "--website" }
if ($VoiceWav) {
    $arguments += "--voice-wav"
    $arguments += "`"$([System.IO.Path]::GetFullPath($VoiceWav))`""
}
if ($onlyNames.Count -gt 0) {
    $arguments += "--only"
    $arguments += ($onlyNames -join ",")
}
$commandLine = "`"$pythonw`" " + ($arguments -join " ")

# Started through WMI so the supervisor is not tied to this console and keeps running when it closes.
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $commandLine; CurrentDirectory = $root }
if ($result.ReturnValue -ne 0) { throw "Could not start the supervisor (WMI return value $($result.ReturnValue))." }
Write-Host "JARVIS supervisor started (PID $($result.ProcessId))."
$supervisorRecord = Wait-LivePidRecord (Join-Path $Logs "supervisor.pid") 15
if ($null -eq $supervisorRecord) {
    Show-SupervisorFailure $Logs
    exit 1
}

$startsGateway = $onlyNames.Count -eq 0 -or $onlyNames -contains "gateway"
if ($startsGateway) {
    $gateway = "http://127.0.0.1:$(Get-Port 'gateway')/health"
    $wait = if ($NoLlm) { 120 } else { 420 }
    Write-Host "Waiting for the gateway..."
    if (-not (Wait-Http $gateway $wait $supervisorRecord $Logs)) {
        Write-Host "The gateway did not answer in time; see $(Join-Path $Logs 'supervisor.jsonl') and $(Join-Path $Logs 'supervisor.status.json')."
        exit 1
    }
    Write-Host "Gateway ready: $gateway"
}

if (-not $NoWebsite) {
    $website = "http://127.0.0.1:$(Get-Port 'website')/"
    if (Wait-Http $website 120 $supervisorRecord $Logs) {
        Start-Process $website
        Write-Host "Website opened: $website"
    } else {
        Write-Host "The website did not answer in time; see $(Join-Path $Logs 'website.out')."
        exit 1
    }
}
Write-Host "Stop everything with: powershell -ExecutionPolicy Bypass -File launcher\stop_all.ps1"
