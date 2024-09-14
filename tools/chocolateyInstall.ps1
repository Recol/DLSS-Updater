$toolsDir   = "$(Split-Path -parent $MyInvocation.MyCommand.Definition)"
$fileLocation = Join-Path $toolsDir 'DLSS_Updater.exe'

$packageArgs = @{
  packageName   = $env:ChocolateyPackageName
  fileType      = 'exe'
  file          = $fileLocation
  softwareName  = 'DLSS Updater*'
  silentArgs    = "--gui"
  validExitCodes= @(0)
}

Install-ChocolateyInstallPackage @packageArgs