import os
import winreg
from pathlib import Path
from .whitelist import is_whitelisted

def get_steam_install_path():
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")
        value, _ = winreg.QueryValueEx(key, "InstallPath")
        return value
    except FileNotFoundError:
        return None

def get_steam_libraries(steam_path):
    library_folders_path = Path(steam_path) / "steamapps" / "libraryfolders.vdf"
    if not library_folders_path.exists():
        return [Path(steam_path) / "steamapps" / "common"]

    libraries = []
    with library_folders_path.open('r') as file:
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

def get_ea_install_path():
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Electronic Arts\EA Desktop")
        value, _ = winreg.QueryValueEx(key, "InstallationDir")
        return value
    except FileNotFoundError:
        return None

def get_ea_games(ea_path):
    ea_games_path = Path(ea_path) / "EA Games"
    if not ea_games_path.exists():
        return []
    return [ea_games_path]

def get_ubisoft_install_path():
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher")
        value, _ = winreg.QueryValueEx(key, "InstallDir")
        return value
    except FileNotFoundError:
        return None

def get_ubisoft_games(ubisoft_path):
    ubisoft_games_path = Path(ubisoft_path) / "games"
    if not ubisoft_games_path.exists():
        return []
    return [ubisoft_games_path]

def get_epic_games_install_path():
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Epic Games\EpicGamesLauncher")
        value, _ = winreg.QueryValueEx(key, "AppDataPath")
        return value
    except FileNotFoundError:
        return None

def get_epic_games_libraries(epic_path):
    epic_games_path = Path(epic_path).parent / "UnrealEngine" / "Launcher" / "Installed"
    if not epic_games_path.exists():
        return []
    return [epic_games_path]

def find_all_dlss_dlls():
    all_dll_paths = []

    steam_path = get_steam_install_path()
    if steam_path:
        steam_libraries = get_steam_libraries(steam_path)
        all_dll_paths.extend(find_nvngx_dlss_dll(steam_libraries, "Steam"))

    ea_path = get_ea_install_path()
    if ea_path:
        ea_games = get_ea_games(ea_path)
        all_dll_paths.extend(find_nvngx_dlss_dll(ea_games, "EA Launcher"))

    ubisoft_path = get_ubisoft_install_path()
    if ubisoft_path:
        ubisoft_games = get_ubisoft_games(ubisoft_path)
        all_dll_paths.extend(find_nvngx_dlss_dll(ubisoft_games, "Ubisoft Launcher"))

    epic_path = get_epic_games_install_path()
    if epic_path:
        epic_libraries = get_epic_games_libraries(epic_path)
        all_dll_paths.extend(find_nvngx_dlss_dll(epic_libraries, "Epic Games Launcher"))

    return all_dll_paths
