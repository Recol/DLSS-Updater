import os
import sys
import threading
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

import msgspec
import msgspec.toml

from .logger import setup_logger
from .models import (
    MAX_PATHS_PER_LAUNCHER,
    DiscordBannerConfig,
    ImageCacheConfig,
    LauncherPathsConfig,
    LinuxDLSSConfig,
    PerformanceConfig,
    SteamAPIConfig,
    UIPreferencesConfig,
    UpdatePreferencesConfig,
    WindowStateConfig,
    WindowsDLSSConfig,
)

logger = setup_logger()


def normalize_user_path(path: str) -> str:
    """
    Expand ``~`` and environment variables in a user-supplied path.

    Users often enter paths like ``~/.steam`` or ``$HOME/Games``; the raw
    string would never match a real directory because neither ``~`` nor
    ``$VAR`` are resolved by :class:`pathlib.Path`. This normalizes such
    paths before they are validated or stored (Issue #228).

    Args:
        path: A user-entered path that may contain ``~`` or env vars.

    Returns:
        The expanded path, or the original value if empty/None.
    """
    if not path:
        return path
    return os.path.expanduser(os.path.expandvars(path))


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
    #
    # CPU cap: PE parsing is genuinely parallel on the free-threaded build
    # (no GIL to serialise it), so lift the cap to 64 there to exploit more
    # cores; keep 32 on GIL builds where extra CPU threads mostly contend on
    # the GIL and only add ~1MB-stack-per-thread memory pressure.
    _THREADPOOL_CPU_CAP: int = 64 if GIL_DISABLED else 32
    THREADPOOL_CPU: int = max(8, min(_THREADPOOL_CPU_CAP, int(CPU_THREADS * 0.9)))  # Cap for CPU work
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
#
# _config_lock is a *reentrant* lock: several public setters mutate the config
# and then call save() while still holding the lock. With a plain Lock that
# re-acquisition would self-deadlock; RLock keeps cross-thread mutual exclusion
# (the property those setters rely on) while allowing same-thread re-entry.
_config_lock = threading.RLock()
_dll_paths_lock = threading.Lock()


def get_config_path():
    """Get the path for storing configuration files using centralized config dir.

    Returns the path to the active config file (``config.toml``). The legacy
    ``config.ini`` (if present) is only read once during migration; see
    :meth:`ConfigManager._load_config`.
    """
    from dlss_updater.platform_utils import APP_CONFIG_DIR
    return str(APP_CONFIG_DIR / "config.toml")


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


# Maps the public ``get/set_update_preference`` technology tokens to the
# corresponding UpdatePreferencesConfig field. Kept here so both the getter and
# setter agree on the mapping.
_UPDATE_PREF_FIELDS = {
    "DLSS": "update_dlss",
    "DirectStorage": "update_direct_storage",
    "XeSS": "update_xess",
    "FSR": "update_fsr",
    "Streamline": "update_streamline",
}


def _default_launcher_paths() -> dict[str, str]:
    """All known launcher slots, empty by default (mirrors the old INI init)."""
    return {p.value: "" for p in LauncherPathName}


def _default_update_preferences() -> UpdatePreferencesConfig:
    """Fresh-install update prefs. Streamline defaults OFF to match the legacy INI."""
    return UpdatePreferencesConfig(update_streamline=False)


def _default_performance() -> PerformanceConfig:
    """Historical default was 16 worker threads (INI wrote "16" on first run)."""
    return PerformanceConfig(max_worker_threads=16)


