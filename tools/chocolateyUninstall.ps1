$ErrorActionPreference = 'Stop'

# Remove desktop shortcut if present
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "DLSS Updater.lnk"
if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
}

$packageArgs = @{
    packageName    = $env:ChocolateyPackageName
    softwareName   = 'DLSS Updater*'
    fileType       = 'msi'
    silentArgs     = "/qn /norestart"
    validExitCodes = @(0, 3010, 1641)
}

[array]$key = Get-UninstallRegistryKey -SoftwareName $packageArgs['softwareName']

if ($key.Count -eq 1) {
    $key | ForEach-Object {
        $packageArgs['file'] = "$($_.UninstallString)"
        Uninstall-ChocolateyPackage @packageArgs
    }
} elseif ($key.Count -eq 0) {
    Write-Warning "$env:ChocolateyPackageName has already been uninstalled by other means."
} elseif ($key.Count -gt 1) {
    Write-Warning "$($key.Count) matches found!"
    Write-Warning "The following is the first 10 of the keys:"
    $key[0..9] | ForEach-Object { Write-Warning "- $($_.DisplayName)" }
    throw "Multiple uninstall entries found. Manual intervention required."
}
