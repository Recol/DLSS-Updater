import os
import asyncio
import inspect
import threading
from pathlib import Path
import msgspec
import aiohttp
import aiofiles
import concurrent.futures
from .logger import setup_logger
from .config import initialize_dll_paths, Concurrency

logger = setup_logger()

# msgspec decoder for better performance
_json_decoder = msgspec.json.Decoder()

# Configuration
GITHUB_DLL_REPO = "Recol/DLSS-Updater-DLLs"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_DLL_REPO}"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_DLL_REPO}/main"
DLL_MANIFEST_URL = f"{GITHUB_RAW_BASE}/manifest.json"
def _get_dll_cache_dir():
    """Get the DLL cache directory using centralized config path."""
    from dlss_updater.platform_utils import APP_CONFIG_DIR
    return str(APP_CONFIG_DIR / "dll_cache")


LOCAL_DLL_CACHE_DIR = _get_dll_cache_dir()

# Thread-safety locks for free-threading (Python 3.14+)
_session_lock = threading.Lock()
_cache_init_lock = threading.Lock()

# Shared aiohttp session for connection reuse
_http_session: aiohttp.ClientSession | None = None


async def get_http_session() -> aiohttp.ClientSession:
    """Get or create shared HTTP session.

    Thread-safe for free-threading (Python 3.14+).
    """
    global _http_session

    # Quick check without lock
    if _http_session is not None and not _http_session.closed:
        return _http_session

    # Need to create session - use lock
    with _session_lock:
        # Double-check after acquiring lock
        if _http_session is None or _http_session.closed:
            _http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )
    return _http_session


async def close_http_session() -> None:
    """Close shared HTTP session (call on app shutdown).

    Thread-safe for free-threading (Python 3.14+).
    """
    global _http_session

    with _session_lock:
        if _http_session and not _http_session.closed:
            await _http_session.close()
            _http_session = None


def ensure_cache_dir():
    """Ensure local cache directory exists"""
    Path(LOCAL_DLL_CACHE_DIR).mkdir(parents=True, exist_ok=True)


def get_local_dll_path(dll_name, skip_update_check=False):
    """Get path to cached DLL, download if newer version exists

    Args:
        dll_name: Name of the DLL file
        skip_update_check: If True, skip version comparison (used after cache init)
    """
    ensure_cache_dir()
    local_path = Path(LOCAL_DLL_CACHE_DIR) / dll_name

    # If it doesn't exist locally, try to download
    if not local_path.exists():
        if download_latest_dll(dll_name):
            return str(local_path)
        else:
            logger.error(f"Failed to download {dll_name} and no local copy exists")
            return None

    # Skip update check if cache is already initialized or explicitly skipped
    with _cache_init_lock:
        cache_init = _cache_initialized

    if not skip_update_check and not cache_init:
        if check_for_dll_update(dll_name):
            download_latest_dll(dll_name)

    return str(local_path)


async def get_remote_manifest_async() -> dict | None:
    """Fetch the remote DLL manifest (async version)"""
    try:
        session = await get_http_session()
        async with session.get(
            DLL_MANIFEST_URL,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            if response.status != 200:
                logger.error(f"Failed to fetch DLL manifest: HTTP {response.status}")
                return None
            content = await response.read()
            return _json_decoder.decode(content)
    except TimeoutError:
        logger.error("Timeout fetching DLL manifest")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"Failed to fetch DLL manifest: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch DLL manifest: {e}")
        return None


def get_remote_manifest():
    """Fetch the remote DLL manifest (sync version - for backward compatibility)"""
    import requests
    try:
        response = requests.get(DLL_MANIFEST_URL, timeout=10)
        response.raise_for_status()
        return _json_decoder.decode(response.content)
    except Exception as e:
        logger.error(f"Failed to fetch DLL manifest: {e}")
        return None


def get_cached_manifest():
    """Get the cached manifest if available (sync version)"""
    manifest_path = Path(LOCAL_DLL_CACHE_DIR) / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, "rb") as f:
                return _json_decoder.decode(f.read())
        except Exception as e:
            logger.error(f"Failed to read cached manifest: {e}")
    return None


async def get_cached_manifest_async():
    """Get the cached manifest if available (async version)"""
    manifest_path = Path(LOCAL_DLL_CACHE_DIR) / "manifest.json"
    if manifest_path.exists():
        try:
            async with aiofiles.open(manifest_path, "rb") as f:
                content = await f.read()
                return _json_decoder.decode(content)
        except Exception as e:
            logger.error(f"Failed to read cached manifest: {e}")
    return None


