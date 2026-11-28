<#
.SYNOPSIS
    PSYCHO-C.A.R.L.A. — Windows 11 installer.

.DESCRIPTION
    Creates a virtual environment, installs PSYCHO-C.A.R.L.A. (optionally with
    the CPU-only voice stack), writes a starter configuration, and prints next steps.

.PARAMETER Voice
    Install the CPU-only voice stack (Kokoro / Piper / faster-whisper / openWakeWord).
    Requires eSpeak NG for Kokoro — see https://github.com/hexgrad/kokoro

.PARAMETER Tier
    Provider for fast_tier in the starter config. Defaults to 'native_gguf' (C.A.R.L.A.'s own llama.cpp engine, no Ollama). Others: openrouter, openai_compat.

.PARAMETER Model
    The model identifier for the chosen tier.

.EXAMPLE
    .\install.ps1 -Voice

.EXAMPLE
    .\install.ps1 -Tier openai_compat -Model gpt-4o
#>
[CmdletBinding()]
param(
    [switch]$Voice,
    [string]$Tier = "native_gguf",
    [string]$Model = "",
    [string]$PythonVersion = "3.11",
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

function Write-Step  { param($m) Write-Host "`n[CARLA] $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Warn2 { param($m) Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err   { param($m) Write-Host "  [err]  $m" -ForegroundColor Red }

Write-Host ""
Write-Host "   ___                       _____ ____  _     _____" -ForegroundColor Cyan
Write-Host "  / _ \ _ __  ___ _   _ _   |  ___|  _ \| |   |_   _| ___  _ __ __ _  ___" -ForegroundColor Cyan
Write-Host " | | | | '_ \/ -_) ' \ | | | |_   | | | | |    | | |/ _ \/ '__/ _` |/ _ \" -ForegroundColor Cyan
Write-Host " | |_| | |_) \___|\_||_||_| |___| |_| |_|_| |_| |_|_|\__\_\| |_| \__,_|\__/" -ForegroundColor Cyan
Write-Host "  \___/| .__/" -ForegroundColor Cyan
Write-Host "       |_|            P S Y C H O - F . R . I . D . A . Y ." -ForegroundColor Cyan
Write-Host ""

# --- 1. Locate Python -------------------------------------------------------
Write-Step "Locating Python $PythonVersion"

$python = $null
$candidates = @("py", "python")
foreach ($cand in $candidates) {
    $exe = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $exe) { continue }
    try {
        $ver = & $exe --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 11) {
                if ($cand -eq "py") {
                    $python = @("py", "-$PythonVersion")
                } else {
                    $python = @("python")
                }
                Write-Ok "Found $ver"
                break
            }
        }
    } catch { }
}
if (-not $python) {
    Write-Err "Python 3.11+ not found. Install from https://www.python.org/downloads/windows/"
    exit 1
}

# --- 2. Project directory ----------------------------------------------------
if (-not $InstallDir) {
    $InstallDir = Join-Path $PSScriptRoot ""
}
Set-Location $InstallDir
Write-Step "Working in $InstallDir"

# --- 3. Virtual environment --------------------------------------------------
$venvPath = Join-Path $InstallDir ".venv"
if (-not (Test-Path $venvPath)) {
    Write-Step "Creating virtual environment"
    & $python[0] @($python[1..($python.Length-1)]) -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Err "venv creation failed."
        exit 1
    }
    Write-Ok "Created .venv"
} else {
    Write-Ok "Reusing existing .venv"
}

$activate = Join-Path $venvPath "Scripts\Activate.ps1"
$pip = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $pip)) { $pip = Join-Path $venvPath "Scripts\pip.exe" }

# --- 4. Install --------------------------------------------------------------
Write-Step "Installing PSYCHO-C.A.R.L.A."
& $pip install --upgrade pip --quiet
& $pip install -e .
if ($LASTEXITCODE -ne 0) { Write-Err "Base install failed."; exit 1 }
Write-Ok "Core package installed"

if ($Voice) {
    Write-Step "Installing the CPU-only voice stack"
    Write-Warn2 "Kokoro needs eSpeak NG. Install with: winget install --id eSpeak-NG.eSpeak-NG"
    & $pip install -e ".[voice]"
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Voice extras failed — the core install is still fine."
    } else {
        Write-Ok "Voice stack installed (Kokoro, Piper, faster-whisper, openWakeWord)"
    }
}

# --- 5. Configuration --------------------------------------------------------
$stateDir = Join-Path $env:LOCALAPPDATA "PsychoCarla"
$cfgPath  = Join-Path $stateDir "psycho_carla.toml"
Write-Step "Writing configuration to $cfgPath"

& $pip -c @"
from pathlib import Path
from psycho_carla.config_template import render_default_config
p = Path(r'$cfgPath')
p.parent.mkdir(parents=True, exist_ok=True)
text = render_default_config()
text = text.replace('provider       = "native_gguf"\nmodel          = ""',
                    'provider       = "$Tier"\nmodel          = "$Model"', 1)
p.write_text(text, encoding='utf-8')
print('wrote', p)
"@

if (-not (Test-Path $cfgPath)) {
    Write-Warn2 "Could not write the config — run 'carla init --force' manually."
}

# --- 6. Verify ---------------------------------------------------------------
Write-Step "Verifying installation"
& $pip -m psycho_carla.cli.main doctor

Write-Host ""
Write-Host "  ══════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "   PSYCHO-C.A.R.L.A. is installed." -ForegroundColor Cyan
Write-Host "  ══════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "   Activate:   .\.venv\Scripts\Activate.ps1" -ForegroundColor White
Write-Host "   Diagnose:   carla doctor" -ForegroundColor White
Write-Host "   Launch:     carla serve" -ForegroundColor White
Write-Host "   Console:    http://127.0.0.1:8787" -ForegroundColor White
Write-Host "   Ask:        carla ask 'What can you do?'" -ForegroundColor White
Write-Host ""
Write-Host "   Config:     $cfgPath" -ForegroundColor DarkGray
Write-Host ""
