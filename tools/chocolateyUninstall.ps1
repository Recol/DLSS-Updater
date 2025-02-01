$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "DLSS Updater.lnk"
if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
}

$packageArgs = @{
  packageName   = $env:ChocolateyPackageName
  softwareName  = 'DLSS Updater*'
  fileType      = 'exe'
}