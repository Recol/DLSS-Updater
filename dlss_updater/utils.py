import os
import asyncio
import concurrent.futures
import threading
from typing import List, Tuple, Dict, Optional
import sys
from pathlib import Path
from dlss_updater.logger import setup_logger
from dlss_updater.config import config_manager, Concurrency
from dlss_updater.models import ProcessedDLLResult
from dlss_updater.platform_utils import IS_WINDOWS, IS_LINUX

# Only import ctypes on Windows (not available/needed on Linux for our use case)
if IS_WINDOWS:
    import ctypes


logger = setup_logger()


def process_single_dll_thread_safe(dll_path, launcher, progress_tracker):
    """Thread-safe version of process_single_dll"""
    try:
        result = process_single_dll(dll_path, launcher)
        progress_tracker.increment(f"{dll_path.name} from {launcher}")
        return result
    except Exception as e:
        logger.error(f"Error processing {dll_path}: {e}")
        return ProcessedDLLResult(success=False, dll_type=str(e))


async def process_single_dll_with_backup(dll_path, launcher, backup_path, progress_tracker):
    """Process a single DLL with pre-created backup (async version)"""
    try:
        # Get DLL type and other info
        dll_name = dll_path.name.lower()
        dll_type = DLL_TYPE_MAP.get(dll_name, "Unknown DLL type")

        logger.info(f"Processing {dll_type}: {dll_path}")

        # Check version and other logic
        game_name = extract_game_name(str(dll_path), launcher)
        if "warframe" in game_name.lower():
            return None

        # Check if whitelisted (using async/await properly)
        whitelisted = await is_whitelisted(str(dll_path))

        if whitelisted:
            logger.debug(f"Game {game_name} is in whitelist")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        # Update DLL with pre-created backup
        if dll_name in config.LATEST_DLL_PATHS:
            latest_dll_path = config.LATEST_DLL_PATHS[dll_name]
            # Pass the backup path to update_dll
            from dlss_updater.updater import update_dll_with_backup

            # Run file I/O in thread pool to avoid blocking
            result = await asyncio.to_thread(
                update_dll_with_backup,
                str(dll_path), latest_dll_path, backup_path
            )

            progress_tracker.increment(f"{dll_path.name} from {launcher}")
            return result

        return ProcessedDLLResult(success=False, dll_type=dll_type)
    except Exception as e:
        logger.error(f"Error processing DLL {dll_path}: {e}")
        return ProcessedDLLResult(success=False, dll_type="Error")


async def process_dlls_parallel(dll_tasks, max_workers=None, progress_callback=None):
    """Process DLLs using asyncio with progress tracking (maximum hardware utilization)"""
    if max_workers is None:
        max_workers = Concurrency.IO_HEAVY
    import asyncio

    results = {
        "updated_games": [],
        "skipped_games": [],
        "successful_backups": [],
        "errors": [],
    }

    # Create progress tracker
    total_dlls = len(dll_tasks)
    completed = 0
    completed_lock = asyncio.Lock()

    async def update_progress():
        nonlocal completed
        async with completed_lock:
            completed += 1
            if progress_callback:
                # Call with (current, total, message) signature for async_updater compatibility
                progress_callback(completed, total_dlls, f"Processing DLL {completed}/{total_dlls}")
                logger.info(f"Progress: {completed}/{total_dlls}")
                # Yield control to event loop to allow UI updates to process
                await asyncio.sleep(0)

    async def process_with_progress(dll_path, launcher):
        """Process single DLL and update progress"""
        try:
            result = await process_single_dll(dll_path, launcher)
            await update_progress()
            return result, dll_path, launcher
        except Exception as e:
            logger.error(f"Error processing {dll_path}: {e}")
            await update_progress()
            return ProcessedDLLResult(success=False, dll_type=str(e)), dll_path, launcher

    # Process all DLLs concurrently using asyncio.gather
    # Use semaphore to limit concurrency (similar to max_workers in ThreadPoolExecutor)
    semaphore = asyncio.Semaphore(max_workers)

    async def process_with_semaphore(dll_path, launcher):
        async with semaphore:
            return await process_with_progress(dll_path, launcher)

    # Create tasks for all DLLs
    tasks = [
        process_with_semaphore(dll_path, launcher)
        for dll_path, launcher in dll_tasks
    ]

    # Wait for all tasks to complete
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    for task_result in task_results:
        if isinstance(task_result, Exception):
            logger.error(f"Task error: {task_result}")
            results["errors"].append(("Unknown", "Unknown", str(task_result)))
            continue

        result, dll_path, launcher = task_result
        if result:
            # result is now a ProcessedDLLResult object
            if result.success:
                results["updated_games"].append(
                    (str(dll_path), launcher, result.dll_type)
                )
                if result.backup_path:
                    results["successful_backups"].append(
                        (str(dll_path), result.backup_path)
                    )
            else:
                reason = (
                    result.dll_type if isinstance(result.dll_type, str) else "Update failed"
                )
                results["skipped_games"].append(
                    (str(dll_path), launcher, reason, result.dll_type)
                )

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
        return ProcessedDLLResult(success=False, dll_type=str(e))


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
    # Import directly from modules to avoid circular dependency through __init__.py
    from dlss_updater.updater import update_dll
    from dlss_updater.whitelist import is_whitelisted
    from dlss_updater.version import __version__
    from dlss_updater import config  # Import module, not variable
    from dlss_updater.constants import DLL_TYPE_MAP
    # Note: find_dlls import moved to function level to avoid circular dependency
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
    """
    Request elevated privileges.
    On Windows: Uses ShellExecuteW with 'runas' verb.
    On Linux: Uses pkexec (if available) or sudo.
    """
    if IS_WINDOWS:
        script = Path(sys.argv[0]).resolve()
        params = " ".join([str(script)] + sys.argv[1:])
        logger.info("Re-running script with admin privileges...")
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    elif IS_LINUX:
        import subprocess
        import shutil

        script = sys.argv[0]
        args = sys.argv[1:]

        logger.info("Re-running script with elevated privileges...")

        # Try pkexec first (GUI-friendly), fall back to sudo
        if os.environ.get('DISPLAY') and shutil.which('pkexec'):
            # GUI environment - use pkexec for graphical sudo prompt
            try:
                subprocess.Popen(['pkexec', sys.executable, script] + args)
            except Exception as e:
                logger.warning(f"pkexec failed: {e}, falling back to sudo")
                subprocess.Popen(['sudo', sys.executable, script] + args)
        else:
            # Terminal environment or pkexec not available - use sudo
            subprocess.Popen(['sudo', sys.executable, script] + args)


