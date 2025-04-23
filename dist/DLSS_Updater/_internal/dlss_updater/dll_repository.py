import os
import json
import requests
import shutil
from pathlib import Path
from packaging import version
from .logger import setup_logger

logger = setup_logger()

# Configuration
GITHUB_DLL_REPO = "Recol/DLSS-Updater-DLLs"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_DLL_REPO}"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_DLL_REPO}/main"
DLL_MANIFEST_URL = f"{GITHUB_RAW_BASE}/manifest.json"
LOCAL_DLL_CACHE_DIR = os.path.join(
    os.path.expanduser("~"), ".dlss_updater", "dll_cache"
)


def ensure_cache_dir():
    """Ensure local cache directory exists"""
    os.makedirs(LOCAL_DLL_CACHE_DIR, exist_ok=True)


def get_local_dll_path(dll_name):
    """Get path to cached DLL, download if newer version exists"""
    ensure_cache_dir()
    local_path = os.path.join(LOCAL_DLL_CACHE_DIR, dll_name)

    # If it doesn't exist locally, try to download
    if not os.path.exists(local_path):
        if download_latest_dll(dll_name):
            return local_path
        else:
            logger.error(f"Failed to download {dll_name} and no local copy exists")
            return None

    # Check for updates
    if check_for_dll_update(dll_name):
        download_latest_dll(dll_name)

    return local_path


def get_remote_manifest():
    """Fetch the remote DLL manifest"""
    try:
        response = requests.get(DLL_MANIFEST_URL, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch DLL manifest: {e}")
        return None


def get_cached_manifest():
    """Get the cached manifest if available"""
    manifest_path = os.path.join(LOCAL_DLL_CACHE_DIR, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read cached manifest: {e}")
    return None


def update_cached_manifest(manifest):
    """Update the cached manifest"""
    manifest_path = os.path.join(LOCAL_DLL_CACHE_DIR, "manifest.json")
    try:
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to update cached manifest: {e}")
        return False


def check_for_dll_update(dll_name):
    """Check if a newer version of the DLL is available"""
    # Import parse_version and get_dll_version inside the function
    from .updater import get_dll_version, parse_version

    local_path = os.path.join(LOCAL_DLL_CACHE_DIR, dll_name)
    if not os.path.exists(local_path):
        logger.info(f"No local copy of {dll_name} exists, download needed")
        return True  # No local copy, need to download

    local_version = get_dll_version(local_path)
    if not local_version:
        logger.info(
            f"Could not determine version of local {dll_name}, assuming update needed"
        )
        return True  # Can't determine local version, assume update needed

    # Get remote version info
    manifest = get_remote_manifest() or get_cached_manifest()
    if not manifest:
        logger.warning("No manifest available, can't check for updates")
        return False

    if dll_name not in manifest:
        logger.warning(f"{dll_name} not found in manifest")
        return False

    remote_version = manifest[dll_name]["version"]

    # Compare versions with better error handling
    try:
        local_parsed = parse_version(local_version)
        remote_parsed = parse_version(remote_version)

        # Add detailed logging for debugging
        logger.info(
            f"Comparing versions for {dll_name}: local={local_version} ({local_parsed}) remote={remote_version} ({remote_parsed})"
        )

        if remote_parsed > local_parsed:
            logger.info(
                f"Update available for {dll_name}: {local_version} -> {remote_version}"
            )
            return True
        else:
            logger.info(
                f"{dll_name} is up to date (local: {local_version}, remote: {remote_version})"
            )
            return False
    except Exception as e:
        logger.error(f"Version comparison error for {dll_name}: {e}")
        # Assume update needed if we can't compare versions safely
        logger.info(
            f"Assuming update needed for {dll_name} due to version comparison error"
        )
        return True


def download_latest_dll(dll_name):
    """Download the latest version of a DLL"""
    manifest = get_remote_manifest()
    if not manifest:
        logger.error("Failed to fetch manifest, cannot download DLL")
        return False

    if dll_name not in manifest:
        logger.error(f"{dll_name} not found in manifest")
        return False

    # Get download URL from manifest
    dll_info = manifest[dll_name]
    download_url = f"{GITHUB_RAW_BASE}/dlls/{dll_name}"

    # Download the DLL
    local_path = os.path.join(LOCAL_DLL_CACHE_DIR, dll_name)
    try:
        response = requests.get(download_url, stream=True, timeout=30)
        response.raise_for_status()

        # Save to temp file first, then rename to avoid partial downloads
        temp_path = f"{local_path}.tmp"
        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Replace existing file
        if os.path.exists(local_path):
            os.remove(local_path)
        os.rename(temp_path, local_path)

        logger.info(f"Successfully downloaded {dll_name} v{dll_info['version']}")
        return True
    except Exception as e:
        logger.error(f"Failed to download {dll_name}: {e}")
        return False


_cache_initialized = False


def initialize_dll_cache():
    """Initialize the DLL cache on application startup"""
    global _cache_initialized

    # Skip if already initialized
    if _cache_initialized:
        logger.debug("DLL cache already initialized, skipping")
        return

    # Ensure we initialize the DLL paths
    from .config import initialize_dll_paths

    logger.info("Initializing DLL cache")
    ensure_cache_dir()

    # Fetch latest manifest
    manifest = get_remote_manifest()
    if manifest:
        update_cached_manifest(manifest)

        # Check all DLLs for updates
        for dll_name in manifest:
            if check_for_dll_update(dll_name):
                logger.info(
                    f"Updating {dll_name} to version {manifest[dll_name]['version']}"
                )
                download_latest_dll(dll_name)
            else:
                logger.info(f"{dll_name} is up to date")
    else:
        logger.warning("Using cached manifest, updates may not be available")

    # Initialize DLL paths after cache initialization
    initialize_dll_paths()

    # Mark as initialized
    _cache_initialized = True
