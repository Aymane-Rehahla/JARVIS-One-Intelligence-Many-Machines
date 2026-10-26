<#
.SYNOPSIS
Shared launcher helpers for reading PID records and proving the process identity recorded by the resource monitor.
Dot-source this file from a launcher that defines `$root` and `$python`; Test-SameProcess uses those caller variables.
#>

function Get-PidRecord([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $record = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $processId = [int]$record.pid
        $created = [Int64]$record.created
        if ($processId -le 0 -or $created -le 0) { return $null }
        return [PSCustomObject]@{ pid = $processId; created = $created }
    } catch {
        return $null
    }
}

function Test-SameProcess($record) {
    if ($null -eq $record) { return $false }
    $tools = Join-Path $root "tools"
    $code = "import sys; sys.path.insert(0, sys.argv[1]); from resource_monitor import same_process; print('1' if same_process(int(sys.argv[2]), int(sys.argv[3])) else '0')"
    $result = & $python -c $code $tools $record.pid $record.created
    return $LASTEXITCODE -eq 0 -and $result -eq "1"
}
