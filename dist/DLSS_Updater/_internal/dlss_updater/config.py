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


# Version information without paths (paths will be resolved at runtime)
LATEST_DLL_VERSIONS = {
    "nvngx_dlss.dll": "310.2.1.0",
    "nvngx_dlssg.dll": "310.2.1.0",
    "nvngx_dlssd.dll": "310.2.1.0",
    "libxess.dll": "2.0.1.41",
    "libxess_dx11.dll": "2.0.1.41",
    "dstorage.dll": "1.2.2504.401",
    "dstoragecore.dll": "1.2.2504.401",
    "sl.common.dll": "2.7.30.0",
    "sl.dlss.dll": "2.7.30.0",
    "sl.dlss_g.dll": "2.7.30.0",
    "sl.interposer.dll": "2.7.30.0",
    "sl.pcl.dll": "2.7.30.0",
    "sl.reflex.dll": "2.7.30.0",
    "sl.directsr.dll": "2.8.0.0",
    "sl.dlss_d.dll": "2.8.0.0",
    "sl.nis.dll": "2.8.0.0",
}


# IMPORTANT: We'll initialize this later to avoid circular imports
LATEST_DLL_PATHS = {}


class LauncherPathName(StrEnum):
    STEAM = "SteamPath"
    EA = "EAPath"
    EPIC = "EpicPath"
    GOG = "GOGPath"
    UBISOFT = "UbisoftPath"
    BATTLENET = "BattleDotNetPath"
    XBOX = "XboxPath"
    CUSTOM1 = "CustomPath1"
    CUSTOM2 = "CustomPath2"
    CUSTOM3 = "CustomPath3"
    CUSTOM4 = "CustomPath4"


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

            # Initialize launcher paths section
            if not self.has_section("LauncherPaths"):
                self.add_section("LauncherPaths")
                self["LauncherPaths"].update(
                    {
                        LauncherPathName.STEAM: "",
                        LauncherPathName.EA: "",
                        LauncherPathName.EPIC: "",
                        LauncherPathName.GOG: "",
                        LauncherPathName.UBISOFT: "",
                        LauncherPathName.BATTLENET: "",
                        LauncherPathName.XBOX: "",
                        LauncherPathName.CUSTOM1: "",
                        LauncherPathName.CUSTOM2: "",
                        LauncherPathName.CUSTOM3: "",
                        LauncherPathName.CUSTOM4: "",
                    }
                )
                self.save()

            # Initialize update preferences section
            if not self.has_section("UpdatePreferences"):
                self.add_section("UpdatePreferences")
                self["UpdatePreferences"].update(
                    {
                        "UpdateDLSS": "true",
                        "UpdateDirectStorage": "true",
                        "UpdateXeSS": "true",
                        "UpdateFSR": "true",
                        "UpdateStreamline": "false",  # Default to false
                    }
                )
                self.save()
            else:
                # Add Streamline preference if it doesn't exist (for existing configs)
                if "UpdateStreamline" not in self["UpdatePreferences"]:
                    self["UpdatePreferences"]["UpdateStreamline"] = "false"
                    self.save()

            self.initialized = True

    def update_launcher_path(
        self, path_to_update: LauncherPathName, new_launcher_path: str
    ):
        self.logger.debug(f"Attempting to update path for {path_to_update}.")
        self["LauncherPaths"][path_to_update] = new_launcher_path
        self.save()
        self.logger.debug(f"Updated path for {path_to_update}.")

    def check_path_value(self, path_to_check: LauncherPathName) -> str:
        return self["LauncherPaths"].get(path_to_check, "")

    def reset_launcher_path(self, path_to_reset: LauncherPathName):
        self.logger.debug(f"Resetting path for {path_to_reset}.")
        self["LauncherPaths"][path_to_reset] = ""
        self.save()
        self.logger.debug(f"Reset path for {path_to_reset}.")

    def get_update_preference(self, technology):
        """Get update preference for a specific technology"""
        return self["UpdatePreferences"].getboolean(f"Update{technology}", True)

    def set_update_preference(self, technology, enabled):
        """Set update preference for a specific technology"""
        self["UpdatePreferences"][f"Update{technology}"] = str(enabled).lower()
        self.save()

    def get_all_blacklist_skips(self):
        """Get all games to skip in the blacklist"""
        if not self.has_section("BlacklistSkips"):
            self.add_section("BlacklistSkips")
            self.save()
        return [
            game
            for game, value in self["BlacklistSkips"].items()
            if value.lower() == "true"
        ]

    def add_blacklist_skip(self, game_name):
        """Add a game to skip in the blacklist"""
        if not self.has_section("BlacklistSkips"):
            self.add_section("BlacklistSkips")
        self["BlacklistSkips"][game_name] = "true"
        self.save()

    def clear_all_blacklist_skips(self):
        """Clear all blacklist skips"""
        if self.has_section("BlacklistSkips"):
            self.remove_section("BlacklistSkips")
            self.add_section("BlacklistSkips")
            self.save()

    def is_blacklist_skipped(self, game_name):
        """Check if a game is in the blacklist skip list"""
        if not self.has_section("BlacklistSkips"):
            return False
        return self["BlacklistSkips"].getboolean(game_name, False)

    def save(self):
        """Save configuration to disk"""
        with open(self.config_path, "w") as configfile:
            self.write(configfile)

    def get_max_worker_threads(self):
        """Get the maximum number of worker threads for parallel processing"""
        if not self.has_section("Performance"):
            self.add_section("Performance")
            self["Performance"]["MaxWorkerThreads"] = "16"
            self.save()
        return int(self["Performance"].get("MaxWorkerThreads", "16"))

    def set_max_worker_threads(self, count):
        """Set the maximum number of worker threads"""
        if not self.has_section("Performance"):
            self.add_section("Performance")
        self["Performance"]["MaxWorkerThreads"] = str(count)
        self.save()


