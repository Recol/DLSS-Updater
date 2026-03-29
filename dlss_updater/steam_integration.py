"""
Steam Integration for DLSS Updater
Universal image fetching for ALL games (not just Steam launcher) via name matching
"""

import asyncio
import re
import threading
import msgspec
from pathlib import Path
from datetime import datetime, timedelta
import aiohttp

from dlss_updater.logger import setup_logger
from dlss_updater.database import db_manager

logger = setup_logger()

# msgspec decoder for better performance
_json_decoder = msgspec.json.Decoder()

# Thread-safe cache for normalize_game_name (replaces @lru_cache for free-threading)
_normalize_cache_lock = threading.Lock()
_normalize_cache: dict = {}


class SteamIntegration:
    """
    Steam integration for fetching game images and app list
    """

    # GitHub repository with daily-updated Steam app lists (no API key required)
    GITHUB_APP_LIST_BASE = "https://raw.githubusercontent.com/jsnli/steamappidlist/master/data"
    STEAM_APP_LIST_URLS = [
        f"{GITHUB_APP_LIST_BASE}/games_appid.json",
        f"{GITHUB_APP_LIST_BASE}/dlc_appid.json",
        f"{GITHUB_APP_LIST_BASE}/software_appid.json",
    ]
    CDN_PRIMARY = "https://cdn.cloudflare.steamstatic.com/steam/apps"
    CDN_FALLBACK = "https://cdn.akamai.steamstatic.com/steam/apps"

    APP_LIST_CACHE_DAYS = 7  # Re-download app list after 7 days
    IMAGE_SEMAPHORE = 5  # Max 5 concurrent image downloads

    def __init__(self):
        from dlss_updater.platform_utils import APP_CONFIG_DIR
        self.image_cache_dir = APP_CONFIG_DIR / "steam_images"
        self.image_cache_dir.mkdir(parents=True, exist_ok=True)

        # Local cache file for Steam app list (used for downloading, then stored in DB)
        self.app_list_cache_file = APP_CONFIG_DIR / "steam_app_list.json"

        # Database-backed storage replaces in-memory indexes
        # This saves ~20-30 MB RAM by eliminating the 207K-entry dictionaries
        self._db_populated = False  # Track if database has been populated

        self.semaphore = asyncio.Semaphore(self.IMAGE_SEMAPHORE)

        # Steam API owned games cache (for tiered resolution)
        self._owned_games_cache: dict[str, int] | None = None  # normalized_name -> app_id
        self._owned_games_lock = threading.Lock()

        logger.info(f"Steam image cache directory: {self.image_cache_dir}")

    async def migrate_image_cache(self) -> bool:
        """
        One-time migration to WebP thumbnail cache format.

        Purges old full-size JPEG images for all users upgrading.
        Non-blocking: file ops via asyncio.to_thread(), DB ops via aiosqlite.

        Returns True if migration was performed, False if already up-to-date.
        """
        from .config import config_manager
        import shutil

        CURRENT_CACHE_VERSION = 3  # WebP thumbnails with proper sizing (v2 had low-res bug)

        if config_manager.get_image_cache_version() >= CURRENT_CACHE_VERSION:
            return False  # Already migrated

        logger.info("Migrating image cache to WebP thumbnail format...")

        # 1. Clear the steam_images directory (async via thread pool)
        if self.image_cache_dir.exists():
            await asyncio.to_thread(shutil.rmtree, self.image_cache_dir)
        await asyncio.to_thread(self.image_cache_dir.mkdir, parents=True, exist_ok=True)

        # 2. Clear the database table (async via aiosqlite)
        await db_manager.clear_steam_images_cache()

        # 3. Update version flag (sync - small INI write, acceptable)
        config_manager.set_image_cache_version(CURRENT_CACHE_VERSION)

        logger.info("Image cache migration complete - old cache purged")
        return True

    async def update_app_list_if_needed(self):
        """
        Update Steam app list in database if needed.

        Checks:
        1. If database is empty, download and populate
        2. If JSON cache file is stale (>7 days old), re-download and update DB
        """
        try:
            # Check database first - if populated, check if cache file is stale
            db_count = await db_manager.get_steam_apps_count()

            if db_count == 0:
                # Database empty - need to populate
                logger.info("Steam apps database empty, downloading app list...")
                await self.download_steam_app_list()
                self._db_populated = True
            elif self.app_list_cache_file.exists():
                # Check file age for staleness
                file_time = datetime.fromtimestamp(self.app_list_cache_file.stat().st_mtime)
                age_days = (datetime.now() - file_time).days

                if age_days > self.APP_LIST_CACHE_DAYS:
                    logger.info(f"Steam app list cache is {age_days} days old, updating...")
                    await self.download_steam_app_list()
                else:
                    logger.info(f"Steam apps database has {db_count} entries (cache age: {age_days} days)")
                    self._db_populated = True
            else:
                # Database populated but no cache file - create cache file timestamp
                logger.info(f"Steam apps database has {db_count} entries")
                self._db_populated = True

        except Exception as e:
            logger.error(f"Error checking Steam app list: {e}", exc_info=True)

    async def download_steam_app_list(self):
        """
        Download full Steam app list and store in database with FTS5 indexing.

        Downloads from GitHub repository, normalizes names, and bulk-inserts
        into the steam_apps table with FTS5 triggers handling the search index.
        """
        try:
            logger.info("Downloading Steam app list from GitHub repository...")
            all_apps = []

            async with aiohttp.ClientSession() as session:
                # Download all category files
                for url in self.STEAM_APP_LIST_URLS:
                    try:
                        logger.info(f"Fetching {url.split('/')[-1]}...")
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                            if response.status != 200:
                                logger.warning(f"Failed to download {url}: HTTP {response.status}")
                                continue

                            # Read raw bytes and decode with msgspec (GitHub returns text/plain MIME type)
                            content = await response.read()
                            apps = _json_decoder.decode(content)

                            # GitHub repo format: array of {appid, name, last_modified, price_change_number}
                            if isinstance(apps, list):
                                all_apps.extend(apps)
                                logger.info(f"Downloaded {len(apps)} apps from {url.split('/')[-1]}")
                            else:
                                logger.warning(f"Unexpected format from {url}")

                    except TimeoutError:
                        logger.warning(f"Timeout downloading {url}")
                        continue
                    except Exception as e:
                        logger.warning(f"Error downloading {url}: {e}")
                        continue

            if not all_apps:
                logger.error("Failed to download any Steam app data")
                return

            logger.info(f"Total: {len(all_apps)} Steam apps, processing for database...")

            # Prepare data for database: (appid, name, name_normalized)
            # Normalize names by lowercasing and removing spaces for exact matching
            db_apps = []
            for app in all_apps:
                if not app.get('name'):
                    continue

                appid = app['appid']
                name = app['name']
                # Normalize: lowercase and remove spaces for exact matching
                name_normalized = self.normalize_game_name(name).replace(' ', '')
                db_apps.append((appid, name, name_normalized))

            logger.info(f"Inserting {len(db_apps)} apps into database with FTS5 indexing...")

            # Clear existing data and insert fresh
            await db_manager.clear_steam_apps()
            count = await db_manager.upsert_steam_apps(db_apps)

            # Save to local cache file for timestamp tracking (smaller subset)
            cache_data = msgspec.json.encode(all_apps)
            await asyncio.to_thread(self.app_list_cache_file.write_bytes, cache_data)

            self._db_populated = True
            logger.info(f"Steam app list saved to database ({count} apps with FTS5 index)")

        except Exception as e:
            logger.error(f"Error downloading Steam app list: {e}", exc_info=True)

    @staticmethod
    def normalize_game_name(name: str) -> str:
        """
        Normalize game name for matching
        - Lowercase
        - Remove special characters
        - Remove "The" prefix
        - Remove trademark symbols

        Thread-safe cached for performance (up to 1024 unique names).
        Uses manual cache instead of @lru_cache for Python 3.14 free-threading compatibility.
        """
        # Check cache first (with lock for thread safety)
        with _normalize_cache_lock:
            if name in _normalize_cache:
                return _normalize_cache[name]

        # Compute normalized name
        normalized = name.lower().strip()

        # Remove trademark symbols
        normalized = re.sub(r'[™®©]', '', normalized)

        # Remove "The" prefix
        if normalized.startswith('the '):
            normalized = normalized[4:]

        # Remove special characters except spaces and alphanumeric
        normalized = re.sub(r'[^a-z0-9\s]', '', normalized)

        # Collapse multiple spaces
        normalized = re.sub(r'\s+', ' ', normalized).strip()

        # Store in cache with size limit (thread-safe)
        with _normalize_cache_lock:
            # Limit cache size to 1024 entries (FIFO eviction)
            if len(_normalize_cache) >= 1024:
                # Remove oldest entry
                oldest_key = next(iter(_normalize_cache))
                del _normalize_cache[oldest_key]
            _normalize_cache[name] = normalized

        return normalized

    async def find_steam_app_id_by_name(self, game_name: str) -> int | None:
        """
        Find Steam app ID by game name using database-backed FTS5 search.

        Matching strategies (in order):
        1. Exact match on normalized name (O(log n) B-tree lookup)
        2. FTS5 search for partial matches

        Memory efficient: No in-memory indexes needed (~20-30 MB saved).

        Args:
            game_name: Name of the game to search for

        Returns:
            Steam app ID if found, None otherwise
        """
        try:
            # Normalize the game name
            normalized_name = self.normalize_game_name(game_name)
            spaceless = normalized_name.replace(' ', '')

            # Strategy 1: Exact match on normalized name (fast path)
            app_id = await db_manager.get_steam_app_by_name(spaceless)

            if app_id:
                logger.debug(f"Found Steam app ID {app_id} for '{game_name}' (exact match)")
                return app_id

            # Strategy 2: FTS5 search for partial/fuzzy matches
            results = await db_manager.search_steam_app(normalized_name, limit=1)

            if results:
                app_id, matched_name = results[0]
                logger.info(f"Found Steam app ID {app_id} for '{game_name}' via FTS5 (matched: '{matched_name}')")
                return app_id

            logger.debug(f"No Steam app ID found for '{game_name}'")
            return None

        except Exception as e:
            logger.error(f"Error finding Steam app ID for '{game_name}': {e}", exc_info=True)
            return None

    async def search_steam_apps(self, query: str, limit: int = 10) -> list[tuple[int, str]]:
        """
        Search Steam apps by name using FTS5 full-text search.

        Provides fast prefix and fuzzy matching for UI autocomplete.

        Args:
            query: Search query string
            limit: Maximum results to return (default 10)

        Returns:
            List of tuples (appid, name) matching the query
        """
        try:
            return await db_manager.search_steam_app(query, limit)
        except Exception as e:
            logger.error(f"Error searching Steam apps: {e}", exc_info=True)
            return []

    async def detect_steam_app_id_from_manifest(self, game_dir: Path) -> int | None:
        """
        Detect Steam app ID from appmanifest files (for Steam launcher games)
        This is faster and more accurate than name matching

        Args:
            game_dir: Path to game directory

        Returns:
            Steam app ID if found, None otherwise
        """
        try:
            # Navigate up to find steamapps directory
            current = game_dir
            steamapps_dir = None

            for _ in range(5):  # Max 5 levels up
                current = current.parent
                if current.name.lower() == 'steamapps':
                    steamapps_dir = current
                    break

            if not steamapps_dir or not steamapps_dir.exists():
                return None

            # Get game folder name for comparison
            game_folder_name = game_dir.name.lower()

            # Search appmanifest files
            for manifest_file in steamapps_dir.glob("appmanifest_*.acf"):
                try:
                    content = manifest_file.read_text(encoding='utf-8', errors='ignore')

                    # Parse VDF for installdir
                    match = re.search(r'"installdir"\s+"([^"]+)"', content, re.IGNORECASE)
                    if match:
                        install_dir = match.group(1).lower()
                        if install_dir == game_folder_name:
                            # Extract app ID from filename: appmanifest_271590.acf -> 271590
                            app_id = int(manifest_file.stem.split('_')[1])
                            logger.info(f"Found Steam app ID {app_id} from appmanifest for {game_dir.name}")
                            return app_id

                except Exception as e:
                    logger.debug(f"Error parsing {manifest_file}: {e}")
                    continue

            return None

        except Exception as e:
            logger.error(f"Error detecting Steam app ID from manifest for {game_dir}: {e}", exc_info=True)
            return None

    async def fetch_steam_header_image(self, app_id: int) -> Path | None:
        """
        Fetch Steam game header image and save as optimized WebP thumbnail.

        Fully async/non-blocking:
        - Network requests via aiohttp
        - Image processing via asyncio.to_thread() (Pillow)
        - File I/O via aiofiles
        - Database ops via aiosqlite

        Args:
            app_id: Steam app ID

        Returns:
            Path to cached WebP thumbnail file, or None if failed
        """
        try:
            # Check if already cached (async DB query)
            cached_path = await db_manager.get_cached_image_path(app_id)
            if cached_path:
                cache_file = Path(cached_path)
                # File exists check is fast enough to not need async
                if cache_file.exists():
                    logger.debug(f"Using cached image for app {app_id}")
                    return cache_file

            # Check if previously failed - skip immediately (async DB query)
            if await db_manager.is_image_fetch_failed(app_id):
                logger.debug(f"Skipping image fetch for app {app_id} - previously failed")
                return None

            # Use semaphore to limit concurrent downloads
            async with self.semaphore:
                # Save as .webp instead of .jpg for optimized thumbnails
                cache_file = self.image_cache_dir / f"{app_id}.webp"

                # Try primary CDN first, then fallback
                urls = [
                    f"{self.CDN_PRIMARY}/{app_id}/header.jpg",
                    f"{self.CDN_FALLBACK}/{app_id}/header.jpg"
                ]

                async with aiohttp.ClientSession() as session:
                    for url in urls:
                        try:
                            # Async network request
                            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                                if response.status == 200:
                                    raw_data = await response.read()

                                    # Create and save WebP thumbnail (async CPU + file I/O)
                                    from .image_optimizer import save_thumbnail
                                    await save_thumbnail(raw_data, cache_file)

                                    # Record in database (async DB write)
                                    await db_manager.cache_steam_image(app_id, str(cache_file))

                                    logger.info(f"Fetched and cached thumbnail for Steam app {app_id}")
                                    return cache_file

                                elif response.status == 404:
                                    logger.debug(f"Image not found (404) for app {app_id} at {url}")
                                    continue

                        except TimeoutError:
                            logger.debug(f"Timeout fetching image from {url}")
                            continue
                        except Exception as e:
                            logger.debug(f"Error fetching from {url}: {e}")
                            continue

                # All URLs failed - expected for games without images
                logger.debug(f"Failed to fetch image for Steam app {app_id} from all CDNs")
                await db_manager.mark_image_fetch_failed(app_id)
                return None

        except Exception as e:
            logger.error(f"Error fetching Steam image for app {app_id}: {e}", exc_info=True)
            return None

    async def queue_image_downloads(self, app_ids: list[int]):
        """
        Queue multiple image downloads with concurrency control

        Args:
            app_ids: List of Steam app IDs to download
        """
        logger.info(f"Queueing {len(app_ids)} image downloads...")

        tasks = [self.fetch_steam_header_image(app_id) for app_id in app_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        successful = sum(1 for r in results if isinstance(r, Path))
        logger.info(f"Image download complete: {successful}/{len(app_ids)} successful")

    async def validate_api_key(self, api_key: str) -> bool:
        """Validate Steam API key via ISteamWebAPIUtil/GetSupportedAPIList.

        This is a lightweight endpoint with no rate limiting that requires
        a valid API key. Returns True if the key is valid.
        """
        url = f"https://api.steampowered.com/ISteamWebAPIUtil/GetSupportedAPIList/v1/?key={api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    is_valid = resp.status == 200
                    if is_valid:
                        logger.info("Steam API key validated successfully")
                    else:
                        logger.warning(f"Steam API key validation failed: HTTP {resp.status}")
                    return is_valid
        except Exception as e:
            logger.error(f"Error validating Steam API key: {e}")
            return False

    async def get_owned_games(self, api_key: str, steam_id: str) -> dict[str, int]:
        """Fetch user's owned games via IPlayerService/GetOwnedGames.

        Returns dict mapping normalized game name (spaceless) -> app_id.
        This is a single API call that returns all owned games with names.
        The result is cached in _owned_games_cache for use by find_app_id_via_api().

        Args:
            api_key: Steam Web API key
            steam_id: Steam 64-bit user ID

        Returns:
            Dict mapping normalized_name -> app_id

        Raises:
            ValueError: If API returns non-200 status
        """
        url = (
            f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
            f"?key={api_key}&steamid={steam_id}"
            f"&include_appinfo=1&include_played_free_games=1"
            f"&skip_unvetted_apps=0"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 401:
                    raise ValueError("Invalid API key")
                if resp.status != 200:
                    raise ValueError(f"Steam API returned HTTP {resp.status}")
                data = _json_decoder.decode(await resp.read())

        response = data.get("response", {})
        games = response.get("games", [])

        if not games and response.get("game_count", 0) == 0:
            logger.warning(
                "GetOwnedGames returned 0 games. "
                "User's Steam profile game details may be set to private."
            )

        result = {}
        for game in games:
            name = game.get("name", "")
            appid = game.get("appid")
            if name and appid:
                normalized = self.normalize_game_name(name).replace(' ', '')
                result[normalized] = appid

        logger.info(f"Fetched {len(result)} owned games from Steam API for user {steam_id}")

        with self._owned_games_lock:
            self._owned_games_cache = result

        return result

    async def find_app_id_via_api(self, game_name: str) -> int | None:
        """Look up a game in the cached owned games list.

        Must call get_owned_games() first to populate the cache.
        Performs a normalized name match against the user's Steam library.

        Args:
            game_name: Game name (typically the directory name)

        Returns:
            Steam app ID if found in owned games, None otherwise
        """
        with self._owned_games_lock:
            cache = self._owned_games_cache

        if cache is None:
            return None

        normalized = self.normalize_game_name(game_name).replace(' ', '')
        app_id = cache.get(normalized)

        if app_id:
            logger.debug(f"Found app ID {app_id} for '{game_name}' via Steam API owned games")

        return app_id

    async def find_app_id_via_store_search(self, game_name: str) -> int | None:
        """Search Steam Store API for a game by name.

        Uses store.steampowered.com/api/storesearch which is unauthenticated
        but rate-limited to ~200 requests per 5 minutes.

        Args:
            game_name: Game name to search for

        Returns:
            Steam app ID of the best match, or None
        """
        import urllib.parse
        encoded_name = urllib.parse.quote(game_name)
        url = f"https://store.steampowered.com/api/storesearch/?term={encoded_name}&l=english&cc=US"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 429:
                        logger.warning("Steam store search rate limited, skipping")
                        return None
                    if resp.status != 200:
                        logger.debug(f"Store search returned HTTP {resp.status} for '{game_name}'")
                        return None
                    data = _json_decoder.decode(await resp.read())

            items = data.get("items", [])
            if not items:
                logger.debug(f"Store search found no results for '{game_name}'")
                return None

            best = items[0]
            app_id = best.get("id")
            matched_name = best.get("name", "unknown")
            logger.debug(f"Store search for '{game_name}' -> app_id {app_id} ('{matched_name}')")
            return app_id

        except TimeoutError:
            logger.debug(f"Store search timed out for '{game_name}'")
            return None
        except Exception as e:
            logger.debug(f"Store search error for '{game_name}': {e}")
            return None

    async def detect_steam_id_from_loginusers_vdf(self) -> str | None:
        """Auto-detect Steam 64-bit ID from loginusers.vdf.

        Parses Steam's config/loginusers.vdf to find the most recently
        logged-in user (MostRecent=1). The top-level keys in the 'users'
        block are Steam64 IDs.

        Returns:
            Steam 64-bit ID string, or None if not detectable
        """
        try:
            from .scanner import get_steam_install_path

            steam_path = get_steam_install_path()
            if not steam_path:
                logger.debug("Cannot detect Steam ID: Steam installation not found")
                return None

            vdf_path = Path(steam_path) / "config" / "loginusers.vdf"
            if not vdf_path.exists():
                logger.debug(f"loginusers.vdf not found at {vdf_path}")
                return None

            # Parse the VDF file (simple key-value format)
            content = await asyncio.to_thread(vdf_path.read_text, encoding='utf-8', errors='ignore')

            # Parse users block - keys are Steam64 IDs
            # Format: "76561198084417777" { "PersonaName" "User" "MostRecent" "1" }
            current_steam_id = None
            most_recent_id = None
            first_id = None

            for line in content.split('\n'):
                line = line.strip()

                # Match Steam64 ID (17-digit number as a quoted key)
                id_match = re.match(r'^"(\d{17})"', line)
                if id_match:
                    current_steam_id = id_match.group(1)
                    if first_id is None:
                        first_id = current_steam_id

                # Check for MostRecent flag
                if current_steam_id and '"MostRecent"' in line and '"1"' in line:
                    most_recent_id = current_steam_id

            result = most_recent_id or first_id
            if result:
                logger.info(f"Auto-detected Steam ID: {result}")
            else:
                logger.debug("No Steam ID found in loginusers.vdf")

            return result

        except Exception as e:
            logger.error(f"Error detecting Steam ID from loginusers.vdf: {e}", exc_info=True)
            return None


# Singleton instance
steam_integration = SteamIntegration()


# Convenience functions for external use
async def update_steam_app_list_if_needed():
    """Update Steam app list if stale"""
    await steam_integration.update_app_list_if_needed()


async def find_steam_app_id_by_name(game_name: str) -> int | None:
    """Find Steam app ID by game name"""
    return await steam_integration.find_steam_app_id_by_name(game_name)


async def detect_steam_app_id_from_manifest(game_dir: Path) -> int | None:
    """Detect Steam app ID from appmanifest files"""
    return await steam_integration.detect_steam_app_id_from_manifest(game_dir)


async def fetch_steam_image(app_id: int) -> Path | None:
    """Fetch Steam header image"""
    return await steam_integration.fetch_steam_header_image(app_id)


async def migrate_image_cache_if_needed() -> bool:
    """
    One-time migration to WebP thumbnail cache format.

    Should be called early during app startup. Purges old full-size JPEG images
    and clears the database cache for all users upgrading to the new format.

    Returns True if migration was performed, False if already up-to-date.
    """
    return await steam_integration.migrate_image_cache()


async def queue_image_downloads(app_ids: list[int]):
    """Queue multiple image downloads"""
    await steam_integration.queue_image_downloads(app_ids)


async def search_steam_apps(query: str, limit: int = 10) -> list[tuple[int, str]]:
    """
    Search Steam apps by name using FTS5 full-text search.

    Args:
        query: Search query string
        limit: Maximum results to return (default 10)

    Returns:
        List of tuples (appid, name) matching the query
    """
    return await steam_integration.search_steam_apps(query, limit)


async def validate_steam_api_key(api_key: str) -> bool:
    """Validate a Steam Web API key."""
    return await steam_integration.validate_api_key(api_key)


async def get_steam_owned_games(api_key: str, steam_id: str) -> dict[str, int]:
    """Fetch user's owned games from Steam API."""
    return await steam_integration.get_owned_games(api_key, steam_id)


async def find_app_id_via_api(game_name: str) -> int | None:
    """Look up game in cached Steam API owned games."""
    return await steam_integration.find_app_id_via_api(game_name)


async def find_app_id_via_store_search(game_name: str) -> int | None:
    """Search Steam Store for a game by name."""
    return await steam_integration.find_app_id_via_store_search(game_name)


async def detect_steam_id() -> str | None:
    """Auto-detect Steam 64-bit ID from local Steam installation."""
    return await steam_integration.detect_steam_id_from_loginusers_vdf()
