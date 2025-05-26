import os
import concurrent.futures
import threading
from typing import List, Tuple, Dict
import sys
from pathlib import Path
import ctypes
from PyQt6.QtCore import QObject, pyqtSignal
from dlss_updater.logger import setup_logger
from dlss_updater.config import config_manager


logger = setup_logger()


class ParallelProgressTracker(QObject):
    """Track progress for parallel DLL processing with Qt signals"""

    progress_updated = pyqtSignal(int)  # Signal for progress percentage

    def __init__(self, total_dlls, logger):
        super().__init__()
        self.total = total_dlls
        self.completed = 0
        self.lock = threading.Lock()
        self.logger = logger

    def increment(self, dll_info):
        with self.lock:
            self.completed += 1
            progress = int((self.completed / self.total) * 100)
            self.logger.info(f"Progress: {progress}% - Processed {dll_info}")
            self.progress_updated.emit(progress)
            return progress


def process_single_dll_thread_safe(dll_path, launcher, progress_tracker):
    """Thread-safe version of process_single_dll"""
    try:
        result = process_single_dll(dll_path, launcher)
        progress_tracker.increment(f"{dll_path.name} from {launcher}")
        return result
    except Exception as e:
        logger.error(f"Error processing {dll_path}: {e}")
        return False, None, str(e)


def process_single_dll_with_backup(dll_path, launcher, backup_path, progress_tracker):
    """Process a single DLL with pre-created backup"""
    try:
        # Get DLL type and other info
        dll_name = dll_path.name.lower()
        dll_type = DLL_TYPE_MAP.get(dll_name, "Unknown DLL type")

        logger.info(f"Processing {dll_type}: {dll_path}")

        # Check version and other logic
        game_name = extract_game_name(str(dll_path), launcher)
        if "warframe" in game_name.lower():
            return None

        # Check if whitelisted
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        whitelisted = loop.run_until_complete(is_whitelisted(str(dll_path)))
        loop.close()

        if whitelisted:
            logger.debug(f"Game {game_name} is in whitelist")
            return False, None, dll_type

        # Update DLL with pre-created backup
        if dll_name in LATEST_DLL_PATHS:
            latest_dll_path = LATEST_DLL_PATHS[dll_name]
            # Pass the backup path to update_dll
            from dlss_updater.updater import update_dll_with_backup

            success, _, dll_type = update_dll_with_backup(
                str(dll_path), latest_dll_path, backup_path
            )

            progress_tracker.increment(f"{dll_path.name} from {launcher}")
            return success, backup_path, dll_type

        return False, None, dll_type
    except Exception as e:
        logger.error(f"Error processing DLL {dll_path}: {e}")
        return False, None, "Error"


def process_dlls_parallel(dll_tasks, max_workers=32, progress_signal=None):
    """Process DLLs using a thread pool with progress tracking"""
    results = {
        "updated_games": [],
        "skipped_games": [],
        "successful_backups": [],
        "errors": [],
    }

    # Create progress tracker
    total_dlls = len(dll_tasks)
    completed = 0
    completed_lock = threading.Lock()

    def update_progress():
        nonlocal completed
        with completed_lock:
            completed += 1
            if progress_signal:
                percentage = int((completed / total_dlls) * 100)
                progress_signal.emit(percentage)
                logger.info(f"Progress: {percentage}% ({completed}/{total_dlls})")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_dll = {
            executor.submit(
                process_single_dll_with_progress, dll_path, launcher, update_progress
            ): (dll_path, launcher)
            for dll_path, launcher in dll_tasks
        }

        # Process results as they complete
        for future in concurrent.futures.as_completed(future_to_dll):
            dll_path, launcher = future_to_dll[future]
            try:
                result = future.result()
                if result:
                    success, backup_path, dll_type = result
                    if success:
                        results["updated_games"].append(
                            (str(dll_path), launcher, dll_type)
                        )
                        if backup_path:
                            results["successful_backups"].append(
                                (str(dll_path), backup_path)
                            )
                    else:
                        reason = (
                            dll_type if isinstance(dll_type, str) else "Update failed"
                        )
                        results["skipped_games"].append(
                            (str(dll_path), launcher, reason, dll_type)
                        )
            except Exception as e:
                results["errors"].append((str(dll_path), launcher, str(e)))
                logger.error(f"Thread error processing {dll_path}: {e}")

    return results


