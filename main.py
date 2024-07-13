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

def display_release_notes():
    release_notes_file = Path(__file__).parent / 'release_notes.txt'
    if release_notes_file.exists():
        with open(release_notes_file, 'r') as file:
            print("\nRelease Notes:")
            print(file.read())
    else:
        print("\nRelease Notes file not found.")

def extract_game_name(dll_path, launcher_name):
    parts = Path(dll_path).parts
    if launcher_name == "Steam":
        return parts[parts.index('steamapps') + 2]
    elif launcher_name == "EA Launcher":
        return parts[parts.index('EA Games') + 1]
    elif launcher_name == "Ubisoft Launcher":
        return parts[parts.index('games') + 1]
    elif launcher_name == "Epic Games Launcher":
        return parts[parts.index('Epic Games') + 1]
    elif launcher_name == "GOG Launcher":
        return parts[parts.index('Games') + 1]
    elif launcher_name == "Battle.net Launcher":
        return parts[parts.index('Games') + 1]
    else:
        return "Unknown Game"

def main():
    if gui_mode:
        log_file = os.path.join(os.path.dirname(sys.executable), 'dlss_updater.log')
        sys.stdout = sys.stderr = open(log_file, 'w')

    print(f"DLSS Updater version {__version__}")

    display_release_notes()

    all_dll_paths = find_all_dlss_dlls()

    if not any(all_dll_paths.values()):
        print("No DLLs found.")
        return

    updated_games = []
    skipped_games = []

    for launcher, dll_paths in all_dll_paths.items():
        for dll_path in dll_paths:
            if not is_whitelisted(str(dll_path)):
                if update_dll(dll_path, LATEST_DLL_PATH):
                    updated_games.append((dll_path, launcher))
                else:
                    skipped_games.append((dll_path, launcher))
            else:
                skipped_games.append((dll_path, launcher))

    print("\nSummary:")
    if updated_games:
        print("Games updated successfully:")
        for dll_path, launcher in updated_games:
            game_name = extract_game_name(dll_path, launcher)
            print(f" - {game_name} - {launcher}")
    else:
        print("No games were updated.")

    if skipped_games:
        print("\nGames skipped:")
        for dll_path, launcher in skipped_games:
            game_name = extract_game_name(dll_path, launcher)
            print(f" - {game_name} - {launcher}")
    else:
        print("No games were skipped.")

    input("\nPress Enter to exit...")

if __name__ == "__main__":
    gui_mode = '--gui' in sys.argv
    if not check_dependencies():
        sys.exit(1)

    if not is_admin():
        run_as_admin()
    else:
        main()
