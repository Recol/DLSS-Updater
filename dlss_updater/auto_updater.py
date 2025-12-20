import asyncio
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


async def check_for_updates_async() -> tuple[str | None, bool]:
    """
    Check for available updates by comparing versions (async version).
    Returns (latest_version, is_update_available) tuple or (None, False) if check fails.
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
                    return None, False

                content = await response.read()
                latest_release = _json_decoder.decode(content)

        latest_version = latest_release["tag_name"].lstrip("Vv")

        if version.parse(latest_version) > version.parse(__version__):
            logger.info(f"New version available: {latest_version}")
            return latest_version, True
        else:
            logger.info("You have the latest version.")
            return latest_version, False

    except asyncio.TimeoutError:
        logger.error("Timeout checking for updates")
        return None, False
    except aiohttp.ClientError as e:
        logger.error(f"Error checking for updates: {e}")
        return None, False
    except Exception as e:
        logger.error(f"Unexpected error checking for updates: {e}")
        return None, False


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