def update_cached_manifest(manifest):
    """Update the cached manifest (sync version)"""
    manifest_path = Path(LOCAL_DLL_CACHE_DIR) / "manifest.json"
    try:
        with open(manifest_path, "wb") as f:
            f.write(msgspec.json.format(msgspec.json.encode(manifest), indent=2))
        return True
    except Exception as e:
        logger.error(f"Failed to update cached manifest: {e}")
        return False


async def update_cached_manifest_async(manifest):
    """Update the cached manifest (async version)"""
    manifest_path = Path(LOCAL_DLL_CACHE_DIR) / "manifest.json"
    try:
        encoded = msgspec.json.format(msgspec.json.encode(manifest), indent=2)
        async with aiofiles.open(manifest_path, "wb") as f:
            await f.write(encoded)
        return True
    except Exception as e:
        logger.error(f"Failed to update cached manifest: {e}")
        return False


async def check_for_dll_update_async(dll_name: str, manifest: dict | None = None) -> bool:
    """Check if a newer version of the DLL is available (async version)"""
    from .updater import get_dll_version, parse_version

    local_path = Path(LOCAL_DLL_CACHE_DIR) / dll_name
    if not local_path.exists():
        logger.info(f"No local copy of {dll_name} exists, download needed")
        return True

    # get_dll_version is CPU-bound (reads PE headers), run in thread
    local_version = await asyncio.to_thread(get_dll_version, str(local_path))
    if not local_version:
        logger.info(f"Could not determine version of local {dll_name}, assuming update needed")
        return True

    # Use provided manifest or fetch it
    if manifest is None:
        manifest = await get_remote_manifest_async()
        if not manifest:
            manifest = await get_cached_manifest_async()

    if not manifest:
        logger.warning("No manifest available, can't check for updates")
        return False

    if dll_name not in manifest:
        logger.warning(f"{dll_name} not found in manifest")
        return False

    remote_version = manifest[dll_name]["version"]

    try:
        local_parsed = parse_version(local_version)
        remote_parsed = parse_version(remote_version)

        logger.info(
            f"Comparing versions for {dll_name}: local={local_version} ({local_parsed}) remote={remote_version} ({remote_parsed})"
        )

        if remote_parsed > local_parsed:
            logger.info(f"Update available for {dll_name}: {local_version} -> {remote_version}")
            return True
        else:
            logger.info(f"{dll_name} is up to date (local: {local_version}, remote: {remote_version})")
            return False
    except Exception as e:
        logger.error(f"Version comparison error for {dll_name}: {e}")
        logger.info(f"Assuming update needed for {dll_name} due to version comparison error")
        return True


def check_for_dll_update(dll_name):
    """Check if a newer version of the DLL is available (sync version)"""
    from .updater import get_dll_version, parse_version

    local_path = Path(LOCAL_DLL_CACHE_DIR) / dll_name
    if not local_path.exists():
        logger.info(f"No local copy of {dll_name} exists, download needed")
        return True

    local_version = get_dll_version(str(local_path))
    if not local_version:
        logger.info(f"Could not determine version of local {dll_name}, assuming update needed")
        return True

    manifest = get_remote_manifest() or get_cached_manifest()
    if not manifest:
        logger.warning("No manifest available, can't check for updates")
        return False

    if dll_name not in manifest:
        logger.warning(f"{dll_name} not found in manifest")
        return False

    remote_version = manifest[dll_name]["version"]

    try:
        local_parsed = parse_version(local_version)
        remote_parsed = parse_version(remote_version)

        logger.info(
            f"Comparing versions for {dll_name}: local={local_version} ({local_parsed}) remote={remote_version} ({remote_parsed})"
        )

        if remote_parsed > local_parsed:
            logger.info(f"Update available for {dll_name}: {local_version} -> {remote_version}")
            return True
        else:
            logger.info(f"{dll_name} is up to date (local: {local_version}, remote: {remote_version})")
            return False
    except Exception as e:
        logger.error(f"Version comparison error for {dll_name}: {e}")
        logger.info(f"Assuming update needed for {dll_name} due to version comparison error")
        return True


