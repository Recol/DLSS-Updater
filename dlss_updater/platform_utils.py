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
    dlss_linux_overlay: bool        # Can use debug overlay via env vars (Linux only)
    dlss_linux_presets: bool        # Can configure SR presets via env vars (Linux only)
    nvidia_gpu_detected: bool       # NVIDIA GPU present (checked at startup)


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


def _check_nvidia_gpu_present() -> bool:
    """
    Check if an NVIDIA GPU is present using NVML.

    This is a synchronous check performed at import time to determine
    whether NVIDIA-specific features should be available.

    Returns:
        True if NVIDIA GPU detected, False otherwise.
    """
    try:
        import pynvml
        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        pynvml.nvmlShutdown()
        return device_count > 0
    except Exception:
        # pynvml not installed, NVML not available, or no GPU
        return False


def get_feature_support() -> FeatureSupport:
    """
    Get feature availability for current platform.

    Returns:
        FeatureSupport tuple indicating which features are available.
    """
    platform = get_platform()
    nvidia_gpu = _check_nvidia_gpu_present()

    if platform == Platform.WINDOWS:
        return FeatureSupport(
            registry_access=True,
            dlss_overlay=True,
            directstorage=True,
            auto_launcher_detection=True,
            admin_elevation=True,
            dlss_linux_overlay=False,           # Windows doesn't need this
            dlss_linux_presets=False,           # Windows doesn't need this
            nvidia_gpu_detected=nvidia_gpu,
        )
    elif platform == Platform.LINUX:
        return FeatureSupport(
            registry_access=False,  # No native Windows registry
            dlss_overlay=nvidia_gpu,  # Linux: via DXVK-NVAPI env vars
            directstorage=False,    # Windows-only technology
            auto_launcher_detection=False,  # No registry for launcher paths
            admin_elevation=False,  # Flatpak sandboxing handles permissions
            dlss_linux_overlay=True,            # Always available on Linux
            dlss_linux_presets=True,            # Always available on Linux
            nvidia_gpu_detected=nvidia_gpu,
        )
    else:
        # Unknown/unsupported platform - conservative defaults
        return FeatureSupport(
            registry_access=False,
            dlss_overlay=False,
            directstorage=False,
            auto_launcher_detection=False,
            admin_elevation=False,
            dlss_linux_overlay=False,
            dlss_linux_presets=False,
            nvidia_gpu_detected=False,
        )


# Pre-computed constants for performance - computed once at import time
PLATFORM = get_platform()
IS_WINDOWS = PLATFORM == Platform.WINDOWS
IS_LINUX = PLATFORM == Platform.LINUX
FEATURES = get_feature_support()


def get_app_config_dir() -> Path:
    """
    Get the application config directory based on platform.

    - Windows: Uses platformdirs (typically AppData/Local/Recol/DLSS-Updater)
    - Linux: Uses ~/.local/share/dlss-updater (XDG-compliant, Flatpak-compatible)

    Returns:
        Path to the config directory (created if doesn't exist)
    """
    if IS_WINDOWS:
        import platformdirs
        config_dir = Path(platformdirs.user_config_dir("DLSS-Updater", "Recol"))
    elif IS_LINUX:
        # Use XDG data dir for Flatpak compatibility
        config_dir = Path.home() / ".local" / "share" / "dlss-updater"
    else:
        # Fallback for unknown platforms
        import platformdirs
        config_dir = Path(platformdirs.user_config_dir("DLSS-Updater", "Recol"))

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


# Pre-computed config dir for performance
APP_CONFIG_DIR = get_app_config_dir()
