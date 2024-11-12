import sys
import os
from pathlib import Path
import ctypes
import asyncio
from dlss_updater.logger import setup_logger

logger = setup_logger()


def check_update_completion():
    update_log_path = os.path.join(os.path.dirname(sys.executable), "update_log.txt")
    if os.path.exists(update_log_path):
        with open(update_log_path, "r") as f:
            logger.info(f"Update completed: {f.read()}")
        os.remove(update_log_path)


def check_update_error():
    error_log_path = os.path.join(
        os.path.dirname(sys.executable), "update_error_log.txt"
    )
    if os.path.exists(error_log_path):
        with open(error_log_path, "r") as f:
            logger.error(f"Update error occurred: {f.read()}")
        os.remove(error_log_path)


try:
    from dlss_updater import (
        update_dll,
        is_whitelisted,
        __version__,
        LATEST_DLL_PATHS,
        DLL_TYPE_MAP,
        # protect_warframe_dll,
    )
    from dlss_updater.scanner import find_all_dlss_dlls
    from dlss_updater.auto_updater import auto_update
except ImportError as e:
    logger.error(f"Error importing dlss_updater modules: {e}")
    logger.error("Current sys.path:")
    for path in sys.path:
        logger.error(path)
    logger.error("\nCurrent directory contents:")
    for item in os.listdir():
        logger.error(item)
    logger.error("\ndlss_updater directory contents:")
    try:
        for item in os.listdir("dlss_updater"):
            logger.error(item)
    except FileNotFoundError:
        logger.error("dlss_updater directory not found")
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
            logger.info(f"Missing dependencies: {', '.join(missing)}")
            return False
        return True
    except ImportError:
        logger.error("Unable to check dependencies. Proceeding anyway.")
        return True


def run_as_admin():
    script = Path(sys.argv[0]).resolve()
    params = " ".join([str(script)] + sys.argv[1:])
    logger.info("Re-running script with admin privileges...")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def display_release_notes():
    release_notes_file = Path(__file__).parent / "release_notes.txt"
    logger.info(f"Looking for release notes at: {release_notes_file}")
    if release_notes_file.exists():
        with open(release_notes_file, "r") as file:
            logger.info("\nRelease Notes:")
            logger.info(file.read())
    else:
        logger.info("\nRelease Notes file not found.")


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
        logger.error(
            f"Error extracting game name for {dll_path} in {launcher_name}: {e}"
        )
        return "Unknown Game"


# async def verify_warframe_protection(dll_path):
#     dll_path = Path(dll_path).resolve()
#     backup_path = (dll_path.parent / "DLSS_Updater_Backup" / dll_path.name).resolve()

#     if not os.path.exists(dll_path):
#         logger.warning(f"Warframe DLL not found: {dll_path}")
#         return False

#     if not os.path.exists(backup_path):
#         logger.warning(f"Backup DLL not found: {backup_path}")
#         return False

#     # Check if the DLL is read-only and system
#     attributes = ctypes.windll.kernel32.GetFileAttributesW(str(dll_path))
#     is_protected = (attributes & 1) != 0 and (attributes & 4) != 0

#     if is_protected:
#         logger.info(f"Warframe DLL protection verified: {dll_path}")
#         return True
#     else:
#         logger.warning(f"Warframe DLL is not properly protected: {dll_path}")
#         return False


