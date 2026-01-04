"""
Platform Utilities Module
Provides centralized platform detection and feature availability for cross-platform compatibility.
"""

import os
import sys
from pathlib import Path
from enum import Enum, auto
from typing import NamedTuple


class Platform(Enum):
    """Supported operating system platforms."""
    WINDOWS = auto()
    LINUX = auto()
    MACOS = auto()  # Future-proofing
    UNKNOWN = auto()


class FeatureSupport(NamedTuple):
    """Feature availability on current platform."""
    registry_access: bool
    dlss_overlay: bool
    directstorage: bool
    auto_launcher_detection: bool
    admin_elevation: bool


def get_platform() -> Platform:
    """
    Detect the current operating system.

    Returns:
        Platform enum value for the current OS.
    """
    if sys.platform == 'win32':
        return Platform.WINDOWS
    elif sys.platform == 'linux':
        return Platform.LINUX
    elif sys.platform == 'darwin':
        return Platform.MACOS
    return Platform.UNKNOWN


def is_windows() -> bool:
    """Check if running on Windows."""
    return get_platform() == Platform.WINDOWS


def is_linux() -> bool:
    """Check if running on Linux."""
    return get_platform() == Platform.LINUX


def get_feature_support() -> FeatureSupport:
    """
    Get feature availability for current platform.

    Returns:
        FeatureSupport tuple indicating which features are available.
    """
    platform = get_platform()

    if platform == Platform.WINDOWS:
        return FeatureSupport(
            registry_access=True,
            dlss_overlay=True,
            directstorage=True,
            auto_launcher_detection=True,
            admin_elevation=True,
        )
    elif platform == Platform.LINUX:
        return FeatureSupport(
            registry_access=False,  # No native Windows registry
            dlss_overlay=False,     # Requires Windows registry for NVIDIA settings
            directstorage=False,    # Windows-only technology
            auto_launcher_detection=False,  # No registry for launcher paths
            admin_elevation=True,   # sudo/pkexec available
        )
    else:
        # Unknown/unsupported platform - conservative defaults
        return FeatureSupport(
            registry_access=False,
            dlss_overlay=False,
            directstorage=False,
            auto_launcher_detection=False,
            admin_elevation=False,
        )


# Pre-computed constants for performance - computed once at import time
PLATFORM = get_platform()
IS_WINDOWS = PLATFORM == Platform.WINDOWS
IS_LINUX = PLATFORM == Platform.LINUX
FEATURES = get_feature_support()


def _is_root() -> bool:
    """Check if running as root/admin on Linux."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def get_app_config_dir() -> Path:
    """
    Get the application config directory based on platform and privileges.

    - Windows: Uses appdirs (typically AppData/Local/Recol/DLSS-Updater)
    - Linux as root: Uses appdirs (~/.config/DLSS-Updater)
    - Linux as non-root: Uses ~/.local/share/dlss-updater (avoids root-owned dirs)

    Returns:
        Path to the config directory (created if doesn't exist)
    """
    if IS_WINDOWS:
        import appdirs
        config_dir = Path(appdirs.user_config_dir("DLSS-Updater", "Recol"))
    elif IS_LINUX:
        if _is_root():
            # Running as root - use standard appdirs location
            import appdirs
            config_dir = Path(appdirs.user_config_dir("DLSS-Updater", "Recol"))
        else:
            # Running as normal user - use XDG data dir to avoid root-owned config
            config_dir = Path.home() / ".local" / "share" / "dlss-updater"
    else:
        # Fallback for unknown platforms
        import appdirs
        config_dir = Path(appdirs.user_config_dir("DLSS-Updater", "Recol"))

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


# Pre-computed config dir for performance
APP_CONFIG_DIR = get_app_config_dir()