async def download_latest_dll_async(dll_name: str, manifest: dict | None = None, progress_callback=None) -> bool:
    """
    Download the latest version of a DLL (async version with streaming)

    Args:
        dll_name: Name of the DLL to download
        manifest: Optional manifest to use (avoids refetching)
        progress_callback: Optional async callback(bytes_downloaded, total_bytes, dll_name) for progress
    """
    if manifest is None:
        manifest = await get_remote_manifest_async()

    if not manifest:
        logger.error("Failed to fetch manifest, cannot download DLL")
        return False

    if dll_name not in manifest:
        logger.error(f"{dll_name} not found in manifest")
        return False

    dll_info = manifest[dll_name]
    download_url = f"{GITHUB_RAW_BASE}/dlls/{dll_name}"
    local_path = Path(LOCAL_DLL_CACHE_DIR) / dll_name
    temp_path = local_path.with_suffix(local_path.suffix + ".tmp")

    try:
        session = await get_http_session()
        async with session.get(
            download_url,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            if response.status != 200:
                logger.error(f"Failed to download {dll_name}: HTTP {response.status}")
                return False

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            async with aiofiles.open(temp_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(8192):
                    await f.write(chunk)
                    downloaded += len(chunk)

                    if progress_callback and total_size > 0:
                        if inspect.iscoroutinefunction(progress_callback):
                            await progress_callback(downloaded, total_size, dll_name)
                        else:
                            progress_callback(downloaded, total_size, dll_name)

        # Replace existing file using Python 3.14 Path.move()
        if local_path.exists():
            local_path.unlink()
        temp_path.move(local_path)

        logger.info(f"Successfully downloaded {dll_name} v{dll_info['version']}")
        return True

    except TimeoutError:
        logger.error(f"Timeout downloading {dll_name}")
        return False
    except aiohttp.ClientError as e:
        logger.error(f"Failed to download {dll_name}: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to download {dll_name}: {e}")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except:
                pass
        return False


def download_latest_dll(dll_name, progress_callback=None):
    """
    Download the latest version of a DLL (sync version - for backward compatibility)

    Args:
        dll_name: Name of the DLL to download
        progress_callback: Optional callback(bytes_downloaded, total_bytes, dll_name) for progress
    """
    import requests

    manifest = get_remote_manifest()
    if not manifest:
        logger.error("Failed to fetch manifest, cannot download DLL")
        return False

    if dll_name not in manifest:
        logger.error(f"{dll_name} not found in manifest")
        return False

    dll_info = manifest[dll_name]
    download_url = f"{GITHUB_RAW_BASE}/dlls/{dll_name}"
    local_path = Path(LOCAL_DLL_CACHE_DIR) / dll_name

    try:
        response = requests.get(download_url, stream=True, timeout=30)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        temp_path = local_path.with_suffix(local_path.suffix + ".tmp")
        downloaded = 0

        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)

                if progress_callback and total_size > 0:
                    progress_callback(downloaded, total_size, dll_name)

        if local_path.exists():
            local_path.unlink()
        temp_path.move(local_path)

        logger.info(f"Successfully downloaded {dll_name} v{dll_info['version']}")
        return True
    except Exception as e:
        logger.error(f"Failed to download {dll_name}: {e}")
        return False


_cache_initialized = False


async def initialize_dll_cache_async(progress_callback=None):
    """
    Fully async DLL cache initialization

    Args:
        progress_callback: Optional async callback(current, total, message) for progress updates

    Thread-safe for free-threading (Python 3.14+).
    """
    global _cache_initialized

    # Quick check with lock
    with _cache_init_lock:
        if _cache_initialized:
            logger.debug("DLL cache already initialized, skipping")
            return

    logger.info("Initializing DLL cache (async)")
    ensure_cache_dir()

    async def report_progress(current, total, message):
        if progress_callback:
            if inspect.iscoroutinefunction(progress_callback):
                await progress_callback(current, total, message)
            else:
                progress_callback(current, total, message)

    await report_progress(0, 100, "Fetching DLL manifest...")

    # Fetch latest manifest
    manifest = await get_remote_manifest_async()
    if manifest:
        await update_cached_manifest_async(manifest)

        await report_progress(10, 100, "Checking for DLL updates...")

        # Check all DLLs for updates concurrently
        dll_names = list(manifest.keys())
        total_dlls = len(dll_names)

        # CPU-bound PE parsing - scale with CPU threads
        check_semaphore = asyncio.Semaphore(Concurrency.IO_HEAVY)

        async def bounded_check(name):
            async with check_semaphore:
                return await check_for_dll_update_async(name, manifest)

        # Create bounded check tasks
        check_tasks = [bounded_check(name) for name in dll_names]

        # Run checks with progress reporting
        dlls_to_update = []
        results = await asyncio.gather(*check_tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error checking {dll_names[i]}: {result}")
            elif result:
                dlls_to_update.append(dll_names[i])

            progress_pct = int(10 + ((i + 1) / total_dlls) * 30)
            await report_progress(progress_pct, 100, f"Checked {i + 1}/{total_dlls} DLLs")

        # Download updates concurrently
        if dlls_to_update:
            await report_progress(40, 100, f"Downloading {len(dlls_to_update)} DLL updates...")

            # Network I/O - use extreme concurrency to saturate bandwidth
            download_semaphore = asyncio.Semaphore(Concurrency.IO_EXTREME)

            async def bounded_download(name):
                async with download_semaphore:
                    return await download_latest_dll_async(name, manifest)

            download_tasks = [bounded_download(name) for name in dlls_to_update]

            download_results = await asyncio.gather(*download_tasks, return_exceptions=True)

            for i, result in enumerate(download_results):
                if isinstance(result, Exception):
                    logger.error(f"Error downloading {dlls_to_update[i]}: {result}")
                elif not result:
                    logger.error(f"Failed to download {dlls_to_update[i]}")

                progress_pct = int(40 + ((i + 1) / len(dlls_to_update)) * 60)
                await report_progress(progress_pct, 100, f"Downloaded {i + 1}/{len(dlls_to_update)} DLLs")
        else:
            await report_progress(100, 100, "All DLLs up to date")
    else:
        logger.warning("Using cached manifest, updates may not be available")
        await report_progress(100, 100, "Using cached manifest")

    # Set initialized flag with lock for thread safety
    with _cache_init_lock:
        _cache_initialized = True
    initialize_dll_paths()

    await report_progress(100, 100, "DLL cache initialized")


def initialize_dll_cache(progress_callback=None):
    """
    Initialize the DLL cache on application startup - parallel version (sync)

    Args:
        progress_callback: Optional callback(current, total, message) for progress updates

    Thread-safe for free-threading (Python 3.14+).
    """
    global _cache_initialized

    # Quick check with lock
    with _cache_init_lock:
        if _cache_initialized:
            logger.debug("DLL cache already initialized, skipping")
            return

    logger.info("Initializing DLL cache")
    ensure_cache_dir()

    if progress_callback:
        progress_callback(0, 100, "Fetching DLL manifest...")

    manifest = get_remote_manifest()
    if manifest:
        update_cached_manifest(manifest)

        if progress_callback:
            progress_callback(10, 100, "Checking for DLL updates...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=Concurrency.THREADPOOL_IO) as executor:
            check_futures = {
                executor.submit(check_for_dll_update, dll_name): dll_name
                for dll_name in manifest
            }

            dlls_to_update = []
            checked_count = 0
            total_dlls = len(manifest)

            for future in concurrent.futures.as_completed(check_futures):
                dll_name = check_futures[future]
                try:
                    needs_update = future.result()
                    if needs_update:
                        logger.info(f"Updating {dll_name} to version {manifest[dll_name]['version']}")
                        dlls_to_update.append(dll_name)
                    else:
                        logger.info(f"{dll_name} is up to date")
                except Exception as e:
                    logger.error(f"Error checking {dll_name}: {e}")

                checked_count += 1
                if progress_callback:
                    progress_pct = int(10 + (checked_count / total_dlls) * 30)
                    progress_callback(progress_pct, 100, f"Checked {checked_count}/{total_dlls} DLLs")

            if dlls_to_update:
                if progress_callback:
                    progress_callback(40, 100, f"Downloading {len(dlls_to_update)} DLL updates...")

                download_futures = {
                    executor.submit(download_latest_dll, dll_name): dll_name
                    for dll_name in dlls_to_update
                }

                downloaded_count = 0
                total_downloads = len(dlls_to_update)

                for future in concurrent.futures.as_completed(download_futures):
                    dll_name = download_futures[future]
                    try:
                        success = future.result()
                        if not success:
                            logger.error(f"Failed to download {dll_name}")
                        else:
                            logger.info(f"Downloaded {dll_name}")
                    except Exception as e:
                        logger.error(f"Error downloading {dll_name}: {e}")

                    downloaded_count += 1
                    if progress_callback:
                        progress_pct = int(40 + (downloaded_count / total_downloads) * 60)
                        progress_callback(progress_pct, 100, f"Downloaded {downloaded_count}/{total_downloads} DLLs")
            else:
                if progress_callback:
                    progress_callback(100, 100, "All DLLs up to date")
    else:
        logger.warning("Using cached manifest, updates may not be available")
        if progress_callback:
            progress_callback(100, 100, "Using cached manifest")

    with _cache_init_lock:
        _cache_initialized = True
    initialize_dll_paths()

    if progress_callback:
        progress_callback(100, 100, "DLL cache initialized")
