$ErrorActionPreference = 'Stop'

$toolsDir = "$(Split-Path -Parent $MyInvocation.MyCommand.Definition)"
$version = $env:chocolateyPackageVersion

# Clean up old portable exe if upgrading from pre-MSI version
$oldExe = Join-Path $toolsDir 'DLSS_Updater.exe'
if (Test-Path $oldExe) {
    Write-Host "Upgrading from portable to MSI installation..."
    Get-Process -Name "DLSS_Updater" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    $oldShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "DLSS Updater.lnk"
    if (Test-Path $oldShortcut) {
        Remove-Item $oldShortcut -Force
    }
}

$url = "https://github.com/Recol/DLSS-Updater/releases/download/V$version/DLSS.Updater.$version.msi"

$packageArgs = @{
    packageName    = $env:ChocolateyPackageName
    fileType       = 'msi'
    url64bit       = $url
    softwareName   = 'DLSS Updater*'
    silentArgs     = "/qn /norestart /l*v `"$($env:TEMP)\$($env:ChocolateyPackageName).$($env:chocolateyPackageVersion).MsiInstall.log`""
    validExitCodes = @(0, 3010, 1641)
    checksum64     = (Get-Content "$toolsDir\CHECKSUM" -ErrorAction SilentlyContinue)
    checksumType64 = 'sha256'
}

Install-ChocolateyPackage @packageArgs
