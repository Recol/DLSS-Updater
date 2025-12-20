"""
Steam Integration for DLSS Updater
Universal image fetching for ALL games (not just Steam launcher) via name matching
"""

import asyncio
import re
import msgspec
from functools import lru_cache
from pathlib import Path
from typing import Optional, List
from datetime import datetime, timedelta
import aiohttp
import appdirs

from dlss_updater.logger import setup_logger
from dlss_updater.database import db_manager

logger = setup_logger()

# msgspec decoder for better performance
_json_decoder = msgspec.json.Decoder()


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
        app_name = "DLSS-Updater"
        app_author = "Recol"
        config_dir = appdirs.user_config_dir(app_name, app_author)
        self.image_cache_dir = Path(config_dir) / "steam_images"
        self.image_cache_dir.mkdir(parents=True, exist_ok=True)

        # Local cache file for Steam app list (instead of storing all 200k+ apps in database)
        self.app_list_cache_file = Path(config_dir) / "steam_app_list.json"
        self.app_list_cache = None  # Lazy-loaded

        # Pre-computed indexes for fast O(1) lookups
        self.app_index = None  # {normalized_name: app_id}
        self.app_index_spaceless = None  # {normalized_name_no_spaces: app_id}

        self.semaphore = asyncio.Semaphore(self.IMAGE_SEMAPHORE)
        logger.info(f"Steam image cache directory: {self.image_cache_dir}")
        logger.info(f"Steam app list cache file: {self.app_list_cache_file}")

    async def update_app_list_if_needed(self):
        """Update Steam app list cache file if stale (>7 days old)"""
        try:
            if not self.app_list_cache_file.exists():
                logger.info("No Steam app list cache found, downloading...")
                await self.download_steam_app_list()
            else:
                # Check file age
                file_time = datetime.fromtimestamp(self.app_list_cache_file.stat().st_mtime)
                age_days = (datetime.now() - file_time).days

                if age_days > self.APP_LIST_CACHE_DAYS:
                    logger.info(f"Steam app list cache is {age_days} days old, updating...")
                    await self.download_steam_app_list()
                else:
                    logger.info(f"Steam app list cache is up to date (age: {age_days} days)")

        except Exception as e:
            logger.error(f"Error checking Steam app list cache: {e}", exc_info=True)

    async def download_steam_app_list(self):
        """Download full Steam app list to local cache file (not database)"""
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

                    except asyncio.TimeoutError:
                        logger.warning(f"Timeout downloading {url}")
                        continue
                    except Exception as e:
                        logger.warning(f"Error downloading {url}: {e}")
                        continue

            if not all_apps:
                logger.error("Failed to download any Steam app data")
                return

            logger.info(f"Total: {len(all_apps)} Steam apps, saving to cache file...")

            # Save to local cache file (not database)
            cache_data = msgspec.json.encode(all_apps)
            await asyncio.to_thread(self.app_list_cache_file.write_bytes, cache_data)

            # Clear in-memory cache to force reload
            self.app_list_cache = None

            logger.info(f"Steam app list cache saved successfully ({len(all_apps)} apps)")

        except Exception as e:
            logger.error(f"Error downloading Steam app list: {e}", exc_info=True)

    @staticmethod
    @lru_cache(maxsize=1024)
    def normalize_game_name(name: str) -> str:
        """
        Normalize game name for matching
        - Lowercase
        - Remove special characters
        - Remove "The" prefix
        - Remove trademark symbols

        Cached for performance (up to 1024 unique names).
        """
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

        return normalized

    async def _load_app_list_cache(self):
        """Load Steam app list from cache file into memory"""
        if self.app_list_cache is not None:
            return  # Already loaded

        if not self.app_list_cache_file.exists():
            logger.warning("Steam app list cache file not found")
            return

        try:
            logger.debug("Loading Steam app list cache...")
            cache_data = await asyncio.to_thread(self.app_list_cache_file.read_bytes)
            self.app_list_cache = _json_decoder.decode(cache_data)
            logger.info(f"Loaded {len(self.app_list_cache)} apps from cache")
        except Exception as e:
            logger.error(f"Error loading Steam app list cache: {e}", exc_info=True)
            self.app_list_cache = []

    async def _build_app_index(self):
        """
        Build normalized indexes from app list cache for O(1) lookups
        One-time cost: O(n) where n = 207,746 apps
        """
        if self.app_index is not None:
            return  # Already built

        await self._load_app_list_cache()

        if not self.app_list_cache:
            logger.warning("Cannot build app index - cache not available")
            return

        logger.info(f"Building Steam app indexes from {len(self.app_list_cache)} apps...")

        self.app_index = {}
        self.app_index_spaceless = {}

        for app in self.app_list_cache:
            if not app.get('name'):
                continue

            app_id = app['appid']

            # Index with spaces (exact match)
            normalized = self.normalize_game_name(app['name'])
            self.app_index[normalized] = app_id

            # Index without spaces (for folder name matching)
            spaceless = normalized.replace(' ', '')
            self.app_index_spaceless[spaceless] = app_id

        logger.info(f"App indexes built: {len(self.app_index)} normalized names, {len(self.app_index_spaceless)} spaceless names")

    async def find_steam_app_id_by_name(self, game_name: str) -> Optional[int]:
        """
        Find Steam app ID by game name using pre-built indexes
        Complexity: O(1) hash lookup vs previous O(n) linear search

        Matching strategies (in order):
        1. Database check (previously matched games)
        2. Exact match (with spaces)
        3. Spaceless match (handles folder names like "BlackMythWukong")

        Args:
            game_name: Name of the game to search for

        Returns:
            Steam app ID if found, None otherwise
        """
        try:
            # Strategy 1: Check database first (fast path for previously matched games)
            normalized_name = self.normalize_game_name(game_name)
            app_id = await db_manager.find_steam_app_by_name(normalized_name)

            if app_id:
                logger.debug(f"Found cached Steam app ID {app_id} for '{game_name}'")
                return app_id

            # Build index if not loaded
            await self._build_app_index()

            if not self.app_index:
                logger.warning(f"Cannot search for '{game_name}' - app indexes not available")
                return None

            # Strategy 2: Exact match (with spaces) - O(1)
            if normalized_name in self.app_index:
                app_id = self.app_index[normalized_name]
                logger.info(f"Found Steam app ID {app_id} for '{game_name}' (exact match)")

                # Store in database for faster future lookups
                await db_manager.upsert_steam_app(app_id, game_name)
                return app_id

            # Strategy 3: Spaceless match (handles folder names) - O(1)
            spaceless = normalized_name.replace(' ', '')
            if spaceless in self.app_index_spaceless:
                app_id = self.app_index_spaceless[spaceless]
                logger.info(f"Found Steam app ID {app_id} for '{game_name}' (spaceless match)")

                # Store in database for faster future lookups
                await db_manager.upsert_steam_app(app_id, game_name)
                return app_id

            logger.debug(f"No Steam app ID found for '{game_name}'")
            return None

        except Exception as e:
            logger.error(f"Error finding Steam app ID for '{game_name}': {e}", exc_info=True)
            return None

    async def detect_steam_app_id_from_manifest(self, game_dir: Path) -> Optional[int]:
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

    async def fetch_steam_header_image(self, app_id: int) -> Optional[Path]:
        """
        Fetch Steam game header image from CDN

        Args:
            app_id: Steam app ID

        Returns:
            Path to cached image file, or None if failed
        """
        try:
            # Check if already cached
            cached_path = await db_manager.get_cached_image_path(app_id)
            if cached_path:
                cache_file = Path(cached_path)
                if cache_file.exists():
                    logger.debug(f"Using cached image for app {app_id}")
                    return cache_file

            # Check if previously failed - skip immediately
            if await db_manager.is_image_fetch_failed(app_id):
                logger.debug(f"Skipping image fetch for app {app_id} - previously failed")
                return None

            # Use semaphore to limit concurrent downloads
            async with self.semaphore:
                cache_file = self.image_cache_dir / f"{app_id}.jpg"

                # Try primary CDN first, then fallback
                urls = [
                    f"{self.CDN_PRIMARY}/{app_id}/header.jpg",
                    f"{self.CDN_FALLBACK}/{app_id}/header.jpg"
                ]

                async with aiohttp.ClientSession() as session:
                    for url in urls:
                        try:
                            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                                if response.status == 200:
                                    # Save image to cache
                                    image_data = await response.read()
                                    cache_file.write_bytes(image_data)

                                    # Record in database
                                    await db_manager.cache_steam_image(app_id, str(cache_file))

                                    logger.info(f"Fetched and cached image for Steam app {app_id}")
                                    return cache_file

                                elif response.status == 404:
                                    logger.debug(f"Image not found (404) for app {app_id} at {url}")
                                    continue

                        except asyncio.TimeoutError:
                            logger.debug(f"Timeout fetching image from {url}")
                            continue
                        except Exception as e:
                            logger.debug(f"Network error fetching from {url}: {e}")
                            continue

                # All URLs failed - expected for games without images
                logger.debug(f"Failed to fetch image for Steam app {app_id} from all CDNs")
                await db_manager.mark_image_fetch_failed(app_id)
                return None

        except Exception as e:
            logger.error(f"Error fetching Steam image for app {app_id}: {e}", exc_info=True)
            return None

    async def queue_image_downloads(self, app_ids: List[int]):
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


# Singleton instance
steam_integration = SteamIntegration()


# Convenience functions for external use
async def update_steam_app_list_if_needed():
    """Update Steam app list if stale"""
    await steam_integration.update_app_list_if_needed()


async def find_steam_app_id_by_name(game_name: str) -> Optional[int]:
    """Find Steam app ID by game name"""
    return await steam_integration.find_steam_app_id_by_name(game_name)


async def detect_steam_app_id_from_manifest(game_dir: Path) -> Optional[int]:
    """Detect Steam app ID from appmanifest files"""
    return await steam_integration.detect_steam_app_id_from_manifest(game_dir)


async def fetch_steam_image(app_id: int) -> Optional[Path]:
    """Fetch Steam header image"""
    return await steam_integration.fetch_steam_header_image(app_id)


async def queue_image_downloads(app_ids: List[int]):
    """Queue multiple image downloads"""
    await steam_integration.queue_image_downloads(app_ids)
