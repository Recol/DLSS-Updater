"""
Backup Manager for DLSS Updater
Enhanced backup creation and restoration with database integration
"""

import shutil
import os
import stat
import tempfile
import asyncio
import aiofiles
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from dlss_updater.logger import setup_logger
from dlss_updater.database import db_manager
from dlss_updater.config import Concurrency

logger = setup_logger()


async def async_copy2(src: Path, dst: Path, chunk_size: int = 65536) -> None:
    """
    Async file copy that preserves metadata (similar to shutil.copy2).

    Uses aiofiles for non-blocking I/O, improving UI responsiveness.

    Args:
        src: Source file path
        dst: Destination file path
        chunk_size: Size of chunks for streaming copy (default 64KB)
    """
    async with aiofiles.open(src, 'rb') as fsrc:
        async with aiofiles.open(dst, 'wb') as fdst:
            while chunk := await fsrc.read(chunk_size):
                await fdst.write(chunk)

    # Copy metadata (stat info) - run in thread pool since it's sync
    await asyncio.to_thread(shutil.copystat, src, dst)


def record_backup_metadata_sync(dll_path: Path, backup_path: Path) -> Optional[int]:
    """
    Record backup metadata in database (synchronous version).

    Use this when calling from sync code running in a thread pool.

    Args:
        dll_path: Path to original DLL
        backup_path: Path to backup file

    Returns:
        Backup ID if successful, None otherwise
    """
    try:
        # Use sync database methods directly
        game_dll = db_manager._get_game_dll_by_path(str(dll_path))

        if not game_dll:
            logger.warning(f"No game DLL record found for {dll_path}, cannot record backup metadata")
            return None

        # Mark old backups inactive
        db_manager._mark_old_backups_inactive(game_dll.id)

        # Get DLL version
        from dlss_updater.updater import get_dll_version
        version = get_dll_version(dll_path)

        # Get backup file size
        backup_size = backup_path.stat().st_size if backup_path.exists() else 0

        # Insert backup record
        backup_id = db_manager._insert_backup({
            'game_dll_id': game_dll.id,
            'backup_path': str(backup_path),
            'original_version': version,
            'backup_size': backup_size
        })

        if backup_id:
            logger.info(f"Recorded backup metadata for {dll_path.name} (backup_id: {backup_id})")
        else:
            logger.warning(f"Failed to record backup metadata for {dll_path.name}")

        return backup_id

    except Exception as e:
        logger.error(f"Error recording backup metadata: {e}", exc_info=True)
        return None


async def record_backup_metadata(dll_path: Path, backup_path: Path) -> Optional[int]:
    """
    Record backup metadata in database (async version).

    Args:
        dll_path: Path to original DLL
        backup_path: Path to backup file

    Returns:
        Backup ID if successful, None otherwise
    """
    try:
        # Get game DLL record
        game_dll = await db_manager.get_game_dll_by_path(str(dll_path))

        if not game_dll:
            logger.warning(f"No game DLL record found for {dll_path}, cannot record backup metadata")
            return None

        # Mark all existing backups for this DLL as inactive before creating a new one
        await db_manager.mark_old_backups_inactive(game_dll.id)

        # Get DLL version asynchronously (avoids blocking event loop)
        from dlss_updater.updater import get_dll_version_async
        version = await get_dll_version_async(dll_path)

        # Get backup file size (run in thread pool to avoid blocking)
        if backup_path.exists():
            stat_result = await asyncio.to_thread(backup_path.stat)
            backup_size = stat_result.st_size
        else:
            backup_size = 0

        # Insert backup record
        backup_id = await db_manager.insert_backup({
            'game_dll_id': game_dll.id,
            'backup_path': str(backup_path),
            'original_version': version,
            'backup_size': backup_size
        })

        if backup_id:
            logger.info(f"Recorded backup metadata for {dll_path.name} (backup_id: {backup_id})")
        else:
            logger.warning(f"Failed to record backup metadata for {dll_path.name}")

        return backup_id

    except Exception as e:
        logger.error(f"Error recording backup metadata: {e}", exc_info=True)
        return None


