import os
import winreg
from pathlib import Path

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

def find_nvngx_dlss_dll(library_paths):
    dll_paths = []
    for library_path in library_paths:
        for root, _, files in os.walk(library_path):
            if "nvngx_dlss.dll" in files:
                dll_path = Path(root) / "nvngx_dlss.dll"
                dll_paths.append(dll_path)
    return dll_paths
