import os
import sys
import json
import zipfile
from urllib import request
from urllib.error import URLError
from packaging import version
from dlss_updater.version import __version__

GITHUB_API_URL = "https://api.github.com/repos/Recol/DLSS-Updater/releases/latest"

def check_for_updates():
    try:
        with request.urlopen(GITHUB_API_URL) as response:
            latest_release = json.loads(response.read().decode())
        latest_version = latest_release['tag_name'].lstrip('V')
        
        if version.parse(latest_version) > version.parse(__version__):
            return latest_version, latest_release['assets'][0]['browser_download_url']
        else:
            return None, None
    except URLError as e:
        print(f"Error checking for updates: {e}")
        return None, None

def download_and_install_update(download_url):
    try:
        update_zip = 'update.zip'
        request.urlretrieve(download_url, update_zip)
        
        # Extract the zip file
        with zipfile.ZipFile(update_zip, 'r') as zip_ref:
            zip_ref.extractall('update')
        
        # Remove the zip file
        os.remove(update_zip)
        
        # Get the path to the new executable
        new_exe = os.path.join('update', 'DLSS_Updater', 'DLSS_Updater.exe')
        
        if os.path.exists(new_exe):
            # Replace the current executable
            current_exe = sys.executable
            os.rename(new_exe, current_exe)
            
            # Clean up the update directory
            os.rmdir(os.path.join('update', 'DLSS_Updater'))
            os.rmdir('update')
            
            print("Update installed successfully. Please restart the application.")
            return True
        else:
            print("Error: New executable not found in the update package.")
            return False
    except Exception as e:
        print(f"Error downloading or installing update: {e}")
        return False

def auto_update():
    print("Checking for updates...")
    latest_version, download_url = check_for_updates()
    
    if latest_version:
        print(f"New version available: {latest_version}")
        user_input = input("Do you want to download and install the update? (y/n): ").lower()
        
        if user_input == 'y':
            print("Downloading and installing update...")
            if download_and_install_update(download_url):
                print("Update process completed. Please restart the application.")
                return True
    else:
        print("No updates available. You have the latest version.")
    
    return False