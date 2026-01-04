import asyncio
import sys
import msgspec
import aiohttp
from packaging import version
from dlss_updater.version import __version__
from dlss_updater.logger import setup_logger

logger = setup_logger()

GITHUB_API_URL = "https://api.github.com/repos/Recol/DLSS-Updater/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/Recol/DLSS-Updater/releases/latest"

# msgspec decoder for better performance
_json_decoder = msgspec.json.Decoder()


def get_platform_asset_pattern() -> str:
    """Get the expected asset filename pattern for the current platform."""
    if sys.platform == 'win32':
        return "DLSS.Updater"  # Matches DLSS.Updater.X.Y.Z.zip
    elif sys.platform == 'linux':
        return "DLSS_Updater_Linux"  # Matches DLSS_Updater_Linux_X.Y.Z.tar.gz
    return ""


def get_platform_name() -> str:
    """Get friendly platform name for display."""
    if sys.platform == 'win32':
        return "Windows"
    elif sys.platform == 'linux':
        return "Linux"
    return "Unknown"


async def check_for_updates_async() -> tuple[str | None, bool, str | None]:
    """
    Check for available updates by comparing versions (async version).
    Returns (latest_version, is_update_available, download_url) tuple.
    download_url is the platform-specific asset URL if available, otherwise generic releases page.
    """
    try:
        logger.info("Checking for updates...")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                GITHUB_API_URL,
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"Accept": "application/vnd.github.v3+json"}
            ) as response:
                if response.status != 200:
                    logger.error(f"GitHub API returned status {response.status}")
                    return None, False, None

                content = await response.read()
                latest_release = _json_decoder.decode(content)

        latest_version = latest_release["tag_name"].lstrip("Vv")

        # Find platform-specific download URL
        download_url = GITHUB_RELEASES_URL  # Default to generic releases page
        asset_pattern = get_platform_asset_pattern()

        if asset_pattern and "assets" in latest_release:
            for asset in latest_release["assets"]:
                asset_name = asset.get("name", "")
                if asset_pattern in asset_name:
                    download_url = asset.get("browser_download_url", GITHUB_RELEASES_URL)
                    logger.info(f"Found platform-specific asset: {asset_name}")
                    break

        if version.parse(latest_version) > version.parse(__version__):
            logger.info(f"New version available: {latest_version} ({get_platform_name()})")
            return latest_version, True, download_url
        else:
            logger.info("You have the latest version.")
            return latest_version, False, download_url

    except asyncio.TimeoutError:
        logger.error("Timeout checking for updates")
        return None, False, None
    except aiohttp.ClientError as e:
        logger.error(f"Error checking for updates: {e}")
        return None, False, None
    except Exception as e:
        logger.error(f"Unexpected error checking for updates: {e}")
        return None, False, None


def check_for_updates() -> tuple[str | None, bool]:
    """
    Check for available updates by comparing versions (sync version - deprecated).
    Returns (latest_version, is_update_available) tuple or (None, False) if check fails.

    Note: Prefer check_for_updates_async() for non-blocking operation.
    """
    from urllib import request
    from urllib.error import URLError

    try:
        logger.info("Checking for updates...")
        with request.urlopen(GITHUB_API_URL, timeout=10) as response:
            latest_release = _json_decoder.decode(response.read())
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


def get_releases_url() -> str:
    """Get the URL to the GitHub releases page"""
    return GITHUB_RELEASES_URL
