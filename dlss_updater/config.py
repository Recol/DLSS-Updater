import configparser
import os
import sys
import threading
from enum import StrEnum
from pathlib import Path

import msgspec

from .logger import setup_logger
from .models import (
    MAX_PATHS_PER_LAUNCHER,
    LauncherPathsConfig,
    LinuxDLSSConfig,
    PerformanceConfig,
    UpdatePreferencesConfig,
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
    # Keep modest to limit memory usage (~1MB stack per thread on Windows)
    THREADPOOL_CPU: int = max(8, min(32, int(CPU_THREADS * 0.9)))  # Cap at 32 for CPU work
    THREADPOOL_IO: int = min(32, CPU_THREADS * 2)  # Cap at 32 for I/O (was 128, too much memory)

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
    "libxess.dll": "2.0.2.68",
    "libxess_dx11.dll": "2.0.2.68",
    "libxess_fg.dll": "1.2.2.118",
    "libxell.dll": "1.2.1.13",
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


def update_latest_dll_versions_from_cache() -> None:
    """Update LATEST_DLL_VERSIONS by reading actual versions from cached DLL files.

    Called after initialize_dll_paths() so the UI displays the real version
    of each DLL in the local cache — i.e. what a game would be updated TO.
    The hardcoded values above serve only as fallbacks before cache init.
    """
    from .updater import get_dll_version

    with _dll_paths_lock:
        paths = dict(LATEST_DLL_PATHS)

    for dll_name, dll_path in paths.items():
        if dll_path and Path(dll_path).exists():
            version = get_dll_version(dll_path)
            if version:
                with _dll_paths_lock:
                    LATEST_DLL_VERSIONS[dll_name] = version


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
        # Thread-safe singleton for free-threaded Python 3.14+
        # Always acquire lock first - outer check is NOT safe without GIL
        with _config_lock:
            if cls._instance is None:
                cls._instance = super(ConfigManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # Thread-safe initialization for free-threaded Python 3.14+
        # Lock ensures hasattr check and initialization are atomic
        with _config_lock:
            if hasattr(self, "initialized") and self.initialized:
                return

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
                self._save_unlocked()

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
                self._save_unlocked()
            else:
                # Add Streamline preference if it doesn't exist (for existing configs)
                if "UpdateStreamline" not in self["UpdatePreferences"]:
                    self["UpdatePreferences"]["UpdateStreamline"] = "false"
                    self._save_unlocked()
                # Add CreateBackups preference if it doesn't exist (for existing configs)
                if "CreateBackups" not in self["UpdatePreferences"]:
                    self["UpdatePreferences"]["CreateBackups"] = "true"
                    self._save_unlocked()
                # Add HighPerformanceMode preference if it doesn't exist (for existing configs)
                if "HighPerformanceMode" not in self["UpdatePreferences"]:
                    self["UpdatePreferences"]["HighPerformanceMode"] = "false"
                    self._save_unlocked()

            # Initialize DiscordBanner section
            if not self.has_section("DiscordBanner"):
                self.add_section("DiscordBanner")
                # Fresh install: show banner to new users (dismissed=false)
                # Upgrade: don't re-show banner (dismissed=true)
                self["DiscordBanner"]["dismissed"] = "false" if is_fresh_install else "true"
                self._save_unlocked()

            # Initialize ImageCache section (for migration tracking)
            if not self.has_section("ImageCache"):
                self.add_section("ImageCache")
                self["ImageCache"]["Version"] = "0"  # Pre-WebP thumbnails
                self._save_unlocked()

            # Initialize UIPreferences section with defaults if missing
            if not self.has_section("UIPreferences"):
                self.add_section("UIPreferences")
                self["UIPreferences"]["SmoothScrolling"] = "true"  # Enabled by default
                self._save_unlocked()

            # Initialize LinuxDLSSPresets section with defaults if missing
            if not self.has_section("LinuxDLSSPresets"):
                self.add_section("LinuxDLSSPresets")
                self["LinuxDLSSPresets"]["SelectedPreset"] = "default"
                self["LinuxDLSSPresets"]["OverlayEnabled"] = "false"
                self["LinuxDLSSPresets"]["WaylandEnabled"] = "false"
                self["LinuxDLSSPresets"]["HDREnabled"] = "false"
                self._save_unlocked()

            # Initialize SteamAPI section for Steam Web API authentication
            if not self.has_section("SteamAPI"):
                self.add_section("SteamAPI")
                self["SteamAPI"]["ApiKey"] = ""
                self["SteamAPI"]["SteamId"] = ""
                self["SteamAPI"]["AutoDetectedSteamId"] = ""
                self._save_unlocked()

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
                # Explicit type to ensure list[str] is returned (Issue #126 fix)
                paths = msgspec.json.decode(raw_value.encode(), type=list[str])
                return [p for p in paths if p]  # Filter empty strings
            except msgspec.DecodeError as e:
                self.logger.warning(f"Failed to parse JSON paths for {launcher}: {e}")
            except Exception as e:
                self.logger.warning(f"Unexpected error parsing paths for {launcher}: {e}")

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

    def get_grid_density(self) -> str:
        """Get grid density preference (default: comfortable)

        Returns:
            One of 'compact', 'comfortable', or 'large'
        """
        with _config_lock:
            if not self.has_section("UIPreferences"):
                return "comfortable"
            value = self["UIPreferences"].get("GridDensity", "comfortable")
            if value not in ("compact", "comfortable", "large"):
                return "comfortable"
            return value

    def set_grid_density(self, density: str):
        """Set grid density preference and persist to config file

        Args:
            density: One of 'compact', 'comfortable', or 'large'
        """
        if density not in ("compact", "comfortable", "large"):
            density = "comfortable"
        with _config_lock:
            if not self.has_section("UIPreferences"):
                self.add_section("UIPreferences")
            self["UIPreferences"]["GridDensity"] = density
            self._save_unlocked()

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

    def get_sort_preference(self) -> str:
        """Get game sort preference (default: 'name_asc')"""
        with _config_lock:
            if not self.has_section("UIPreferences"):
                return "name_asc"
            return self["UIPreferences"].get("SortPreference", "name_asc")

    def set_sort_preference(self, sort: str):
        """Set game sort preference"""
        valid = ("name_asc", "name_desc", "dll_count", "outdated_first")
        if sort not in valid:
            return
        with _config_lock:
            if not self.has_section("UIPreferences"):
                self.add_section("UIPreferences")
            self["UIPreferences"]["SortPreference"] = sort
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

    def _save_unlocked(self):
        """
        Save configuration to disk (internal, no lock).

        Used during initialization when lock is already held.
        External callers should use save() instead.
        """
        with open(self.config_path, "w") as configfile:
            self.write(configfile)

    def save(self):
        """
        Save configuration to disk (thread-safe).

        Thread-safe for free-threaded Python 3.14+: Uses _config_lock
        to prevent concurrent writes from corrupting the config file.
        """
        with _config_lock:
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

    # =========================================================================
    # Linux DLSS SR Presets Configuration
    # =========================================================================

    def get_linux_dlss_config(self) -> LinuxDLSSConfig:
        """
        Get Linux DLSS preset configuration.

        Returns:
            LinuxDLSSConfig with current settings
        """
        with _config_lock:
            if not self.has_section("LinuxDLSSPresets"):
                return LinuxDLSSConfig()

            section = self["LinuxDLSSPresets"]
            return LinuxDLSSConfig(
                selected_preset=section.get("SelectedPreset", "default"),
                overlay_enabled=section.getboolean("OverlayEnabled", False),
                wayland_enabled=section.getboolean("WaylandEnabled", False),
                hdr_enabled=section.getboolean("HDREnabled", False),
            )

    def save_linux_dlss_config(self, config: LinuxDLSSConfig) -> None:
        """
        Save Linux DLSS preset configuration.

        Args:
            config: LinuxDLSSConfig to save
        """
        with _config_lock:
            if not self.has_section("LinuxDLSSPresets"):
                self.add_section("LinuxDLSSPresets")

            self["LinuxDLSSPresets"]["SelectedPreset"] = config.selected_preset
            self["LinuxDLSSPresets"]["OverlayEnabled"] = str(config.overlay_enabled).lower()
            self["LinuxDLSSPresets"]["WaylandEnabled"] = str(config.wayland_enabled).lower()
            self["LinuxDLSSPresets"]["HDREnabled"] = str(config.hdr_enabled).lower()
            self._save_unlocked()

    # =========================================================================
    # Steam Web API Configuration
    # =========================================================================

    def get_steam_api_key(self) -> str:
        """Get the user's Steam Web API key."""
        with _config_lock:
            if not self.has_section("SteamAPI"):
                return ""
            return self["SteamAPI"].get("ApiKey", "")

    def set_steam_api_key(self, key: str):
        """Store the user's Steam Web API key."""
        with _config_lock:
            if not self.has_section("SteamAPI"):
                self.add_section("SteamAPI")
            self["SteamAPI"]["ApiKey"] = key
            self._save_unlocked()

    def get_steam_id(self) -> str:
        """Get the Steam 64-bit ID. Prefers user-set ID, falls back to auto-detected."""
        with _config_lock:
            if not self.has_section("SteamAPI"):
                return ""
            user_id = self["SteamAPI"].get("SteamId", "")
            return user_id or self["SteamAPI"].get("AutoDetectedSteamId", "")

    def set_steam_id(self, steam_id: str):
        """Set the Steam 64-bit ID explicitly."""
        with _config_lock:
            if not self.has_section("SteamAPI"):
                self.add_section("SteamAPI")
            self["SteamAPI"]["SteamId"] = steam_id
            self._save_unlocked()

    def set_auto_detected_steam_id(self, steam_id: str):
        """Store an auto-detected Steam 64-bit ID (from loginusers.vdf)."""
        with _config_lock:
            if not self.has_section("SteamAPI"):
                self.add_section("SteamAPI")
            self["SteamAPI"]["AutoDetectedSteamId"] = steam_id
            self._save_unlocked()

    def has_steam_api_credentials(self) -> bool:
        """Check if both API key and Steam ID are configured."""
        return bool(self.get_steam_api_key()) and bool(self.get_steam_id())

    def clear_steam_api_credentials(self):
        """Remove all Steam API credentials."""
        with _config_lock:
            if self.has_section("SteamAPI"):
                self["SteamAPI"]["ApiKey"] = ""
                self["SteamAPI"]["SteamId"] = ""
                self["SteamAPI"]["AutoDetectedSteamId"] = ""
                self._save_unlocked()

    # =========================================================================
    # Window State Persistence
    # =========================================================================

    # Default window dimensions (used on first launch or if saved state is invalid)
    _WINDOW_DEFAULTS = {
        "Width": "900",
        "Height": "700",
        "Top": "",
        "Left": "",
        "Maximized": "false",
    }

    def get_window_state(self) -> dict[str, float | bool | None]:
        """Get saved window state (position, size, maximized).

        Returns:
            Dict with keys: width, height, top, left, maximized.
            top/left may be None if never saved (let OS position the window).
        """
        with _config_lock:
            if not self.has_section("WindowState"):
                return {
                    "width": 900.0,
                    "height": 700.0,
                    "top": None,
                    "left": None,
                    "maximized": False,
                }

            section = self["WindowState"]

            def _float_or_none(key: str) -> float | None:
                val = section.get(key, "")
                if not val:
                    return None
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None

            return {
                "width": _float_or_none("Width") or 900.0,
                "height": _float_or_none("Height") or 700.0,
                "top": _float_or_none("Top"),
                "left": _float_or_none("Left"),
                "maximized": section.getboolean("Maximized", False),
            }

    def save_window_state(
        self,
        width: float,
        height: float,
        top: float | None,
        left: float | None,
        maximized: bool,
    ) -> None:
        """Save window state to config (thread-safe)."""
        with _config_lock:
            if not self.has_section("WindowState"):
                self.add_section("WindowState")

            self["WindowState"]["Width"] = str(int(width))
            self["WindowState"]["Height"] = str(int(height))
            self["WindowState"]["Top"] = str(int(top)) if top is not None else ""
            self["WindowState"]["Left"] = str(int(left)) if left is not None else ""
            self["WindowState"]["Maximized"] = str(maximized).lower()
            self._save_unlocked()


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


def get_dll_path(dll_name: str) -> str | None:
    """
    Get DLL path thread-safely.

    Thread-safe for free-threaded Python 3.14+: Uses _dll_paths_lock
    to prevent reading while another thread is writing.

    Args:
        dll_name: Name of the DLL (e.g., "nvngx_dlss.dll")

    Returns:
        Path to the DLL if found, None otherwise
    """
    with _dll_paths_lock:
        return LATEST_DLL_PATHS.get(dll_name)


def get_all_dll_paths() -> dict[str, str]:
    """
    Get all DLL paths thread-safely (returns a copy).

    Thread-safe for free-threaded Python 3.14+: Returns a shallow copy
    of the dict to prevent external code from holding references to
    internal state that could be modified by another thread.

    Returns:
        Copy of the LATEST_DLL_PATHS dictionary
    """
    with _dll_paths_lock:
        return dict(LATEST_DLL_PATHS)


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
        "libxess_fg.dll": get_local_dll_path("libxess_fg.dll"),
        "libxell.dll": get_local_dll_path("libxell.dll"),
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
