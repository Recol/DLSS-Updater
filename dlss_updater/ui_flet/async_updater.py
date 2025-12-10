"""
Async Update Coordinator
Bridges Flet UI with existing scanner/updater modules using async patterns
"""

import asyncio
import logging
from typing import Callable, Optional, Dict, List

from dlss_updater.scanner import find_all_dlls
from dlss_updater.utils import update_dlss_versions
from dlss_updater.config import config_manager, get_current_settings
from dlss_updater.models import UpdateProgress, UpdateResult


class AsyncUpdateCoordinator:
    """
    Coordinates async update operations between UI and core business logic
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._progress_callback: Optional[Callable] = None
        self._cancel_requested = False

    async def scan_for_games(
        self,
        progress_callback: Optional[Callable[[UpdateProgress], None]] = None
    ) -> Dict[str, List]:
        """
        Scan all configured launchers for games with DLLs

        Args:
            progress_callback: Optional callback for progress updates

        Returns:
            Dictionary of launcher -> list of DLL paths
        """
        self.logger.info("Starting game scan...")
        self._progress_callback = progress_callback

        # Get current settings
        settings = get_current_settings()
        self.logger.info(f"Scan settings: {settings}")

        # Run scanner (already async)
        try:
            # Create progress wrapper that converts (current, total, msg) to UpdateProgress
            async def scanner_progress_wrapper(current, total, message):
                if self._progress_callback:
                    percentage = int((current / total * 100)) if total > 0 else 0
                    await self._progress_callback(UpdateProgress(
                        current=int(current),
                        total=int(total),
                        message=message,
                        percentage=percentage
                    ))

            # find_all_dlls is already async and now accepts progress_callback
            dll_dict = await find_all_dlls(progress_callback=scanner_progress_wrapper)

            # Count total games found
            total_games = sum(len(dlls) for dlls in dll_dict.values())
            self.logger.info(f"Scan complete: {total_games} games found")

            return dll_dict

        except Exception as e:
            self.logger.error(f"Scan failed: {e}", exc_info=True)
            raise

    async def update_games(
        self,
        dll_dict: Dict[str, List],
        progress_callback: Optional[Callable[[UpdateProgress], None]] = None
    ) -> UpdateResult:
        """
        Update DLLs for discovered games

        Args:
            dll_dict: Dictionary of DLL paths from scanner
            progress_callback: Optional callback for progress updates

        Returns:
            UpdateResult with details of what was updated
        """
        self.logger.info("Starting DLL updates...")
        self._progress_callback = progress_callback
        self._cancel_requested = False

        # Get current settings
        settings = get_current_settings()
        backup_enabled = config_manager.get_backup_preference()

        # Count total DLLs to process
        total_dlls = sum(len(dlls) for dlls in dll_dict.values())
        self.logger.info(f"Processing {total_dlls} DLLs...")

        # Report initial progress
        if self._progress_callback:
            await self._progress_callback(UpdateProgress(
                current=0,
                total=total_dlls,
                message="Starting updates...",
                percentage=0
            ))

        # Run updater (now fully async, no thread pool needed)
        try:
            # Create progress wrapper that calls our async callback
            processed_count = 0
            # Store pending progress tasks to ensure they complete
            _pending_progress_tasks = []

            def sync_progress_callback(current, total, message):
                """Synchronous progress callback for compatibility"""
                nonlocal processed_count, _pending_progress_tasks
                processed_count = current
                percentage = int((current / total * 100)) if total > 0 else 0

                # Call async callback directly (we're in async context now)
                if self._progress_callback:
                    # Schedule as a task and track it for completion
                    task = asyncio.create_task(self._progress_callback(UpdateProgress(
                        current=current,
                        total=total,
                        message=message,
                        percentage=percentage
                    )))
                    _pending_progress_tasks.append(task)
                    # Clean up completed tasks to avoid memory growth
                    _pending_progress_tasks[:] = [t for t in _pending_progress_tasks if not t.done()]

            # Call async update_dlss_versions directly (no thread pool)
            result = await update_dlss_versions(
                dll_dict,
                settings,
                sync_progress_callback
            )

            # Parse result (update_dlss_versions returns dict with results)
            updated_games = result.get("updated_games", [])
            skipped_games = result.get("skipped_games", [])
            errors = result.get("errors", [])

            self.logger.info(
                f"Update complete: {len(updated_games)} updated, "
                f"{len(skipped_games)} skipped, {len(errors)} errors"
            )

            # Report completion
            if self._progress_callback:
                await self._progress_callback(UpdateProgress(
                    current=total_dlls,
                    total=total_dlls,
                    message="Update complete",
                    percentage=100
                ))

            return UpdateResult(
                updated_games=updated_games,
                skipped_games=skipped_games,
                errors=errors,
                backup_created=backup_enabled,
                total_processed=total_dlls
            )

        except Exception as e:
            self.logger.error(f"Update failed: {e}", exc_info=True)
            raise

    async def scan_and_update(
        self,
        progress_callback: Optional[Callable[[UpdateProgress], None]] = None
    ) -> UpdateResult:
        """
        Convenience method: scan then update in one operation

        Args:
            progress_callback: Optional callback for progress updates

        Returns:
            UpdateResult with details of what was updated
        """
        self.logger.info("Starting scan and update operation...")

        # Phase 1: Scan
        if progress_callback:
            await progress_callback(UpdateProgress(
                current=0,
                total=100,
                message="Scanning for games...",
                percentage=0
            ))

        dll_dict = await self.scan_for_games(progress_callback)

        # Check if any games found
        total_games = sum(len(dlls) for dlls in dll_dict.values())
        if total_games == 0:
            self.logger.warning("No games found to update")
            return UpdateResult(
                updated_games=[],
                skipped_games=[],
                errors=[],
                backup_created=False,
                total_processed=0
            )

        # Phase 2: Update
        if progress_callback:
            await progress_callback(UpdateProgress(
                current=0,
                total=100,
                message=f"Updating {total_games} games...",
                percentage=0
            ))

        result = await self.update_games(dll_dict, progress_callback)

        return result

    def cancel(self):
        """Request cancellation of current operation"""
        self.logger.info("Update cancellation requested")
        self._cancel_requested = True