def is_admin():
    """
    Check if running with elevated privileges.
    On Windows: Checks IsUserAnAdmin().
    On Linux: Checks if running as root (euid == 0).
    """
    if IS_WINDOWS:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return False
    elif IS_LINUX:
        # On Linux, check if running as root
        return os.geteuid() == 0
    return False


def find_game_root(dll_path: Path, launcher: str) -> Path:
    """
    Find the game root directory by walking upward from DLL location

    Handles cases where DLLs are in subdirectories:
    - Arc Raiders/bin/nvngx_dlss.dll → Arc Raiders/
    - Arc Raiders/engine/nvngx_dlss.dll → Arc Raiders/
    - Arc Raiders/plugins/nvngx_dlss.dll → Arc Raiders/

    Args:
        dll_path: Path to the DLL file
        launcher: Launcher name (Steam, Epic Games Launcher, etc.)

    Returns:
        Path to the game root directory
    """
    current = dll_path.parent

    # For Steam: Always use steamapps/common/<GameName>
    if launcher == "Steam":
        parts = current.parts
        try:
            if "common" in parts:
                common_idx = parts.index("common")
                if common_idx + 1 < len(parts):
                    # Return one level after "common" - the game root
                    game_root_parts = parts[:common_idx + 2]
                    return Path(*game_root_parts)
        except (ValueError, IndexError):
            pass

    # For other launchers: Walk up with stricter heuristics
    max_depth = 5  # Increased from 3 for better detection
    for _ in range(max_depth):
        # Strong indicators of game root
        has_exe = list(current.glob("*.exe"))
        has_bin = (current / "bin").exists() or (current / "Binaries").exists()
        has_engine = (current / "engine").exists() or (current / "Engine").exists()
        has_data = (current / "data").exists() or (current / "Data").exists()
        has_content = (current / "Content").exists()

        # Count strong indicators
        indicators = [
            len(has_exe) > 0,
            has_bin,
            has_engine,
            has_data,
            has_content
        ]

        # If we have 2+ indicators, this is likely the root
        if sum(indicators) >= 2:
            return current

        # Move up one level
        if current.parent == current:  # Reached filesystem root
            break
        current = current.parent

    # Fallback: return parent of dll_path (original behavior)
    return dll_path.parent


def get_dll_technology_group(dll_name):
    """
    Get the technology group (DLSS, Streamline, etc.) for a DLL filename

    Args:
        dll_name: DLL filename (lowercase)

    Returns:
        Technology group name or None if not found
    """
    from dlss_updater.constants import DLL_GROUPS

    for group_name, dll_list in DLL_GROUPS.items():
        if dll_name in [dll.lower() for dll in dll_list]:
            return group_name
    return None


