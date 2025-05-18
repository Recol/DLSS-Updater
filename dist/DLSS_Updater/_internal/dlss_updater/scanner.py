import os
from pathlib import Path
from .config import LauncherPathName, config_manager
from .whitelist import is_whitelisted
from .constants import DLL_GROUPS
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


async def find_all_dlls():
    logger.info("Starting find_all_dlls function")
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
    update_xess = config_manager.get_update_preference("XeSS")
    update_fsr = config_manager.get_update_preference("FSR")

    # Build list of DLLs to search for based on preferences
    dll_names = []
    if update_dlss:
        dll_names.extend(DLL_GROUPS["DLSS"])
    if update_ds:
        dll_names.extend(DLL_GROUPS["DirectStorage"])
    if update_xess and DLL_GROUPS["XeSS"]:  # Only add if there are XeSS DLLs defined
        dll_names.extend(DLL_GROUPS["XeSS"])
    if update_fsr and DLL_GROUPS["FSR"]:  # Add FSR DLLs if FSR is selected
        dll_names.extend(DLL_GROUPS["FSR"])

    # Skip if no technologies selected
    if not dll_names:
        logger.info("No technologies selected for update, skipping scan")
        return all_dll_paths

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

    # Wait for all tasks to complete and gather results
    for launcher_name, task in tasks.items():
        try:
            dlls = await task
            all_dll_paths[launcher_name] = dlls
            if dlls:
                logger.info(f"Found {len(dlls)} DLLs in {launcher_name}")
        except Exception as e:
            logger.error(f"Error scanning {launcher_name}: {e}")
            all_dll_paths[launcher_name] = []

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