def process_single_dll_with_progress(dll_path, launcher, progress_callback):
    """Process a single DLL and update progress"""
    try:
        result = process_single_dll(dll_path, launcher)
        progress_callback()  # Update progress after processing
        return result
    except Exception as e:
        logger.error(f"Error processing {dll_path}: {e}")
        progress_callback()  # Update progress even on error
        return False, None, str(e)


def aggregate_dll_tasks(all_dll_paths):
    """Flatten all DLLs into a single list with launcher info"""
    dll_tasks = []
    for launcher, dll_paths in all_dll_paths.items():
        for dll_path in dll_paths:
            try:
                dll_path = Path(dll_path) if isinstance(dll_path, str) else dll_path
                dll_tasks.append((dll_path, launcher))
            except Exception as e:
                logger.error(f"Error processing path {dll_path}: {e}")
    return dll_tasks


try:
    from dlss_updater import (
        update_dll,
        is_whitelisted,
        __version__,
        LATEST_DLL_PATHS,
        DLL_TYPE_MAP,
        find_all_dlss_dlls,
        resource_path,
    )
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


def find_file_in_directory(directory, filename):
    for root, _, files in os.walk(directory):
        if filename in files:
            return os.path.join(root, filename)
    return None


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
            gog_index = parts.index("GOG Galaxy")
            if "Games" in parts:
                # Pattern: D:\GOG Galaxy\Games\<GameName>\...
                return parts[parts.index("Games") + 1]
            else:
                # Pattern: D:\GOG Galaxy\<GameName>\...
                if gog_index + 1 < len(parts):
                    return parts[gog_index + 1]
        elif "Battle.net" in parts:
            return parts[parts.index("Battle.net") + 1]
        elif "Custom Path" in launcher_name:
            # For custom paths, try to get parent directory name
            return parts[-2]
        else:
            # If we can't determine the game name, use the parent directory name
            return parts[-2]
    except (ValueError, IndexError) as e:
        logger.error(
            f"Error extracting game name for {dll_path} in {launcher_name}: {e}"
        )
        return "Unknown Game"


