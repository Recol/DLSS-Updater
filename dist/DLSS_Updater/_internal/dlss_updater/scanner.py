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
    """
    Find DLSS and XeSS DLLs in the given list of library paths.
    """
    dll_names = [
        "nvngx_dlss.dll",
        "nvngx_dlssg.dll",
        "nvngx_dlssd.dll",
        "libxess.dll",
        "libxess_dx11.dll",
    ]
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
                                f"Found non-whitelisted DLL in {launcher_name}: {dll_path}"
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
    """
    Find EA game directories and DLLs within the given path.
    """
    ea_path = config_manager.check_path_value(LauncherPathName.EA)
    ea_games_paths = []
    ea_dll_paths = []

    if ea_path and ea_path != "":
        for root, dirs, files in os.walk(ea_path):
            for file in files:
                if file.lower() in [
                    "nvngx_dlss.dll",
                    "nvngx_dlssg.dll",
                    "nvngx_dlssd.dll",
                    "libxess.dll",
                    "libxess_dx11.dll",
                ]:
                    dll_path = os.path.join(root, file)
                    ea_dll_paths.append(dll_path)
                    logger.debug(f"Found EA DLL: {dll_path}")

            for dir in dirs:
                if dir.lower() == "ea games":
                    games_path = os.path.join(root, dir)
                    ea_games_paths.append(games_path)
                    logger.debug(f"Found EA games directory: {games_path}")

    return ea_games_paths, ea_dll_paths


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
        logger.debug(f"Ubisoft install path: {value}")
        return value
    except (FileNotFoundError, ImportError):
        logger.error("Could not find Ubisoft install path")
        return None


def get_ubisoft_games(ubisoft_path):
    """
    Find Ubisoft game directories and DLLs within the given path.
    """
    ubisoft_games_paths = []
    ubisoft_dll_paths = []

    for root, dirs, files in os.walk(ubisoft_path):
        for file in files:
            if file.lower() in [
                "nvngx_dlss.dll",
                "nvngx_dlssg.dll",
                "nvngx_dlssd.dll",
                "libxess.dll",
                "libxess_dx11.dll",
            ]:
                dll_path = os.path.join(root, file)
                ubisoft_dll_paths.append(dll_path)
                logger.debug(f"Found Ubisoft DLL: {dll_path}")

        for dir in dirs:
            if dir.lower() == "games":
                games_path = os.path.join(root, dir)
                ubisoft_games_paths.append(games_path)
                logger.debug(f"Found Ubisoft games directory: {games_path}")

    return ubisoft_games_paths, ubisoft_dll_paths


def get_xbox_games():
    """
    Find Xbox game directories and DLLs within the given path.
    """
    xbox_path = config_manager.check_path_value(LauncherPathName.XBOX)
    xbox_games_paths = []
    xbox_dll_paths = []

    if xbox_path and xbox_path != "":
        for root, dirs, files in os.walk(xbox_path):
            for file in files:
                if file.lower() in [
                    "nvngx_dlss.dll",
                    "nvngx_dlssg.dll",
                    "nvngx_dlssd.dll",
                    "libxess.dll",
                    "libxess_dx11.dll",
                ]:
                    dll_path = os.path.join(root, file)
                    xbox_dll_paths.append(dll_path)
                    logger.debug(f"Found Xbox DLL: {dll_path}")

            for dir in dirs:
                if dir.lower() == "games":
                    games_path = os.path.join(root, dir)
                    xbox_games_paths.append(games_path)
                    logger.debug(f"Found Xbox games directory: {games_path}")

    return xbox_games_paths, xbox_dll_paths


def get_epic_games():
    """
    Find Epic Games directories and DLLs within the given path.
    """
    epic_path = config_manager.check_path_value(LauncherPathName.EPIC)
    epic_games_paths = []
    epic_dll_paths = []

    if epic_path and epic_path != "":
        for root, dirs, files in os.walk(epic_path):
            for file in files:
                if file.lower() in [
                    "nvngx_dlss.dll",
                    "nvngx_dlssg.dll",
                    "nvngx_dlssd.dll",
                    "libxess.dll",
                    "libxess_dx11.dll",
                ]:
                    dll_path = os.path.join(root, file)
                    epic_dll_paths.append(dll_path)
                    logger.debug(f"Found Epic Games DLL: {dll_path}")

            for dir in dirs:
                if dir.lower() == "games":
                    games_path = os.path.join(root, dir)
                    epic_games_paths.append(games_path)
                    logger.debug(f"Found Epic Games directory: {games_path}")

    return epic_games_paths, epic_dll_paths


