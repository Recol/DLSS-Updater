import sys
import json
from urllib import request
from urllib.error import URLError
from packaging import version
from dlss_updater.version import __version__
from dlss_updater.logger import setup_logger

logger = setup_logger()

GITHUB_API_URL = "https://api.github.com/repos/Recol/DLSS-Updater/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/Recol/DLSS-Updater/releases/latest"


def check_for_updates():
    """
    Check for available updates by comparing versions.
    Returns (latest_version, is_update_available) tuple or (None, False) if check fails.
    """
    try:
        logger.info("Checking for updates...")
        with request.urlopen(GITHUB_API_URL) as response:
            latest_release = json.loads(response.read().decode())
        latest_version = latest_release["tag_name"].lstrip("Vv")

        if version.parse(latest_version) > version.parse(__version__):
            logger.info(f"New version available: {latest_version}")
            return latest_version, True
        else:
            logger.info("You have the latest version.")
            return latest_version, False
    except URLError as e:
        logger.error(f"Error checking for updates: {e}")
        return None, False
    except Exception as e:
        logger.error(f"Unexpected error checking for updates: {e}")
        return None, False


def get_releases_url():
    """Get the URL to the GitHub releases page"""
    return GITHUB_RELEASES_URL