def is_dll_update_enabled(dll_name):
    """
    Check if updates are enabled for this DLL type based on user preferences

    Args:
        dll_name: DLL filename (lowercase)

    Returns:
        True if updates are enabled, False otherwise
    """
    # Get the technology group for this DLL
    tech_group = get_dll_technology_group(dll_name)

    if tech_group is None:
        logger.warning(f"Unknown DLL type: {dll_name}, skipping")
        return False

    # Check if this technology is enabled in preferences
    is_enabled = config_manager.get_update_preference(tech_group)

    if not is_enabled:
        logger.info(f"Skipping {dll_name} - {tech_group} updates are disabled in preferences")

    return is_enabled


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


async def update_dlss_versions(dll_dict=None, settings=None, progress_callback=None):
    """
    Update DLSS versions across all games (fully async version)

    Args:
        dll_dict: Pre-scanned dictionary of DLLs, or None to scan
        settings: Update settings
        progress_callback: Progress callback function

    Returns:
        Dict with updated_games, skipped_games, successful_backups, errors
    """
    logger.info(f"DLSS Updater version {__version__}")
    logger.info(f"LATEST_DLL_PATHS status: {len(config.LATEST_DLL_PATHS)} DLLs available")
    if len(config.LATEST_DLL_PATHS) == 0:
        logger.error("CRITICAL: LATEST_DLL_PATHS is empty! DLL cache may not be initialized.")
        logger.error("Please check for errors during 'Initializing DLL cache' at startup.")

    updated_games = []
    skipped_games = []
    successful_backups = []

    try:
        # Remove auto-update check - now handled manually through GUI
        logger.info("Starting DLL update process...")

        # If dll_dict not provided, scan for DLLs
        if dll_dict is None:
            logger.info("Starting DLL search...")
            try:
                # Lazy import to avoid circular dependency (scanner imports from utils)
                from dlss_updater.scanner import find_all_dlls
                # Use the async version of find_all_dlls
                all_dll_paths = await find_all_dlls()
                logger.info("DLL search completed.")
            except Exception as e:
                logger.error(f"Error finding DLLs: {e}")
                import traceback

                trace = traceback.format_exc()
                logger.error(trace)
                return {
                    "updated_games": [],
                    "skipped_games": [],
                    "successful_backups": [],
                    "errors": [{"message": str(e), "traceback": trace}]
                }
        else:
            # Use provided dll_dict
            logger.info("Using pre-scanned DLL dictionary...")
            all_dll_paths = dll_dict

        # Aggregate all DLL tasks
        dll_tasks = aggregate_dll_tasks(all_dll_paths)

        if dll_tasks:
            logger.info(f"\nFound {len(dll_tasks)} DLLs to process.")
            logger.info("Processing DLLs in parallel...")

            # Determine optimal concurrency - scale with hardware
            max_workers = min(len(dll_tasks), Concurrency.IO_HEAVY)
            logger.info(f"Using {max_workers} parallel workers (IO_HEAVY={Concurrency.IO_HEAVY})")

            # Process all DLLs in parallel with progress callback (now async)
            results = await process_dlls_parallel(dll_tasks, max_workers, progress_callback)

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

        # Return dict format for async_updater compatibility
        return {
            "updated_games": updated_games,
            "skipped_games": skipped_games,
            "successful_backups": successful_backups,
            "errors": []  # Errors are already merged into skipped_games
        }

    except Exception as e:
        import traceback

        trace = traceback.format_exc()
        logger.error(f"Critical error in update process: {e}")
        logger.error(f"Traceback:\n{trace}")
        # Return dict format for consistency
        return {
            "updated_games": [],
            "skipped_games": [],
            "successful_backups": [],
            "errors": [{"message": str(e), "traceback": trace}]
        }


async def process_single_dll(dll_path, launcher):
    """Process a single DLL file (async version)"""
    try:
        # Get the lowercase filename for consistency
        dll_name = dll_path.name.lower()

        # Check if updates are enabled for this DLL type
        if not is_dll_update_enabled(dll_name):
            dll_type = DLL_TYPE_MAP.get(dll_name, "Unknown DLL type")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

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

        # Check if whitelisted (now using async/await properly)
        whitelisted = await is_whitelisted(str(dll_path))

        if whitelisted:
            logger.debug(
                f"Game {game_name} is in whitelist, checking if it's on skip list..."
            )
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        if dll_name in config.LATEST_DLL_PATHS:
            latest_dll_path = config.LATEST_DLL_PATHS[dll_name]
            logger.debug(f"Found {dll_name} in LATEST_DLL_PATHS, latest_dll_path: {latest_dll_path}")
            # Run file I/O in thread pool to avoid blocking
            return await asyncio.to_thread(update_dll, str(dll_path), latest_dll_path)

        logger.debug(f"DLL {dll_name} not in LATEST_DLL_PATHS (available: {list(config.LATEST_DLL_PATHS.keys())[:5]}...)")
        return ProcessedDLLResult(success=False, dll_type=dll_type)
    except Exception as e:
        logger.error(f"Error processing DLL {dll_path}: {e}")
        return ProcessedDLLResult(success=False, dll_type="Error")


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
