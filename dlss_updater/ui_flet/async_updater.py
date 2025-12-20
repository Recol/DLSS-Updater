"""
Async Update Coordinator
Bridges Flet UI with existing scanner/updater modules using async patterns
"""

import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional, Dict, List, Any

from dlss_updater.scanner import find_all_dlls
from dlss_updater.utils import update_dlss_versions, process_single_dll
from dlss_updater.config import config_manager, get_current_settings
from dlss_updater.models import UpdateProgress, UpdateResult
from dlss_updater.database import db_manager


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

    async def update_single_game(
        self,
        game_id: int,
        game_name: str,
        dll_groups: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[UpdateProgress], None]] = None
    ) -> Dict[str, Any]:
        """
        Update DLLs for a single game

        Args:
            game_id: Database ID of the game to update
            game_name: Name of the game (for logging)
            dll_groups: Optional list of DLL groups to update (e.g., ["DLSS", "XeSS"]).
                       If None, updates all DLLs.
            progress_callback: Optional callback for progress updates

        Returns:
            Dict with 'updated', 'skipped', 'errors' lists and 'success' bool
        """
        groups_str = ", ".join(dll_groups) if dll_groups else "all"
        self.logger.info(f"Starting single-game update for: {game_name} (id: {game_id}, groups: {groups_str})")
        self._progress_callback = progress_callback

        results: Dict[str, Any] = {
            'updated': [],
            'skipped': [],
            'errors': [],
            'success': False
        }

        try:
            # Get DLLs for this game from database
            game_dlls = await db_manager.get_dlls_for_game(game_id)

            if not game_dlls:
                self.logger.warning(f"No DLLs found for game: {game_name}")
                results['errors'].append({
                    'message': 'No DLLs found for this game',
                    'dll_type': None
                })
                return results

            # Filter DLLs by selected groups if specified
            if dll_groups:
                from dlss_updater.constants import DLL_GROUPS

                filtered_dlls = []
                for game_dll in game_dlls:
                    dll_filename = game_dll.dll_filename.lower()
                    for group in dll_groups:
                        if group in DLL_GROUPS:
                            group_dll_names = [d.lower() for d in DLL_GROUPS[group]]
                            if dll_filename in group_dll_names:
                                filtered_dlls.append(game_dll)
                                break
                game_dlls = filtered_dlls

                if not game_dlls:
                    self.logger.warning(f"No DLLs matching selected groups for game: {game_name}")
                    results['skipped'].append({
                        'dll_type': 'Selected groups',
                        'dll_path': '',
                        'reason': f'No DLLs found matching groups: {", ".join(dll_groups)}'
                    })
                    return results

            total_dlls = len(game_dlls)
            processed = 0

            # Report initial progress
            if progress_callback:
                await progress_callback(UpdateProgress(
                    current=0,
                    total=total_dlls,
                    message=f"Preparing to update {total_dlls} DLL(s)...",
                    percentage=0
                ))

            # Process each DLL
            for game_dll in game_dlls:
                dll_path = Path(game_dll.dll_path)

                # Report progress for current DLL
                if progress_callback:
                    await progress_callback(UpdateProgress(
                        current=processed,
                        total=total_dlls,
                        message=f"Updating {game_dll.dll_type}...",
                        percentage=int((processed / total_dlls) * 100) if total_dlls > 0 else 0
                    ))

                try:
                    # Use existing process_single_dll which handles all update logic
                    # Pass game's launcher as the second parameter
                    result = await process_single_dll(dll_path, "Single Game Update")

                    if result and result.success:
                        results['updated'].append({
                            'dll_type': game_dll.dll_type,
                            'dll_path': str(dll_path),
                            'backup_path': getattr(result, 'backup_path', None)
                        })
                        # Update version in database
                        from dlss_updater.updater import get_dll_version
                        new_version = await asyncio.to_thread(get_dll_version, dll_path)
                        if new_version:
                            await db_manager.update_game_dll_version(game_dll.id, new_version)
                    elif result is None:
                        # Warframe or other skipped game
                        results['skipped'].append({
                            'dll_type': game_dll.dll_type,
                            'dll_path': str(dll_path),
                            'reason': 'Game is in skip list'
                        })
                    else:
                        # DLL was not updated (already up-to-date or disabled)
                        reason = getattr(result, 'dll_type', 'Already up-to-date or update disabled')
                        results['skipped'].append({
                            'dll_type': game_dll.dll_type,
                            'dll_path': str(dll_path),
                            'reason': reason
                        })

                except Exception as e:
                    self.logger.error(f"Error updating {dll_path}: {e}")
                    results['errors'].append({
                        'dll_type': game_dll.dll_type,
                        'dll_path': str(dll_path),
                        'message': str(e)
                    })

                processed += 1

            # Final progress
            if progress_callback:
                await progress_callback(UpdateProgress(
                    current=total_dlls,
                    total=total_dlls,
                    message="Update complete",
                    percentage=100
                ))

            results['success'] = len(results['updated']) > 0
            self.logger.info(
                f"Single-game update complete for {game_name}: "
                f"{len(results['updated'])} updated, "
                f"{len(results['skipped'])} skipped, "
                f"{len(results['errors'])} errors"
            )

            return results

        except Exception as e:
            self.logger.error(f"Single-game update failed for {game_name}: {e}", exc_info=True)
            results['errors'].append({'message': str(e), 'dll_type': None})
            return results

    def cancel(self):
        """Request cancellation of current operation"""
        self.logger.info("Update cancellation requested")
        self._cancel_requested = True
