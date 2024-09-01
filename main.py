import sys
import os
from pathlib import Path
import ctypes
import asyncio


try:
    from dlss_updater import (
        update_dll,
        is_whitelisted,
        __version__,
        LATEST_DLL_PATHS,
    )
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

# Add the directory containing the executable to sys.path
if getattr(sys, "frozen", False):
    application_path = os.path.dirname(sys.executable)
    sys.path.insert(0, application_path)


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
        if "steamapps" in parts:
            return parts[parts.index("steamapps") + 2]
        elif "EA Games" in parts:
            return parts[parts.index("EA Games") + 1]
        elif "Ubisoft Game Launcher" in parts:
            return parts[parts.index("games") + 1]
        elif "Epic Games" in parts:
            return parts[parts.index("Epic Games") + 2]
        elif "GOG Galaxy" in parts:
            return parts[parts.index("Games") + 1]
        elif "Battle.net" in parts:
            return parts[parts.index("Battle.net") + 1]
        else:
            # If we can't determine the game name, use the parent directory name
            return parts[-2]
    except (ValueError, IndexError) as e:
        print(f"Error extracting game name for {dll_path} in {launcher_name}: {e}")
        return "Unknown Game"


async def main():
    if gui_mode:
        log_file = os.path.join(os.path.dirname(sys.executable), "dlss_updater.log")
        sys.stdout = sys.stderr = open(log_file, "w")

    print(f"DLSS Updater version {__version__}")
    print("Starting DLL search...")

    try:
        print("Checking for updates...")
        if auto_update is None:
            print("No updates were found.")
        else:
            try:
                update_available = await asyncio.to_thread(auto_update)
                if update_available:
                    print(
                        "The application will now close for the update. If the update does NOT automatically restart, please manually reboot it from the /update/ folder."
                    )
                    return  # Exit here to allow the update process to take over
            except Exception as e:
                print(f"Error during update check: {e}")
                import traceback
                traceback.print_exc()

        display_release_notes()

        print("Searching for DLSS DLLs...")
        all_dll_paths = await find_all_dlss_dlls()
        print("DLL search completed.")

        updated_games = []
        skipped_games = []
        successful_backups = []
        processed_dlls = set()  # Keep track of processed DLLs

        if any(all_dll_paths.values()):
            print("\nFound DLLs in the following launchers:")
            update_tasks = []
            dll_paths_to_update = []
            for launcher, dll_paths in all_dll_paths.items():
                if dll_paths:
                    print(f"{launcher}:")
                    for dll_path in dll_paths:
                        if str(dll_path) not in processed_dlls:
                            print(f" - {dll_path}")
                            if is_whitelisted(str(dll_path)):
                                skipped_games.append(
                                    (dll_path, launcher, "Whitelisted")
                                )
                            else:
                                dll_name = dll_path.name.lower()
                                if dll_name in LATEST_DLL_PATHS:
                                    latest_dll_path = LATEST_DLL_PATHS[dll_name]
                                    update_tasks.append(
                                        update_dll(dll_path, latest_dll_path)
                                    )
                                    dll_paths_to_update.append((dll_path, launcher))
                                else:
                                    skipped_games.append(
                                        (dll_path, launcher, "No update available")
                                    )
                            processed_dlls.add(str(dll_path))

            if update_tasks:
                print("\nUpdating DLLs...")
                update_results = await asyncio.gather(*update_tasks)
                for (dll_path, launcher), (result, backup_path) in zip(
                    dll_paths_to_update, update_results
                ):
                    if result:
                        print(f"Successfully updated DLSS DLL at {dll_path}.")
                        updated_games.append((dll_path, launcher))
                        if backup_path:
                            successful_backups.append((dll_path, backup_path))
                    else:
                        print(f"Failed to update DLSS DLL at {dll_path}.")
                        skipped_games.append((dll_path, launcher, "Update failed"))
                print("DLL updates completed.")
            elif skipped_games:
                print("All found DLLs were skipped.")
            else:
                print("No DLLs were eligible for update.")
        else:
            print("No DLLs found.")

        # Display summary
        print("\nSummary:")
        if updated_games or skipped_games or successful_backups:
            if updated_games:
                print("Games updated successfully:")
                for dll_path, launcher in updated_games:
                    game_name = extract_game_name(dll_path, launcher)
                    print(f" - {game_name} - {launcher}")
            else:
                print("No games were updated.")

            if successful_backups:
                print("\nSuccessful backups:")
                for dll_path, backup_path in successful_backups:
                    game_name = extract_game_name(dll_path, "Unknown")
                    print(f" - {game_name}: {backup_path}")
            else:
                print("\nNo backups were created.")

            if skipped_games:
                print("\nGames skipped:")
                for dll_path, launcher, reason in skipped_games:
                    game_name = extract_game_name(dll_path, launcher)
                    print(f" - {game_name} - {launcher} (Reason: {reason})")
        else:
            print("No DLLs were found or processed.")

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
        asyncio.run(main())
