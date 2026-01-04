"""
Platform Utilities Module
Provides centralized platform detection and feature availability for cross-platform compatibility.
"""

import sys
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
