import os
import sys
import configparser
from enum import StrEnum
from .logger import setup_logger

logger = setup_logger()


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


LATEST_DLL_VERSIONS = {
    "nvngx_dlss.dll": "3.17.20.0",
    "nvngx_dlssg.dll": "3.17.10.0",
    "nvngx_dlssd.dll": "3.17.10.0",
}

LATEST_DLL_PATHS = {
    "nvngx_dlss.dll": resource_path(os.path.join("latest_dll", "nvngx_dlss.dll")),
    "nvngx_dlssg.dll": resource_path(os.path.join("latest_dll", "nvngx_dlssg.dll")),
    "nvngx_dlssd.dll": resource_path(os.path.join("latest_dll", "nvngx_dlssd.dll")),
}

config_manager = configparser.ConfigParser()

config_manager.read('dlss_updater/config.ini')

class LauncherPathName(StrEnum):
    STEAM = 'SteamPath'
    EA = 'EAPath'
    EPIC = 'EpicPath'
    GOG = 'GOGPath'
    UBISOFT = 'UbisoftPath'


def update_launcher_path(path_to_update: LauncherPathName, new_launcher_path: str):
    logger.debug(f'Attempting to update path for {path_to_update}.')
    # Write changes back to the INI file
    config_manager['LauncherPaths'][path_to_update] = new_launcher_path
    with open('dlss_updater/config.ini', 'w') as configfile:
        config_manager.write(configfile)
    logger.debug(f'Updated path for {path_to_update}.')


def check_path_value(path_to_check: LauncherPathName) -> str:
    if config_manager['LauncherPaths'].get(path_to_check):
        return config_manager['LauncherPaths'].get(path_to_check)