class AppConfig(msgspec.Struct):
    """
    Root persistence schema for the application, serialized to ``config.toml``
    via ``msgspec.toml``.

    Every field is a TOML-native container/scalar (struct, dict, bool, int, str)
    so encoding is total and lossless - crucially there are **no ``None`` values**
    anywhere in the tree, because TOML has no null type. Optionality is modelled
    with empty strings (``SteamAPIConfig``), a presence flag
    (``WindowStateConfig.has_position``) or simple absence from a dict.

    Sub-structs are reused from ``dlss_updater.models`` where they already
    existed (update prefs, launcher-path helper struct, Linux/Windows DLSS
    presets, performance) and added there for the remaining sections.

    ``launcher_paths`` values are raw strings that may hold either a single path
    or a JSON array (multi-path support), matching the historical INI encoding.
    ``appearance``/``extra`` back the generic configparser-style accessors
    (``get``/``set``/``has_section``/``add_section``) still used by the theme
    manager.
    """
    launcher_paths: dict[str, str] = msgspec.field(default_factory=_default_launcher_paths)
    update_preferences: UpdatePreferencesConfig = msgspec.field(default_factory=_default_update_preferences)
    ui_preferences: UIPreferencesConfig = msgspec.field(default_factory=UIPreferencesConfig)
    discord_banner: DiscordBannerConfig = msgspec.field(default_factory=DiscordBannerConfig)
    image_cache: ImageCacheConfig = msgspec.field(default_factory=ImageCacheConfig)
    linux_dlss: LinuxDLSSConfig = msgspec.field(default_factory=LinuxDLSSConfig)
    windows_dlss: WindowsDLSSConfig = msgspec.field(default_factory=WindowsDLSSConfig)
    steam_api: SteamAPIConfig = msgspec.field(default_factory=SteamAPIConfig)
    performance: PerformanceConfig = msgspec.field(default_factory=_default_performance)
    blacklist_skips: dict[str, bool] = msgspec.field(default_factory=dict)
    window_state: WindowStateConfig = msgspec.field(default_factory=WindowStateConfig)
    appearance: dict[str, str] = msgspec.field(default_factory=dict)
    extra: dict[str, dict[str, str]] = msgspec.field(default_factory=dict)