def get_gog_games():
    """
    Find GOG game directories and DLLs within the given path.
    """
    gog_path = config_manager.check_path_value(LauncherPathName.GOG)
    gog_games_paths = []
    gog_dll_paths = []

    if gog_path and gog_path != "":
        for root, dirs, files in os.walk(gog_path):
            for file in files:
                if file.lower() in [
                    "nvngx_dlss.dll",
                    "nvngx_dlssg.dll",
                    "nvngx_dlssd.dll",
                    "libxess.dll",
                    "libxess_dx11.dll",
                ]:
                    dll_path = os.path.join(root, file)
                    gog_dll_paths.append(dll_path)
                    logger.debug(f"Found GOG DLL: {dll_path}")

            for dir in dirs:
                if dir.lower() == "games":
                    games_path = os.path.join(root, dir)
                    gog_games_paths.append(games_path)
                    logger.debug(f"Found GOG games directory: {games_path}")

    return gog_games_paths, gog_dll_paths


def get_battlenet_games(battlenet_path):
    """
    Find Battle.net game directories and DLLs within the given path.
    """
    battlenet_games_paths = []
    battlenet_dll_paths = []

    for root, dirs, files in os.walk(battlenet_path):
        for file in files:
            if file.lower() in [
                "nvngx_dlss.dll",
                "nvngx_dlssg.dll",
                "nvngx_dlssd.dll",
                "libxess.dll",
                "libxess_dx11.dll",
            ]:
                dll_path = os.path.join(root, file)
                battlenet_dll_paths.append(dll_path)
                logger.debug(f"Found Battle.net DLL: {dll_path}")

        for dir in dirs:
            if dir.lower() == "games":
                games_path = os.path.join(root, dir)
                battlenet_games_paths.append(games_path)
                logger.debug(f"Found Battle.net games directory: {games_path}")

    return battlenet_games_paths, battlenet_dll_paths


def get_custom_games(path_enum):
    """
    Find DLLs in custom game directories.
    """
    custom_path = config_manager.check_path_value(path_enum)
    custom_dll_paths = []

    if custom_path and custom_path != "":
        for root, _, files in os.walk(custom_path):
            for file in [
                f
                for f in files
                if f.lower()
                in [
                    "nvngx_dlss.dll",
                    "nvngx_dlssg.dll",
                    "nvngx_dlssd.dll",
                    "libxess.dll",
                    "libxess_dx11.dll",
                ]
            ]:
                dll_path = os.path.join(root, file)
                custom_dll_paths.append(dll_path)
                logger.debug(f"Found DLL in custom path: {dll_path}")

    return custom_dll_paths


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
        "Custom Path 1": [],
        "Custom Path 2": [],
        "Custom Path 3": [],
        "Custom Path 4": [],
    }

    # Steam
    steam_path = get_steam_install_path()
    if steam_path:
        steam_libraries = get_steam_libraries(steam_path)
        all_dll_paths["Steam"] = find_dlss_dlls(steam_libraries, "Steam")

    # EA
    ea_games, ea_dlls = get_ea_games()
    if ea_games:
        all_dll_paths["EA Launcher"].extend(ea_dlls)

    # Ubisoft
    ubisoft_path = get_ubisoft_install_path()
    if ubisoft_path:
        ubisoft_games, ubisoft_dlls = get_ubisoft_games(ubisoft_path)
        all_dll_paths["Ubisoft Launcher"].extend(ubisoft_dlls)

    # Epic Games
    epic_games, epic_dlls = get_epic_games()
    if epic_games:
        all_dll_paths["Epic Games Launcher"].extend(epic_dlls)

    # Xbox
    xbox_games, xbox_dlls = get_xbox_games()
    if xbox_games:
        all_dll_paths["Xbox Launcher"].extend(xbox_dlls)

    # GOG
    gog_games, gog_dlls = get_gog_games()
    if gog_games:
        all_dll_paths["GOG Launcher"].extend(gog_dlls)

    # Battle.net
    battlenet_path = config_manager.check_path_value(LauncherPathName.BATTLENET)
    if battlenet_path:
        battlenet_games, battlenet_dlls = get_battlenet_games(battlenet_path)
        all_dll_paths["Battle.net Launcher"].extend(battlenet_dlls)

    # Custom paths
    custom_paths = [
        (LauncherPathName.CUSTOM1, "Custom Path 1"),
        (LauncherPathName.CUSTOM2, "Custom Path 2"),
        (LauncherPathName.CUSTOM3, "Custom Path 3"),
        (LauncherPathName.CUSTOM4, "Custom Path 4"),
    ]

    for path_enum, key in custom_paths:
        custom_dlls = get_custom_games(path_enum)
        if custom_dlls:
            all_dll_paths[key].extend(custom_dlls)

    # Remove duplicates
    for launcher in all_dll_paths:
        all_dll_paths[launcher] = list(set(all_dll_paths[launcher]))

    return all_dll_paths
