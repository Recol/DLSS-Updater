$toolsDir   = "$(Split-Path -parent $MyInvocation.MyCommand.Definition)"
$fileLocation = Join-Path $toolsDir 'DLSS_Updater.exe'

# Create a shortcut that runs as admin
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "DLSS Updater.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $fileLocation
$shortcut.Arguments = "--gui"
$shortcut.Save()

# Set the shortcut to run as administrator
$bytes = [System.IO.File]::ReadAllBytes($shortcutPath)
$bytes[0x15] = $bytes[0x15] -bor 0x20 # Set run as administrator flag
[System.IO.File]::WriteAllBytes($shortcutPath, $bytes)

$packageArgs = @{
  packageName   = $env:ChocolateyPackageName
  fileType      = 'exe'
  file          = $fileLocation
  softwareName  = 'DLSS Updater*'
  silentArgs    = "--gui"
  validExitCodes= @(0)
  # Force running as admin
  requireAdmin  = $true
}