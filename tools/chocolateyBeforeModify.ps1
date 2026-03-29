$ErrorActionPreference = 'Stop'

# Stop the application if it's running before upgrade/uninstall
Get-Process -Name "DLSS_Updater" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process -Name "DLSS Updater" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
