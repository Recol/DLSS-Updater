import os
import sys
import configparser
from enum import StrEnum
import appdirs
from .logger import setup_logger

logger = setup_logger()


def get_config_path():
    """Get the path for storing configuration files"""
    app_name = "DLSS-Updater"
    app_author = "Recol"
    config_dir = appdirs.user_config_dir(app_name, app_author)
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "config.ini")


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


LATEST_DLL_VERSIONS = {
    "nvngx_dlss.dll": "310.1.0.0",
    "nvngx_dlssg.dll": "310.1.0.0",
    "nvngx_dlssd.dll": "310.1.0.0",
}

LATEST_DLL_PATHS = {
    "nvngx_dlss.dll": resource_path(os.path.join("latest_dll", "nvngx_dlss.dll")),
    "nvngx_dlssg.dll": resource_path(os.path.join("latest_dll", "nvngx_dlssg.dll")),
    "nvngx_dlssd.dll": resource_path(os.path.join("latest_dll", "nvngx_dlssd.dll")),
}


class LauncherPathName(StrEnum):
    STEAM = "SteamPath"
    EA = "EAPath"
    EPIC = "EpicPath"
    GOG = "GOGPath"
    UBISOFT = "UbisoftPath"
    BATTLENET = "BattleDotNetPath"
    XBOX = "XboxPath"


class ConfigManager(configparser.ConfigParser):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "initialized"):
            super().__init__()
            self.logger = setup_logger()
            self.config_path = get_config_path()
            self.read(self.config_path)
            
            # Initialize sections
            sections = {
                "LauncherPaths": {
                    LauncherPathName.STEAM: "",
                    LauncherPathName.EA: "",
                    LauncherPathName.EPIC: "",
                    LauncherPathName.GOG: "",
                    LauncherPathName.UBISOFT: "",
                    LauncherPathName.BATTLENET: "",
                    LauncherPathName.XBOX: "",
                },
                "Settings": {
                    "CheckForUpdatesOnStart": "True",
                    "AutoBackup": "True",
                    "MinimizeToTray": "False",
                },
                "Updates": {
                    "LastUpdateCheck": "",
                    "CurrentDLSSVersion": LATEST_DLL_VERSIONS["nvngx_dlss.dll"],
                }
            }
            
            for section, values in sections.items():
                if not self.has_section(section):
                    self.add_section(section)
                for key, value in values.items():
                    if key not in self[section]:
                        self[section][key] = value
            
            self.save()
            self.initialized = True

    def update_launcher_path(self, path_to_update: LauncherPathName, new_launcher_path: str):
        """Update launcher path in config"""
        self.logger.debug(f"Attempting to update path for {path_to_update}.")
        self["LauncherPaths"][path_to_update] = new_launcher_path
        self.save()
        self.logger.debug(f"Updated path for {path_to_update}.")

    def check_path_value(self, path_to_check: LauncherPathName) -> str:
        """Get launcher path from config"""
        return self["LauncherPaths"].get(path_to_check, "")

    def reset_launcher_path(self, path_to_reset: LauncherPathName):
        """Reset launcher path to default empty value"""
        self.logger.debug(f"Resetting path for {path_to_reset}")
        self["LauncherPaths"][path_to_reset] = ""
        self.save()

    def get_setting(self, setting_name: str, default_value: str = "") -> str:
        """Get setting value with fallback"""
        return self["Settings"].get(setting_name, default_value)

    def update_setting(self, setting_name: str, value: str):
        """Update setting value"""
        self["Settings"][setting_name] = value
        self.save()

    def update_last_check_time(self, timestamp: str):
        """Update last update check timestamp"""
        self["Updates"]["LastUpdateCheck"] = timestamp
        self.save()

    def update_current_dlss_version(self, version: str):
        """Update current DLSS version"""
        self["Updates"]["CurrentDLSSVersion"] = version
        self.save()

    def save(self):
        """Save configuration to disk"""
        with open(self.config_path, "w") as configfile:
            self.write(configfile)


config_manager = ConfigManager()