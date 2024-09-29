import os
from pathlib import Path

from .config import LauncherPathName, update_launcher_path, check_path_value, config_manager
from .whitelist import is_whitelisted
import asyncio
from dlss_updater.logger import setup_logger

logger = setup_logger()


def get_steam_install_path():
    try:
        if check_path_value(LauncherPathName.STEAM):
            return check_path_value(LauncherPathName.STEAM)
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"
        )
        value, _ = winreg.QueryValueEx(key, "InstallPath")
        update_launcher_path(LauncherPathName.STEAM, str(value))
        return value
    except (FileNotFoundError, ImportError):
        return None


def get_steam_libraries(steam_path):
    library_folders_path = Path(steam_path) / "steamapps" / "libraryfolders.vdf"
    if not library_folders_path.exists():
        return [Path(steam_path) / "steamapps" / "common"]

    libraries = []
    with library_folders_path.open("r") as file:
        lines = file.readlines()
        for line in lines:
            if "path" in line:
                path = line.split('"')[3]
                libraries.append(Path(path) / "steamapps" / "common")
    return libraries


async def find_dlss_dlls(library_paths, launcher_name):
    dll_names = ["nvngx_dlss.dll", "nvngx_dlssg.dll", "nvngx_dlssd.dll"]
    dll_paths = []
    for library_path in library_paths:
        for root, _, files in os.walk(library_path):
            for dll_name in dll_names:
                if dll_name.lower() in [f.lower() for f in files]:
                    dll_path = os.path.join(root, dll_name)
                    logger.debug(f"Checking DLL: {dll_path}")
                    if not await is_whitelisted(dll_path):  # Use await here
                        logger.info(
                            f"Found non-whitelisted DLSS DLL in {launcher_name}: {dll_path}"
                        )
                        dll_paths.append(dll_path)
                    else:
                        logger.info(
                            f"Skipped whitelisted game in {launcher_name}: {dll_path}"
                        )
            await asyncio.sleep(0)  # Yield control to allow other tasks to run
    return dll_paths


def get_user_input(prompt):
    user_input = input(prompt).strip()
    return None if user_input.lower() in ["n/a", ""] else user_input


async def get_ea_games():
    if check_path_value(LauncherPathName.EA):
        ea_games_path = Path(check_path_value(LauncherPathName.EA))
        return [ea_games_path]
    ea_path = get_user_input(
        "Please enter the path for EA games or press Enter to skip: "
    )
    if ea_path is None:
        logger.info("EA games path skipped.")
        return []
    ea_games_path = Path(ea_path)
    if not ea_games_path.exists():
        logger.info(f"Invalid path for EA games: {ea_games_path}")
        return []
    logger.info(f"EA games path set to: {ea_games_path}")
    update_launcher_path(LauncherPathName.EA, str(ea_games_path))
    return [ea_games_path]


def get_ubisoft_install_path():
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher"
        )
        value, _ = winreg.QueryValueEx(key, "InstallDir")
        return value
    except (FileNotFoundError, ImportError):
        return None


async def get_ubisoft_games(ubisoft_path):
    ubisoft_games_path = Path(ubisoft_path) / "games"
    if not ubisoft_games_path.exists():
        return []
    return [ubisoft_games_path]


async def get_epic_games():
    epic_path = get_user_input(
        "Please enter the path for Epic Games or press Enter to skip: "
    )
    if epic_path is None:
        return []
    epic_games_path = Path(epic_path)
    if not epic_games_path.exists():
        logger.info("Invalid path for Epic Games.")
        return []
    return [epic_games_path]


async def get_gog_games():
    gog_path = get_user_input(
        "Please enter the path for GOG games or press Enter to skip: "
    )
    if gog_path is None:
        return []
    gog_games_path = Path(gog_path)
    if not gog_games_path.exists():
        logger.info("Invalid path for GOG games.")
        return []
    return [gog_games_path]


async def get_battlenet_games():
    battlenet_path = get_user_input(
        "Please enter the path for Battle.net games (Note: Please ensure you have the launcher opened first) or press Enter to skip: "
    )
    if battlenet_path is None:
        return []
    battlenet_games_path = Path(battlenet_path)
    if not battlenet_games_path.exists():
        logger.info("Invalid path for Battle.net games.")
        return []
    return [battlenet_games_path]


async def find_all_dlss_dlls():
    logger.info("Starting find_all_dlss_dlls function")
    all_dll_paths = {
        "Steam": [],
        "EA Launcher": [],
        "Ubisoft Launcher": [],
        "Epic Games Launcher": [],
        "GOG Launcher": [],
        "Battle.net Launcher": [],
    }

    # Steam
    logger.info("Checking Steam...")
    steam_path = get_steam_install_path()
    if steam_path:
        steam_libraries = get_steam_libraries(steam_path)
        all_dll_paths["Steam"] = await find_dlss_dlls(steam_libraries, "Steam")

    # EA
    logger.info("Checking EA...")
    ea_games = await get_ea_games()
    if ea_games:
        ea_dlls = await find_dlss_dlls(ea_games, "EA Launcher")
        all_dll_paths["EA Launcher"].extend(ea_dlls)

    # Ubisoft
    logger.info("Checking Ubisoft...")
    ubisoft_path = get_ubisoft_install_path()
    if ubisoft_path:
        ubisoft_games = await get_ubisoft_games(ubisoft_path)
        all_dll_paths["Ubisoft Launcher"] = await find_dlss_dlls(
            ubisoft_games, "Ubisoft Launcher"
        )

    # Epic Games
    logger.info("Checking Epic Games...")
    epic_games = await get_epic_games()
    if epic_games:
        all_dll_paths["Epic Games Launcher"] = await find_dlss_dlls(
            epic_games, "Epic Games Launcher"
        )

    # GOG
    logger.info("Checking GOG...")
    gog_games = await get_gog_games()
    if gog_games:
        all_dll_paths["GOG Launcher"] = await find_dlss_dlls(gog_games, "GOG Launcher")

    # Battle.net
    logger.info("Checking Battle.net...")
    battlenet_games = await get_battlenet_games()
    if battlenet_games:
        all_dll_paths["Battle.net Launcher"] = await find_dlss_dlls(
            battlenet_games, "Battle.net Launcher"
        )

    # Remove duplicates
    logger.info("Removing duplicates...")
    unique_dlls = set()
    for launcher in all_dll_paths:
        all_dll_paths[launcher] = [
            dll
            for dll in all_dll_paths[launcher]
            if str(dll) not in unique_dlls and not unique_dlls.add(str(dll))
        ]

    logger.info("find_all_dlss_dlls function completed")
    return all_dll_paths