class ConfigManager:
    """
    Thread-safe application configuration backed by a msgspec ``AppConfig`` and
    persisted to ``config.toml`` (``msgspec.toml``).

    The public API is unchanged from the previous ``configparser``-based
    implementation: every ``get_*``/``set_*`` accessor, the ``*_struct`` helpers,
    ``save()`` and the small configparser-compatible surface
    (``get``/``set``/``has_section``/``add_section``) behave identically. Only the
    on-disk format and internal storage changed.
    """

    _instance = None

    # External INI-style section name -> AppConfig attribute holding a dict[str,str].
    # Only "Appearance" is accessed generically today; anything else falls through
    # to the `extra` catch-all so unknown sections still behave like configparser.
    _GENERIC_DICT_SECTIONS = {"Appearance": "appearance"}

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
            if getattr(self, "initialized", False):
                return

            self.logger = setup_logger()
            self.config_path = get_config_path()
            self._config: AppConfig = self._load_config()
            # Deferred-save batching state (guarded by _config_lock). When
            # _save_defer_depth > 0, _save_unlocked() records the intent to
            # save instead of rewriting config.toml; the outermost
            # deferred_save() context flushes a single write on exit. See
            # deferred_save() for the batching contract.
            self._save_defer_depth = 0
            self._save_deferred_pending = False
            self.initialized = True

    # =========================================================================
    # Loading / migration / persistence
    # =========================================================================

    def _load_config(self) -> AppConfig:
        """
        Load ``config.toml`` if present, otherwise perform the one-time
        ``config.ini`` -> ``config.toml`` migration, otherwise create a fresh
        config. Always leaves a valid ``config.toml`` on disk afterwards.
        """
        toml_path = Path(self.config_path)
        legacy_ini = toml_path.with_name("config.ini")

        # 1) Primary path: an existing TOML config.
        if toml_path.exists():
            try:
                return msgspec.toml.decode(toml_path.read_bytes(), type=AppConfig)
            except Exception as e:
                # Corrupt/unreadable TOML: rebuild from defaults rather than crash.
                self.logger.error(
                    f"Failed to parse {toml_path.name}; recreating from defaults: {e}"
                )
                cfg = AppConfig()
                self._write_config(cfg)
                return cfg

        # 2) One-time, non-destructive migration from the legacy INI file.
        if legacy_ini.exists():
            self.logger.info(
                f"Legacy {legacy_ini.name} found and no {toml_path.name}; "
                f"running one-time migration to TOML."
            )
            try:
                cfg = self._migrate_from_ini(legacy_ini)
                self._write_config(cfg)
                self.logger.info(
                    f"Config migration complete: wrote {toml_path.name}; "
                    f"original {legacy_ini.name} left untouched."
                )
                return cfg
            except Exception as e:
                self.logger.error(
                    f"{legacy_ini.name} -> {toml_path.name} migration failed ({e}); "
                    f"starting from defaults. Original {legacy_ini.name} left untouched."
                )
                cfg = AppConfig()
                self._write_config(cfg)
                return cfg

        # 3) Fresh install (no TOML, no INI). New users should see the Discord
        #    banner, which is the AppConfig default (dismissed=False).
        self.logger.info(f"No existing configuration found; creating fresh {toml_path.name}.")
        cfg = AppConfig()
        self._write_config(cfg)
        return cfg

    def _migrate_from_ini(self, ini_path: Path) -> AppConfig:
        """
        Parse the legacy ``config.ini`` one last time and convert it into an
        :class:`AppConfig`. This is the *only* place ``configparser`` is used, and
        it is scoped to this function. The original INI file is never modified.
        """
        import configparser  # migration-only; intentionally not a module import

        parser = configparser.ConfigParser()
        parser.read(str(ini_path))

        cfg = AppConfig()
        known: set[str] = set()

        # -- LauncherPaths (read via canonical names; configparser is case-insensitive)
        if parser.has_section("LauncherPaths"):
            known.add("LauncherPaths")
            for slot in LauncherPathName:
                cfg.launcher_paths[slot.value] = parser.get(
                    "LauncherPaths", slot.value, fallback=""
                )

        # -- UpdatePreferences
        if parser.has_section("UpdatePreferences"):
            known.add("UpdatePreferences")
            up = cfg.update_preferences
            up.update_dlss = parser.getboolean("UpdatePreferences", "UpdateDLSS", fallback=True)
            up.update_direct_storage = parser.getboolean("UpdatePreferences", "UpdateDirectStorage", fallback=True)
            up.update_xess = parser.getboolean("UpdatePreferences", "UpdateXeSS", fallback=True)
            up.update_fsr = parser.getboolean("UpdatePreferences", "UpdateFSR", fallback=True)
            up.update_streamline = parser.getboolean("UpdatePreferences", "UpdateStreamline", fallback=False)
            up.create_backups = parser.getboolean("UpdatePreferences", "CreateBackups", fallback=True)
            up.high_performance_mode = parser.getboolean("UpdatePreferences", "HighPerformanceMode", fallback=False)

        # -- DiscordBanner: preserve dismissed; an existing user upgrading should
        #    NOT be re-shown the banner, so absence -> dismissed=True.
        if parser.has_section("DiscordBanner"):
            known.add("DiscordBanner")
            cfg.discord_banner.dismissed = parser.getboolean("DiscordBanner", "dismissed", fallback=True)
        else:
            cfg.discord_banner.dismissed = True

        # -- ImageCache
        if parser.has_section("ImageCache"):
            known.add("ImageCache")
            try:
                cfg.image_cache.version = int(parser.get("ImageCache", "Version", fallback="0"))
            except (ValueError, TypeError):
                cfg.image_cache.version = 0

        # -- UIPreferences
        if parser.has_section("UIPreferences"):
            known.add("UIPreferences")
            ui = cfg.ui_preferences
            ui.smooth_scrolling = parser.getboolean("UIPreferences", "SmoothScrolling", fallback=True)
            ui.grid_density = parser.get("UIPreferences", "GridDensity", fallback="comfortable")
            ui.keep_games_in_memory = parser.getboolean("UIPreferences", "KeepGamesInMemory", fallback=True)
            ui.sort_preference = parser.get("UIPreferences", "SortPreference", fallback="name_asc")

        # -- LinuxDLSSPresets (frozen struct -> rebuild)
        if parser.has_section("LinuxDLSSPresets"):
            known.add("LinuxDLSSPresets")
            cfg.linux_dlss = LinuxDLSSConfig(
                selected_preset=parser.get("LinuxDLSSPresets", "SelectedPreset", fallback="default"),
                overlay_enabled=parser.getboolean("LinuxDLSSPresets", "OverlayEnabled", fallback=False),
                wayland_enabled=parser.getboolean("LinuxDLSSPresets", "WaylandEnabled", fallback=False),
                hdr_enabled=parser.getboolean("LinuxDLSSPresets", "HDREnabled", fallback=False),
            )

        # -- WindowsDLSSPresets (frozen struct -> rebuild)
        if parser.has_section("WindowsDLSSPresets"):
            known.add("WindowsDLSSPresets")
            cfg.windows_dlss = WindowsDLSSConfig(
                selected_preset=parser.get("WindowsDLSSPresets", "SelectedPreset", fallback="default"),
                rr_preset=parser.get("WindowsDLSSPresets", "RRPreset", fallback="default"),
                fg_preset=parser.get("WindowsDLSSPresets", "FGPreset", fallback="default"),
            )

        # -- SteamAPI
        if parser.has_section("SteamAPI"):
            known.add("SteamAPI")
            sa = cfg.steam_api
            sa.api_key = parser.get("SteamAPI", "ApiKey", fallback="")
            sa.steam_id = parser.get("SteamAPI", "SteamId", fallback="")
            sa.auto_detected_steam_id = parser.get("SteamAPI", "AutoDetectedSteamId", fallback="")

        # -- Performance
        if parser.has_section("Performance"):
            known.add("Performance")
            try:
                cfg.performance.max_worker_threads = int(
                    parser.get("Performance", "MaxWorkerThreads", fallback="16")
                )
            except (ValueError, TypeError):
                cfg.performance.max_worker_threads = 16

        # -- BlacklistSkips (configparser already lower-cased the game keys)
        if parser.has_section("BlacklistSkips"):
            known.add("BlacklistSkips")
            for game in parser["BlacklistSkips"]:
                cfg.blacklist_skips[game] = parser.getboolean("BlacklistSkips", game, fallback=False)

        # -- WindowState
        if parser.has_section("WindowState"):
            known.add("WindowState")
            ws = cfg.window_state

            def _int_or_none(key: str) -> int | None:
                raw = parser.get("WindowState", key, fallback="")
                if not raw:
                    return None
                try:
                    return int(float(raw))
                except (ValueError, TypeError):
                    return None

            width = _int_or_none("Width")
            height = _int_or_none("Height")
            top = _int_or_none("Top")
            left = _int_or_none("Left")
            ws.width = width if width is not None else 900
            ws.height = height if height is not None else 700
            ws.has_position = top is not None and left is not None
            ws.top = top if top is not None else 0
            ws.left = left if left is not None else 0
            ws.maximized = parser.getboolean("WindowState", "Maximized", fallback=False)

        # -- Appearance (generic section: theme, user_override)
        if parser.has_section("Appearance"):
            known.add("Appearance")
            for key in parser["Appearance"]:
                cfg.appearance[key] = parser.get("Appearance", key)

        # -- Any other/unrecognised sections: preserve verbatim under `extra`.
        for section in parser.sections():
            if section in known:
                continue
            cfg.extra[section] = {k: v for k, v in parser[section].items()}

        return cfg

    def _write_config(self, cfg: AppConfig) -> None:
        """
        Encode ``cfg`` to TOML and write it to ``self.config_path``.

        The write goes through a temp file + ``os.replace`` so a crash mid-write
        can't leave a half-written (corrupt) config. Callers must hold
        ``_config_lock`` (or be in single-threaded ``__init__``).
        """
        data = msgspec.toml.encode(cfg)
        target = Path(self.config_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    def _save_unlocked(self):
        """
        Persist the current config to disk (internal, assumes ``_config_lock``
        is already held). External callers should use :meth:`save`.

        When a :meth:`deferred_save` batch is active (``_save_defer_depth`` > 0)
        the write is suppressed and only the intent is recorded; the outermost
        ``deferred_save`` context performs a single write on exit. This collapses
        the write amplification of setters that each call ``_save_unlocked`` (e.g.
        ``add_launcher_path`` invoked once per auto-detected launcher path).
        """
        if self._save_defer_depth > 0:
            self._save_deferred_pending = True
            return
        self._write_config(self._config)

    @contextmanager
    def deferred_save(self):
        """
        Batch config writes: suppress per-setter ``_save_unlocked`` writes for
        the duration of the ``with`` block, then perform a single write on exit
        if anything requested a save.

        Re-entrant and thread-safe for free-threaded Python 3.14+: the defer
        depth and pending flag are guarded by the reentrant ``_config_lock``.
        Nested ``deferred_save`` blocks share one depth counter, so only the
        outermost exit flushes. The lock is *not* held across the ``with`` body
        (only around the brief depth adjust/flush), so ``await`` points inside
        the block — as in the scanner's auto-detection path — never serialise
        other threads.

        Note: an explicit :meth:`save` call still writes immediately; only the
        internal ``_save_unlocked`` path is deferred.
        """
        with _config_lock:
            self._save_defer_depth += 1
        try:
            yield
        finally:
            with _config_lock:
                self._save_defer_depth -= 1
                if self._save_defer_depth == 0 and self._save_deferred_pending:
                    self._save_deferred_pending = False
                    self._write_config(self._config)

    def save(self):
        """
        Save configuration to disk (thread-safe).

        Thread-safe for free-threaded Python 3.14+: uses ``_config_lock`` to
        prevent concurrent writes from corrupting the config file.
        """
        with _config_lock:
            self._write_config(self._config)

    # =========================================================================
    # configparser-compatible generic accessors (used by the theme manager)
    # =========================================================================

    def _generic_section(self, section: str, create: bool) -> dict[str, str] | None:
        """Return the dict backing a generically-accessed section, or None."""
        attr = self._GENERIC_DICT_SECTIONS.get(section)
        if attr is not None:
            return getattr(self._config, attr)
        if section in self._config.extra:
            return self._config.extra[section]
        if create:
            new: dict[str, str] = {}
            self._config.extra[section] = new
            return new
        return None

    @staticmethod
    def _optionxform(option: str) -> str:
        """Mirror configparser's default lower-casing of option keys."""
        return option.lower()

    def has_section(self, section: str) -> bool:
        """configparser-compatible: does the section exist?"""
        if section in self._GENERIC_DICT_SECTIONS:
            return True  # backing dict field always exists
        with _config_lock:
            return section in self._config.extra

    def add_section(self, section: str) -> None:
        """configparser-compatible: ensure a generic section exists."""
        if section in self._GENERIC_DICT_SECTIONS:
            return
        with _config_lock:
            self._config.extra.setdefault(section, {})

    def get(self, section, option, fallback=None):
        """configparser-compatible ``get(section, option, fallback=...)``."""
        with _config_lock:
            data = self._generic_section(section, create=False)
            if data is None:
                return fallback
            return data.get(self._optionxform(option), fallback)

    def set(self, section, option, value):
        """
        configparser-compatible ``set(section, option, value)``.

        Like configparser, this only mutates in memory; callers persist with a
        subsequent :meth:`save`.
        """
        with _config_lock:
            data = self._generic_section(section, create=True)
            data[self._optionxform(option)] = value

    # =========================================================================
    # Launcher paths
    # =========================================================================

    def update_launcher_path(
        self, path_to_update: LauncherPathName, new_launcher_path: str
    ):
        self.logger.debug(f"Attempting to update path for {path_to_update}.")
        with _config_lock:
            self._config.launcher_paths[str(path_to_update)] = new_launcher_path
            self._save_unlocked()
        self.logger.debug(f"Updated path for {path_to_update}.")

    def check_path_value(self, path_to_check: LauncherPathName) -> str:
        with _config_lock:
            return self._config.launcher_paths.get(str(path_to_check), "")

    def reset_launcher_path(self, path_to_reset: LauncherPathName):
        self.logger.debug(f"Resetting path for {path_to_reset}.")
        with _config_lock:
            self._config.launcher_paths[str(path_to_reset)] = ""
            self._save_unlocked()
        self.logger.debug(f"Reset path for {path_to_reset}.")

    # -------------------------------------------------------------------------
    # Multi-Path Methods (Sub-Folder Support)
    # -------------------------------------------------------------------------

    def get_launcher_paths(self, launcher: LauncherPathName) -> list[str]:
        """
        Get all paths for a launcher (multi-path support).

        Handles both legacy single-path format and new JSON array format.

        Args:
            launcher: The launcher to get paths for

        Returns:
            List of paths, or empty list if none configured
        """
        with _config_lock:
            raw_value = self._config.launcher_paths.get(str(launcher), "")
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
        # Expand ~ and environment variables, filter empty paths, enforce limit (Issue #228)
        filtered_paths = [normalize_user_path(p) for p in paths if p][:MAX_PATHS_PER_LAUNCHER]

        # Encode as JSON array
        json_value = msgspec.json.encode(filtered_paths).decode()
        with _config_lock:
            self._config.launcher_paths[str(launcher)] = json_value
            self._save_unlocked()
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

        # Expand ~ and environment variables before validation/storage (Issue #228)
        new_path = normalize_user_path(new_path)

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

    # =========================================================================
    # Update preferences
    # =========================================================================

    def get_update_preference(self, technology):
        """Get update preference for a specific technology"""
        field_name = _UPDATE_PREF_FIELDS.get(technology)
        if field_name is None:
            return True  # Unknown technology -> default enabled (matches old fallback)
        with _config_lock:
            return getattr(self._config.update_preferences, field_name)

    def set_update_preference(self, technology, enabled):
        """Set update preference for a specific technology"""
        field_name = _UPDATE_PREF_FIELDS.get(technology)
        if field_name is None:
            return
        with _config_lock:
            setattr(self._config.update_preferences, field_name, bool(enabled))
            self._save_unlocked()

    def get_backup_preference(self):
        """Get backup creation preference"""
        with _config_lock:
            return self._config.update_preferences.create_backups

    def set_backup_preference(self, enabled):
        """Set backup creation preference"""
        with _config_lock:
            self._config.update_preferences.create_backups = bool(enabled)
            self._save_unlocked()

    # =========================================================================
    # Discord banner
    # =========================================================================

    def get_discord_banner_dismissed(self) -> bool:
        """Get whether the Discord invite banner has been dismissed"""
        with _config_lock:
            return self._config.discord_banner.dismissed

    def set_discord_banner_dismissed(self, dismissed: bool):
        """Set the Discord invite banner dismissed state"""
        with _config_lock:
            self._config.discord_banner.dismissed = bool(dismissed)
            self._save_unlocked()

    # =========================================================================
    # Image cache
    # =========================================================================

    def get_image_cache_version(self) -> int:
        """Get the image cache version for migration tracking."""
        with _config_lock:
            return self._config.image_cache.version

    def set_image_cache_version(self, version: int):
        """Set the image cache version after migration."""
        with _config_lock:
            self._config.image_cache.version = int(version)
            self._save_unlocked()

    # =========================================================================
    # UI preferences
    # =========================================================================

    def get_smooth_scrolling_enabled(self) -> bool:
        """Get smooth scrolling preference (default: enabled)"""
        with _config_lock:
            return self._config.ui_preferences.smooth_scrolling

    def set_smooth_scrolling_enabled(self, enabled: bool):
        """Set smooth scrolling preference and persist to config file"""
        with _config_lock:
            self._config.ui_preferences.smooth_scrolling = bool(enabled)
            self._save_unlocked()

    def get_grid_density(self) -> str:
        """Get grid density preference (default: comfortable)

        Returns:
            One of 'compact', 'comfortable', or 'large'
        """
        with _config_lock:
            value = self._config.ui_preferences.grid_density
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
            self._config.ui_preferences.grid_density = density
            self._save_unlocked()

    def get_keep_games_in_memory(self) -> bool:
        """Get keep games in memory preference (default: enabled)"""
        with _config_lock:
            return self._config.ui_preferences.keep_games_in_memory

    def set_keep_games_in_memory(self, enabled: bool):
        """Set keep games in memory preference and persist to config file"""
        with _config_lock:
            self._config.ui_preferences.keep_games_in_memory = bool(enabled)
            self._save_unlocked()

    def get_sort_preference(self) -> str:
        """Get game sort preference (default: 'name_asc')"""
        with _config_lock:
            return self._config.ui_preferences.sort_preference

    def set_sort_preference(self, sort: str):
        """Set game sort preference"""
        valid = ("name_asc", "name_desc", "dll_count", "outdated_first")
        if sort not in valid:
            return
        with _config_lock:
            self._config.ui_preferences.sort_preference = sort
            self._save_unlocked()

    # =========================================================================
    # Blacklist skips
    # =========================================================================
    # Keys are lower-cased to preserve the case-insensitive behaviour the old
    # configparser backend had (option keys were normalised via optionxform).

    def get_all_blacklist_skips(self):
        """Get all games to skip in the blacklist"""
        with _config_lock:
            return [game for game, value in self._config.blacklist_skips.items() if value]

    def add_blacklist_skip(self, game_name):
        """Add a game to skip in the blacklist"""
        with _config_lock:
            self._config.blacklist_skips[self._optionxform(game_name)] = True
            self._save_unlocked()

    def clear_all_blacklist_skips(self):
        """Clear all blacklist skips"""
        with _config_lock:
            if self._config.blacklist_skips:
                self._config.blacklist_skips.clear()
                self._save_unlocked()

    def is_blacklist_skipped(self, game_name):
        """Check if a game is in the blacklist skip list"""
        with _config_lock:
            return self._config.blacklist_skips.get(self._optionxform(game_name), False)

    # =========================================================================
    # Struct-based accessors
    # =========================================================================

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
        """Save update preferences from msgspec struct"""
        with _config_lock:
            up = self._config.update_preferences
            up.update_dlss = prefs.update_dlss
            up.update_direct_storage = prefs.update_direct_storage
            up.update_xess = prefs.update_xess
            up.update_fsr = prefs.update_fsr
            up.update_streamline = prefs.update_streamline
            up.create_backups = prefs.create_backups
            up.high_performance_mode = prefs.high_performance_mode
            self._save_unlocked()

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
        """Save launcher paths from msgspec struct"""
        with _config_lock:
            lp = self._config.launcher_paths
            lp[str(LauncherPathName.STEAM)] = paths.steam_path or ""
            lp[str(LauncherPathName.EA)] = paths.ea_path or ""
            lp[str(LauncherPathName.EPIC)] = paths.epic_path or ""
            lp[str(LauncherPathName.GOG)] = paths.gog_path or ""
            lp[str(LauncherPathName.UBISOFT)] = paths.ubisoft_path or ""
            lp[str(LauncherPathName.BATTLENET)] = paths.battle_net_path or ""
            lp[str(LauncherPathName.XBOX)] = paths.xbox_path or ""
            lp[str(LauncherPathName.CUSTOM1)] = paths.custom_path_1 or ""
            lp[str(LauncherPathName.CUSTOM2)] = paths.custom_path_2 or ""
            lp[str(LauncherPathName.CUSTOM3)] = paths.custom_path_3 or ""
            lp[str(LauncherPathName.CUSTOM4)] = paths.custom_path_4 or ""
            self._save_unlocked()

    def get_performance_config_struct(self) -> PerformanceConfig:
        """Get performance config as validated msgspec struct"""
        return PerformanceConfig(
            max_worker_threads=self.get_max_worker_threads()
        )

    def save_performance_config_struct(self, perf: PerformanceConfig):
        """Save performance config from msgspec struct"""
        self.set_max_worker_threads(perf.max_worker_threads)

    # =========================================================================
    # Performance
    # =========================================================================

    def get_max_worker_threads(self):
        """Get the maximum number of worker threads for parallel processing"""
        with _config_lock:
            return int(self._config.performance.max_worker_threads)

    def set_max_worker_threads(self, count):
        """Set the maximum number of worker threads"""
        with _config_lock:
            # Direct field assignment intentionally skips PerformanceConfig's
            # range validation to preserve the old setter's permissive behaviour.
            self._config.performance.max_worker_threads = int(count)
            self._save_unlocked()

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
            return self._config.linux_dlss

    def save_linux_dlss_config(self, config: LinuxDLSSConfig) -> None:
        """
        Save Linux DLSS preset configuration.

        Args:
            config: LinuxDLSSConfig to save
        """
        with _config_lock:
            self._config.linux_dlss = config
            self._save_unlocked()

    # =========================================================================
    # Windows DLSS SR Presets Configuration (NvAPI Driver Settings)
    # =========================================================================

    def get_windows_dlss_config(self) -> WindowsDLSSConfig:
        """
        Get Windows DLSS preset configuration (SR/RR/FG).

        Returns:
            WindowsDLSSConfig with the persisted global preset selections.
        """
        with _config_lock:
            return self._config.windows_dlss

    def save_windows_dlss_config(self, config: WindowsDLSSConfig) -> None:
        """
        Save Windows DLSS preset configuration (SR/RR/FG).

        Note: this only persists the selection. Applying it to the driver is
        done separately via dlss_updater.nvapi_drs.apply_presets().

        Args:
            config: WindowsDLSSConfig to save.
        """
        with _config_lock:
            self._config.windows_dlss = config
            self._save_unlocked()

    # =========================================================================
    # Steam Web API Configuration
    # =========================================================================

    def get_steam_api_key(self) -> str:
        """Get the user's Steam Web API key."""
        with _config_lock:
            return self._config.steam_api.api_key

    def set_steam_api_key(self, key: str):
        """Store the user's Steam Web API key."""
        with _config_lock:
            self._config.steam_api.api_key = key
            self._save_unlocked()

    def get_steam_id(self) -> str:
        """Get the Steam 64-bit ID. Prefers user-set ID, falls back to auto-detected."""
        with _config_lock:
            sa = self._config.steam_api
            return sa.steam_id or sa.auto_detected_steam_id

    def set_steam_id(self, steam_id: str):
        """Set the Steam 64-bit ID explicitly."""
        with _config_lock:
            self._config.steam_api.steam_id = steam_id
            self._save_unlocked()

    def set_auto_detected_steam_id(self, steam_id: str):
        """Store an auto-detected Steam 64-bit ID (from loginusers.vdf)."""
        with _config_lock:
            self._config.steam_api.auto_detected_steam_id = steam_id
            self._save_unlocked()

    def has_steam_api_credentials(self) -> bool:
        """Check if both API key and Steam ID are configured."""
        return bool(self.get_steam_api_key()) and bool(self.get_steam_id())

    def clear_steam_api_credentials(self):
        """Remove all Steam API credentials."""
        with _config_lock:
            sa = self._config.steam_api
            sa.api_key = ""
            sa.steam_id = ""
            sa.auto_detected_steam_id = ""
            self._save_unlocked()

    # =========================================================================
    # Window State Persistence
    # =========================================================================

    def get_window_state(self) -> dict[str, float | bool | None]:
        """Get saved window state (position, size, maximized).

        Returns:
            Dict with keys: width, height, top, left, maximized.
            top/left may be None if never saved (let OS position the window).
        """
        with _config_lock:
            ws = self._config.window_state
            width = float(ws.width) or 900.0
            height = float(ws.height) or 700.0
            if ws.has_position:
                top: float | None = float(ws.top)
                left: float | None = float(ws.left)
            else:
                top = None
                left = None
            return {
                "width": width,
                "height": height,
                "top": top,
                "left": left,
                "maximized": ws.maximized,
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
            ws = self._config.window_state
            ws.width = int(width)
            ws.height = int(height)
            ws.has_position = top is not None and left is not None
            ws.top = int(top) if top is not None else 0
            ws.left = int(left) if left is not None else 0
            ws.maximized = bool(maximized)
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
