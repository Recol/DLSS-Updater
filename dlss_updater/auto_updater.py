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
from dlss_updater.logger import setup_logger

logger = setup_logger()

GITHUB_API_URL = "https://api.github.com/repos/Recol/DLSS-Updater/releases/latest"


def auto_update():
    logger.info("Checking for updates...")
    latest_version, download_url = check_for_updates()

    if latest_version:
        logger.info("New version available: %s", latest_version)
        user_input = input(
            "Do you want to download and install the update? (y/n): "
        ).lower()

        if user_input == "y":
            logger.info("Downloading update...")
            new_exe_path = download_update(download_url)
            if new_exe_path:
                logger.info("Update downloaded successfully.")
                logger.info(
                    "The application will now close and update. It will restart automatically."
                )
                perform_update(new_exe_path)
                return True
    else:
        logger.info("No updates available. You have the latest version.")

    return False


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
        logger.error(f"Error checking for updates: {e}")
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

        # Look for the new executable in the update directory
        new_exe = None
        for root, dirs, files in os.walk(update_dir):
            for file in files:
                if file.lower() == "dlss_updater.exe":
                    new_exe = os.path.join(root, file)
                    break
            if new_exe:
                break

        if new_exe:
            logger.info(f"Found new executable: {new_exe}")
            return new_exe
        else:
            logger.error("Error: New executable not found in the update package.")
            return None
    except Exception as e:
        logger.error(f"Error downloading update: {e}")
        if os.path.exists(update_dir):
            shutil.rmtree(update_dir)
        return None


def update_script(current_exe, new_exe):
    # Wait for the original process to exit
    time.sleep(2)

    try:
        # Replace the old executable with the new one
        os.remove(current_exe)
        shutil.move(new_exe, current_exe)

        # Clean up the update directory
        update_dir = os.path.dirname(new_exe)
        shutil.rmtree(update_dir)

        # Start the updated executable
        subprocess.Popen([current_exe], creationflags=subprocess.CREATE_NEW_CONSOLE)
        
        # Log the update completion
        with open(os.path.join(os.path.dirname(current_exe), "update_log.txt"), "w") as f:
            f.write(f"Update completed at {time.ctime()}. New executable started.")
        
    except Exception as e:
        # Log the error
        with open(os.path.join(os.path.dirname(current_exe), "update_error_log.txt"), "w") as f:
            f.write(f"Error during update process at {time.ctime()}: {str(e)}")


def perform_update(new_exe_path):
    current_exe = sys.executable

    # Start the update process
    subprocess.Popen(
        [sys.executable, __file__, "update", current_exe, new_exe_path],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # Exit the current process
    sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        update_script(sys.argv[2], sys.argv[3])