async def main():
    if gui_mode:
        log_file = os.path.join(os.path.dirname(sys.executable), "dlss_updater.log")
        sys.stdout = sys.stderr = open(log_file, "w")

    logger.info(f"DLSS Updater version {__version__}")
    logger.info("Starting DLL search...")

    # observer = Observer()
    # observer.start()
    try:
        logger.info("Checking for updates...")
        if auto_update is None:
            logger.info("No updates were found.")
        else:
            try:
                update_available = await asyncio.to_thread(auto_update)
                if update_available:
                    logger.info(
                        "The application will now close for the update. If the update does NOT automatically restart, please manually reboot it from the /update/ folder."
                    )
                    return  # Exit here to allow the update process to take over
            except Exception as e:
                logger.error(f"Error during update check: {e}")
                import traceback

                traceback.logger.info_exc()

        display_release_notes()

        logger.info("Searching for DLSS DLLs...")
        all_dll_paths = await find_all_dlss_dlls()
        logger.info("DLL search completed.")

        updated_games = []
        skipped_games = []
        successful_backups = []
        processed_dlls = set()  # Keep track of processed DLLs

        if any(all_dll_paths.values()):
            logger.info("\nFound DLLs in the following launchers:")
            update_tasks = []
            dll_paths_to_update = []
            for launcher, dll_paths in all_dll_paths.items():
                if dll_paths:
                    logger.info(f"{launcher}:")
                    for dll_path in dll_paths:
                        dll_path = (
                            Path(dll_path) if isinstance(dll_path, str) else dll_path
                        )
                        if str(dll_path) not in processed_dlls:
                            dll_type = DLL_TYPE_MAP.get(
                                dll_path.name.lower(), "Unknown DLL type"
                            )
                            logger.info(f" - {dll_type}: {dll_path}")

                            game_name = extract_game_name(str(dll_path), launcher)
                            if "warframe" in game_name.lower():
                                continue
                                # logger.info(
                                #     f"Applying Warframe-specific protection to: {dll_path}"
                                # )
                                # protected = await protect_warframe_dll(str(dll_path))
                                # if protected:
                                #     logger.info(f"Warframe DLL protected: {dll_path}")
                                #     verified = await verify_warframe_protection(
                                #         str(dll_path)
                                #     )
                                #     if verified:
                                #         logger.info(
                                #             f"Warframe DLL protection verified: {dll_path}"
                                #         )
                                #     else:
                                #         logger.warning(
                                #             f"Warframe DLL protection could not be verified: {dll_path}"
                                #         )
                                # else:
                                #     logger.warning(
                                #         f"Failed to protect Warframe DLL: {dll_path}"
                                #     )
                            elif await is_whitelisted(str(dll_path)):
                                skipped_games.append(
                                    (dll_path, launcher, "Whitelisted", dll_type)
                                )
                            else:
                                dll_name = dll_path.name.lower()
                                if dll_name in LATEST_DLL_PATHS:
                                    latest_dll_path = LATEST_DLL_PATHS[dll_name]
                                    update_tasks.append(
                                        update_dll(str(dll_path), latest_dll_path)
                                    )
                                    dll_paths_to_update.append(
                                        (str(dll_path), launcher, dll_type)
                                    )
                                else:
                                    skipped_games.append(
                                        (
                                            str(dll_path),
                                            launcher,
                                            "No update available",
                                            dll_type,
                                        )
                                    )
                            processed_dlls.add(str(dll_path))

            if update_tasks:
                logger.info("\nUpdating DLLs...")
                update_results = await asyncio.gather(*update_tasks)
                for (dll_path, launcher, dll_type), result in zip(
                    dll_paths_to_update, update_results
                ):
                    if isinstance(result, tuple) and len(result) >= 2:
                        update_success, backup_path = result[:2]
                        if update_success:
                            logger.info(
                                f"Successfully updated {dll_type} at {dll_path}."
                            )
                            updated_games.append((dll_path, launcher, dll_type))
                            if backup_path:
                                successful_backups.append((dll_path, backup_path))
                        else:
                            logger.info(f"Failed to update {dll_type} at {dll_path}.")
                            skipped_games.append(
                                (dll_path, launcher, "Update failed", dll_type)
                            )
                    else:
                        logger.error(
                            f"Unexpected result format for {dll_path}: {result}"
                        )
                        skipped_games.append(
                            (dll_path, launcher, "Unexpected result", dll_type)
                        )
                logger.info("DLL updates completed.")
            elif skipped_games:
                logger.info("All found DLLs were skipped.")
            else:
                logger.info("No DLLs were eligible for update.")
        else:
            logger.info("No DLLs found.")

        # Display summary
        logger.info("\nSummary:")
        logger.info("")
        if updated_games or skipped_games or successful_backups:
            if updated_games:
                logger.info("Games updated successfully:")
                for dll_path, launcher, dll_type in updated_games:
                    game_name = extract_game_name(dll_path, launcher)
                    logger.info(f" - {game_name} - {launcher} ({dll_type})")
            else:
                logger.info("No games were updated.")

            if successful_backups:
                logger.info("\nSuccessful backups:")
                for dll_path, backup_path in successful_backups:
                    game_name = extract_game_name(dll_path, "Unknown")
                    dll_type = DLL_TYPE_MAP.get(
                        Path(dll_path).name.lower(), "Unknown DLL type"
                    )
                    logger.info(f" - {game_name}: {backup_path} ({dll_type})")
            else:
                logger.info("\nNo backups were created.")

            if skipped_games:
                logger.info("\nGames skipped:")
                for dll_path, launcher, reason, dll_type in skipped_games:
                    game_name = extract_game_name(dll_path, launcher)
                    logger.info(
                        f" - {game_name} - {launcher} ({dll_type}) (Reason: {reason})"
                    )
        else:
            logger.info("No DLLs were found or processed.")

    # except KeyboardInterrupt:
    #     observer.stop()
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        import traceback

        logger.error(traceback.format_exc())

    finally:
        # observer.join()
        input("\nPress Enter to exit...")
        logger.info("Application exiting.")


if __name__ == "__main__":
    check_update_completion()
    check_update_error()
    gui_mode = "--gui" in sys.argv
    logger.debug("Python executable: %s", sys.executable)
    logger.debug("sys.path: %s", sys.path)
    logger.info("DLSS Updater started")
    if not check_dependencies():
        sys.exit(1)
    if not is_admin():
        run_as_admin()
    else:
        asyncio.run(main())
