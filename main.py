import sys
import os
from pathlib import Path
import ctypes


# Add the directory containing the executable to sys.path
if getattr(sys, "frozen", False):
    application_path = os.path.dirname(sys.executable)
    sys.path.insert(0, application_path)

# Import dlss_updater modules, handling potential import errors
try:
    from dlss_updater import update_dll, is_whitelisted, __version__, LATEST_DLL_PATH
    from dlss_updater.scanner import find_all_dlss_dlls
    from dlss_updater.auto_updater import auto_update
except ImportError as e:
    print(f"Error importing dlss_updater modules: {e}")
    print("Current sys.path:")
    for path in sys.path:
        print(path)
    print("\nCurrent directory contents:")
    for item in os.listdir():
        print(item)
    print("\ndlss_updater directory contents:")
    try:
        for item in os.listdir("dlss_updater"):
            print(item)
    except FileNotFoundError:
        print("dlss_updater directory not found")
    sys.exit(1)


def check_dependencies():
    try:
        from importlib.metadata import distributions

        required = {"pefile", "psutil"}
        installed = set()
        for dist in distributions():
            name = dist.metadata.get("Name")
            if name:
                installed.add(name.lower())
        missing = required - installed
        if missing:
            print(f"Missing dependencies: {', '.join(missing)}")
            return False
        return True
    except ImportError:
        print("Unable to check dependencies. Proceeding anyway.")
        return True


def run_as_admin():
    script = Path(sys.argv[0]).resolve()
    params = " ".join([str(script)] + sys.argv[1:])
    print("Re-running script with admin privileges...")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def display_release_notes():
    release_notes_file = Path(__file__).parent / "release_notes.txt"
    print(f"Looking for release notes at: {release_notes_file}")
    if release_notes_file.exists():
        with open(release_notes_file, "r") as file:
            print("\nRelease Notes:")
            print(file.read())
    else:
        print("\nRelease Notes file not found.")


def extract_game_name(dll_path, launcher_name):
    parts = Path(dll_path).parts
    try:
        if launcher_name == "Steam":
            return parts[parts.index("steamapps") + 2]
        elif launcher_name == "EA Launcher":
            if "EA Games" in parts:
                return parts[parts.index("EA Games") + 1]
            else:
                return parts[-2]  # Assume the parent directory is the game name
        elif launcher_name == "Ubisoft Launcher":
            return parts[parts.index("games") + 1]
        elif launcher_name == "Epic Games Launcher":
            return parts[parts.index("Installed") + 1]
        elif launcher_name == "GOG Launcher":
            return parts[parts.index("Games") + 1]
        elif launcher_name == "Battle.net Launcher":
            return parts[parts.index("Games") + 1]
        else:
            return "Unknown Game"
    except ValueError as e:
        print(f"Error extracting game name for {dll_path} in {launcher_name}: {e}")
        return "Unknown Game"


def main():
    if gui_mode:
        log_file = os.path.join(os.path.dirname(sys.executable), "dlss_updater.log")
        sys.stdout = sys.stderr = open(log_file, "w")

    print(f"DLSS Updater version {__version__}")

    try:
        print("Checking for updates...")
        if auto_update is None:
            print("No updates were found.")
        else:
            try:
                update_available = auto_update()
                if update_available:
                    print(
                        "The application will now close for the update. It will restart automatically."
                    )
                    return  # Exit here to allow the update process to take over

            except Exception as e:
                print(f"Error during update check: {e}")
                import traceback

                traceback.print_exc()

        display_release_notes()

        all_dll_paths = find_all_dlss_dlls()

        updated_games = []
        skipped_games = []

        if any(all_dll_paths.values()):
            print("Found DLLs in the following launchers:")
            for launcher, dll_paths in all_dll_paths.items():
                if dll_paths:
                    print(f"{launcher}:")
                    for dll_path in dll_paths:
                        print(f" - {dll_path}")
                        if not is_whitelisted(str(dll_path)):
                            if update_dll(dll_path, LATEST_DLL_PATH):
                                print(f"Updated DLSS DLL at {dll_path}.")
                                updated_games.append(str(dll_path))
                            else:
                                print(f"DLSS DLL not updated at {dll_path}.")
                                skipped_games.append((dll_path, launcher))
                        else:
                            print(f"Skipped whitelisted game: {dll_path}")
                            skipped_games.append((dll_path, launcher))
        else:
            print("No DLLs found.")

        # Always display the summary
        print("\nSummary:")
        if updated_games:
            print("Games updated successfully:")
            for game in updated_games:
                print(f" - {game}")
        else:
            print("No games were updated.")

        if skipped_games:
            print("\nGames skipped:")
            for dll_path, launcher in skipped_games:
                game_name = extract_game_name(dll_path, launcher)
                print(f" - {game_name} - {launcher}")
        else:
            print("\nNo games were skipped.")

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback

        traceback.print_exc()

    finally:
        input("\nPress Enter to exit...")


if __name__ == "__main__":
    gui_mode = "--gui" in sys.argv
    print("Python executable:", sys.executable)
    print("sys.path:", sys.path)
    if not check_dependencies():
        sys.exit(1)
    if not is_admin():
        run_as_admin()
    else:
        main()
