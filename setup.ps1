<#
.SYNOPSIS
Sets JARVIS up on a Windows PC: Python environment, models, programs, website. Run it once, then launcher\start_all.ps1.

.DESCRIPTION
1. Finds Python 3.12 (installs it with winget when it is missing).
2. Creates .venv and installs the exact, hashed Python packages from requirements.lock.
3. Runs tools\download.py: the model weights (about 3.8 GB), llama.cpp, the NATS server and a portable Node.js, every
   file checked against its SHA-256. Running it again resumes and skips what is already in place.
4. Adds the 3D farm models: a JARVIS-farm-assets.zip beside the project, -Assets, or the GitHub release.
5. Installs the website's packages (npm ci).

.PARAMETER Assets
The 3D farm models: a local JARVIS-farm-assets.zip or an https URL (default: the GitHub release).
#>
param([string]$Assets = "")

# The 3D farm models (too large for git), attached to the project's GitHub release
$AssetsRelease = "https://github.com/Aymane-Rehahla/JARVIS-One-Intelligence-Many-Machines/releases/download/v1.0/JARVIS-farm-assets.zip"

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

Step "Python 3.12"
$py = $null
try { & py -3.12 -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-3.12") } } catch {}
if (-not $py) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.12 is missing and winget is not available: install it from https://www.python.org/downloads/ and run setup.ps1 again."
    }
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    $py = @("py", "-3.12")
}

Step "Python environment (.venv)"
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { & $py[0] $py[1] -m venv .venv; if ($LASTEXITCODE) { throw "could not create .venv" } }
& $python -m pip install --upgrade pip --quiet
& $python -m pip install --require-hashes -r requirements.lock
if ($LASTEXITCODE) { throw "pip install failed" }

Step "Models and programs (tools\download.py)"
if (-not $Assets) {
    foreach ($candidate in @("JARVIS-farm-assets.zip", "..\JARVIS-farm-assets.zip")) {
        if (Test-Path (Join-Path $root $candidate)) { $Assets = (Resolve-Path (Join-Path $root $candidate)).Path; break }
    }
}
& $python tools\download.py
if ($LASTEXITCODE) { throw "the download did not finish: run setup.ps1 again to resume" }
$ErrorActionPreference = "Continue"                  # the check's own output is not an error of this script
& $python tools\farm_assets.py check *> $null
$assetsMissing = $LASTEXITCODE -ne 0
$ErrorActionPreference = "Stop"
if ($assetsMissing -or $Assets) {
    if (-not $Assets) { $Assets = $AssetsRelease }
    & $python tools\download.py --assets $Assets
    if ($LASTEXITCODE) {
        Write-Host "The 3D farm models could not be added: everything works, but the 3D farm view stays empty." -ForegroundColor Yellow
        Write-Host "Add them later with: .\setup.ps1 -Assets <JARVIS-farm-assets.zip or its URL>" -ForegroundColor Yellow
    }
}

Step "Website packages (npm ci)"
$node = Join-Path $root "third_party\node\node-v24.22.1-win-x64"
$env:PATH = "$node;$env:PATH"
Push-Location (Join-Path $root "interface\web")
try { & (Join-Path $node "npm.cmd") ci --no-audit --no-fund; if ($LASTEXITCODE) { throw "npm ci failed" } }
finally { Pop-Location }

Step "Done"
Write-Host "Start JARVIS:  .\launcher\start_all.ps1 -NoMonitor" -ForegroundColor Green
Write-Host "Then open http://127.0.0.1:5173 , create an account and follow the short tour."
Write-Host "Stop it:       .\launcher\stop_all.ps1"
