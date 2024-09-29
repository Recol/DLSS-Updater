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


class LauncherPathName(StrEnum):
    STEAM = 'SteamPath'
    EA = 'EAPath'
    EPIC = 'EpicPath'
    GOG = 'GOGPath'
    UBISOFT = 'UbisoftPath'


class ConfigManager(configparser.ConfigParser):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            super().__init__()
            self.logger = setup_logger()
            self.read('dlss_updater/config.ini')
            self.initialized = True

    def update_launcher_path(self, path_to_update: LauncherPathName, new_launcher_path: str):
        self.logger.debug(f'Attempting to update path for {path_to_update}.')
        # Write changes back to the INI file
        self['LauncherPaths'][path_to_update] = new_launcher_path
        with open('dlss_updater/config.ini', 'w') as configfile:
            self.write(configfile)
        self.logger.debug(f'Updated path for {path_to_update}.')


    def check_path_value(self, path_to_check: LauncherPathName) -> str:
        if self['LauncherPaths'].get(path_to_check):
            return self['LauncherPaths'].get(path_to_check)

config_manager = ConfigManager()
