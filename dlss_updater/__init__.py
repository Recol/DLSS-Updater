from .scanner import (
    get_steam_install_path,
    get_steam_libraries,
    find_dlls,
    find_all_dlls_sync,
)
from .updater import update_dll
from .whitelist import is_whitelisted
from .version import __version__
from .config import resource_path, initialize_dll_paths, config_manager
from .auto_updater import auto_update
from .logger import setup_logger
from .constants import DLL_TYPE_MAP
from .lib.threading_lib import ThreadManager, WorkerSignals

# We rename find_dlss_dlls to find_dlls and keep it for backward compatibility
find_dlss_dlls = find_dlls

# Let's export find_all_dlls_sync instead of the async version
find_all_dlss_dlls = find_all_dlls_sync

# Don't initialize DLL paths at import time anymore
# This will be done explicitly after admin check
LATEST_DLL_PATHS = {}

__all__ = [
    "get_steam_install_path",
    "get_steam_libraries",
    "find_dlss_dlls",
    "find_all_dlss_dlls",
    "update_dll",
    "is_whitelisted",
    "__version__",
    "LATEST_DLL_PATHS",
    "resource_path",
    "auto_update",
    "setup_logger",
    "DLL_TYPE_MAP",
    "ThreadManager",
    "WorkerSignals",
    "config_manager",
]
