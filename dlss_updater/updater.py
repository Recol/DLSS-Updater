import os
import shutil
import pefile
from .config import LATEST_DLL_VERSIONS, LATEST_DLL_PATHS
from pathlib import Path
import concurrent.futures
import stat
import time
import psutil
from packaging import version
from .logger import setup_logger
from .constants import DLL_TYPE_MAP, FSR4_DLL_RENAME_MAP
from .config import config_manager
from .models import ProcessedDLLResult

logger = setup_logger()


def parse_version(version_string):
    """
    Parse a version string into a format that can be compared.
    Handles both dot and comma-separated versions.
    """
    if not version_string:
        return version.parse("0.0.0")

    # Replace commas with dots
    cleaned_version = version_string.replace(",", ".")

    # Split by dots and take the first three components to standardize format
    components = cleaned_version.split(".")
    if len(components) >= 3:
        # Use the first three components for comparison
        standardized_version = ".".join(components[:3])
    else:
        # If less than three components, use what we have
        standardized_version = cleaned_version

    try:
        return version.parse(standardized_version)
    except Exception as e:
        logger.error(f"Error parsing version '{version_string}': {e}")
        # Return a very low version to encourage updates when parsing fails
        return version.parse("0.0.0")


def get_dll_version(dll_path):
    try:
        with open(dll_path, "rb") as file:
            pe = pefile.PE(data=file.read())
            for fileinfo in pe.FileInfo:
                for entry in fileinfo:
                    if hasattr(entry, "StringTable"):
                        for st in entry.StringTable:
                            for key, value in st.entries.items():
                                if key == b"FileVersion":
                                    return value.decode("utf-8").strip()
    except Exception as e:
        logger.error(f"Error reading version from {dll_path}: {e}")
    return None


def remove_read_only(file_path):
    if not os.access(file_path, os.W_OK):
        logger.info(f"Removing read-only attribute from {file_path}")
        os.chmod(file_path, stat.S_IWRITE)


def restore_permissions(file_path, original_permissions):
    os.chmod(file_path, original_permissions)


def is_file_in_use(file_path, timeout=5):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with open(file_path, "rb"):
                return False
        except PermissionError:
            for proc in psutil.process_iter(["pid", "name", "open_files"]):
                try:
                    for file in proc.open_files():
                        if file.path == file_path:
                            logger.error(
                                f"File {file_path} is in use by process {proc.name()} (PID: {proc.pid})"
                            )
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        time.sleep(0.1)
    logger.info(f"Timeout reached while checking if file {file_path} is in use")
    return True  # Assume file is NOT in use if we can't determine otherwise to prevent hanging conditions


def normalize_path(path):
    return os.path.normpath(path)


def record_update_history_sync(dll_path, from_version, to_version, success):
    """Record update history in database (synchronous wrapper for async operation)"""
    try:
        import asyncio
        from dlss_updater.database import db_manager

        # Use asyncio.run() for thread-safe async execution
        async def _record():
            game_dll = await db_manager.get_game_dll_by_path(str(dll_path))
            if game_dll:
                await db_manager.record_update_history({
                    'game_dll_id': game_dll.id,
                    'from_version': from_version,
                    'to_version': to_version,
                    'success': success
                })
                logger.debug(f"Recorded update history for {dll_path.name}")

        asyncio.run(_record())
    except Exception as e:
        logger.warning(f"Failed to record update history: {e}")
        # Don't fail update if history recording fails


