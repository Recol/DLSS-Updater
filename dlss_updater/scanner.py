import os
from pathlib import Path
from typing import List, Dict, Set, Any, Optional
from .config import LauncherPathName, config_manager, Concurrency
from .whitelist import is_whitelisted
from .constants import DLL_GROUPS
from .utils import find_game_root
from .vdf_parser import VDFParser
import asyncio
import concurrent.futures
from dlss_updater.logger import setup_logger
import sys

# Try to import scandir-rs for 6-70x faster directory scanning on Windows
try:
    from scandir_rs import Walk as FastWalk
    HAVE_SCANDIR_RS = True
except ImportError:
    HAVE_SCANDIR_RS = False

# Check if running on free-threaded Python (GIL disabled)
try:
    GIL_DISABLED = not sys._is_gil_enabled()
except AttributeError:
    GIL_DISABLED = False

logger = setup_logger()

# Log scanner configuration at module load
if HAVE_SCANDIR_RS:
    logger.info("Scanner: Using scandir-rs (Rust-based, fastest)")
elif GIL_DISABLED:
    logger.info("Scanner: Using parallel os.scandir() with GIL disabled (true parallelism)")
else:
    logger.info("Scanner: Using parallel os.scandir() with GIL enabled (I/O parallelism)")


def _parallel_scandir_walk(root_path: str, dll_names_lower: frozenset, max_workers: int = None) -> List[str]:
    """
    High-performance parallel directory scanner using os.scandir().

    When running on free-threaded Python (GIL disabled), this achieves true
    parallelism across CPU cores. On regular Python, it still provides
    I/O parallelism benefits.

    Strategy:
    - Use os.scandir() which is faster than os.listdir() + os.stat()
    - Parallelize across top-level directories for better load distribution
    - Use thread pool sized to CPU count (more beneficial with no GIL)

    Args:
        root_path: Root directory to scan
        dll_names_lower: Frozenset of lowercase DLL names to find
        max_workers: Number of worker threads (default: CPU count * 2 for I/O)

    Returns:
        List of found DLL paths
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if max_workers is None:
        # Use thread pool sized for I/O operations - scale aggressively
        max_workers = Concurrency.THREADPOOL_IO

    results = []

    def scan_directory_recursive(directory: str) -> List[str]:
        """Recursively scan a directory for DLLs using os.scandir()"""
        found = []
        try:
            with os.scandir(directory) as entries:
                subdirs = []
                for entry in entries:
                    try:
                        name_lower = entry.name.lower()
                        if entry.is_file(follow_symlinks=False):
                            if name_lower in dll_names_lower:
                                found.append(entry.path)
                        elif entry.is_dir(follow_symlinks=False):
                            if name_lower not in _SKIP_DIRECTORIES:
                                subdirs.append(entry.path)
                    except (OSError, PermissionError):
                        continue

                # Recurse into subdirectories
                for subdir in subdirs:
                    found.extend(scan_directory_recursive(subdir))

        except (OSError, PermissionError):
            pass
        return found

    # Get top-level directories for parallel processing
    try:
        with os.scandir(root_path) as entries:
            top_level_dirs = []
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() not in _SKIP_DIRECTORIES:
                            top_level_dirs.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        if entry.name.lower() in dll_names_lower:
                            results.append(entry.path)
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError) as e:
        logger.debug(f"Cannot access {root_path}: {e}")
        return results

    if not top_level_dirs:
        return results

    # Parallel scan of top-level directories
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scan_directory_recursive, d): d for d in top_level_dirs}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as e:
                logger.debug(f"Error scanning {futures[future]}: {e}")

    return results

# Pre-computed frozen set of DLL names for O(1) lookup (populated at scan time)
_dll_names_lower: frozenset = frozenset()

# Directories to skip during scanning (common non-game folders)
_SKIP_DIRECTORIES: frozenset = frozenset({
    '__pycache__', '.git', '.svn', '.hg', 'node_modules',
    'logs', 'log', 'saves', 'save', 'screenshots', 'crash',
    'crashdumps', 'dumps', 'temp', 'tmp', 'cache', '.cache',
    'shader_cache', 'shadercache', 'gpucache', 'webcache'
})


def get_steam_install_path():
    """Get Steam install path, auto-detecting from registry if not configured."""
    try:
        # Check for configured paths first
        existing_paths = config_manager.get_launcher_paths(LauncherPathName.STEAM)
        if existing_paths:
            # Return first path (main Steam installation)
            path = existing_paths[0]
            # Remove \steamapps\common if it exists
            path = path.replace("\\steamapps\\common", "")
            logger.debug(f"Using configured Steam path: {path}")
            return path

        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"
        )
        value, _ = winreg.QueryValueEx(key, "InstallPath")
        config_manager.add_launcher_path(LauncherPathName.STEAM, str(value))
        return value
    except (FileNotFoundError, ImportError) as e:
        logger.debug(f"Could not find Steam install path: {e}")
        return None


def get_steam_manual_paths() -> List[Path]:
    """
    Get manually configured Steam paths (sub-folders).

    These are additional paths beyond the auto-detected Steam libraries.
    Returns paths with steamapps/common appended if they don't have it.
    """
    manual_paths = []
    configured_paths = config_manager.get_launcher_paths(LauncherPathName.STEAM)

    for path_str in configured_paths:
        path = Path(path_str)
        # Check if this is a Steam root (has steamapps/common)
        common_path = path / "steamapps" / "common"
        if common_path.exists():
            manual_paths.append(common_path)
        # Or if path itself exists (user specified a custom games directory)
        elif path.exists():
            manual_paths.append(path)

    return manual_paths


def get_steam_libraries(steam_path):
    logger.debug(f"Looking for Steam libraries in: {steam_path}")
    library_folders_path = Path(steam_path) / "steamapps" / "libraryfolders.vdf"
    logger.debug(f"Checking libraryfolders.vdf at: {library_folders_path}")

    if not library_folders_path.exists():
        default_path = Path(steam_path) / "steamapps" / "common"
        logger.debug(
            f"libraryfolders.vdf not found, using default path: {default_path}"
        )
        return [default_path]

    libraries = []
    with library_folders_path.open("r", encoding="utf-8") as file:
        lines = file.readlines()
        for line in lines:
            if "path" in line:
                path = line.split('"')[3]
                library_path = Path(path) / "steamapps" / "common"
                logger.debug(f"Found Steam library: {library_path}")
                libraries.append(library_path)

    logger.debug(f"Found total Steam libraries: {len(libraries)}")
    for lib in libraries:
        logger.debug(f"Steam library path: {lib}")
    return libraries


async def find_dlls(library_paths, launcher_name, dll_names):
    """Find DLLs from a filtered list of DLL names using batch whitelist checking"""
    dll_paths = []
    logger.debug(f"Searching for DLLs in {launcher_name}")

    # Pre-compute lowercase DLL names for O(1) lookup
    dll_names_lower = frozenset(d.lower() for d in dll_names)

    # First, collect all potential DLL paths
    potential_dlls = []

    for library_path in library_paths:
        logger.debug(f"Scanning directory: {library_path}")
        try:
            # Use asyncio.to_thread to avoid blocking the event loop
            def _scan_library():
                results = []

                # Use scandir-rs for 6-70x faster scanning on Windows if available
                if HAVE_SCANDIR_RS:
                    try:
                        for entry in FastWalk(str(library_path)):
                            # Skip directories in skip list
                            if entry.is_dir:
                                if entry.name.lower() in _SKIP_DIRECTORIES:
                                    continue
                            elif entry.is_file:
                                if entry.name.lower() in dll_names_lower:
                                    results.append(entry.path)
                        return results
                    except Exception as e:
                        logger.warning(f"scandir-rs failed, falling back to os.walk: {e}")

                # Fallback to parallel scandir (faster than os.walk, especially with GIL disabled)
                return _parallel_scandir_walk(str(library_path), dll_names_lower)

            lib_dlls = await asyncio.to_thread(_scan_library)
            potential_dlls.extend(lib_dlls)
        except Exception as e:
            logger.error(f"Error scanning {library_path}: {e}")

    # Batch check whitelist status
    if potential_dlls:
        from .whitelist import check_whitelist_batch

        whitelist_results = await check_whitelist_batch(potential_dlls)

        for dll_path in potential_dlls:
            if not whitelist_results.get(dll_path, True):  # Default to True if error
                logger.info(f"Found non-whitelisted DLL in {launcher_name}: {dll_path}")
                dll_paths.append(dll_path)
            else:
                logger.info(f"Skipped whitelisted game in {launcher_name}: {dll_path}")

    logger.debug(f"Found {len(dll_paths)} DLLs in {launcher_name}")
    return dll_paths


def get_user_input(prompt):
    user_input = input(prompt).strip()
    return None if user_input.lower() in ["n/a", ""] else user_input


async def get_ea_games():
    """Get all configured EA game paths (multi-path support)."""
    ea_paths = config_manager.get_launcher_paths(LauncherPathName.EA)
    return [Path(p) for p in ea_paths if Path(p).exists()]


def get_ubisoft_install_path():
    """Get Ubisoft install path from registry (auto-detection for first path)."""
    try:
        # Check if we already have configured paths
        existing_paths = config_manager.get_launcher_paths(LauncherPathName.UBISOFT)
        if existing_paths:
            return existing_paths[0]  # Return first path for backward compat

        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher"
        )
        value, _ = winreg.QueryValueEx(key, "InstallDir")
        config_manager.add_launcher_path(LauncherPathName.UBISOFT, str(value))
        return value
    except (FileNotFoundError, ImportError):
        return None


async def get_ubisoft_games():
    """Get all configured Ubisoft game paths (multi-path support)."""
    ubisoft_paths = config_manager.get_launcher_paths(LauncherPathName.UBISOFT)
    valid_paths = []

    for ubisoft_path in ubisoft_paths:
        # Try with /games subdirectory first (standard Ubisoft layout)
        games_path = Path(ubisoft_path) / "games"
        if games_path.exists():
            valid_paths.append(games_path)
        # Also check the path directly (for custom sub-folders)
        elif Path(ubisoft_path).exists():
            valid_paths.append(Path(ubisoft_path))

    return valid_paths


async def get_epic_games():
    """Get all configured Epic game paths (multi-path support)."""
    epic_paths = config_manager.get_launcher_paths(LauncherPathName.EPIC)
    return [Path(p) for p in epic_paths if Path(p).exists()]


async def get_gog_games():
    """Get all configured GOG game paths (multi-path support)."""
    gog_paths = config_manager.get_launcher_paths(LauncherPathName.GOG)
    return [Path(p) for p in gog_paths if Path(p).exists()]


async def get_battlenet_games():
    """Get all configured Battle.net game paths (multi-path support)."""
    battlenet_paths = config_manager.get_launcher_paths(LauncherPathName.BATTLENET)
    return [Path(p) for p in battlenet_paths if Path(p).exists()]


async def get_xbox_games():
    """Get all configured Xbox game paths (multi-path support)."""
    xbox_paths = config_manager.get_launcher_paths(LauncherPathName.XBOX)
    return [Path(p) for p in xbox_paths if Path(p).exists()]


async def get_custom_folder(folder_num):
    """Get all configured paths for a custom folder (multi-path support)."""
    launcher = getattr(LauncherPathName, f"CUSTOM{folder_num}")
    custom_paths = config_manager.get_launcher_paths(launcher)
    return [Path(p) for p in custom_paths if Path(p).exists()]


async def scan_game_for_dlls(game_path: Path, dll_names_lower: frozenset) -> List[str]:
    """
    Scan a single game directory for DLLs using optimized os.scandir().

    This is MUCH faster than os.walk() because:
    - Uses os.scandir() which avoids extra stat() calls
    - Uses frozenset for O(1) membership testing
    - Skips known non-game directories
    - Runs in thread pool to avoid blocking event loop

    Args:
        game_path: Path to the game directory
        dll_names_lower: Frozenset of lowercase DLL names to search for

    Returns:
        List of found DLL paths
    """
    def _scan_sync() -> List[str]:
        """Synchronous scanning using os.scandir() (runs in thread pool)"""
        results = []
        dirs_to_scan = [str(game_path)]

        while dirs_to_scan:
            current_dir = dirs_to_scan.pop()
            try:
                with os.scandir(current_dir) as entries:
                    for entry in entries:
                        try:
                            name_lower = entry.name.lower()
                            if entry.is_file(follow_symlinks=False):
                                if name_lower in dll_names_lower:
                                    results.append(entry.path)
                            elif entry.is_dir(follow_symlinks=False):
                                if name_lower not in _SKIP_DIRECTORIES:
                                    dirs_to_scan.append(entry.path)
                        except (OSError, PermissionError):
                            continue
            except PermissionError:
                logger.debug(f"Permission denied accessing {current_dir}")
            except Exception as e:
                logger.debug(f"Error scanning {current_dir}: {e}")

        return results

    return await asyncio.to_thread(_scan_sync)


async def scan_games_for_dlls_parallel(
    games: List[Dict[str, Any]],
    dll_names_lower: frozenset,
    max_concurrent: int = None
) -> Dict[str, List[str]]:
    """
    Scan multiple game directories for DLLs in parallel with maximum concurrency.

    Args:
        games: List of game dicts with 'path' key
        dll_names_lower: Frozenset of lowercase DLL names
        max_concurrent: Maximum concurrent scans (default: IO_HEAVY from Concurrency)

    Returns:
        Dict mapping game path string to list of found DLLs
    """
    if max_concurrent is None:
        max_concurrent = Concurrency.IO_HEAVY
    results = {}
    semaphore = asyncio.Semaphore(max_concurrent)

    async def scan_with_limit(game: Dict[str, Any]):
        async with semaphore:
            game_path = game['path']
            dlls = await scan_game_for_dlls(game_path, dll_names_lower)
            return str(game_path), dlls, game

    tasks = [scan_with_limit(g) for g in games]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for result in completed:
        if isinstance(result, Exception):
            logger.error(f"Error in parallel scan: {result}")
            continue
        path_str, dlls, game = result
        if dlls:
            results[path_str] = {
                'dlls': dlls,
                'game': game
            }

    return results


async def enumerate_steam_games_fast(steam_path: str) -> List[Dict[str, Any]]:
    """
    Enumerate Steam games using appmanifest files (FAST).

    This is MUCH faster than walking entire Steam libraries because:
    - Only parses small manifest files (~1KB each)
    - O(n) where n = installed games, not total files
    - No directory traversal needed for enumeration

    Args:
        steam_path: Steam installation path

    Returns:
        List of game dicts with: app_id, name, path, steamapps_dir
    """
    try:
        games = await VDFParser.enumerate_steam_games(Path(steam_path))
        logger.info(f"Fast enumeration found {len(games)} Steam games via appmanifest")
        return games
    except Exception as e:
        logger.error(f"Error in fast Steam enumeration: {e}")
        return []


async def scan_steam_fast(steam_path: str, dll_names: List[str]) -> List[str]:
    """
    Optimized Steam scanning using appmanifest enumeration + targeted scanning.

    Performance improvement:
    - Old: Walk entire Steam library (millions of files)
    - New: Parse manifests (hundreds of small files) + scan only game folders

    Also merges auto-detected Steam libraries with manually configured paths.

    Args:
        steam_path: Steam installation path
        dll_names: List of DLL names to search for

    Returns:
        List of found DLL paths
    """
    dll_names_lower = frozenset(d.lower() for d in dll_names)
    all_dlls = []

    # Step 1: Enumerate installed games via appmanifest (fast) - auto-detection
    games = await enumerate_steam_games_fast(steam_path)

    if games:
        # Step 2: Scan game directories in parallel
        logger.info(f"Scanning {len(games)} Steam game directories for DLLs...")
        scan_results = await scan_games_for_dlls_parallel(games, dll_names_lower)

        # Collect all DLLs
        for path_str, data in scan_results.items():
            all_dlls.extend(data['dlls'])

        logger.info(f"Found {len(all_dlls)} DLLs in {len(scan_results)} Steam games (auto-detected)")
    else:
        logger.warning("No Steam games found via appmanifest, using library scan")
        # Fallback to legacy scanning
        steam_libraries = get_steam_libraries(steam_path)
        all_dlls = scan_steam_libraries_parallel(steam_libraries, dll_names)

    # Step 3: Also scan manually configured Steam paths (sub-folders)
    # This handles cases where users have additional game directories
    manual_paths = get_steam_manual_paths()

    # De-duplicate: remove paths that are already covered by auto-detected libraries
    auto_detected_libs = set()
    if steam_path:
        for lib in get_steam_libraries(steam_path):
            auto_detected_libs.add(str(lib).lower())

    unique_manual_paths = []
    for manual_path in manual_paths:
        manual_str = str(manual_path).lower()
        # Check if this path is not already covered
        is_duplicate = False
        for auto_lib in auto_detected_libs:
            if manual_str.startswith(auto_lib) or auto_lib.startswith(manual_str):
                is_duplicate = True
                break
        if not is_duplicate:
            unique_manual_paths.append(manual_path)

    if unique_manual_paths:
        logger.info(f"Scanning {len(unique_manual_paths)} additional manual Steam paths...")
        manual_dlls = await find_dlls(unique_manual_paths, "Steam (Manual)", dll_names)
        all_dlls.extend(manual_dlls)
        logger.info(f"Found {len(manual_dlls)} DLLs in manual Steam paths")

    # Remove duplicates from all_dlls
    seen = set()
    unique_dlls = []
    for dll in all_dlls:
        dll_lower = str(dll).lower()
        if dll_lower not in seen:
            seen.add(dll_lower)
            unique_dlls.append(dll)

    logger.info(f"Total Steam DLLs found: {len(unique_dlls)}")
    return unique_dlls


async def find_all_dlls(progress_callback=None):
    """
    Find all DLLs across configured launchers

    Args:
        progress_callback: Optional callback(current, total, message) for progress updates
    """
    logger.info("Starting find_all_dlls function")

    # Report initialization
    if progress_callback:
        await progress_callback(0, 100, "Initializing scan...")

    all_dll_paths = {
        "Steam": [],
        "EA Launcher": [],
        "Ubisoft Launcher": [],
        "Epic Games Launcher": [],
        "GOG Launcher": [],
        "Battle.net Launcher": [],
        "Xbox Launcher": [],
        "Custom Folder 1": [],
        "Custom Folder 2": [],
        "Custom Folder 3": [],
        "Custom Folder 4": [],
    }

    # Get user preferences
    update_dlss = config_manager.get_update_preference("DLSS")
    update_ds = config_manager.get_update_preference("DirectStorage")
    update_streamline = config_manager.get_update_preference("Streamline")
    update_xess = config_manager.get_update_preference("XeSS")
    update_fsr = config_manager.get_update_preference("FSR")

    # Build list of DLLs to search for based on preferences
    dll_names = []
    if update_dlss:
        dll_names.extend(DLL_GROUPS["DLSS"])
    if update_streamline:
        dll_names.extend(DLL_GROUPS["Streamline"])
    # DirectStorage DLLs are optional, so only add if requested
    if update_ds:
        dll_names.extend(DLL_GROUPS["DirectStorage"])
    if update_xess and DLL_GROUPS["XeSS"]:  # Only add if there are XeSS DLLs defined
        dll_names.extend(DLL_GROUPS["XeSS"])
    if update_fsr and DLL_GROUPS["FSR"]:  # Add FSR DLLs if FSR is selected
        dll_names.extend(DLL_GROUPS["FSR"])

    # Skip if no technologies selected
    if not dll_names:
        logger.info("No technologies selected for update, skipping scan")
        if progress_callback:
            await progress_callback(100, 100, "No technologies selected")
        return all_dll_paths

    # Report preparation complete
    if progress_callback:
        await progress_callback(5, 100, "Preparing to scan launchers...")

    # Define async functions for each launcher
    async def scan_steam():
        steam_path = get_steam_install_path()
        if steam_path:
            # Use optimized appmanifest-based scanning (FAST)
            all_steam_dlls = await scan_steam_fast(steam_path, dll_names)

            # Now filter by whitelist
            from .whitelist import check_whitelist_batch

            whitelist_results = await check_whitelist_batch(all_steam_dlls)

            filtered_dlls = []
            for dll_path in all_steam_dlls:
                if not whitelist_results.get(dll_path, True):
                    logger.info(f"Found non-whitelisted DLL in Steam: {dll_path}")
                    filtered_dlls.append(dll_path)
                else:
                    logger.info(f"Skipped whitelisted game in Steam: {dll_path}")

            return filtered_dlls
        return []

    async def scan_ea():
        ea_games = await get_ea_games()
        if ea_games:
            return await find_dlls(ea_games, "EA Launcher", dll_names)
        return []

    async def scan_ubisoft():
        # Try auto-detection first if no paths configured
        get_ubisoft_install_path()  # This will auto-add path if found in registry
        ubisoft_games = await get_ubisoft_games()
        if ubisoft_games:
            return await find_dlls(ubisoft_games, "Ubisoft Launcher", dll_names)
        return []

    async def scan_epic():
        epic_games = await get_epic_games()
        if epic_games:
            return await find_dlls(epic_games, "Epic Games Launcher", dll_names)
        return []

    async def scan_gog():
        gog_games = await get_gog_games()
        if gog_games:
            return await find_dlls(gog_games, "GOG Launcher", dll_names)
        return []

    async def scan_battlenet():
        battlenet_games = await get_battlenet_games()
        if battlenet_games:
            return await find_dlls(battlenet_games, "Battle.net Launcher", dll_names)
        return []

    async def scan_xbox():
        xbox_games = await get_xbox_games()
        if xbox_games:
            return await find_dlls(xbox_games, "Xbox Launcher", dll_names)
        return []

    async def scan_custom(folder_num):
        custom_folder = await get_custom_folder(folder_num)
        if custom_folder:
            return await find_dlls(
                custom_folder, f"Custom Folder {folder_num}", dll_names
            )
        return []

    # Create tasks for all launchers
    tasks = {
        "Steam": asyncio.create_task(scan_steam()),
        "EA Launcher": asyncio.create_task(scan_ea()),
        "Ubisoft Launcher": asyncio.create_task(scan_ubisoft()),
        "Epic Games Launcher": asyncio.create_task(scan_epic()),
        "GOG Launcher": asyncio.create_task(scan_gog()),
        "Battle.net Launcher": asyncio.create_task(scan_battlenet()),
        "Xbox Launcher": asyncio.create_task(scan_xbox()),
        "Custom Folder 1": asyncio.create_task(scan_custom(1)),
        "Custom Folder 2": asyncio.create_task(scan_custom(2)),
        "Custom Folder 3": asyncio.create_task(scan_custom(3)),
        "Custom Folder 4": asyncio.create_task(scan_custom(4)),
    }

    # Wait for all tasks to complete and gather results with progress reporting
    completed_count = 0
    total_launchers = len(tasks)

    for launcher_name, task in tasks.items():
        try:
            dlls = await task
            all_dll_paths[launcher_name] = dlls
            completed_count += 1

            # Calculate progress (5% base + 65% for launchers = 5-70%)
            progress_pct = int(5 + (completed_count / total_launchers) * 65)

            if progress_callback:
                await progress_callback(
                    progress_pct,
                    100,
                    f"Scanned {launcher_name}: {len(dlls)} DLLs found"
                )

            if dlls:
                logger.info(f"Found {len(dlls)} DLLs in {launcher_name}")
        except Exception as e:
            logger.error(f"Error scanning {launcher_name}: {e}")
            all_dll_paths[launcher_name] = []
            completed_count += 1

            # Report progress even on error
            progress_pct = int(5 + (completed_count / total_launchers) * 65)
            if progress_callback:
                await progress_callback(
                    progress_pct,
                    100,
                    f"Error scanning {launcher_name}"
                )

    # Report whitelist filtering phase
    if progress_callback:
        await progress_callback(70, 100, "Filtering whitelisted games...")

    # Remove duplicates
    unique_dlls = set()
    for launcher in all_dll_paths:
        all_dll_paths[launcher] = [
            dll
            for dll in all_dll_paths[launcher]
            if str(dll) not in unique_dlls and not unique_dlls.add(str(dll))
        ]

    # Log summary
    total_dlls = sum(len(dlls) for dlls in all_dll_paths.values())
    logger.info(f"Scan complete. Found {total_dlls} total DLLs across all launchers")

    # Record discovered games and DLLs in database using BATCH operations
    try:
        from dlss_updater.database import db_manager
        from dlss_updater.steam_integration import (
            find_steam_app_id_by_name,
            detect_steam_app_id_from_manifest
        )
        from dlss_updater.utils import extract_game_name
        from dlss_updater.constants import DLL_TYPE_MAP

        # Report database recording start
        if progress_callback:
            await progress_callback(75, 100, "Preparing game data...")

        logger.info("Recording scanned games in database (batch mode)...")

        # Phase 1: Group DLLs by game directory
        all_games_dict = {}
        for launcher, dll_paths in all_dll_paths.items():
            games_dict = {}
            normalized_to_original = {}

            for dll_path in dll_paths:
                game_dir = find_game_root(Path(dll_path), launcher)
                game_dir_normalized = os.path.normpath(str(game_dir)).lower()

                if game_dir_normalized not in games_dict:
                    games_dict[game_dir_normalized] = []
                    normalized_to_original[game_dir_normalized] = str(game_dir)

                games_dict[game_dir_normalized].append(dll_path)

            games_dict_original = {
                normalized_to_original[norm_path]: dlls
                for norm_path, dlls in games_dict.items()
            }

            all_games_dict[launcher] = games_dict_original

        # Phase 2: Prepare game records with Steam app IDs (parallel lookup)
        if progress_callback:
            await progress_callback(78, 100, "Looking up Steam app IDs...")

        games_to_insert = []
        game_dll_mapping = {}  # Maps game path to list of DLL paths

        async def prepare_game_data(launcher: str, game_dir_str: str, game_dlls: list):
            game_dir = Path(game_dir_str)
            game_name = extract_game_name(str(game_dlls[0]), launcher)

            # Try to find Steam app ID
            app_id = None
            if launcher == "Steam":
                app_id = await detect_steam_app_id_from_manifest(game_dir)

            if app_id is None:
                app_id = await find_steam_app_id_by_name(game_name)

            return {
                'name': game_name,
                'path': game_dir_str,
                'launcher': launcher,
                'steam_app_id': app_id,
                'dlls': game_dlls  # Keep track of DLLs for this game
            }

        # Gather all game preparation tasks
        prepare_tasks = []
        for launcher, games_dict in all_games_dict.items():
            for game_dir_str, game_dlls in games_dict.items():
                prepare_tasks.append(prepare_game_data(launcher, game_dir_str, game_dlls))

        # Execute all Steam ID lookups in parallel
        prepared_games = await asyncio.gather(*prepare_tasks, return_exceptions=True)

        # Filter out exceptions and extract game data
        for result in prepared_games:
            if isinstance(result, Exception):
                logger.error(f"Error preparing game data: {result}")
                continue
            game_dll_mapping[result['path']] = result.pop('dlls')
            games_to_insert.append(result)

        if progress_callback:
            await progress_callback(82, 100, f"Batch inserting {len(games_to_insert)} games...")

        # Phase 3: Batch upsert all games
        games_result = await db_manager.batch_upsert_games(games_to_insert)
        recorded_games = len(games_result)

        if progress_callback:
            await progress_callback(88, 100, "Extracting DLL versions...")

        # Phase 4: Prepare DLL records with version extraction (parallel)
        from .updater import get_dll_version

        dlls_to_insert = []
        dlls_with_versions = 0

        async def extract_dll_info(game_path: str, dll_path: str, game_id: int):
            dll_filename = Path(dll_path).name
            dll_type = DLL_TYPE_MAP.get(dll_filename.lower(), "Unknown")

            # Run version extraction in thread pool
            dll_version = await asyncio.to_thread(get_dll_version, dll_path)

            return {
                'game_id': game_id,
                'dll_type': dll_type,
                'dll_filename': dll_filename,
                'dll_path': str(dll_path),
                'current_version': dll_version,
            }

        # Gather all DLL extraction tasks
        dll_tasks = []
        for game_path, game in games_result.items():
            if game_path in game_dll_mapping:
                for dll_path in game_dll_mapping[game_path]:
                    dll_tasks.append(extract_dll_info(game_path, dll_path, game.id))

        # Execute all DLL version extractions in parallel - CPU-bound PE parsing
        semaphore = asyncio.Semaphore(Concurrency.IO_HEAVY)  # Scale with CPU for PE parsing

        async def extract_with_limit(task):
            async with semaphore:
                return await task

        dll_results = await asyncio.gather(
            *[extract_with_limit(t) for t in dll_tasks],
            return_exceptions=True
        )

        # Collect DLL data
        for result in dll_results:
            if isinstance(result, Exception):
                logger.error(f"Error extracting DLL info: {result}")
                continue
            dlls_to_insert.append(result)
            if result.get('current_version'):
                dlls_with_versions += 1

        if progress_callback:
            await progress_callback(95, 100, f"Batch inserting {len(dlls_to_insert)} DLLs...")

        # Phase 5: Batch upsert all DLLs
        recorded_dlls = await db_manager.batch_upsert_dlls(dlls_to_insert)

        logger.info(f"Database recording complete: {recorded_games} games, {recorded_dlls} DLLs")
        logger.info(f"Version extraction: {dlls_with_versions}/{recorded_dlls} DLLs have valid versions")

        if dlls_with_versions == 0 and recorded_dlls > 0:
            logger.warning("No DLL versions extracted! Check get_dll_version() function")

        # Report completion
        if progress_callback:
            await progress_callback(100, 100, f"Scan complete: {recorded_games} games, {recorded_dlls} DLLs")

    except Exception as e:
        logger.error(f"Error recording games in database: {e}", exc_info=True)
        if progress_callback:
            await progress_callback(100, 100, "Scan complete (database recording failed)")

    return all_dll_paths


def find_all_dlls_sync():
    """Synchronous wrapper for find_all_dlls to use in non-async contexts"""
    import asyncio

    try:
        # Use a new event loop to avoid conflicts with Qt
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(find_all_dlls())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Error in find_all_dlls_sync: {e}")
        import traceback

        logger.error(traceback.format_exc())
        # Return an empty dict as fallback
        return {
            "Steam": [],
            "EA Launcher": [],
            "Ubisoft Launcher": [],
            "Epic Games Launcher": [],
            "GOG Launcher": [],
            "Battle.net Launcher": [],
            "Xbox Launcher": [],
            "Custom Folder 1": [],
            "Custom Folder 2": [],
            "Custom Folder 3": [],
            "Custom Folder 4": [],
        }


def scan_directory_for_dlls(directory, dll_names):
    """Scan a single directory for DLLs using optimized os.scandir()"""
    # Pre-compute lowercase DLL names for O(1) lookup
    dll_names_lower = frozenset(d.lower() for d in dll_names) if not isinstance(dll_names, frozenset) else dll_names

    # Use parallel scandir for better performance (especially with GIL disabled)
    return _parallel_scandir_walk(str(directory), dll_names_lower)


def scan_steam_libraries_parallel(library_paths, dll_names, max_workers=None):
    """Scan multiple Steam libraries in parallel with maximum concurrency"""
    if max_workers is None:
        max_workers = Concurrency.THREADPOOL_IO
    logger.info(f"Scanning {len(library_paths)} Steam libraries in parallel (workers={max_workers})")
    all_dlls = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_library = {
            executor.submit(scan_directory_for_dlls, lib_path, dll_names): lib_path
            for lib_path in library_paths
        }

        for future in concurrent.futures.as_completed(future_to_library):
            lib_path = future_to_library[future]
            try:
                dlls = future.result()
                all_dlls.extend(dlls)
                logger.info(f"Found {len(dlls)} DLLs in {lib_path}")
            except Exception as e:
                logger.error(f"Error scanning library {lib_path}: {e}")

    return all_dlls
