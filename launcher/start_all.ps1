<#
.SYNOPSIS
Starts JARVIS with one click: the resource monitor and supervisor run in the background. The supervisor starts every
program in order (NATS, the two LLM servers, the farm runtime, the four nodes, the gateway and the website); then the
website opens in the default browser.

.PARAMETER NoLlm
Leave out the two llama.cpp servers. While the Phase 5 training runs, no LLM server may be started (owner, 24 Oct):
use this switch until the owner says the training is done.

.PARAMETER NoWebsite
Do not start the website and do not open the browser.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File launcher\start_all.ps1 -NoLlm
#>
param([switch]$NoLlm, [switch]$NoWebsite)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$supervisor = Join-Path $root "launcher\supervisor.py"
$resourceMonitor = Join-Path $root "tools\resource_monitor.py"
$portsText = Get-Content (Join-Path $root "launcher\ports.toml") -Raw

function Get-Port([string]$name) {
    if ($portsText -match "(?m)^$name\s*=\s*(\d+)") { return [int]$Matches[1] }
    throw "launcher\ports.toml has no '$name'"
}

function Wait-Http([string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $url
            if ($response.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}

if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "Missing project Python: $pythonw"
}
if (-not (Test-Path -LiteralPath $resourceMonitor)) {
    throw "Missing resource monitor: $resourceMonitor"
}

$monitorCommandLine = "`"$pythonw`" `"$resourceMonitor`""
$monitorResult = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $monitorCommandLine; CurrentDirectory = $root }
if ($monitorResult.ReturnValue -ne 0) {
    throw "Could not start the resource monitor (WMI return value $($monitorResult.ReturnValue))."
}
Write-Host "Requested resource monitor (PID $($monitorResult.ProcessId))."

$arguments = @("`"$supervisor`"")
if ($NoLlm) { $arguments += "--no-llm" }
if (-not $NoWebsite) { $arguments += "--website" }
$commandLine = "`"$pythonw`" " + ($arguments -join " ")

# Started through WMI so the supervisor is not tied to this console and keeps running when it closes.
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $commandLine; CurrentDirectory = $root }
if ($result.ReturnValue -ne 0) { throw "could not start the supervisor (WMI return value $($result.ReturnValue))" }
Write-Host "JARVIS supervisor started (pid $($result.ProcessId)). Waiting for the gateway..."

$gateway = "http://127.0.0.1:$(Get-Port 'gateway')/health"
$wait = if ($NoLlm) { 120 } else { 420 }
if (-not (Wait-Http $gateway $wait)) {
    Write-Host "The gateway did not answer in time; see logs\supervisor.jsonl and logs\supervisor.status.json."
    exit 1
}
Write-Host "Gateway ready: $gateway"

if (-not $NoWebsite) {
    $website = "http://127.0.0.1:$(Get-Port 'website')/"
    if (Wait-Http $website 120) {
        Start-Process $website
        Write-Host "Website opened: $website"
    } else {
        Write-Host "The website did not answer in time; see logs\website.out."
        exit 1
    }
}
Write-Host "Stop everything with: powershell -ExecutionPolicy Bypass -File launcher\stop_all.ps1"