def create_backup(dll_path):
    backup_path = dll_path.with_suffix(".dlsss")
    try:
        logger.info(f"[BACKUP] Attempting to create backup at: {backup_path}")

        # Pre-flight check 1: Verify write permission to directory
        if not os.access(dll_path.parent, os.W_OK):
            logger.error(f"[BACKUP] No write permission to directory: {dll_path.parent}")
            return None

        # Pre-flight check 2: Verify sufficient disk space (need 2x DLL size for safety)
        try:
            dll_size = dll_path.stat().st_size
            disk_stat = shutil.disk_usage(dll_path.parent)
            required_space = dll_size * 2

            if disk_stat.free < required_space:
                logger.error(
                    f"[BACKUP] Insufficient disk space. "
                    f"Available: {disk_stat.free / (1024**2):.1f}MB, "
                    f"Required: {required_space / (1024**2):.1f}MB"
                )
                return None

            logger.debug(f"[BACKUP] Disk space check passed. Available: {disk_stat.free / (1024**2):.1f}MB")
        except Exception as e:
            logger.warning(f"[BACKUP] Could not check disk space: {e}")
            # Continue anyway - not critical

        # Remove old backup if exists
        if backup_path.exists():
            logger.info(f"[BACKUP] Previous backup exists, removing...")
            try:
                os.chmod(backup_path, stat.S_IWRITE)
                os.remove(backup_path)
                logger.info(f"[BACKUP] Successfully removed old backup")
            except Exception as e:
                logger.error(f"[BACKUP] Failed to remove old backup: {e}", exc_info=True)
                return None

        # Set directory permissions
        try:
            dir_path = os.path.dirname(backup_path)
            os.chmod(dir_path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        except Exception as e:
            logger.warning(f"[BACKUP] Could not set directory permissions: {e}")
            # Continue anyway - not always critical

        # Perform backup copy
        shutil.copy2(dll_path, backup_path)

        # Verification 1: Check backup file exists
        if not backup_path.exists():
            logger.error(f"[BACKUP] Backup file not created at {backup_path}")
            return None

        # Verification 2: Check backup file size matches original
        try:
            original_size = dll_path.stat().st_size
            backup_size = backup_path.stat().st_size

            if original_size != backup_size:
                logger.error(
                    f"[BACKUP] Size mismatch! Original: {original_size} bytes, "
                    f"Backup: {backup_size} bytes"
                )
                # Delete corrupted backup
                try:
                    backup_path.unlink()
                    logger.info(f"[BACKUP] Deleted corrupted backup file")
                except Exception:
                    pass
                return None

            logger.debug(f"[BACKUP] Size verification passed: {original_size} bytes")
        except Exception as e:
            logger.warning(f"[BACKUP] Could not verify backup size: {e}")
            # Continue anyway - file exists at least

        # Set backup file permissions
        try:
            os.chmod(backup_path, stat.S_IWRITE | stat.S_IREAD)
        except Exception as e:
            logger.warning(f"[BACKUP] Could not set backup file permissions: {e}")

        logger.info(f"[BACKUP] Successfully created and verified backup at: {backup_path}")

        # Record backup metadata in database (fixed asyncio event loop handling)
        try:
            import asyncio
            from dlss_updater.backup_manager import record_backup_metadata

            # Use existing event loop instead of creating new one
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Non-blocking: schedule as task to run later
                    asyncio.create_task(record_backup_metadata(dll_path, backup_path))
                    logger.debug(f"[BACKUP] Scheduled metadata recording as async task")
                else:
                    # If no loop running, use asyncio.run()
                    asyncio.run(record_backup_metadata(dll_path, backup_path))
                    logger.debug(f"[BACKUP] Recorded metadata synchronously")
            except RuntimeError as e:
                # If we can't get loop or create task, fall back to asyncio.run()
                logger.debug(f"[BACKUP] Falling back to asyncio.run() for metadata: {e}")
                asyncio.run(record_backup_metadata(dll_path, backup_path))
        except Exception as e:
            logger.warning(f"[BACKUP] Failed to record backup metadata: {e}", exc_info=True)
            # Don't fail backup creation if metadata recording fails

        return backup_path

    except PermissionError as e:
        logger.error(f"[BACKUP] Permission denied creating backup for {dll_path}: {e}")
        return None
    except OSError as e:
        logger.error(f"[BACKUP] OS error creating backup for {dll_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"[BACKUP] Unexpected error creating backup for {dll_path}: {e}", exc_info=True)
        logger.error(f"[BACKUP] Error type: {type(e).__name__}")
        return None


def update_dll(dll_path, latest_dll_path):
    dll_path = Path(normalize_path(dll_path)).resolve()
    latest_dll_path = Path(normalize_path(latest_dll_path)).resolve()
    logger.info(f"Checking DLL at {dll_path}...")

    dll_type = DLL_TYPE_MAP.get(dll_path.name.lower(), "Unknown DLL type")
    original_permissions = os.stat(dll_path).st_mode

    try:
        existing_version = get_dll_version(dll_path)
        latest_version = get_dll_version(latest_dll_path)

        if existing_version and latest_version:
            existing_parsed = parse_version(existing_version)
            latest_parsed = parse_version(latest_version)

            logger.info(
                f"Existing version: {existing_version}, Latest version: {latest_version}"
            )

            # Do not include DLSS DLLs in the update check if version < 2.0.0
            if dll_type == "DLSS DLL" and existing_parsed < parse_version("2.0.0"):
                logger.info(
                    f"Skipping update for {dll_path}: Version {existing_version} is less than 2.0.0 and cannot be updated."
                )
                return ProcessedDLLResult(success=False, dll_type=dll_type)

            if existing_parsed >= latest_parsed:
                logger.info(
                    f"{dll_path} is already up-to-date (version {existing_version})."
                )
                return ProcessedDLLResult(success=False, dll_type=dll_type)

        if not dll_path.exists():
            logger.error(f"Error: Target DLL path does not exist: {dll_path}")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        if not latest_dll_path.exists():
            logger.error(f"Error: Latest DLL path does not exist: {latest_dll_path}")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        if not os.access(dll_path.parent, os.W_OK):
            logger.error(
                f"Error: No write permission to the directory: {dll_path.parent}"
            )
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        backup_path = None
        if config_manager.get_backup_preference():
            backup_path = create_backup(dll_path)
            if not backup_path:
                logger.error(f"Failed to create backup for {dll_path}")
                return ProcessedDLLResult(success=False, dll_type=dll_type)
        else:
            logger.info(f"Backup creation disabled by user preference for {dll_path}")

        remove_read_only(dll_path)

        retry_count = 3
        while retry_count > 0:
            if not is_file_in_use(str(dll_path)):
                break
            logger.info(
                f"File is in use. Retrying in 2 seconds... (Attempts left: {retry_count})"
            )
            time.sleep(2)
            retry_count -= 1

        if retry_count == 0:
            logger.info(
                f"File {dll_path} is still in use after multiple attempts. Cannot update."
            )
            restore_permissions(dll_path, original_permissions)
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        try:
            os.remove(dll_path)
            shutil.copyfile(latest_dll_path, dll_path)
            restore_permissions(dll_path, original_permissions)

            # Verify update
            new_version = get_dll_version(dll_path)
            if new_version == latest_version:
                logger.info(
                    f"Successfully updated {dll_path} from version {existing_version} to {latest_version}."
                )
                # Record successful update in database
                record_update_history_sync(dll_path, existing_version, latest_version, True)
                return ProcessedDLLResult(success=True, backup_path=str(backup_path), dll_type=dll_type)
            else:
                logger.error(
                    f"Version verification failed - Expected: {latest_version}, Got: {new_version}"
                )
                # Record failed update in database
                record_update_history_sync(dll_path, existing_version, latest_version, False)
                return ProcessedDLLResult(success=False, backup_path=str(backup_path), dll_type=dll_type)

        except Exception as e:
            logger.error(f"File update operation failed: {e}")
            if backup_path and backup_path.exists():
                try:
                    shutil.copyfile(backup_path, dll_path)
                    logger.info("Restored backup after failed update")
                except Exception as restore_error:
                    logger.error(f"Failed to restore backup: {restore_error}")
            return ProcessedDLLResult(success=False, backup_path=str(backup_path), dll_type=dll_type)

    except Exception as e:
        logger.error(f"Error updating {dll_path}: {e}")
        restore_permissions(dll_path, original_permissions)
        return ProcessedDLLResult(success=False, dll_type=dll_type)


def update_dll_with_backup(dll_path, latest_dll_path, pre_created_backup_path=None):
    """Update DLL with pre-created backup path"""
    dll_path = Path(normalize_path(dll_path)).resolve()
    latest_dll_path = Path(normalize_path(latest_dll_path)).resolve()
    logger.info(f"Checking DLL at {dll_path}...")

    dll_type = DLL_TYPE_MAP.get(dll_path.name.lower(), "Unknown DLL type")
    original_permissions = os.stat(dll_path).st_mode

    try:
        existing_version = get_dll_version(dll_path)
        latest_version = get_dll_version(latest_dll_path)

        if existing_version and latest_version:
            existing_parsed = parse_version(existing_version)
            latest_parsed = parse_version(latest_version)

            logger.info(
                f"Existing version: {existing_version}, Latest version: {latest_version}"
            )
            # Do not include FG/RR DLLs in the update check
            if dll_type == "nvngx_dlss.dll" and existing_parsed < parse_version(
                "2.0.0"
            ):
                logger.info(
                    f"Skipping update for {dll_path}: Version {existing_version} is less than 2.0.0 and cannot be updated."
                )
                return ProcessedDLLResult(success=False, dll_type=dll_type)

            if existing_parsed >= latest_parsed:
                logger.info(
                    f"{dll_path} is already up-to-date (version {existing_version})."
                )
                return ProcessedDLLResult(success=False, dll_type=dll_type)

        if not dll_path.exists():
            logger.error(f"Error: Target DLL path does not exist: {dll_path}")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        if not latest_dll_path.exists():
            logger.error(f"Error: Latest DLL path does not exist: {latest_dll_path}")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        if not os.access(dll_path.parent, os.W_OK):
            logger.error(
                f"Error: No write permission to the directory: {dll_path.parent}"
            )
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        # Use pre-created backup path if provided, otherwise create backup conditionally
        backup_path = pre_created_backup_path
        if not backup_path:
            if config_manager.get_backup_preference():
                backup_path = create_backup(dll_path)
                if not backup_path:
                    logger.error(f"Failed to create backup for {dll_path}")
                    return ProcessedDLLResult(success=False, dll_type=dll_type)
            else:
                logger.info(f"Backup creation disabled by user preference for {dll_path}")
        else:
            # Validate pre-created backup exists
            backup_path = Path(backup_path)
            if not backup_path.exists():
                logger.error(f"Pre-created backup does not exist: {backup_path}")
                # Try to create a new backup if backups are enabled
                if config_manager.get_backup_preference():
                    backup_path = create_backup(dll_path)
                    if not backup_path:
                        return ProcessedDLLResult(success=False, dll_type=dll_type)
                else:
                    backup_path = None

        remove_read_only(dll_path)

        retry_count = 3
        while retry_count > 0:
            if not is_file_in_use(str(dll_path)):
                break
            logger.info(
                f"File is in use. Retrying in 2 seconds... (Attempts left: {retry_count})"
            )
            time.sleep(2)
            retry_count -= 1

        if retry_count == 0:
            logger.info(
                f"File {dll_path} is still in use after multiple attempts. Cannot update."
            )
            restore_permissions(dll_path, original_permissions)
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        try:
            os.remove(dll_path)
            shutil.copyfile(latest_dll_path, dll_path)
            restore_permissions(dll_path, original_permissions)

            # Verify update
            new_version = get_dll_version(dll_path)
            if new_version == latest_version:
                logger.info(
                    f"Successfully updated {dll_path} from version {existing_version} to {latest_version}."
                )
                # Record successful update in database
                record_update_history_sync(dll_path, existing_version, latest_version, True)
                return ProcessedDLLResult(success=True, backup_path=str(backup_path), dll_type=dll_type)
            else:
                logger.error(
                    f"Version verification failed - Expected: {latest_version}, Got: {new_version}"
                )
                # Record failed update in database
                record_update_history_sync(dll_path, existing_version, latest_version, False)
                return ProcessedDLLResult(success=False, backup_path=str(backup_path), dll_type=dll_type)

        except Exception as e:
            logger.error(f"File update operation failed: {e}")
            if backup_path and backup_path.exists():
                try:
                    shutil.copyfile(backup_path, dll_path)
                    logger.info("Restored backup after failed update")
                except Exception as restore_error:
                    logger.error(f"Failed to restore backup: {restore_error}")
            return ProcessedDLLResult(success=False, backup_path=str(backup_path), dll_type=dll_type)

    except Exception as e:
        logger.error(f"Error updating {dll_path}: {e}")
        restore_permissions(dll_path, original_permissions)
        return ProcessedDLLResult(success=False, dll_type=dll_type)


def create_backups_parallel(dll_paths, max_workers=4, progress_callback=None):
    """
    Create backups for multiple DLLs in parallel

    Args:
        dll_paths: List of DLL paths to back up
        max_workers: Maximum number of parallel workers
        progress_callback: Optional callback(current, total, message) for progress
    """
    backup_results = []
    total_dlls = len(dll_paths)
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_dll = {
            executor.submit(create_backup, Path(dll_path)): dll_path
            for dll_path in dll_paths
        }

        for future in concurrent.futures.as_completed(future_to_dll):
            dll_path = future_to_dll[future]
            try:
                backup_path = future.result()
                if backup_path:
                    backup_results.append((dll_path, backup_path))
                    logger.info(f"Created backup for {dll_path}")
                else:
                    logger.warning(f"Failed to create backup for {dll_path}")
            except Exception as e:
                logger.error(f"Error creating backup for {dll_path}: {e}")

            completed += 1
            if progress_callback:
                progress_callback(completed, total_dlls, f"Backed up {completed}/{total_dlls} files")

    return backup_results


def validate_fsr_version(dll_version_string, min_version="3.1.0"):
    """
    Validate that FSR DLL version meets minimum requirements
    Returns True if version >= min_version, False otherwise
    """
    if not dll_version_string:
        logger.warning("No version string provided for FSR validation")
        return False
    
    try:
        dll_version = parse_version(dll_version_string)
        min_required = parse_version(min_version)
        
        if dll_version >= min_required:
            logger.info(f"FSR version {dll_version_string} meets minimum requirement ({min_version})")
            return True
        else:
            logger.warning(f"FSR version {dll_version_string} does not meet minimum requirement ({min_version})")
            return False
    except Exception as e:
        logger.error(f"Error validating FSR version '{dll_version_string}': {e}")
        return False


def update_fsr4_dll_with_rename(source_dll_path, target_dll_path, latest_dll_path, pre_created_backup_path=None):
    """
    Update FSR4 DLL with rename handling (amd_fidelityfx_loader_dx12.dll -> amd_fidelityfx_dx12.dll)
    
    Args:
        source_dll_path: Path where the source DLL name is stored in repository
        target_dll_path: Path where the DLL should be installed (existing FSR DLL location)  
        latest_dll_path: Path to the latest DLL file to install
        pre_created_backup_path: Optional pre-created backup path
    """
    source_dll_path = Path(normalize_path(source_dll_path)).resolve()
    target_dll_path = Path(normalize_path(target_dll_path)).resolve()
    latest_dll_path = Path(normalize_path(latest_dll_path)).resolve()
    
    source_dll_name = source_dll_path.name.lower()
    target_dll_name = target_dll_path.name.lower()
    
    logger.info(f"FSR4 rename update: {source_dll_name} -> {target_dll_name} at {target_dll_path}")

    # Get DLL type from the target name (what it will become)
    dll_type = DLL_TYPE_MAP.get(target_dll_name, "Unknown DLL type")
    original_permissions = os.stat(target_dll_path).st_mode if target_dll_path.exists() else 0o644

    try:
        # Get versions for comparison
        existing_version = get_dll_version(target_dll_path) if target_dll_path.exists() else None
        latest_version = get_dll_version(latest_dll_path)

        if not latest_version:
            logger.error(f"Could not determine version of latest FSR4 DLL: {latest_dll_path}")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        # Validate FSR version meets minimum requirements (3.1+)
        if not validate_fsr_version(latest_version, "3.1.0"):
            logger.error(f"FSR4 DLL version {latest_version} does not meet minimum requirement (3.1.0)")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        if existing_version and latest_version:
            existing_parsed = parse_version(existing_version)
            latest_parsed = parse_version(latest_version)

            logger.info(f"Existing version: {existing_version}, Latest version: {latest_version}")

            if existing_parsed >= latest_parsed:
                logger.info(f"{target_dll_path} is already up-to-date (version {existing_version}).")
                return ProcessedDLLResult(success=False, dll_type=dll_type)

        if not latest_dll_path.exists():
            logger.error(f"Error: Latest DLL path does not exist: {latest_dll_path}")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        if target_dll_path.exists() and not os.access(target_dll_path.parent, os.W_OK):
            logger.error(f"Error: No write permission to the directory: {target_dll_path.parent}")
            return ProcessedDLLResult(success=False, dll_type=dll_type)

        # Handle backup creation for target DLL (if it exists)
        backup_path = pre_created_backup_path
        if target_dll_path.exists():
            if not backup_path:
                if config_manager.get_backup_preference():
                    backup_path = create_backup(target_dll_path)
                    if not backup_path:
                        logger.error(f"Failed to create backup for {target_dll_path}")
                        return ProcessedDLLResult(success=False, dll_type=dll_type)
                else:
                    logger.info(f"Backup creation disabled by user preference for {target_dll_path}")
            else:
                # Validate pre-created backup exists
                backup_path = Path(backup_path)
                if not backup_path.exists():
                    logger.error(f"Pre-created backup does not exist: {backup_path}")
                    # Try to create a new backup if backups are enabled
                    if config_manager.get_backup_preference():
                        backup_path = create_backup(target_dll_path)
                        if not backup_path:
                            return ProcessedDLLResult(success=False, dll_type=dll_type)
                    else:
                        backup_path = None

        # Remove read-only attribute if target exists
        if target_dll_path.exists():
            remove_read_only(target_dll_path)

            # Check if file is in use
            retry_count = 3
            while retry_count > 0:
                if not is_file_in_use(str(target_dll_path)):
                    break
                logger.info(f"File is in use. Retrying in 2 seconds... (Attempts left: {retry_count})")
                time.sleep(2)
                retry_count -= 1

            if retry_count == 0:
                logger.info(f"File {target_dll_path} is still in use after multiple attempts. Cannot update.")
                restore_permissions(target_dll_path, original_permissions)
                return ProcessedDLLResult(success=False, dll_type=dll_type)

        try:
            # Remove existing file if it exists
            if target_dll_path.exists():
                os.remove(target_dll_path)
            
            # Copy the source DLL to the target location (this performs the rename)
            shutil.copyfile(latest_dll_path, target_dll_path)
            restore_permissions(target_dll_path, original_permissions)

            # Verify update
            new_version = get_dll_version(target_dll_path)
            if new_version == latest_version:
                logger.info(f"Successfully updated {target_dll_path} from version {existing_version or 'N/A'} to {latest_version} (FSR4 rename)")
                return ProcessedDLLResult(success=True, backup_path=str(backup_path), dll_type=dll_type)
            else:
                logger.error(f"Version verification failed - Expected: {latest_version}, Got: {new_version}")
                return ProcessedDLLResult(success=False, backup_path=str(backup_path), dll_type=dll_type)

        except Exception as e:
            logger.error(f"File update operation failed: {e}")
            # Restore backup if update failed
            if backup_path and backup_path.exists() and target_dll_path.exists():
                try:
                    shutil.copyfile(backup_path, target_dll_path)
                    logger.info("Restored backup after failed FSR4 update")
                except Exception as restore_error:
                    logger.error(f"Failed to restore backup: {restore_error}")
            return ProcessedDLLResult(success=False, backup_path=str(backup_path), dll_type=dll_type)

    except Exception as e:
        logger.error(f"Error updating FSR4 DLL {target_dll_path}: {e}")
        if target_dll_path.exists():
            restore_permissions(target_dll_path, original_permissions)
        return ProcessedDLLResult(success=False, dll_type=dll_type)
