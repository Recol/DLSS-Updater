import os
import sys
import ctypes
import subprocess
import pkg_resources
from pathlib import Path
from dlss_updater.scanner import get_steam_install_path, get_steam_libraries, find_nvngx_dlss_dll
from dlss_updater.updater import update_dll
from dlss_updater.config import LATEST_DLL_PATH

def install_dependencies():
    required = {'pefile', 'psutil'}
    installed = {pkg.key for pkg in pkg_resources.working_set}
    missing = required - installed

    if missing:
        print(f"Installing missing dependencies: {', '.join(missing)}")
        python = sys.executable
        subprocess.check_call([python, '-m', 'pip', 'install', *missing])

def run_as_admin():
    script = Path(sys.argv[0]).resolve()
    params = ' '.join([str(script)] + sys.argv[1:])
    print("Re-running script with admin privileges...")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def main():
    steam_path = get_steam_install_path()
    if not steam_path:
        print("Steam is not installed.")
        return

    library_paths = get_steam_libraries(steam_path)
    dll_paths = find_nvngx_dlss_dll(library_paths)

    if not dll_paths:
        print("No DLLs found.")
        return

    print(f"Found {len(dll_paths)} DLLs.")
    for dll_path in dll_paths:
        if update_dll(dll_path, LATEST_DLL_PATH):
            print(f"Updated DLSS DLL at {dll_path}.")
        else:
            print(f"DLSS DLL not updated at {dll_path}.")

if __name__ == "__main__":
    install_dependencies()
    if not is_admin():
        run_as_admin()
    else:
        main()
