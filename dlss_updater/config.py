import os
import sys
import configparser
import threading
from pathlib import Path
from enum import StrEnum
import msgspec
from .logger import setup_logger
from .models import (
    UpdatePreferencesConfig,
    LauncherPathsConfig,
    PerformanceConfig,
    DLSSPresetConfig,
    MAX_PATHS_PER_LAUNCHER,
)

logger = setup_logger()

# =============================================================================
# CONCURRENCY CONFIGURATION - Maximize hardware utilization
# =============================================================================
# Target: Use ~90% of available CPU threads for maximum performance
# I/O-bound tasks can use much higher multipliers since threads wait on I/O

def _get_cpu_threads() -> int:
    """Get the number of CPU threads (including hyperthreading)."""
    return os.cpu_count() or 8  # Fallback to 8 if detection fails

def _is_gil_disabled() -> bool:
    """Check if running on free-threaded Python (GIL disabled)."""
    try:
        return not sys._is_gil_enabled()
    except AttributeError:
        return False

# Core thread counts
CPU_THREADS = _get_cpu_threads()
GIL_DISABLED = _is_gil_disabled()

# =============================================================================
# CONCURRENCY MULTIPLIERS - Aggressive settings for maximum throughput
# =============================================================================
# CPU-bound tasks: Use 90% of threads (e.g., PE parsing)
# I/O-bound tasks: Use high multipliers since threads wait on I/O, not CPU

