# DLSS Updater - MSI Build Script
# Builds a Windows MSI installer using PyInstaller (onedir) + Briefcase
# Usage: pwsh build_msi.ps1
# Requirements: uv, Python 3.14.3 free-threaded (per .python-version), WiX Toolset v3 (auto-installed by Briefcase if missing)

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

# Step 4b: Inject AppUserModelID into the Start Menu shortcut and rebuild the MSI.
#
# Why: Flet 0.84 spawns flet.exe as a child process that owns the GUI window. When
#      users pin from the taskbar, Windows groups the window under flet.exe unless
#      the launcher shortcut and the window itself declare a matching AUMID.
#      Setting System.AppUserModel.ID on the Start Menu .lnk lets Windows associate
#      taskbar pins with DLSS Updater (a runtime Python fix handles the live window).
#
# Why here: Briefcase treats this app as an "external package" (external_package_path
#      in pyproject.toml), so `briefcase package windows` *always* regenerates the
#      .wxs from its template via create_command (see briefcase/commands/package.py
#      lines 85-101). Editing the .wxs in-place before the package step gets
#      overwritten. We therefore patch the freshly-generated .wxs AFTER Briefcase
#      has produced the MSI, then re-run wix.exe build to overwrite that MSI.
Write-Step "Injecting AppUserModelID into Start Menu shortcut"
$wxsFile = "build\dlss_updater\windows\app\dlss_updater.wxs"
if (-not (Test-Path $wxsFile)) {
    Write-Fail "WiX source not found at $wxsFile - cannot inject AUMID"
    exit 1
}

# Load the WiX file as XML (preserves the v4 WiX namespace on child elements).
[xml]$wxs = Get-Content -Path $wxsFile -Raw
$wixNs = "http://wixtoolset.org/schemas/v4/wxs"
$nsMgr = New-Object System.Xml.XmlNamespaceManager($wxs.NameTable)
$nsMgr.AddNamespace("w", $wixNs)

$shortcutNode = $wxs.SelectSingleNode("//w:Shortcut[@Id='ApplicationShortcut1']", $nsMgr)
if (-not $shortcutNode) {
    Write-Fail "Could not locate <Shortcut Id='ApplicationShortcut1'> in $wxsFile"
    exit 1
}

# Only add ShortcutProperty if it isn't already present (idempotent patch).
$existingProp = $shortcutNode.SelectSingleNode("w:ShortcutProperty[@Key='System.AppUserModel.ID']", $nsMgr)
if (-not $existingProp) {
    $propNode = $wxs.CreateElement("ShortcutProperty", $wixNs)
    $propNode.SetAttribute("Key", "System.AppUserModel.ID")
    $propNode.SetAttribute("Value", "io.github.recol.DLSSUpdater")
    [void]$shortcutNode.AppendChild($propNode)
    $wxs.Save((Resolve-Path $wxsFile))
    Write-Success "Injected <ShortcutProperty System.AppUserModel.ID='io.github.recol.DLSSUpdater'/>"
} else {
    Write-Host "ShortcutProperty already present - skipping injection" -ForegroundColor Yellow
}

# Locate wix.exe. Briefcase's WiX integration installs it under the user's
# BeeWare tool cache. Layout (see briefcase/integrations/wix.py):
#   %LOCALAPPDATA%\BeeWare\briefcase\Cache\tools\wix\PFiles64\WiX Toolset vX.Y\bin\wix.exe
$wixCacheRoot = Join-Path $env:LOCALAPPDATA "BeeWare\briefcase\Cache\tools\wix\PFiles64"
$wixExe = $null
if (Test-Path $wixCacheRoot) {
    $wixExe = Get-ChildItem -Path $wixCacheRoot -Recurse -Filter "wix.exe" -ErrorAction SilentlyContinue |
              Select-Object -First 1 -ExpandProperty FullName
}
# Fallbacks: PATH, or a system-wide install.
if (-not $wixExe) {
    $wixExe = (Get-Command wix.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
}

if (-not $wixExe) {
    Write-Fail "wix.exe not found - cannot rebuild MSI with AUMID patch"
    Write-Host "Searched: $wixCacheRoot and PATH" -ForegroundColor Yellow
    exit 1
}
Write-Host "Using wix.exe at: $wixExe" -ForegroundColor DarkGray

# Resolve the UI extension path (WixToolset.UI.wixext.dll) the same way Briefcase
# does in _package_msi. wix_home is two levels above wix.exe's bin folder
# (wix_home/PFiles64/WiX Toolset vX.Y/bin/wix.exe -> wix_home).
$wixHome = (Get-Item $wixExe).Directory.Parent.Parent.Parent.FullName
$wixVersionOutput = & $wixExe --version 2>$null
# Version output is like "6.0.1+abc123" - strip build metadata.
$wixVersion = ($wixVersionOutput -split '\+')[0].Trim()
$wixVersionParts = $wixVersion -split '\.'
$wixMajor = $wixVersionParts[0]
$uiExtDll = Join-Path $wixHome "CFiles64\WixToolset\extensions\WixToolset.UI.wixext\$wixVersion\wixext$wixMajor\WixToolset.UI.wixext.dll"
if (-not (Test-Path $uiExtDll)) {
    Write-Fail "WiX UI extension not found at: $uiExtDll"
    exit 1
}

# Recompile the patched WiX. Arguments mirror briefcase's _package_msi (see
# briefcase/platforms/windows/__init__.py lines 565-587) so output is identical
# except for the injected ShortcutProperty.
$bundleDir = "build\dlss_updater\windows\app"
$patchedMsi = Join-Path $bundleDir "DLSS_Updater-patched.msi"
Write-Step "Rebuilding MSI from patched WiX"
Push-Location $bundleDir
try {
    & $wixExe build `
        -ext $uiExtDll `
        -arch x64 `
        "dlss_updater.wxs" `
        -loc "unicode.wxl" `
        -pdbtype none `
        -o "DLSS_Updater-patched.msi"
    $wixExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($wixExitCode -ne 0) {
    Write-Fail "wix.exe build failed for patched WiX (exit $wixExitCode)"
    exit 1
}

# Replace Briefcase's MSI in dist/ with our patched build.
$originalMsi = Get-ChildItem -Path dist -Filter "*.msi" -Recurse | Select-Object -First 1
if (-not $originalMsi) {
    Write-Fail "Briefcase MSI not found in dist/ - cannot swap with patched build"
    exit 1
}
Move-Item -Path $patchedMsi -Destination $originalMsi.FullName -Force
Write-Success "Patched MSI written to $($originalMsi.FullName)"

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