config_manager = ConfigManager()


def initialize_dll_paths():
    """Initialize the DLL paths after all modules are loaded"""
    from .dll_repository import get_local_dll_path

    global LATEST_DLL_PATHS
    LATEST_DLL_PATHS = {
        "nvngx_dlss.dll": get_local_dll_path("nvngx_dlss.dll"),
        "nvngx_dlssg.dll": get_local_dll_path("nvngx_dlssg.dll"),
        "nvngx_dlssd.dll": get_local_dll_path("nvngx_dlssd.dll"),
        "libxess.dll": get_local_dll_path("libxess.dll"),
        "libxess_dx11.dll": get_local_dll_path("libxess_dx11.dll"),
        "dstorage.dll": get_local_dll_path("dstorage.dll"),
        "dstoragecore.dll": get_local_dll_path("dstoragecore.dll"),
        "sl.common.dll": get_local_dll_path("sl.common.dll"),
        "sl.dlss.dll": get_local_dll_path("sl.dlss.dll"),
        "sl.dlss_g.dll": get_local_dll_path("sl.dlss_g.dll"),
        "sl.interposer.dll": get_local_dll_path("sl.interposer.dll"),
        "sl.pcl.dll": get_local_dll_path("sl.pcl.dll"),
        "sl.reflex.dll": get_local_dll_path("sl.reflex.dll"),
        "amd_fidelityfx_vk.dll": get_local_dll_path("amd_fidelityfx_vk.dll"),
        "amd_fidelityfx_dx12.dll": get_local_dll_path("amd_fidelityfx_dx12.dll"),
        "sl.directsr.dll": get_local_dll_path("sl.directsr.dll"),
        "sl.dlss_d.dll": get_local_dll_path("sl.dlss_d.dll"),
        "sl.nis.dll": get_local_dll_path("sl.nis.dll"),
    }
    return LATEST_DLL_PATHS