class Concurrency:
    """
    Centralized concurrency limits scaled to hardware.

    For a 16-thread CPU at 90% utilization:
    - CPU_BOUND = 14 threads (90% of 16)
    - IO_LIGHT = 112 (CPU * 8, lightweight I/O like string ops)
    - IO_MEDIUM = 224 (CPU * 16, file operations)
    - IO_HEAVY = 448 (CPU * 32, network/disk heavy ops)
    - IO_EXTREME = 896 (CPU * 64, async I/O that mostly waits)
    """

    # CPU-bound operations (PE parsing, version extraction)
    # Use 90% of CPU threads - these actually consume CPU cycles
    CPU_BOUND: int = max(4, int(CPU_THREADS * 0.9))

    # CPU-bound with GIL disabled gets full parallelism
    CPU_BOUND_PARALLEL: int = max(8, int(CPU_THREADS * 0.9)) if GIL_DISABLED else max(4, CPU_THREADS // 2)

    # I/O-bound operations - threads spend most time waiting, not computing
    # These can be MUCH higher than CPU count

    # Light I/O (string matching, in-memory ops with occasional I/O)
    IO_LIGHT: int = CPU_THREADS * 8

    # Medium I/O (file stat, small file reads, directory listing)
    IO_MEDIUM: int = CPU_THREADS * 16

    # Heavy I/O (file copy, large file reads, database ops)
    IO_HEAVY: int = CPU_THREADS * 32

    # Extreme I/O (network requests, async operations that mostly wait)
    IO_EXTREME: int = CPU_THREADS * 64

    # ThreadPoolExecutor limits (OS has limits on actual threads)
    # These are for actual OS threads, not async tasks
    THREADPOOL_CPU: int = max(8, int(CPU_THREADS * 0.9))
    THREADPOOL_IO: int = min(256, CPU_THREADS * 8)  # Cap at 256 real threads

    @classmethod
    def log_config(cls):
        """Log the concurrency configuration."""
        logger.info(f"Concurrency config: CPU_THREADS={CPU_THREADS}, GIL_DISABLED={GIL_DISABLED}")
        logger.info(f"  CPU_BOUND={cls.CPU_BOUND}, CPU_BOUND_PARALLEL={cls.CPU_BOUND_PARALLEL}")
        logger.info(f"  IO_LIGHT={cls.IO_LIGHT}, IO_MEDIUM={cls.IO_MEDIUM}")
        logger.info(f"  IO_HEAVY={cls.IO_HEAVY}, IO_EXTREME={cls.IO_EXTREME}")
        logger.info(f"  THREADPOOL_CPU={cls.THREADPOOL_CPU}, THREADPOOL_IO={cls.THREADPOOL_IO}")

# Thread-safety locks for free-threading (Python 3.14+)
_config_lock = threading.Lock()
_dll_paths_lock = threading.Lock()


def get_config_path():
    """Get the path for storing configuration files using centralized config dir."""
    from dlss_updater.platform_utils import APP_CONFIG_DIR
    return str(APP_CONFIG_DIR / "config.ini")


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(".").resolve()
    return str(base_path / relative_path)


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
    "amd_fidelityfx_upscaler_dx12.dll": "4.0.2.0",
    "amd_fidelityfx_framegeneration_dx12.dll": "4.0.2.0",
    "amd_fidelityfx_loader_dx12.dll": "4.0.2.0",
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
        # Double-checked locking pattern for free-threading safety
        if cls._instance is None:
            with _config_lock:
                if cls._instance is None:
                    cls._instance = super(ConfigManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "initialized"):
            super().__init__()
            self.logger = setup_logger()
            self.config_path = get_config_path()
            self.read(self.config_path)

            # Detect fresh install BEFORE adding any sections
            # Fresh install = config file was empty/non-existent
            is_fresh_install = len(self.sections()) == 0

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
                        "CreateBackups": "true",  # Default to true for safety
                    }
                )
                self.save()
            else:
                # Add Streamline preference if it doesn't exist (for existing configs)
                if "UpdateStreamline" not in self["UpdatePreferences"]:
                    self["UpdatePreferences"]["UpdateStreamline"] = "false"
                    self.save()
                # Add CreateBackups preference if it doesn't exist (for existing configs)
                if "CreateBackups" not in self["UpdatePreferences"]:
                    self["UpdatePreferences"]["CreateBackups"] = "true"
                    self.save()
                # Add HighPerformanceMode preference if it doesn't exist (for existing configs)
                if "HighPerformanceMode" not in self["UpdatePreferences"]:
                    self["UpdatePreferences"]["HighPerformanceMode"] = "false"
                    self.save()

            # Initialize DiscordBanner section
            if not self.has_section("DiscordBanner"):
                self.add_section("DiscordBanner")
                # Fresh install: show banner to new users (dismissed=false)
                # Upgrade: don't re-show banner (dismissed=true)
                self["DiscordBanner"]["dismissed"] = "false" if is_fresh_install else "true"
                self.save()

            # Initialize ImageCache section (for migration tracking)
            if not self.has_section("ImageCache"):
                self.add_section("ImageCache")
                self["ImageCache"]["Version"] = "0"  # Pre-WebP thumbnails
                self.save()

            # Initialize UIPreferences section with defaults if missing
            if not self.has_section("UIPreferences"):
                self.add_section("UIPreferences")
                self["UIPreferences"]["SmoothScrolling"] = "true"  # Enabled by default
                self.save()

            # Initialize DLSSPresets section with defaults if missing
            if not self.has_section("DLSSPresets"):
                self.add_section("DLSSPresets")
                self["DLSSPresets"]["SelectedPreset"] = "default"
                self["DLSSPresets"]["AutoDetectEnabled"] = "true"
                self["DLSSPresets"]["DetectedArchitecture"] = ""
                self["DLSSPresets"]["LastDetectionTime"] = ""
                self["DLSSPresets"]["LinuxOverlayEnabled"] = "false"
                # New feature for this user - always show dialog on first launch
                # (regardless of whether it's a fresh install or upgrade)
                self["DLSSPresets"]["DialogShown"] = "false"
                self.save()
            else:
                # Existing DLSSPresets section - add DialogShown if missing
                # This handles future upgrades where we add DialogShown to existing section
                if "DialogShown" not in self["DLSSPresets"]:
                    # User already configured presets before DialogShown existed - don't re-show
                    self["DLSSPresets"]["DialogShown"] = "true"
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

    # =========================================================================
    # Multi-Path Methods (Sub-Folder Support)
    # =========================================================================

    def get_launcher_paths(self, launcher: LauncherPathName) -> list[str]:
        """
        Get all paths for a launcher (multi-path support).

        Handles both legacy single-path format and new JSON array format.

        Args:
            launcher: The launcher to get paths for

        Returns:
            List of paths, or empty list if none configured
        """
        raw_value = self["LauncherPaths"].get(launcher, "")
        if not raw_value:
            return []

        # Try parsing as JSON array first (new format)
        if raw_value.startswith("["):
            try:
                paths = msgspec.json.decode(raw_value.encode())
                if isinstance(paths, list):
                    return [p for p in paths if p]  # Filter empty strings
            except Exception as e:
                self.logger.warning(f"Failed to parse JSON paths for {launcher}: {e}")

        # Fallback: treat as single path (legacy format)
        return [raw_value] if raw_value else []

    def set_launcher_paths(self, launcher: LauncherPathName, paths: list[str]):
        """
        Set all paths for a launcher (multi-path support).

        Stores as JSON array format. Enforces MAX_PATHS_PER_LAUNCHER limit.

        Args:
            launcher: The launcher to set paths for
            paths: List of paths to set
        """
        # Filter empty paths and enforce limit
        filtered_paths = [p for p in paths if p][:MAX_PATHS_PER_LAUNCHER]

        # Encode as JSON array
        json_value = msgspec.json.encode(filtered_paths).decode()
        self["LauncherPaths"][launcher] = json_value
        self.save()
        self.logger.debug(f"Set {len(filtered_paths)} paths for {launcher}")

    def add_launcher_path(self, launcher: LauncherPathName, new_path: str) -> bool:
        """
        Add a new path to a launcher's path list.

        Args:
            launcher: The launcher to add path to
            new_path: The path to add

        Returns:
            True if path was added, False if at limit or duplicate
        """
        if not new_path:
            return False

        paths = self.get_launcher_paths(launcher)

        # Check limit
        if len(paths) >= MAX_PATHS_PER_LAUNCHER:
            self.logger.warning(f"Cannot add path to {launcher}: limit of {MAX_PATHS_PER_LAUNCHER} reached")
            return False

        # Check for duplicate (case-insensitive on Windows)
        normalized_new = str(Path(new_path).resolve()).lower()
        for existing in paths:
            if str(Path(existing).resolve()).lower() == normalized_new:
                self.logger.debug(f"Path already exists for {launcher}: {new_path}")
                return False

        paths.append(new_path)
        self.set_launcher_paths(launcher, paths)
        self.logger.info(f"Added path to {launcher}: {new_path}")
        return True

    def remove_launcher_path(self, launcher: LauncherPathName, path_to_remove: str) -> bool:
        """
        Remove a path from a launcher's path list.

        Args:
            launcher: The launcher to remove path from
            path_to_remove: The path to remove

        Returns:
            True if path was removed, False if not found
        """
        paths = self.get_launcher_paths(launcher)

        # Find and remove (case-insensitive on Windows)
        normalized_remove = str(Path(path_to_remove).resolve()).lower()
        new_paths = []
        removed = False

        for p in paths:
            if str(Path(p).resolve()).lower() == normalized_remove:
                removed = True
            else:
                new_paths.append(p)

        if removed:
            self.set_launcher_paths(launcher, new_paths)
            self.logger.info(f"Removed path from {launcher}: {path_to_remove}")

        return removed

    def get_update_preference(self, technology):
        """Get update preference for a specific technology"""
        return self["UpdatePreferences"].getboolean(f"Update{technology}", True)

    def set_update_preference(self, technology, enabled):
        """Set update preference for a specific technology"""
        self["UpdatePreferences"][f"Update{technology}"] = str(enabled).lower()
        self.save()

    def get_backup_preference(self):
        """Get backup creation preference"""
        return self["UpdatePreferences"].getboolean("CreateBackups", True)

    def set_backup_preference(self, enabled):
        """Set backup creation preference"""
        self["UpdatePreferences"]["CreateBackups"] = str(enabled).lower()
        self.save()

    def get_discord_banner_dismissed(self) -> bool:
        """Get whether the Discord invite banner has been dismissed"""
        if not self.has_section("DiscordBanner"):
            return False
        return self["DiscordBanner"].getboolean("dismissed", False)

    def set_discord_banner_dismissed(self, dismissed: bool):
        """Set the Discord invite banner dismissed state"""
        if not self.has_section("DiscordBanner"):
            self.add_section("DiscordBanner")
        self["DiscordBanner"]["dismissed"] = str(dismissed).lower()
        self.save()

    def get_image_cache_version(self) -> int:
        """Get the image cache version for migration tracking."""
        if not self.has_section("ImageCache"):
            return 0
        return int(self["ImageCache"].get("Version", "0"))

    def set_image_cache_version(self, version: int):
        """Set the image cache version after migration."""
        if not self.has_section("ImageCache"):
            self.add_section("ImageCache")
        self["ImageCache"]["Version"] = str(version)
        self.save()

    def get_smooth_scrolling_enabled(self) -> bool:
        """Get smooth scrolling preference (default: enabled)"""
        with _config_lock:
            if not self.has_section("UIPreferences"):
                return True  # Default enabled
            return self["UIPreferences"].getboolean("SmoothScrolling", True)

    def set_smooth_scrolling_enabled(self, enabled: bool):
        """Set smooth scrolling preference and persist to config file"""
        with _config_lock:
            if not self.has_section("UIPreferences"):
                self.add_section("UIPreferences")
            self["UIPreferences"]["SmoothScrolling"] = str(enabled).lower()
            self.save()  # Persist to disk immediately

    def get_keep_games_in_memory(self) -> bool:
        """Get keep games in memory preference (default: enabled)"""
        with _config_lock:
            if not self.has_section("UIPreferences"):
                return True  # Default enabled
            return self["UIPreferences"].getboolean("KeepGamesInMemory", True)

    def set_keep_games_in_memory(self, enabled: bool):
        """Set keep games in memory preference and persist to config file"""
        with _config_lock:
            if not self.has_section("UIPreferences"):
                self.add_section("UIPreferences")
            self["UIPreferences"]["KeepGamesInMemory"] = str(enabled).lower()
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

    def get_update_preferences_struct(self) -> UpdatePreferencesConfig:
        """Get update preferences as validated msgspec struct"""
        return UpdatePreferencesConfig(
            update_dlss=self.get_update_preference("DLSS"),
            update_direct_storage=self.get_update_preference("DirectStorage"),
            update_xess=self.get_update_preference("XeSS"),
            update_fsr=self.get_update_preference("FSR"),
            update_streamline=self.get_update_preference("Streamline"),
            create_backups=self.get_backup_preference(),
            high_performance_mode=self.get_high_performance_mode()
        )

    def save_update_preferences_struct(self, prefs: UpdatePreferencesConfig):
        """Save update preferences from msgspec struct to INI"""
        if not self.has_section("UpdatePreferences"):
            self.add_section("UpdatePreferences")

        self["UpdatePreferences"]["UpdateDLSS"] = str(prefs.update_dlss).lower()
        self["UpdatePreferences"]["UpdateDirectStorage"] = str(prefs.update_direct_storage).lower()
        self["UpdatePreferences"]["UpdateXeSS"] = str(prefs.update_xess).lower()
        self["UpdatePreferences"]["UpdateFSR"] = str(prefs.update_fsr).lower()
        self["UpdatePreferences"]["UpdateStreamline"] = str(prefs.update_streamline).lower()
        self["UpdatePreferences"]["CreateBackups"] = str(prefs.create_backups).lower()
        self["UpdatePreferences"]["HighPerformanceMode"] = str(prefs.high_performance_mode).lower()
        self.save()

    def get_launcher_paths_struct(self) -> LauncherPathsConfig:
        """Get launcher paths as validated msgspec struct"""
        return LauncherPathsConfig(
            steam_path=self.check_path_value(LauncherPathName.STEAM) or None,
            ea_path=self.check_path_value(LauncherPathName.EA) or None,
            epic_path=self.check_path_value(LauncherPathName.EPIC) or None,
            gog_path=self.check_path_value(LauncherPathName.GOG) or None,
            ubisoft_path=self.check_path_value(LauncherPathName.UBISOFT) or None,
            battle_net_path=self.check_path_value(LauncherPathName.BATTLENET) or None,
            xbox_path=self.check_path_value(LauncherPathName.XBOX) or None,
            custom_path_1=self.check_path_value(LauncherPathName.CUSTOM1) or None,
            custom_path_2=self.check_path_value(LauncherPathName.CUSTOM2) or None,
            custom_path_3=self.check_path_value(LauncherPathName.CUSTOM3) or None,
            custom_path_4=self.check_path_value(LauncherPathName.CUSTOM4) or None
        )

    def save_launcher_paths_struct(self, paths: LauncherPathsConfig):
        """Save launcher paths from msgspec struct to INI"""
        if not self.has_section("LauncherPaths"):
            self.add_section("LauncherPaths")

        self["LauncherPaths"][LauncherPathName.STEAM] = paths.steam_path or ""
        self["LauncherPaths"][LauncherPathName.EA] = paths.ea_path or ""
        self["LauncherPaths"][LauncherPathName.EPIC] = paths.epic_path or ""
        self["LauncherPaths"][LauncherPathName.GOG] = paths.gog_path or ""
        self["LauncherPaths"][LauncherPathName.UBISOFT] = paths.ubisoft_path or ""
        self["LauncherPaths"][LauncherPathName.BATTLENET] = paths.battle_net_path or ""
        self["LauncherPaths"][LauncherPathName.XBOX] = paths.xbox_path or ""
        self["LauncherPaths"][LauncherPathName.CUSTOM1] = paths.custom_path_1 or ""
        self["LauncherPaths"][LauncherPathName.CUSTOM2] = paths.custom_path_2 or ""
        self["LauncherPaths"][LauncherPathName.CUSTOM3] = paths.custom_path_3 or ""
        self["LauncherPaths"][LauncherPathName.CUSTOM4] = paths.custom_path_4 or ""
        self.save()

    def get_performance_config_struct(self) -> PerformanceConfig:
        """Get performance config as validated msgspec struct"""
        return PerformanceConfig(
            max_worker_threads=self.get_max_worker_threads()
        )

    def save_performance_config_struct(self, perf: PerformanceConfig):
        """Save performance config from msgspec struct to INI"""
        self.set_max_worker_threads(perf.max_worker_threads)

    def get_dlss_preset_config(self) -> DLSSPresetConfig:
        """
        Get DLSS preset configuration as validated msgspec struct.

        Returns:
            DLSSPresetConfig with current settings
        """
        with _config_lock:
            if not self.has_section("DLSSPresets"):
                return DLSSPresetConfig()

            return DLSSPresetConfig(
                selected_preset=self["DLSSPresets"].get("SelectedPreset", "default"),
                auto_detect_enabled=self["DLSSPresets"].getboolean("AutoDetectEnabled", True),
                detected_architecture=self["DLSSPresets"].get("DetectedArchitecture") or None,
                last_detection_time=self["DLSSPresets"].get("LastDetectionTime") or None,
                linux_overlay_enabled=self["DLSSPresets"].getboolean("LinuxOverlayEnabled", False),
            )

    def save_dlss_preset_config(self, config: DLSSPresetConfig):
        """
        Save DLSS preset configuration from msgspec struct to INI.

        Args:
            config: DLSSPresetConfig to save
        """
        with _config_lock:
            if not self.has_section("DLSSPresets"):
                self.add_section("DLSSPresets")

            self["DLSSPresets"]["SelectedPreset"] = config.selected_preset
            self["DLSSPresets"]["AutoDetectEnabled"] = str(config.auto_detect_enabled).lower()
            self["DLSSPresets"]["DetectedArchitecture"] = config.detected_architecture or ""
            self["DLSSPresets"]["LastDetectionTime"] = config.last_detection_time or ""
            self["DLSSPresets"]["LinuxOverlayEnabled"] = str(config.linux_overlay_enabled).lower()
            self.save()

    def get_dlss_preset_dialog_shown(self) -> bool:
        """
        Get whether the DLSS preset dialog has been shown to the user.

        Returns:
            True if dialog has been shown, False otherwise
        """
        with _config_lock:
            if not self.has_section("DLSSPresets"):
                return False
            return self["DLSSPresets"].getboolean("DialogShown", False)

    def set_dlss_preset_dialog_shown(self, shown: bool):
        """
        Set whether the DLSS preset dialog has been shown to the user.

        Args:
            shown: True to mark as shown, False to re-enable showing
        """
        with _config_lock:
            if not self.has_section("DLSSPresets"):
                self.add_section("DLSSPresets")
            self["DLSSPresets"]["DialogShown"] = str(shown).lower()
            self.save()

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

    def get_high_performance_mode(self) -> bool:
        """
        Get high performance update mode.

        Always returns True as high-performance mode is now the only mode.
        This method is kept for backward compatibility with existing code.
        """
        return True

    def set_high_performance_mode(self, enabled: bool) -> None:
        """
        Set high performance update mode preference (deprecated).

        This method is a no-op since high-performance mode is now always enabled.
        Kept for backward compatibility with existing code.
        """
        # No-op: high-performance mode is now always enabled
        pass


