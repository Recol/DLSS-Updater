import os
from pathlib import Path
from .config import LauncherPathName, config_manager
from .whitelist import is_whitelisted
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

    return libraries


def find_dlss_dlls(library_paths, launcher_name):
    dll_names = ["nvngx_dlss.dll", "nvngx_dlssg.dll", "nvngx_dlssd.dll"]
    dll_paths = []
    logger.debug(f"Searching for DLLs in {launcher_name}")

    for library_path in library_paths:
        logger.debug(f"Scanning directory: {library_path}")
        try:
            for root, _, files in os.walk(library_path):
                for dll_name in dll_names:
                    if dll_name.lower() in [f.lower() for f in files]:
                        dll_path = os.path.join(root, dll_name)
                        logger.debug(f"Found DLL: {dll_path}")
                        if not is_whitelisted(dll_path):
                            logger.info(
                                f"Found non-whitelisted DLSS DLL in {launcher_name}: {dll_path}"
                            )
                            dll_paths.append(dll_path)
                        else:
                            logger.info(
                                f"Skipped whitelisted game in {launcher_name}: {dll_path}"
                            )
        except Exception as e:
            logger.error(f"Error scanning {library_path}: {e}")

    return dll_paths


def get_ea_games():
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


def get_ubisoft_games(ubisoft_path):
    ubisoft_games_path = Path(ubisoft_path) / "games"
    if not ubisoft_games_path.exists():
        return []
    return [ubisoft_games_path]

def get_xbox_games():
    xbox_path = config_manager.check_path_value(LauncherPathName.XBOX)
    if not xbox_path or xbox_path == "":
        return []
    xbox_games_path = Path(xbox_path)
    return [xbox_games_path] if xbox_games_path.exists() else []

def get_epic_games():
    epic_path = config_manager.check_path_value(LauncherPathName.EPIC)
    if not epic_path or epic_path == "":
        return []
    epic_games_path = Path(epic_path)
    return [epic_games_path] if epic_games_path.exists() else []


def get_gog_games():
    gog_path = config_manager.check_path_value(LauncherPathName.GOG)
    if not gog_path or gog_path == "":
        return []
    gog_games_path = Path(gog_path)
    return [gog_games_path] if gog_games_path.exists() else []


def get_battlenet_games():
    battlenet_path = config_manager.check_path_value(LauncherPathName.BATTLENET)
    if not battlenet_path or battlenet_path == "":
        return []
    battlenet_games_path = Path(battlenet_path)
    return [battlenet_games_path] if battlenet_games_path.exists() else []


def find_all_dlss_dlls():
    logger.info("Starting find_all_dlss_dlls function")
    all_dll_paths = {
        "Steam": [],
        "EA Launcher": [],
        "Ubisoft Launcher": [],
        "Epic Games Launcher": [],
        "GOG Launcher": [],
        "Battle.net Launcher": [],
        "Xbox Launcher": [],
    }

    # Steam
    steam_path = get_steam_install_path()
    if steam_path:
        steam_libraries = get_steam_libraries(steam_path)
        all_dll_paths["Steam"] = find_dlss_dlls(steam_libraries, "Steam")

    # EA
    ea_games = get_ea_games()
    if ea_games:
        all_dll_paths["EA Launcher"] = find_dlss_dlls(ea_games, "EA Launcher")

    # Ubisoft
    ubisoft_path = get_ubisoft_install_path()
    if ubisoft_path:
        ubisoft_games = get_ubisoft_games(ubisoft_path)
        all_dll_paths["Ubisoft Launcher"] = find_dlss_dlls(
            ubisoft_games, "Ubisoft Launcher"
        )

    # Epic Games
    epic_games = get_epic_games()
    if epic_games:
        all_dll_paths["Epic Games Launcher"] = find_dlss_dlls(
            epic_games, "Epic Games Launcher"
        )

    # Xbox
    xbox_games = get_xbox_games()
    if xbox_games:
        all_dll_paths["Xbox Launcher"] = find_dlss_dlls(xbox_games, "Xbox Launcher")

    # GOG
    gog_games = get_gog_games()
    if gog_games:
        all_dll_paths["GOG Launcher"] = find_dlss_dlls(gog_games, "GOG Launcher")

    # Battle.net
    battlenet_games = get_battlenet_games()
    if battlenet_games:
        all_dll_paths["Battle.net Launcher"] = find_dlss_dlls(
            battlenet_games, "Battle.net Launcher"
        )

    # Remove duplicates
    unique_dlls = set()
    for launcher in all_dll_paths:
        all_dll_paths[launcher] = [
            dll
            for dll in all_dll_paths[launcher]
            if str(dll) not in unique_dlls and not unique_dlls.add(str(dll))
        ]

    return all_dll_paths
