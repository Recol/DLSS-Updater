"""
Backup Manager for DLSS Updater
Enhanced backup creation and restoration with database integration
"""

import shutil
import os
import stat
import tempfile
import anyio
from pathlib import Path

from dlss_updater.logger import setup_logger
from dlss_updater.database import db_manager
from dlss_updater.concurrency_limiters import io_heavy, thread_io

logger = setup_logger()


async def async_copy2(src: Path, dst: Path, chunk_size: int = 65536) -> None:
    """
    Async file copy that preserves metadata (identical semantics to
    ``shutil.copy2`` — file data plus permission/stat metadata).

    Delegates to :func:`shutil.copy2` on a worker thread via the shared
    ``thread_io`` limiter. ``shutil.copy2`` uses the platform fast-copy path
    (``sendfile``/``copy_file_range`` on Linux, ``CopyFile2`` on Windows)
    instead of streaming 64KB chunks through Python, which is dramatically
    faster for large DLLs while keeping the event loop non-blocking.

    Args:
        src: Source file path
        dst: Destination file path
        chunk_size: Unused; retained for signature/backwards compatibility.
    """
    await anyio.to_thread.run_sync(shutil.copy2, src, dst, limiter=thread_io)


def record_backup_metadata_sync(dll_path: Path, backup_path: Path) -> int | None:
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


def record_post_update_version_sync(dll_path: Path, post_update_version: str) -> None:
    """Record post-update version on the most recent active backup for this DLL.

    Use this when calling from sync code running in a thread pool. Silently no-ops
    if no active backup exists for the DLL (update without backup).

    Args:
        dll_path: Path to the DLL that was just updated
        post_update_version: The version the DLL was updated TO
    """
    if not post_update_version:
        return
    try:
        db_manager._record_post_update_version(str(dll_path), post_update_version)
    except Exception as e:
        logger.error(f"Error recording post-update version for {dll_path}: {e}", exc_info=True)


async def record_backup_metadata(dll_path: Path, backup_path: Path) -> int | None:
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
            stat_result = await anyio.to_thread.run_sync(backup_path.stat, limiter=thread_io)
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


async def restore_dll_from_backup(backup_id: int) -> tuple[bool, str]:
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
                await anyio.to_thread.run_sync(os.chmod, dll_path, stat.S_IWRITE | stat.S_IREAD, limiter=thread_io)

            # Copy backup to DLL location (async to avoid blocking event loop)
            await async_copy2(backup_path, dll_path)

            # Verify restore succeeded
            if not dll_path.exists():
                raise Exception("DLL file not found after restore")

            # Update database with new version (use async version to avoid blocking)
            from dlss_updater.updater import get_dll_version_async
            new_version = await get_dll_version_async(dll_path)
            await db_manager.update_game_dll_version(game_dll.id, new_version)

            # Mark backup as restored (removes it from Backups page AND flags
            # it as a user-initiated rollback for compatibility detection)
            await db_manager.mark_backup_restored(backup_id)

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


async def delete_backup(backup_id: int) -> tuple[bool, str]:
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
                await anyio.to_thread.run_sync(os.chmod, backup_path, stat.S_IWRITE, limiter=thread_io)
                await anyio.to_thread.run_sync(backup_path.unlink, limiter=thread_io)
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


async def validate_backup(backup_id: int) -> tuple[bool, str]:
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
        is_readable = await anyio.to_thread.run_sync(os.access, backup_path, os.R_OK, limiter=thread_io)
        if not is_readable:
            return False, "Backup file is not readable"

        # Check file size matches (run in thread pool to avoid blocking)
        stat_result = await anyio.to_thread.run_sync(backup_path.stat, limiter=thread_io)
        actual_size = stat_result.st_size
        if actual_size != backup.backup_size:
            logger.warning(f"Backup file size mismatch: expected {backup.backup_size}, got {actual_size}")
            return False, f"Backup file may be corrupted (size mismatch)"

        return True, "Backup is valid"

    except Exception as e:
        logger.error(f"Error validating backup: {e}", exc_info=True)
        return False, f"Error validating backup: {str(e)}"


async def validate_backups_batch(
    backup_ids: list[int],
    max_concurrent: int = None
) -> dict[int, tuple[bool, str]]:
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

    # Maximum concurrency for backup validation (async file I/O scales extremely well).
    # Use the shared io_heavy limiter for the default bound; honour an explicit
    # override by creating a one-off CapacityLimiter sized to the caller's request.
    limiter = io_heavy if max_concurrent is None else anyio.CapacityLimiter(max_concurrent)

    results: dict[int, tuple[bool, str]] = {}

    async def bounded_validate(backup_id: int) -> None:
        async with limiter:
            results[backup_id] = await validate_backup(backup_id)

    # Run all validations with bounded concurrency
    async with anyio.create_task_group() as tg:
        for bid in backup_ids:
            tg.start_soon(bounded_validate, bid)

    return results


async def restore_group_for_game(
    game_id: int,
    group: str,  # "all" or specific group name like "DLSS"
) -> tuple[bool, str, list[dict[str, str]]]:
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

        # Restore with bounded concurrency using the shared io_heavy limiter.
        # This prevents overwhelming the filesystem while still achieving parallelism.
        # Results are stored by index into a pre-sized list to preserve ordering.
        results: list[dict[str, str]] = [None] * len(backups_to_restore)

        async def bounded_restore(index: int, backup) -> None:
            """Perform a single restore operation with limiter-bounded concurrency."""
            async with io_heavy:
                success, message = await restore_dll_from_backup(backup.id)
                results[index] = {
                    'dll_filename': backup.dll_filename,
                    'success': str(success),  # Convert to string for consistent Dict[str, str]
                    'message': message
                }

        # Execute all restore operations concurrently
        async with anyio.create_task_group() as tg:
            for index, backup in enumerate(backups_to_restore):
                tg.start_soon(bounded_restore, index, backup)

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