def update_dlss_versions(progress_signal=None):
    logger.info(f"DLSS Updater version {__version__}")
    logger.info("Starting DLL search...")

    updated_games = []
    skipped_games = []
    successful_backups = []

    try:
        # Remove auto-update check - now handled manually through GUI
        logger.info("Starting DLL update process...")

        try:
            # Use the synchronous version of find_all_dlls
            all_dll_paths = find_all_dlss_dlls()
            logger.info("DLL search completed.")
        except Exception as e:
            logger.error(f"Error finding DLLs: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return False, [], [], []

        # Aggregate all DLL tasks
        dll_tasks = aggregate_dll_tasks(all_dll_paths)

        if dll_tasks:
            logger.info(f"\nFound {len(dll_tasks)} DLLs to process.")
            logger.info("Processing DLLs in parallel...")

            # Determine optimal thread count
            max_workers = min(len(dll_tasks), 8)  # Fixed thread count
            logger.info(f"Using {max_workers} parallel workers")

            # Process all DLLs in parallel with progress signal
            results = process_dlls_parallel(dll_tasks, max_workers, progress_signal)

            # Extract results
            updated_games = results["updated_games"]
            skipped_games = results["skipped_games"]
            successful_backups = results["successful_backups"]

            # Handle any errors that occurred
            for dll_path, launcher, error in results["errors"]:
                game_name = extract_game_name(dll_path, launcher)
                logger.error(f"Failed to process {game_name}: {error}")
                skipped_games.append((dll_path, launcher, f"Error: {error}", "Unknown"))

            # Display summary after processing
            if updated_games:
                logger.info("\nGames updated successfully:")
                for dll_path, launcher, dll_type in updated_games:
                    game_name = extract_game_name(dll_path, launcher)
                    logger.info(f" - {game_name} - {launcher} ({dll_type})")
            else:
                logger.info("\nNo games were updated.")

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

        return True, updated_games, skipped_games, successful_backups

    except Exception as e:
        import traceback

        trace = traceback.format_exc()
        logger.error(f"Critical error in update process: {e}")
        logger.error(f"Traceback:\n{trace}")
        return False, [], [], []


def process_single_dll(dll_path, launcher):
    """Process a single DLL file"""
    try:
        # Get the lowercase filename for consistency
        dll_name = dll_path.name.lower()

        # Directly check for known DLL types to avoid case sensitivity issues
        if "nvngx_dlss.dll" == dll_name:
            dll_type = "DLSS DLL"
        elif "nvngx_dlssg.dll" == dll_name:
            dll_type = "DLSS Frame Generation DLL"
        elif "nvngx_dlssd.dll" == dll_name:
            dll_type = "DLSS Ray Reconstruction DLL"
        elif "libxess.dll" == dll_name:
            dll_type = "XeSS DLL"
        elif "libxess_dx11.dll" == dll_name:
            dll_type = "XeSS DX11 DLL"
        elif "dstorage.dll" == dll_name:
            dll_type = "DirectStorage DLL"
        elif "dstoragecore.dll" == dll_name:
            dll_type = "DirectStorage Core DLL"
        # Add Streamline SDK DLL type detection
        elif "sl.common.dll" == dll_name:
            dll_type = "Streamline Shared Library DLL"
        elif "sl.dlss.dll" == dll_name:
            dll_type = "Streamline DLSS Super Resolution DLL"
        elif "sl.dlss_g.dll" == dll_name:
            dll_type = "Streamline DLSS Frame Generation DLL"
        elif "sl.interposer.dll" == dll_name:
            dll_type = "Streamline Graphics API Interception DLL"
        elif "sl.pcl.dll" == dll_name:
            dll_type = "Streamline Parameter/Platform Configuration DLL"
        elif "sl.reflex.dll" == dll_name:
            dll_type = "Streamline Reflex Low-Latency DLL"
        elif "amd_fidelityfx_vk.dll" == dll_name:
            dll_type = "AMD FidelityFX Super Resolution (FSR) Vulkan DLL"
        elif "amd_fidelityfx_dx12.dll" == dll_name:
            dll_type = "AMD FidelityFX Super Resolution (FSR) DirectX 12 DLL"
        else:
            dll_type = "Unknown DLL type"

        logger.info(f" - {dll_type}: {dll_path}")

        game_name = extract_game_name(str(dll_path), launcher)
        if "warframe" in game_name.lower():
            return None

        # Check if whitelisted
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        whitelisted = loop.run_until_complete(is_whitelisted(str(dll_path)))
        loop.close()

        if whitelisted:
            logger.debug(
                f"Game {game_name} is in whitelist, checking if it's on skip list..."
            )
            return False, None, dll_type

        if dll_name in LATEST_DLL_PATHS:
            latest_dll_path = LATEST_DLL_PATHS[dll_name]
            return update_dll(str(dll_path), latest_dll_path)

        return False, None, dll_type
    except Exception as e:
        logger.error(f"Error processing DLL {dll_path}: {e}")
        return False, None, "Error"


def display_update_summary(updated_games, skipped_games, successful_backups):
    """Display a summary of the update process"""
    logger.info("\nSummary:")
    if not (updated_games or skipped_games or successful_backups):
        logger.info("No DLLs were found or processed.")
        return

    if updated_games:
        logger.info("\nGames updated successfully:")
        for dll_path, launcher, dll_type in updated_games:
            game_name = extract_game_name(dll_path, launcher)
            logger.info(f" - {game_name} - {launcher} ({dll_type})")
    else:
        logger.info("\nNo games were updated.")

    if successful_backups:
        logger.info("\nSuccessful backups:")
        for dll_path, backup_path in successful_backups:
            game_name = extract_game_name(dll_path, "Unknown")
            dll_type = DLL_TYPE_MAP.get(Path(dll_path).name.lower(), "Unknown DLL type")
            logger.info(f" - {game_name}: {backup_path} ({dll_type})")
    else:
        logger.info("\nNo backups were created.")

    if skipped_games:
        logger.info("\nGames skipped:")
        for dll_path, launcher, reason, dll_type in skipped_games:
            game_name = extract_game_name(dll_path, launcher)
            logger.info(f" - {game_name} - {launcher} ({dll_type}) (Reason: {reason})")