async def restore_dll_from_backup(backup_id: int) -> Tuple[bool, str]:
    """
    Restore a DLL from backup

    Args:
        backup_id: Database ID of the backup to restore

    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        # Get backup metadata
        backup = await db_manager.get_backup_by_id(backup_id)

        if not backup:
            return False, "Backup not found in database"

        # Get game DLL info
        game_dll = await db_manager.get_game_dll_by_path(
            str(Path(backup.backup_path).with_suffix('.dll'))
        )

        if not game_dll:
            return False, "DLL information not found in database"

        backup_path = Path(backup.backup_path)
        dll_path = Path(game_dll.dll_path)

        # Validate backup file exists
        if not backup_path.exists():
            logger.warning(f"Backup file not found: {backup_path}")
            await db_manager.mark_backup_inactive(backup_id)
            return False, "Backup file not found. It may have been deleted."

        # Validate DLL file exists
        if not dll_path.exists():
            return False, f"Current DLL not found: {dll_path}"

        # Check if file is in use (use async version to avoid blocking event loop)
        from dlss_updater.updater import is_file_in_use_async
        if await is_file_in_use_async(str(dll_path)):
            return False, "DLL is currently in use. Please close the game first."

        # Create temporary backup of current DLL (for rollback)
        temp_backup = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.dll') as tf:
                temp_backup = Path(tf.name)
            # Use async copy to avoid blocking event loop
            await async_copy2(dll_path, temp_backup)
            logger.info(f"Created temporary backup: {temp_backup}")

        except Exception as e:
            logger.error(f"Failed to create temporary backup: {e}")
            return False, f"Failed to create safety backup: {e}"

        # Perform restore
        try:
            # Remove read-only attribute if present (run in thread pool)
            if dll_path.exists():
                await asyncio.to_thread(os.chmod, dll_path, stat.S_IWRITE | stat.S_IREAD)

            # Copy backup to DLL location (async to avoid blocking event loop)
            await async_copy2(backup_path, dll_path)

            # Verify restore succeeded
            if not dll_path.exists():
                raise Exception("DLL file not found after restore")

            # Update database with new version (use async version to avoid blocking)
            from dlss_updater.updater import get_dll_version_async
            new_version = await get_dll_version_async(dll_path)
            await db_manager.update_game_dll_version(game_dll.id, new_version)

            # Mark backup as inactive (removes it from Backups page)
            await db_manager.mark_backup_inactive(backup_id)

            # Cleanup temporary backup
            if temp_backup and temp_backup.exists():
                temp_backup.unlink()

            logger.info(f"Successfully restored {dll_path.name} from backup")
            return True, f"Successfully restored {dll_path.name} to version {backup.original_version or 'unknown'}"

        except Exception as e:
            logger.error(f"Error during restore: {e}", exc_info=True)

            # Rollback: restore from temporary backup
            if temp_backup and temp_backup.exists():
                try:
                    shutil.copy2(temp_backup, dll_path)
                    logger.info("Rolled back to previous version after failed restore")
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback: {rollback_error}")

            # Cleanup temporary backup
            if temp_backup and temp_backup.exists():
                try:
                    temp_backup.unlink()
                except:
                    pass

            return False, f"Restore failed: {str(e)}"

    except Exception as e:
        logger.error(f"Error in restore_dll_from_backup: {e}", exc_info=True)
        return False, f"Unexpected error during restore: {str(e)}"


async def delete_backup(backup_id: int) -> Tuple[bool, str]:
    """
    Delete a backup file and mark it inactive in database

    Args:
        backup_id: Database ID of the backup to delete

    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        # Get backup metadata
        backup = await db_manager.get_backup_by_id(backup_id)

        if not backup:
            return False, "Backup not found in database"

        backup_path = Path(backup.backup_path)

        # Delete backup file if it exists
        if backup_path.exists():
            try:
                # Remove read-only attribute if present (run in thread pool)
                await asyncio.to_thread(os.chmod, backup_path, stat.S_IWRITE)
                await asyncio.to_thread(backup_path.unlink)
                logger.info(f"Deleted backup file: {backup_path}")
            except Exception as e:
                logger.error(f"Failed to delete backup file: {e}")
                return False, f"Failed to delete backup file: {e}"

        # Mark backup as inactive in database
        await db_manager.mark_backup_inactive(backup_id)

        return True, "Backup deleted successfully"

    except Exception as e:
        logger.error(f"Error deleting backup: {e}", exc_info=True)
        return False, f"Error deleting backup: {str(e)}"


