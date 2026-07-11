"""
High-Performance Async VDF Parser for Steam Manifest Files

Parses Steam's VDF (Valve Data Format) files used in:
- libraryfolders.vdf: Steam library locations
- appmanifest_*.acf: Installed game metadata

Uses pre-compiled regex for O(1) key-value extraction and aiofiles for async I/O.
"""

import re
from pathlib import Path
from typing import Any

import aiofiles
import anyio

from dlss_updater.logger import setup_logger

logger = setup_logger()


class VDFParser:
    """
    Lightweight async VDF parser for Steam manifest files.

    VDF format is a simple key-value structure with nested blocks:
    "key"    "value"
    "block"
    {
        "nested_key"    "nested_value"
    }
    """

    # Pre-compiled regex patterns for O(1) matching per line
    _KV_PATTERN = re.compile(r'"([^"]+)"\s+"([^"]*)"')
    _BLOCK_START = re.compile(r'"([^"]+)"\s*$')

    @classmethod
    async def parse_file(cls, file_path: Path, encoding: str = 'utf-8') -> dict[str, Any]:
        """
        Parse a VDF file and return its contents as a nested dictionary.

        Args:
            file_path: Path to the VDF file
            encoding: File encoding (default utf-8)

        Returns:
            Parsed VDF data as nested dict
        """
        try:
            async with aiofiles.open(file_path, 'r', encoding=encoding, errors='ignore') as f:
                content = await f.read()

            return cls._parse_vdf_content(content)
        except Exception as e:
            logger.error(f"Error parsing VDF file {file_path}: {e}")
            return {}

    @classmethod
    def _parse_vdf_content(cls, content: str) -> dict[str, Any]:
        """
        Parse VDF content string into nested dictionary.

        Uses a simple stack-based parser for nested blocks.
        """
        result = {}
        stack = [result]
        current_key = None

        for line in content.splitlines():
            line = line.strip()

            if not line or line.startswith('//'):
                continue

            # Check for key-value pair
            kv_match = cls._KV_PATTERN.match(line)
            if kv_match:
                key, value = kv_match.groups()
                stack[-1][key] = value
                continue

            # Check for block start (key followed by {)
            block_match = cls._BLOCK_START.match(line)
            if block_match:
                current_key = block_match.group(1)
                continue

            # Opening brace - start new nested dict
            if line == '{' and current_key:
                new_block = {}
                stack[-1][current_key] = new_block
                stack.append(new_block)
                current_key = None
                continue

            # Closing brace - pop stack
            if line == '}':
                if len(stack) > 1:
                    stack.pop()
                continue

        return result

    @classmethod
    async def parse_library_folders(cls, vdf_path: Path) -> list[Path]:
        """
        Parse libraryfolders.vdf to extract all Steam library paths.

        Args:
            vdf_path: Path to libraryfolders.vdf

        Returns:
            List of steamapps directories
        """
        libraries = []

        try:
            data = await cls.parse_file(vdf_path)

            # libraryfolders.vdf structure:
            # "libraryfolders"
            # {
            #     "0" { "path" "C:\\Program Files (x86)\\Steam" ... }
            #     "1" { "path" "D:\\SteamLibrary" ... }
            # }

            library_data = data.get('libraryfolders', data)

            for key, value in library_data.items():
                if isinstance(value, dict) and 'path' in value:
                    lib_path = Path(value['path']) / 'steamapps'
                    if lib_path.exists():
                        libraries.append(lib_path)
                        logger.debug(f"Found Steam library: {lib_path}")
                elif key == 'path' and isinstance(value, str):
                    # Handle older VDF format
                    lib_path = Path(value) / 'steamapps'
                    if lib_path.exists():
                        libraries.append(lib_path)
                        logger.debug(f"Found Steam library (legacy format): {lib_path}")

            logger.info(f"Found {len(libraries)} Steam library folders")

        except Exception as e:
            logger.error(f"Error parsing libraryfolders.vdf: {e}")

        return libraries

    @classmethod
    async def parse_appmanifest(cls, acf_path: Path) -> dict[str, str] | None:
        """
        Parse appmanifest_*.acf to extract game metadata.

        Args:
            acf_path: Path to appmanifest_*.acf file

        Returns:
            Dict with appid, name, installdir, or None if parsing fails
        """
        try:
            data = await cls.parse_file(acf_path)

            # appmanifest structure:
            # "AppState"
            # {
            #     "appid"    "123456"
            #     "name"     "Game Name"
            #     "installdir"    "GameFolder"
            #     ...
            # }

            app_state = data.get('AppState', data)

            # Extract required fields
            appid = app_state.get('appid')
            name = app_state.get('name')
            installdir = app_state.get('installdir')

            if installdir:
                return {
                    'appid': appid,
                    'name': name or f"App {appid}",
                    'installdir': installdir
                }

            return None

        except Exception as e:
            logger.debug(f"Error parsing appmanifest {acf_path}: {e}")
            return None

    @classmethod
    async def enumerate_steam_games(cls, steam_path: Path) -> list[dict[str, Any]]:
        """
        Enumerate all installed Steam games using appmanifest files.

        This is MUCH faster than walking entire library directories because:
        - Only parses small manifest files (not entire directory trees)
        - O(n) where n = installed games, not total files
        - Parallel manifest parsing

        Args:
            steam_path: Steam installation path (e.g., C:\\Program Files (x86)\\Steam)

        Returns:
            List of game dicts with: app_id, name, path, steamapps_dir
        """
        games = []

        # Get all library folders
        library_folders_vdf = steam_path / 'steamapps' / 'libraryfolders.vdf'

        if library_folders_vdf.exists():
            steamapps_dirs = await cls.parse_library_folders(library_folders_vdf)
        else:
            # Fallback to default location
            default_steamapps = steam_path / 'steamapps'
            steamapps_dirs = [default_steamapps] if default_steamapps.exists() else []

        if not steamapps_dirs:
            logger.warning("No Steam library folders found")
            return games

        # Process all libraries in parallel
        async def process_library(steamapps_dir: Path) -> list[dict[str, Any]]:
            library_games = []

            # Find all appmanifest files
            manifest_files = list(steamapps_dir.glob('appmanifest_*.acf'))

            if not manifest_files:
                return library_games

            # Parse manifests concurrently with semaphore limit
            semaphore = anyio.Semaphore(20)  # Limit concurrent file reads

            manifest_results: list = [None] * len(manifest_files)
            manifest_errors: list = [None] * len(manifest_files)

            async def parse_with_limit(i: int, manifest_path: Path) -> None:
                async with semaphore:
                    try:
                        manifest_results[i] = (
                            manifest_path,
                            await cls.parse_appmanifest(manifest_path),
                        )
                    except Exception as e:
                        manifest_errors[i] = e

            async with anyio.create_task_group() as tg:
                for i, m in enumerate(manifest_files):
                    tg.start_soon(parse_with_limit, i, m)

            common_dir = steamapps_dir / 'common'

            for i, result in enumerate(manifest_results):
                if manifest_errors[i] is not None or result is None:
                    continue

                manifest_path, game_data = result

                if game_data and game_data.get('installdir'):
                    game_dir = common_dir / game_data['installdir']

                    if game_dir.exists():
                        library_games.append({
                            'app_id': int(game_data['appid']) if game_data.get('appid') else None,
                            'name': game_data['name'],
                            'path': game_dir,
                            'steamapps_dir': steamapps_dir
                        })

            return library_games

        # Process all libraries concurrently
        library_results: list = [None] * len(steamapps_dirs)
        library_errors: list = [None] * len(steamapps_dirs)

        async def _run_library(i: int, lib: Path) -> None:
            try:
                library_results[i] = await process_library(lib)
            except Exception as e:
                library_errors[i] = e

        async with anyio.create_task_group() as tg:
            for i, lib in enumerate(steamapps_dirs):
                tg.start_soon(_run_library, i, lib)

        for i, result in enumerate(library_results):
            if library_errors[i] is not None:
                continue
            if isinstance(result, list):
                games.extend(result)

        logger.info(f"Enumerated {len(games)} installed Steam games via appmanifest files")
        return games
