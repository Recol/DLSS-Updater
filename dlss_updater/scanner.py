import os
from pathlib import Path
from .whitelist import is_whitelisted


def get_steam_install_path():
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"
        )
        value, _ = winreg.QueryValueEx(key, "InstallPath")
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


def find_nvngx_dlss_dll(library_paths, launcher_name):
    dll_paths = []
    for library_path in library_paths:
        for root, _, files in os.walk(library_path):
            if "nvngx_dlss.dll" in files:
                dll_path = Path(root) / "nvngx_dlss.dll"
                if not is_whitelisted(str(dll_path)):
                    print(f"Found DLSS DLL in {launcher_name}: {dll_path}")
                    dll_paths.append(dll_path)
                else:
                    print(f"Skipped whitelisted game in {launcher_name}: {dll_path}")
    return dll_paths


def get_ea_games():
    ea_path = input(
        "Please enter the path for EA games or type 'N/A' to skip: "
    ).strip()
    if ea_path.lower() == "n/a":
        return []
    ea_games_path = Path(ea_path)
    if not ea_games_path.exists():
        print("Invalid path for EA games.")
        return []
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


def get_ubisoft_games(ubisoft_path):
    ubisoft_games_path = Path(ubisoft_path) / "games"
    if not ubisoft_games_path.exists():
        return []
    return [ubisoft_games_path]


def get_epic_games():
    epic_path = input(
        "Please enter the path for Epic Games or type 'N/A' to skip: "
    ).strip()
    if epic_path.lower() == "n/a":
        return []
    epic_games_path = Path(epic_path)
    if not epic_games_path.exists():
        print("Invalid path for Epic Games.")
        return []
    return [epic_games_path]


def get_gog_games():
    gog_path = input(
        "Please enter the path for GOG games or type 'N/A' to skip: "
    ).strip()
    if gog_path.lower() == "n/a":
        return []
    gog_games_path = Path(gog_path)
    if not gog_games_path.exists():
        print("Invalid path for GOG games.")
        return []
    return [gog_games_path]


def get_battlenet_games():
    battlenet_path = input(
        "Please enter the path for Battle.net games or type 'N/A' to skip: "
    ).strip()
    if battlenet_path.lower() == "n/a":
        return []
    battlenet_games_path = Path(battlenet_path)
    if not battlenet_games_path.exists():
        print("Invalid path for Battle.net games.")
        return []
    return [battlenet_games_path]


def find_all_dlss_dlls():
    all_dll_paths = {
        "Steam": [],
        "EA Launcher": [],
        "Ubisoft Launcher": [],
        "Epic Games Launcher": [],
        "GOG Launcher": [],
        "Battle.net Launcher": [],
    }

    steam_path = get_steam_install_path()
    if steam_path:
        steam_libraries = get_steam_libraries(steam_path)
        all_dll_paths["Steam"].extend(find_nvngx_dlss_dll(steam_libraries, "Steam"))

    ea_games = get_ea_games()
    if ea_games:
        all_dll_paths["EA Launcher"].extend(
            find_nvngx_dlss_dll(ea_games, "EA Launcher")
        )

    ubisoft_path = get_ubisoft_install_path()
    if ubisoft_path:
        ubisoft_games = get_ubisoft_games(ubisoft_path)
        all_dll_paths["Ubisoft Launcher"].extend(
            find_nvngx_dlss_dll(ubisoft_games, "Ubisoft Launcher")
        )

    epic_games = get_epic_games()
    if epic_games:
        all_dll_paths["Epic Games Launcher"].extend(
            find_nvngx_dlss_dll(epic_games, "Epic Games Launcher")
        )

    gog_games = get_gog_games()
    if gog_games:
        all_dll_paths["GOG Launcher"].extend(
            find_nvngx_dlss_dll(gog_games, "GOG Launcher")
        )

    battlenet_games = get_battlenet_games()
    if battlenet_games:
        all_dll_paths["Battle.net Launcher"].extend(
            find_nvngx_dlss_dll(battlenet_games, "Battle.net Launcher")
        )

    return all_dll_paths