async def validate_backup(backup_id: int) -> Tuple[bool, str]:
    """
    Validate that a backup file exists and is accessible

    Args:
        backup_id: Database ID of the backup to validate

    Returns:
        Tuple of (valid: bool, message: str)
    """
    try:
        # Get backup metadata
        backup = await db_manager.get_backup_by_id(backup_id)

        if not backup:
            return False, "Backup not found in database"

        backup_path = Path(backup.backup_path)

        # Check if file exists
        if not backup_path.exists():
            await db_manager.mark_backup_inactive(backup_id)
            return False, "Backup file not found"

        # Check if file is readable (run in thread pool to avoid blocking)
        is_readable = await asyncio.to_thread(os.access, backup_path, os.R_OK)
        if not is_readable:
            return False, "Backup file is not readable"

        # Check file size matches (run in thread pool to avoid blocking)
        stat_result = await asyncio.to_thread(backup_path.stat)
        actual_size = stat_result.st_size
        if actual_size != backup.backup_size:
            logger.warning(f"Backup file size mismatch: expected {backup.backup_size}, got {actual_size}")
            return False, f"Backup file may be corrupted (size mismatch)"

        return True, "Backup is valid"

    except Exception as e:
        logger.error(f"Error validating backup: {e}", exc_info=True)
        return False, f"Error validating backup: {str(e)}"


async def validate_backups_batch(
    backup_ids: List[int],
    max_concurrent: int = None
) -> dict[int, Tuple[bool, str]]:
    """
    Validate multiple backups in parallel with maximum concurrency.

    Args:
        backup_ids: List of backup IDs to validate
        max_concurrent: Maximum concurrent validations (default: IO_HEAVY)

    Returns:
        Dict mapping backup_id to (valid: bool, message: str)
    """
    if not backup_ids:
        return {}

    if max_concurrent is None:
        max_concurrent = Concurrency.IO_HEAVY
    # Maximum concurrency for backup validation (async file I/O scales extremely well)
    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded_validate(backup_id: int) -> Tuple[int, Tuple[bool, str]]:
        async with semaphore:
            result = await validate_backup(backup_id)
            return backup_id, result

    # Run all validations with bounded concurrency
    tasks = [bounded_validate(bid) for bid in backup_ids]
    results = await asyncio.gather(*tasks)

    return dict(results)


async def restore_group_for_game(
    game_id: int,
    group: str,  # "all" or specific group name like "DLSS"
) -> Tuple[bool, str, List[Dict[str, str]]]:
    """
    Restore all backups for a game, optionally filtered by DLL group.

    Performs concurrent restore operations with bounded concurrency to avoid
    overwhelming the system while maximizing throughput.

    Args:
        game_id: Database ID of the game
        group: "all" to restore all backups, or group name like "DLSS", "FSR", etc.

    Returns:
        Tuple of (overall_success, summary_message, detailed_results)
        - overall_success: True only if ALL restores succeeded
        - summary_message: Human-readable summary (e.g., "Restored 3/4 DLLs")
        - detailed_results: List of dicts with keys: dll_filename, success, message
    """
    try:
        # Get backups grouped by DLL type
        backup_groups = await db_manager.get_backups_grouped_by_dll_type(game_id)

        if not backup_groups:
            return False, "No backups found for this game", []

        # Determine which backups to restore
        backups_to_restore = []
        if group == "all":
            for group_backups in backup_groups.values():
                backups_to_restore.extend(group_backups)
        elif group in backup_groups:
            backups_to_restore = backup_groups[group]
        else:
            available_groups = ", ".join(backup_groups.keys())
            return False, f"No backups found for group '{group}'. Available: {available_groups}", []

        if not backups_to_restore:
            return False, "No backups to restore", []

        # Restore with bounded concurrency using Concurrency.IO_HEAVY
        # This prevents overwhelming the filesystem while still achieving parallelism
        semaphore = asyncio.Semaphore(Concurrency.IO_HEAVY)

        async def bounded_restore(backup) -> Dict[str, str]:
            """Perform a single restore operation with semaphore-bounded concurrency."""
            async with semaphore:
                success, message = await restore_dll_from_backup(backup.id)
                return {
                    'dll_filename': backup.dll_filename,
                    'success': str(success),  # Convert to string for consistent Dict[str, str]
                    'message': message
                }

        # Execute all restore operations concurrently
        tasks = [bounded_restore(backup) for backup in backups_to_restore]
        results = await asyncio.gather(*tasks)

        # Calculate success metrics
        success_count = sum(1 for r in results if r['success'] == 'True')
        total_count = len(backups_to_restore)
        overall_success = success_count == total_count

        # Generate summary message
        if overall_success:
            summary = f"Successfully restored all {total_count} DLL(s)"
        elif success_count == 0:
            summary = f"Failed to restore any DLLs (0/{total_count})"
        else:
            summary = f"Partially restored {success_count}/{total_count} DLLs"

        logger.info(f"Restore group for game {game_id} ({group}): {summary}")

        return overall_success, summary, results

    except Exception as e:
        logger.error(f"Error in restore_group_for_game: {e}", exc_info=True)
        return False, f"Unexpected error during restore: {str(e)}", []
