import os
from pathlib import Path
from .config import LauncherPathName, config_manager
from .whitelist import is_whitelisted
from .constants import DLL_GROUPS
from .utils import find_game_root
import asyncio
import concurrent.futures
from dlss_updater.logger import setup_logger
import sys

logger = setup_logger()


def get_steam_install_path():
    try:
        if config_manager.check_path_value(LauncherPathName.STEAM):
            path = config_manager.check_path_value(LauncherPathName.STEAM)
            # Remove \steamapps\common if it exists
            path = path.replace("\\steamapps\\common", "")
            logger.debug(f"Using configured Steam path: {path}")
            return path

        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"
        )
        value, _ = winreg.QueryValueEx(key, "InstallPath")
        config_manager.update_launcher_path(LauncherPathName.STEAM, str(value))
        return value
    except (FileNotFoundError, ImportError) as e:
        logger.debug(f"Could not find Steam install path: {e}")
        return None


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

    # First, collect all potential DLL paths
    potential_dlls = []

    for library_path in library_paths:
        logger.debug(f"Scanning directory: {library_path}")
        try:
            for root, _, files in os.walk(library_path):
                for dll_name in dll_names:
                    if dll_name.lower() in [f.lower() for f in files]:
                        dll_path = os.path.join(root, dll_name)
                        potential_dlls.append(dll_path)
                await asyncio.sleep(0)
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
    ea_path = config_manager.check_path_value(LauncherPathName.EA)
    if not ea_path or ea_path == "":
        return []
    ea_games_path = Path(ea_path)
    return [ea_games_path] if ea_games_path.exists() else []


def get_ubisoft_install_path():
    try:
        if config_manager.check_path_value(LauncherPathName.UBISOFT):
            return config_manager.check_path_value(LauncherPathName.UBISOFT)
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher"
        )
        value, _ = winreg.QueryValueEx(key, "InstallDir")
        config_manager.update_launcher_path(LauncherPathName.UBISOFT, str(value))
        return value
    except (FileNotFoundError, ImportError):
        return None


async def get_ubisoft_games(ubisoft_path):
    ubisoft_games_path = Path(ubisoft_path) / "games"
    if not ubisoft_games_path.exists():
        return []
    return [ubisoft_games_path]


async def get_epic_games():
    epic_path = config_manager.check_path_value(LauncherPathName.EPIC)
    if not epic_path or epic_path == "":
        return []
    epic_games_path = Path(epic_path)
    return [epic_games_path] if epic_games_path.exists() else []


async def get_gog_games():
    gog_path = config_manager.check_path_value(LauncherPathName.GOG)
    if not gog_path or gog_path == "":
        return []
    gog_games_path = Path(gog_path)
    return [gog_games_path] if gog_games_path.exists() else []


async def get_battlenet_games():
    battlenet_path = config_manager.check_path_value(LauncherPathName.BATTLENET)
    if not battlenet_path or battlenet_path == "":
        return []
    battlenet_games_path = Path(battlenet_path)
    return [battlenet_games_path] if battlenet_games_path.exists() else []


async def get_xbox_games():
    xbox_path = config_manager.check_path_value(LauncherPathName.XBOX)
    if not xbox_path or xbox_path == "":
        return []
    xbox_games_path = Path(xbox_path)
    return [xbox_games_path] if xbox_games_path.exists() else []