config_manager = ConfigManager()


def get_current_settings():
    """
    Get current update settings from config

    Returns:
        dict: Dictionary with technology update preferences
    """
    return {
        "UpdateDLSS": config_manager.get_update_preference("DLSS"),
        "UpdateDirectStorage": config_manager.get_update_preference("DirectStorage"),
        "UpdateXeSS": config_manager.get_update_preference("XeSS"),
        "UpdateFSR": config_manager.get_update_preference("FSR"),
        "UpdateStreamline": config_manager.get_update_preference("Streamline"),
        "CreateBackups": config_manager.get_backup_preference(),
        "HighPerformanceMode": config_manager.get_high_performance_mode(),
    }


def is_dll_cache_ready():
    """Check if the DLL cache has been initialized (thread-safe)"""
    with _dll_paths_lock:
        return len(LATEST_DLL_PATHS) > 0


def initialize_dll_paths():
    """Initialize the DLL paths after all modules are loaded (thread-safe)"""
    from .dll_repository import get_local_dll_path

    global LATEST_DLL_PATHS

    # Build the dict outside the lock to minimize lock time
    new_paths = {
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
        "amd_fidelityfx_upscaler_dx12.dll": get_local_dll_path("amd_fidelityfx_upscaler_dx12.dll"),
        "amd_fidelityfx_framegeneration_dx12.dll": get_local_dll_path("amd_fidelityfx_framegeneration_dx12.dll"),
        "amd_fidelityfx_loader_dx12.dll": get_local_dll_path("amd_fidelityfx_loader_dx12.dll"),
        "sl.directsr.dll": get_local_dll_path("sl.directsr.dll"),
        "sl.dlss_d.dll": get_local_dll_path("sl.dlss_d.dll"),
        "sl.nis.dll": get_local_dll_path("sl.nis.dll"),
    }

    with _dll_paths_lock:
        LATEST_DLL_PATHS = new_paths

    return LATEST_DLL_PATHS
