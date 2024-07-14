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


LATEST_DLL_VERSION = "3.17.10.0"
LATEST_DLL_PATH = resource_path(os.path.join("latest_dll", "nvngx_dlss.dll"))
