import sys
import os
import ctypes
from pathlib import Path
from importlib.metadata import distributions

from dlss_updater import (
    update_dll, is_whitelisted, __version__, LATEST_DLL_PATH
)
from dlss_updater.scanner import find_all_dlss_dlls

def check_dependencies():
    required = {'pefile', 'psutil'}
    installed = {dist.metadata['Name'].lower() for dist in distributions()}
    missing = required - installed

    if missing:
        print(f"Missing dependencies: {', '.join(missing)}")
        return False
    return True

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
    if gui_mode:
        log_file = os.path.join(os.path.dirname(sys.executable), 'dlss_updater.log')
        sys.stdout = sys.stderr = open(log_file, 'w')

    print(f"DLSS Updater version {__version__}")

    dll_paths = find_all_dlss_dlls()

    if not dll_paths:
        print("No DLLs found.")
        return

    updated_games = []
    skipped_games = []

    print(f"Found {len(dll_paths)} DLLs.")
    for dll_path in dll_paths:
        if not is_whitelisted(str(dll_path)):
            if update_dll(dll_path, LATEST_DLL_PATH):
                print(f"Updated DLSS DLL at {dll_path}.")
                updated_games.append(str(dll_path))
            else:
                print(f"DLSS DLL not updated at {dll_path}.")
                skipped_games.append(str(dll_path))
        else:
            print(f"Skipped whitelisted game: {dll_path}")
            skipped_games.append(str(dll_path))

    print("\nSummary:")
    print("Games updated successfully:")
    for game in updated_games:
        print(f" - {game}")

    print("\nGames skipped:")
    for game in skipped_games:
        print(f" - {game}")

    input("\nPress Enter to exit...")

if __name__ == "__main__":
    gui_mode = '--gui' in sys.argv
    print("Python executable:", sys.executable)
    print("sys.path:", sys.path)
    if not check_dependencies():
        sys.exit(1)

    if not is_admin():
        run_as_admin()
    else:
        main()
