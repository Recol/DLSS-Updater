"""
Backup Manager for DLSS Updater
Enhanced backup creation and restoration with database integration
"""

import shutil
import os
import stat
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from dlss_updater.logger import setup_logger
from dlss_updater.database import db_manager

logger = setup_logger()


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

        # Get DLL version (lazy import to avoid circular dependency)
        from dlss_updater.updater import get_dll_version
        version = get_dll_version(dll_path)

        # Get backup file size
        backup_size = backup_path.stat().st_size if backup_path.exists() else 0

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

        # Check if file is in use (lazy import to avoid circular dependency)
        from dlss_updater.updater import is_file_in_use
        if is_file_in_use(str(dll_path)):
            return False, "DLL is currently in use. Please close the game first."

        # Create temporary backup of current DLL (for rollback)
        temp_backup = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.dll') as tf:
                temp_backup = Path(tf.name)
                shutil.copy2(dll_path, temp_backup)
                logger.info(f"Created temporary backup: {temp_backup}")

        except Exception as e:
            logger.error(f"Failed to create temporary backup: {e}")
            return False, f"Failed to create safety backup: {e}"

        # Perform restore
        try:
            # Remove read-only attribute if present
            if dll_path.exists():
                os.chmod(dll_path, stat.S_IWRITE | stat.S_IREAD)

            # Copy backup to DLL location
            shutil.copy2(backup_path, dll_path)

            # Verify restore succeeded
            if not dll_path.exists():
                raise Exception("DLL file not found after restore")

            # Update database with new version (lazy import to avoid circular dependency)
            from dlss_updater.updater import get_dll_version
            new_version = get_dll_version(dll_path)
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
                # Remove read-only attribute if present
                os.chmod(backup_path, stat.S_IWRITE)
                backup_path.unlink()
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

        # Check if file is readable
        if not os.access(backup_path, os.R_OK):
            return False, "Backup file is not readable"

        # Check file size matches
        actual_size = backup_path.stat().st_size
        if actual_size != backup.backup_size:
            logger.warning(f"Backup file size mismatch: expected {backup.backup_size}, got {actual_size}")
            return False, f"Backup file may be corrupted (size mismatch)"

        return True, "Backup is valid"

    except Exception as e:
        logger.error(f"Error validating backup: {e}", exc_info=True)
        return False, f"Error validating backup: {str(e)}"
