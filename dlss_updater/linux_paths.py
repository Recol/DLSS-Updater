"""
Linux Path Detection Module
Handles Steam, Proton, and Wine path detection on Linux for game DLL scanning.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("DLSSUpdater")

# System-level path prefixes that typically require root access
SYSTEM_PATH_PREFIXES = ('/usr/', '/opt/', '/lib/', '/var/')

# Common Linux Steam installation paths
STEAM_PATHS = [
    Path.home() / ".steam" / "steam",
    Path.home() / ".steam" / "debian-installation",
    Path.home() / ".local" / "share" / "Steam",
    Path("/usr/share/steam"),
    # Flatpak Steam
    Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
]

# Common Wine prefix locations
WINE_PREFIX_PATHS = [
    Path.home() / ".wine",
    Path.home() / ".local" / "share" / "wine",
]

# Lutris game locations
LUTRIS_PATHS = [
    Path.home() / "Games",
    Path.home() / ".local" / "share" / "lutris" / "runners" / "wine",
]


def is_system_path(path: Path) -> bool:
    """
    Check if a path is a system-level installation (requires root).

    System paths include:
    - /usr/share/* - System-wide packages
    - /opt/* - Optional software packages
    - /lib/* - System libraries
    - /var/* - Variable data

    Args:
        path: Path to check

    Returns:
        True if this is a system-level path
    """
    path_str = str(path)
    return any(path_str.startswith(prefix) for prefix in SYSTEM_PATH_PREFIXES)


def can_write_path(path: Path) -> bool:
    """
    Check if current user has write permission to a path.

    Args:
        path: Path to check

    Returns:
        True if writable, False otherwise
    """
    try:
        if path.is_dir():
            return os.access(path, os.W_OK)
        elif path.exists():
            return os.access(path.parent, os.W_OK)
        else:
            # Check parent for non-existent paths
            parent = path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            return os.access(parent, os.W_OK) if parent.exists() else False
    except (OSError, PermissionError):
        return False


async def can_write_path_async(path: Path) -> bool:
    """Async version of can_write_path."""
    return await asyncio.to_thread(can_write_path, path)


async def filter_accessible_paths(
    paths: list[Path],
    collect_skipped: bool = True
) -> tuple[list[Path], list[dict[str, str]]]:
    """
    Filter paths to only those accessible by current user.

    Args:
        paths: List of paths to check
        collect_skipped: Whether to collect info about skipped paths

    Returns:
        Tuple of (accessible_paths, skipped_info)
        skipped_info contains dicts with 'path', 'reason' keys
    """
    accessible: list[Path] = []
    skipped: list[dict[str, str]] = []

    for path in paths:
        if await can_write_path_async(path):
            accessible.append(path)
        elif collect_skipped:
            is_sys = is_system_path(path)
            reason = "System path (requires root)" if is_sys else "Permission denied"
            skipped.append({
                'path': str(path),
                'reason': reason
            })
            logger.debug(f"Skipping inaccessible path: {path} ({reason})")

    return accessible, skipped


def get_linux_steam_path_sync() -> Path | None:
    """
    Detect native Steam installation on Linux (synchronous version).

    Returns:
        Path to Steam installation or None if not found.
    """
    for steam_path in STEAM_PATHS:
        if steam_path.exists() and steam_path.is_dir():
            logger.debug(f"Found Steam at: {steam_path}")
            return steam_path
    return None


async def get_linux_steam_path() -> Path | None:
    """
    Detect native Steam installation on Linux.

    Returns:
        Path to Steam installation or None if not found.
    """
    return await asyncio.to_thread(get_linux_steam_path_sync)


async def get_linux_steam_libraries(steam_path: Path) -> list[Path]:
    """
    Get all Steam library folders on Linux.
    Parses libraryfolders.vdf (same format as Windows).

    Args:
        steam_path: Path to Steam installation.

    Returns:
        List of paths to steamapps/common directories.
    """
    libraries = []
    library_file = steam_path / "steamapps" / "libraryfolders.vdf"

    def _parse_libraries() -> list[Path]:
        result = []
        if not library_file.exists():
            return result

        try:
            with library_file.open("r", encoding="utf-8") as f:
                content = f.read()
                # Parse VDF format - look for "path" entries
                for line in content.split("\n"):
                    line = line.strip()
                    if '"path"' in line:
                        # Extract path value from: "path"		"C:\\SteamLibrary"
                        parts = line.split('"')
                        if len(parts) >= 4:
                            path = parts[3]
                            # Handle both Windows and Linux paths
                            path = path.replace("\\\\", "/").replace("\\", "/")
                            common_path = Path(path) / "steamapps" / "common"
                            if common_path.exists():
                                result.append(common_path)
        except Exception as e:
            logger.warning(f"Error parsing libraryfolders.vdf: {e}")

        return result

    libraries = await asyncio.to_thread(_parse_libraries)

    # Always include default library
    default_lib = steam_path / "steamapps" / "common"
    if default_lib.exists() and default_lib not in libraries:
        libraries.insert(0, default_lib)

    logger.info(f"Found {len(libraries)} Steam libraries on Linux")
    return libraries


async def get_proton_prefixes(steam_path: Path) -> list[Path]:
    """
    Find all Proton prefix directories (Windows compatibility layers).

    Proton prefixes are stored at:
    ~/.steam/steam/steamapps/compatdata/<appid>/pfx/

    Each prefix contains a full Windows-like directory structure with DLLs.

    Args:
        steam_path: Path to Steam installation.

    Returns:
        List of paths to Proton prefix drive_c directories.
    """
    prefixes = []
    compatdata_base = steam_path / "steamapps" / "compatdata"

    if not compatdata_base.exists():
        return prefixes

    def _find_prefixes() -> list[Path]:
        result = []
        try:
            for app_dir in compatdata_base.iterdir():
                if app_dir.is_dir():
                    pfx_path = app_dir / "pfx"
                    if pfx_path.exists():
                        # Proton prefix has drive_c with Windows structure
                        drive_c = pfx_path / "drive_c"
                        if drive_c.exists():
                            result.append(drive_c)
        except PermissionError as e:
            logger.warning(f"Permission denied accessing {compatdata_base}: {e}")
        except Exception as e:
            logger.warning(f"Error scanning Proton prefixes: {e}")
        return result

    prefixes = await asyncio.to_thread(_find_prefixes)
    logger.info(f"Found {len(prefixes)} Proton prefixes")
    return prefixes


async def get_wine_prefixes() -> list[Path]:
    """
    Find Wine prefixes for non-Steam Windows launchers (Lutris, etc.).

    Wine prefixes typically at:
    - ~/.wine/drive_c/
    - Custom locations from Lutris/PlayOnLinux

    Returns:
        List of paths to Wine prefix drive_c directories.
    """
    prefixes = []

    def _find_wine_prefixes() -> list[Path]:
        result = []

        # Standard Wine prefixes
        for wine_base in WINE_PREFIX_PATHS:
            drive_c = wine_base / "drive_c"
            if drive_c.exists():
                result.append(drive_c)
                logger.debug(f"Found Wine prefix: {drive_c}")

        # Lutris prefixes (games installed via Lutris)
        for lutris_path in LUTRIS_PATHS:
            if lutris_path.exists():
                try:
                    for game_dir in lutris_path.iterdir():
                        if game_dir.is_dir():
                            # Lutris often stores prefixes in game folders
                            drive_c = game_dir / "drive_c"
                            if drive_c.exists():
                                result.append(drive_c)
                                logger.debug(f"Found Lutris prefix: {drive_c}")
                            # Also check for prefix subdirectory
                            prefix_drive_c = game_dir / "prefix" / "drive_c"
                            if prefix_drive_c.exists():
                                result.append(prefix_drive_c)
                                logger.debug(f"Found Lutris prefix: {prefix_drive_c}")
                except PermissionError:
                    continue
                except Exception as e:
                    logger.warning(f"Error scanning Lutris path {lutris_path}: {e}")

        return result

    prefixes = await asyncio.to_thread(_find_wine_prefixes)
    logger.info(f"Found {len(prefixes)} Wine prefixes")
    return prefixes


async def scan_proton_games(prefix: Path) -> list[Path]:
    """
    Scan a Proton/Wine prefix for game directories containing DLLs.

    Typical game locations in Wine prefix:
    - drive_c/Program Files/
    - drive_c/Program Files (x86)/
    - drive_c/Games/
    - drive_c/GOG Games/

    Args:
        prefix: Path to Wine/Proton drive_c directory.

    Returns:
        List of game directory paths.
    """

    def _find_games() -> list[Path]:
        result = []
        search_dirs = [
            prefix / "Program Files",
            prefix / "Program Files (x86)",
            prefix / "Games",
            prefix / "GOG Games",
            prefix / "users" / "steamuser" / "Desktop",  # Some games install here
        ]

        for search_dir in search_dirs:
            if search_dir.exists():
                try:
                    for item in search_dir.iterdir():
                        if item.is_dir():
                            result.append(item)
                except PermissionError:
                    continue
                except Exception as e:
                    logger.warning(f"Error scanning {search_dir}: {e}")

        return result

    return await asyncio.to_thread(_find_games)


async def get_all_linux_game_paths() -> dict[str, Any]:
    """
    Get all game paths on Linux for scanning.

    Returns:
        Dict with:
        - 'steam_native': Native Steam library paths (steamapps/common)
        - 'proton': Proton prefix game directories
        - 'wine': Wine prefix game directories
        - 'skipped_paths': List of paths skipped due to permissions
    """
    result: dict[str, Any] = {
        'steam_native': [],
        'proton': [],
        'wine': [],
        'skipped_paths': [],
    }

    all_skipped: list[dict[str, str]] = []

    # Native Steam
    steam_path = await get_linux_steam_path()
    if steam_path:
        steam_libs = await get_linux_steam_libraries(steam_path)
        # Filter Steam libraries for accessibility
        result['steam_native'], steam_skipped = await filter_accessible_paths(steam_libs)
        all_skipped.extend(steam_skipped)

        # Proton games within Steam
        proton_prefixes = await get_proton_prefixes(steam_path)
        proton_games: list[Path] = []
        for prefix in proton_prefixes:
            games = await scan_proton_games(prefix)
            proton_games.extend(games)
        # Filter Proton games for accessibility
        result['proton'], proton_skipped = await filter_accessible_paths(proton_games)
        all_skipped.extend(proton_skipped)

    # Non-Steam Wine games
    wine_prefixes = await get_wine_prefixes()
    wine_games: list[Path] = []
    for prefix in wine_prefixes:
        games = await scan_proton_games(prefix)
        wine_games.extend(games)
    # Filter Wine games for accessibility
    result['wine'], wine_skipped = await filter_accessible_paths(wine_games)
    all_skipped.extend(wine_skipped)

    result['skipped_paths'] = all_skipped

    total = len(result['steam_native']) + len(result['proton']) + len(result['wine'])
    logger.info(f"Found {total} total Linux game paths "
                f"({len(result['steam_native'])} Steam, "
                f"{len(result['proton'])} Proton, "
                f"{len(result['wine'])} Wine)")

    if all_skipped:
        logger.warning(f"Skipped {len(all_skipped)} inaccessible paths (run with sudo for full access)")

    return result
