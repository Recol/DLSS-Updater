import os
import sys
import json
import shutil
import zipfile
import subprocess
import time
from urllib import request
from urllib.error import URLError
from packaging import version
from dlss_updater.version import __version__

GITHUB_API_URL = "https://api.github.com/repos/Recol/DLSS-Updater/releases/latest"


def check_for_updates():
    try:
        with request.urlopen(GITHUB_API_URL) as response:
            latest_release = json.loads(response.read().decode())
        latest_version = latest_release["tag_name"].lstrip("V")

        if version.parse(latest_version) > version.parse(__version__):
            return latest_version, latest_release["assets"][0]["browser_download_url"]
        else:
            return None, None
    except URLError as e:
        print(f"Error checking for updates: {e}")
        return None, None


def download_update(download_url):
    try:
        update_dir = os.path.join(os.path.dirname(sys.executable), "update")
        os.makedirs(update_dir, exist_ok=True)
        update_zip = os.path.join(update_dir, "update.zip")
        request.urlretrieve(download_url, update_zip)

        with zipfile.ZipFile(update_zip, "r") as zip_ref:
            zip_ref.extractall(update_dir)

        os.remove(update_zip)

        new_exe = os.path.join(update_dir, "DLSS_Updater.exe")

        if os.path.exists(new_exe):
            return new_exe
        else:
            print("Error: New executable not found in the update package.")
            return None
    except Exception as e:
        print(f"Error downloading update: {e}")
        if os.path.exists(update_dir):
            shutil.rmtree(update_dir)
        return None


def update_script(current_exe, new_exe):
    """
    Performs the actual update process.
    This function is called by the subprocess created in perform_update.
    """
    # Wait for the original process to exit
    time.sleep(2)

    # Replace the old executable with the new one
    os.remove(current_exe)
    shutil.move(new_exe, current_exe)

    # Clean up the update directory
    update_dir = os.path.dirname(new_exe)
    shutil.rmtree(update_dir)

    # Start the updated executable
    subprocess.Popen([current_exe])

def perform_update(new_exe_path):
    current_exe = sys.executable

    # Start the update process
    subprocess.Popen(
        [sys.executable, __file__, "update", current_exe, new_exe_path],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Exit the current process
    sys.exit(0)


def auto_update():
    print("Checking for updates...")
    latest_version, download_url = check_for_updates()

    if latest_version:
        print(f"New version available: {latest_version}")
        user_input = input(
            "Do you want to download and install the update? (y/n): "
        ).lower()

        if user_input == "y":
            print("Downloading update...")
            new_exe_path = download_update(download_url)
            if new_exe_path:
                print("Update downloaded successfully.")
                print(
                    "The application will now close and update. It will restart automatically."
                )
                perform_update(new_exe_path)
                return True
    else:
        print("No updates available. You have the latest version.")

    return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        update_script(sys.argv[2], sys.argv[3])