async def get_custom_folder(folder_num):
    custom_path = config_manager.check_path_value(
        getattr(LauncherPathName, f"CUSTOM{folder_num}")
    )
    if not custom_path or custom_path == "":
        return []
    custom_path = Path(custom_path)
    return [custom_path] if custom_path.exists() else []


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
            steam_libraries = get_steam_libraries(steam_path)
            # Use parallel library scanning for Steam
            all_steam_dlls = scan_steam_libraries_parallel(steam_libraries, dll_names)

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
        ubisoft_path = get_ubisoft_install_path()
        if ubisoft_path:
            ubisoft_games = await get_ubisoft_games(ubisoft_path)
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

    # Record discovered games and DLLs in database
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
            await progress_callback(85, 100, "Recording games in database...")

        logger.info("Recording scanned games in database...")
        recorded_games = 0
        recorded_dlls = 0

        # Count total games to record for progress tracking
        total_games_to_record = 0
        all_games_dict = {}
        for launcher, dll_paths in all_dll_paths.items():
            # Group DLLs by game directory with normalization to prevent duplicates
            games_dict = {}
            normalized_to_original = {}  # Map normalized paths to original paths

            for dll_path in dll_paths:
                game_dir = find_game_root(Path(dll_path), launcher)

                # Normalize path to prevent duplicates from path variations
                game_dir_normalized = os.path.normpath(str(game_dir)).lower()

                if game_dir_normalized not in games_dict:
                    games_dict[game_dir_normalized] = []
                    normalized_to_original[game_dir_normalized] = str(game_dir)  # Keep original casing

                games_dict[game_dir_normalized].append(dll_path)

            # Convert back to original paths for database recording
            games_dict_original = {
                normalized_to_original[norm_path]: dlls
                for norm_path, dlls in games_dict.items()
            }

            all_games_dict[launcher] = games_dict_original
            total_games_to_record += len(games_dict_original)

        game_count = 0
        dlls_with_versions = 0  # Track DLLs with valid versions
        for launcher, games_dict in all_games_dict.items():
            # Record each game
            for game_dir_str, game_dlls in games_dict.items():
                game_dir = Path(game_dir_str)
                game_name = extract_game_name(str(game_dlls[0]), launcher)

                # Try to find Steam app ID
                app_id = None
                if launcher == "Steam":
                    # For Steam games, try appmanifest first (fast and accurate)
                    app_id = await detect_steam_app_id_from_manifest(game_dir)

                if app_id is None:
                    # For non-Steam games or if appmanifest failed, use name matching
                    app_id = await find_steam_app_id_by_name(game_name)

                # Upsert game record
                game = await db_manager.upsert_game({
                    'name': game_name,
                    'path': game_dir_str,
                    'launcher': launcher,
                    'steam_app_id': app_id,
                })

                if game:
                    recorded_games += 1

                    # Record each DLL for this game
                    for dll_path in game_dlls:
                        # Lazy import to avoid circular dependency
                        from .updater import get_dll_version

                        dll_filename = Path(dll_path).name
                        dll_type = DLL_TYPE_MAP.get(dll_filename.lower(), "Unknown")
                        dll_version = get_dll_version(dll_path)

                        # Log version extraction result
                        if not dll_version:
                            logger.warning(f"Could not extract version from {dll_path}")
                        else:
                            logger.debug(f"Extracted version {dll_version} from {dll_filename}")
                            dlls_with_versions += 1  # Count DLLs with valid versions

                        dll_record = await db_manager.upsert_game_dll({
                            'game_id': game.id,
                            'dll_type': dll_type,
                            'dll_filename': dll_filename,
                            'dll_path': str(dll_path),
                            'current_version': dll_version,
                        })

                        if dll_record:
                            recorded_dlls += 1

                # Update progress periodically (every 5 games or on last game)
                game_count += 1
                if progress_callback and (game_count % 5 == 0 or game_count == total_games_to_record):
                    db_progress = int(85 + (game_count / max(total_games_to_record, 1)) * 15)  # 85-100%
                    await progress_callback(
                        db_progress,
                        100,
                        f"Recorded {game_count}/{total_games_to_record} games"
                    )

        logger.info(f"Database recording complete: {recorded_games} games, {recorded_dlls} DLLs")

        # Log version extraction summary
        logger.info(f"Version extraction: {dlls_with_versions}/{recorded_dlls} DLLs have valid versions")

        if dlls_with_versions == 0 and recorded_dlls > 0:
            logger.warning("⚠️  No DLL versions extracted! Check get_dll_version() function")

        # Report completion
        if progress_callback:
            await progress_callback(100, 100, f"Scan complete: {recorded_games} games, {recorded_dlls} DLLs")

    except Exception as e:
        logger.error(f"Error recording games in database: {e}", exc_info=True)
        # Don't fail the scan if database recording fails
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
    """Scan a single directory for DLLs"""
    found_dlls = []
    try:
        for root, _, files in os.walk(directory):
            for dll_name in dll_names:
                if dll_name.lower() in [f.lower() for f in files]:
                    dll_path = os.path.join(root, dll_name)
                    found_dlls.append(dll_path)
    except Exception as e:
        logger.error(f"Error scanning directory {directory}: {e}")
    return found_dlls


def scan_steam_libraries_parallel(library_paths, dll_names, max_workers=4):
    """Scan multiple Steam libraries in parallel"""
    logger.info(f"Scanning {len(library_paths)} Steam libraries in parallel")
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
