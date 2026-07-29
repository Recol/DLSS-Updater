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


def get_platform_asset_suffix() -> str:
    """Get the release asset extension for the current platform.

    Matched on extension rather than a filename prefix because the two channels
    name their assets differently (``DLSS.Updater.X.Y.Z.msi`` with dots,
    ``DLSS_Updater-X.Y.Z.flatpak`` with an underscore and a dash) and the
    extension is the part that actually identifies the platform.

    Note this replaced a prefix match on ``DLSS_Updater_Linux``, which matched
    the Linux tarball that releases stopped shipping before V4.3.1 - Linux users
    silently fell through to the generic releases page for every release since.
    """
    if sys.platform == 'win32':
        return ".msi"
    elif sys.platform == 'linux':
        return ".flatpak"
    return ""


def get_platform_asset_pattern() -> str:
    """Deprecated alias kept for callers matching on a filename fragment."""
    return get_platform_asset_suffix()


def get_platform_name() -> str:
    """Get friendly platform name for display."""
    if sys.platform == 'win32':
        return "Windows"
    elif sys.platform == 'linux':
        return "Linux"
    return "Unknown"


def find_platform_asset(release: dict) -> dict | None:
    """Pick the release asset matching the running platform, or None.

    Returns the raw asset dict from the GitHub API, which carries the fields the
    self-updater needs: ``browser_download_url``, ``name``, ``size`` and
    ``digest`` (a ``"sha256:..."`` string the API supplies per asset, so update
    downloads can be integrity-checked without publishing a separate checksum).
    """
    suffix = get_platform_asset_suffix()
    if not suffix:
        return None

    for asset in release.get("assets", ()):
        if str(asset.get("name", "")).endswith(suffix):
            return asset
    return None


async def fetch_latest_release() -> dict | None:
    """Fetch the latest release payload from the GitHub API, or None on failure."""
    # Reuse the shared, connection-pooled aiohttp session rather than spinning
    # up (and tearing down) a fresh ClientSession per check. Do NOT close it
    # here - it is owned by dll_repository and lives for the app's lifetime; the
    # per-request timeout below still applies.
    from dlss_updater.dll_repository import get_http_session

    session = await get_http_session()
    async with session.get(
        GITHUB_API_URL,
        timeout=aiohttp.ClientTimeout(total=10),
        headers={"Accept": "application/vnd.github.v3+json"},
    ) as response:
        if response.status != 200:
            logger.error(f"GitHub API returned status {response.status}")
            return None
        return _json_decoder.decode(await response.read())


def is_newer_version(latest: str, current: str) -> bool:
    """True when ``latest`` is strictly newer than ``current``.

    Tolerates a leading ``V``/``v`` on either side and treats an unparseable
    version as "not newer" so a malformed tag can never trigger an update.
    """
    try:
        return version.parse(latest.lstrip("Vv")) > version.parse(current.lstrip("Vv"))
    except Exception as e:
        logger.error(f"Could not compare versions {latest!r} and {current!r}: {e}")
        return False


async def check_for_updates_async() -> tuple[str | None, bool, str | None]:
    """
    Check for available updates by comparing versions (async version).
    Returns (latest_version, is_update_available, download_url) tuple.
    download_url is the platform-specific asset URL if available, otherwise generic releases page.
    """
    try:
        logger.info("Checking for updates...")

        latest_release = await fetch_latest_release()
        if latest_release is None:
            return None, False, None

        latest_version = latest_release["tag_name"].lstrip("Vv")

        # Find platform-specific download URL
        download_url = GITHUB_RELEASES_URL  # Default to generic releases page
        asset = find_platform_asset(latest_release)
        if asset is not None:
            download_url = asset.get("browser_download_url", GITHUB_RELEASES_URL)
            logger.info(f"Found platform-specific asset: {asset.get('name', '')}")

        if is_newer_version(latest_version, __version__):
            logger.info(f"New version available: {latest_version} ({get_platform_name()})")
            return latest_version, True, download_url
        else:
            logger.info("You have the latest version.")
            return latest_version, False, download_url

    except TimeoutError:
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
