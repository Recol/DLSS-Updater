# DLSS Updater - MSI Build Script
# Builds a Windows MSI installer using PyInstaller (onedir) + Briefcase
# Usage: pwsh build_msi.ps1
# Requirements: uv, Python 3.14, WiX Toolset v3 (auto-installed by Briefcase if missing)

param(
    [switch]$SkipClean,
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Step($message) {
    Write-Host "`n=== $message ===" -ForegroundColor Cyan
}

function Write-Success($message) {
    Write-Host $message -ForegroundColor Green
}

function Write-Fail($message) {
    Write-Host $message -ForegroundColor Red
}

# Header
Write-Host ""
Write-Host "DLSS Updater - MSI Build" -ForegroundColor White
Write-Host "========================" -ForegroundColor White

# Step 1: Clean previous build artifacts
if (-not $SkipClean) {
    Write-Step "Cleaning previous build artifacts"
    if (Test-Path build) { Remove-Item -Recurse -Force build }
    if (Test-Path dist) { Remove-Item -Recurse -Force dist }
    Write-Success "Clean complete"
} else {
    Write-Host "`nSkipping clean (--SkipClean)" -ForegroundColor Yellow
}

# Step 2: Install dependencies
if (-not $SkipDeps) {
    Write-Step "Installing dependencies"
    uv sync --frozen --extra build
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to install dependencies"
        exit 1
    }
    Write-Success "Dependencies installed"
} else {
    Write-Host "`nSkipping dependency install (--SkipDeps)" -ForegroundColor Yellow
}

# Step 3: Build with PyInstaller (onedir, no UPX)
Write-Step "Building with PyInstaller (onedir mode, UPX disabled)"
$env:PYTHON_GIL = "0"
uv run pyinstaller DLSS_Updater_MSI.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Fail "PyInstaller build failed"
    exit 1
}

# Verify onedir output exists
if (-not (Test-Path "dist\DLSS_Updater\DLSS_Updater.exe")) {
    Write-Fail "Expected output not found: dist\DLSS_Updater\DLSS_Updater.exe"
    exit 1
}
$dirSize = (Get-ChildItem -Recurse "dist\DLSS_Updater" | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Success ("PyInstaller build complete ({0:N1} MB)" -f $dirSize)

# Step 4: Package MSI with Briefcase
Write-Step "Packaging MSI with Briefcase (WiX Toolset)"
uv run briefcase package windows --no-input
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Briefcase packaging failed"
    exit 1
}

# Step 5: Find and report the MSI
$msiFile = Get-ChildItem -Path dist -Filter "*.msi" -Recurse | Select-Object -First 1
if (-not $msiFile) {
    Write-Fail "No MSI file found in dist/"
    exit 1
}

# Generate hash
$hash = (Get-FileHash $msiFile.FullName -Algorithm SHA256).Hash
$msiSize = $msiFile.Length / 1MB

$stopwatch.Stop()

# Summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " BUILD SUCCESSFUL" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  MSI:    $($msiFile.FullName)"
Write-Host ("  Size:   {0:N1} MB" -f $msiSize)
Write-Host "  SHA256: $hash"
Write-Host ("  Time:   {0:N1}s" -f $stopwatch.Elapsed.TotalSeconds)
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
