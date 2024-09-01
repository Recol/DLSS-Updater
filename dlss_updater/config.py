import os
import sys


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
    "nvngx_dlssg.dll": "3.17.20.0",
    "nvngx_dlssd.dll": "3.17.20.0",
}

LATEST_DLL_PATHS = {
    "nvngx_dlss.dll": resource_path(os.path.join("latest_dll", "nvngx_dlss.dll")),
    "nvngx_dlssg.dll": resource_path(os.path.join("latest_dll", "nvngx_dlssg.dll")),
    "nvngx_dlssd.dll": resource_path(os.path.join("latest_dll", "nvngx_dlssd.dll")),
}
