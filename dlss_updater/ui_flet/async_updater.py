"""
Async Update Coordinator
Bridges Flet UI with existing scanner/updater modules using async patterns
"""

import asyncio
import logging
from pathlib import Path
from typing import Callable, Any

import anyio

from dlss_updater.concurrency_limiters import thread_io
from dlss_updater.scanner import find_all_dlls
from dlss_updater.utils import update_dlss_versions, process_single_dll, extract_game_name, find_game_root
from dlss_updater.config import config_manager, get_current_settings
from dlss_updater.models import UpdateProgress, UpdateResult
from dlss_updater.database import db_manager


class AsyncUpdateCoordinator:
    """
    Coordinates async update operations between UI and core business logic
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._progress_callback: Callable | None = None
        self._cancel_requested = False
        # Reflects the outcome of the most recent run so the UI can render a
        # cancellation summary through the normal completion path. cancel_processed
        # / cancel_total count cancel_unit items ("games" for the standard path,
        # "DLLs" for the high-performance pipeline) for the last batch update run.
        self.was_cancelled = False
        self.cancel_processed = 0
        self.cancel_total = 0
        self.cancel_unit = "games"

    async def scan_for_games(
        self,
        progress_callback: Callable[[UpdateProgress], None] | None = None
    ) -> dict[str, list]:
        """
        Scan all configured launchers for games with DLLs

        Args:
            progress_callback: Optional callback for progress updates

        Returns:
            Dictionary of launcher -> list of DLL paths
        """
        self.logger.info("Starting game scan...")
        self._progress_callback = progress_callback
        self._cancel_requested = False
        self.was_cancelled = False

        # Get current settings
        settings = get_current_settings()
        self.logger.info(f"Scan settings: {settings}")

        # Run scanner (already async)
        try:
            # Create progress wrapper that converts (current, total, msg) to UpdateProgress
            async def scanner_progress_wrapper(current, total, message):
                if self._progress_callback:
                    raw_percentage = int((current / total * 100)) if total > 0 else 0
                    percentage = max(0, min(100, raw_percentage))  # Clamp to [0, 100]
                    await self._progress_callback(UpdateProgress(
                        current=int(current),
                        total=int(total),
                        message=message,
                        percentage=percentage
                    ))

            # find_all_dlls is already async and now accepts progress_callback
            dll_dict = await find_all_dlls(progress_callback=scanner_progress_wrapper)

            # The scanner fans out through scanner.py, which we must not modify;
            # its internal launcher/DB phases can't be interrupted from here, so a
            # cancel requested mid-scan is honoured at this phase boundary — the
            # results are discarded and callers skip any follow-on work.
            if self._cancel_requested:
                self.was_cancelled = True
                self.logger.info("Scan cancelled by user")
                return {}

            # Count total games found
            total_games = sum(len(dlls) for dlls in dll_dict.values())
            self.logger.info(f"Scan complete: {total_games} games found")

            return dll_dict

        except Exception as e:
            self.logger.error(f"Scan failed: {e}", exc_info=True)
            raise

    async def update_games(
        self,
        dll_dict: dict[str, list],
        progress_callback: Callable[[UpdateProgress], None] | None = None
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
        self.was_cancelled = False
        self.cancel_processed = 0
        self.cancel_total = 0
        self.cancel_unit = "games"

        # Filter out DLLs belonging to personally-ignored games
        dll_dict = await self._filter_ignored_games(dll_dict)

        # Get current settings
        settings = get_current_settings()
        backup_enabled = config_manager.get_backup_preference()

        # The high-performance pipeline is the only batch update path (the
        # opt-out setting was removed); the standard per-game loop below is
        # kept solely as an automatic fallback if the pipeline raises.
        try:
            from ..high_performance_updater import HighPerformanceUpdateManager, DLLTask
            from ..config import LATEST_DLL_PATHS

            self.logger.info("Using high-performance update mode")
            manager = HighPerformanceUpdateManager()

            # Build task list from dll_dict with proper DLLTask objects
            dll_tasks = []
            for launcher, dll_paths in dll_dict.items():
                for dll_path in dll_paths:
                    path_obj = Path(dll_path)
                    dll_name = path_obj.name.lower()
                    # Only create task if we have a source DLL for this
                    if dll_name in LATEST_DLL_PATHS and LATEST_DLL_PATHS[dll_name]:
                        dll_tasks.append(DLLTask(
                            target_path=str(dll_path),
                            source_dll_name=dll_name,
                            game_name=extract_game_name(dll_path, launcher),
                        ))

            # Create progress wrapper to convert (int, int, str) to UpdateProgress
            async def hp_progress_wrapper(current: int, total: int, message: str):
                if self._progress_callback:
                    raw_percentage = int((current / total * 100)) if total > 0 else 0
                    percentage = max(0, min(100, raw_percentage))  # Clamp to [0, 100]
                    await self._progress_callback(UpdateProgress(
                        current=current,
                        total=total,
                        message=message,
                        percentage=percentage
                    ))

            # Honour a cancel that arrived during the pre-filter phase
            # before kicking off the pipeline.
            if self._cancel_requested:
                self.was_cancelled = True
                self.logger.info("Update cancelled before high-performance run started")
                return UpdateResult(
                    updated_games=[],
                    skipped_games=[],
                    errors=[],
                    backup_created=False,
                    total_processed=0,
                )

            # Execute high-performance pipeline. The manager polls the
            # cancel flag at phase boundaries and before each queued DLL
            # write, so a mid-run cancel skips remaining DLLs without ever
            # interrupting an in-flight copy.
            result = await manager.execute(
                dll_tasks,
                settings,
                hp_progress_wrapper,
                cancel_check=lambda: self._cancel_requested,
            )

            # Log if fallback was used
            if result.mode_used == "fallback":
                self.logger.warning("Fell back to standard mode due to memory pressure")

            if result.was_cancelled:
                # DLLs skipped purely by the cancel don't count as processed.
                cancelled_dlls = sum(
                    1 for d in result.detailed_skipped
                    if d.get("reason") == "Cancelled by user"
                )
                self.was_cancelled = True
                self.cancel_unit = "DLLs"
                self.cancel_total = len(dll_tasks)
                self.cancel_processed = max(
                    0,
                    result.updates_succeeded + result.updates_failed
                    + result.updates_skipped - cancelled_dlls,
                )
                self.logger.info(
                    f"Update cancelled: processed {self.cancel_processed} of "
                    f"{self.cancel_total} DLLs"
                )

            # Convert detailed results to expected format
            updated_games = []
            for detail in result.detailed_updates:
                game_name = detail.get("game_name", "Unknown Game")
                dll_name = detail.get("dll_name", "")
                old_ver = detail.get("old_version", "?")
                new_ver = detail.get("new_version", "?")
                updated_games.append(f"{game_name} ({dll_name}: {old_ver} → {new_ver})")

            skipped_games = []
            for detail in result.detailed_skipped:
                game_name = detail.get("game_name", "Unknown Game")
                dll_name = detail.get("dll_name", "")
                reason = detail.get("reason", "Already up-to-date")
                skipped_games.append(f"{game_name} ({dll_name}: {reason})")

            return UpdateResult(
                updated_games=updated_games,
                skipped_games=skipped_games,
                errors=result.errors,
                backup_created=result.backups_created > 0,
                total_processed=result.updates_succeeded + result.updates_skipped + len(result.errors)
            )
        except Exception as e:
            self.logger.error(f"High-performance update failed, falling back to standard: {e}")
            # Fall through to standard mode

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

        # Group the flat launcher->DLL mapping into per-game batches so the cancel
        # flag can be honoured between games. The heavy per-DLL work lives in
        # utils.update_dlss_versions (which we must not modify) and processes a
        # batch in a single parallel task group, so a game's own DLLs still update
        # concurrently — cancellation just takes effect at the next game boundary,
        # never mid-file-copy.
        game_groups: list[tuple[str, str, list]] = []  # (game_name, launcher, dll_paths)
        group_index: dict[tuple[str, str], int] = {}
        for launcher, dll_paths in dll_dict.items():
            for dll_path in dll_paths:
                game_root = find_game_root(Path(dll_path), launcher)
                key = (launcher, str(game_root))
                idx = group_index.get(key)
                if idx is None:
                    group_index[key] = len(game_groups)
                    game_groups.append((game_root.name, launcher, [dll_path]))
                else:
                    game_groups[idx][2].append(dll_path)

        self.cancel_total = len(game_groups)

        # Run updater (now fully async, no thread pool needed)
        try:
            # Use a bounded deque for O(1) cleanup - only keep last N pending tasks
            # This prevents memory leak with O(n²) list comprehension on every callback
            from collections import deque
            _pending_progress_tasks: deque[asyncio.Task] = deque(maxlen=32)

            raw_updated: list = []
            raw_skipped: list = []
            errors: list = []
            processed_dlls = 0
            processed_games = 0
            cancelled = False

            def make_progress_callback(base_offset: int, name: str):
                """Build a per-game callback mapping a game's local (current/total)
                onto the run's global progress."""
                def sync_progress_callback(current, total, message):
                    global_current = base_offset + current
                    raw_percentage = int((global_current / total_dlls * 100)) if total_dlls > 0 else 0
                    percentage = max(0, min(100, raw_percentage))  # Clamp to [0, 100]
                    if self._progress_callback:
                        # Schedule as a task - deque automatically evicts old tasks (maxlen=32)
                        task = asyncio.create_task(self._progress_callback(UpdateProgress(
                            current=global_current,
                            total=total_dlls,
                            message=f"Updating {name}...",
                            percentage=percentage
                        )))
                        _pending_progress_tasks.append(task)
                return sync_progress_callback

            for game_name, launcher, dll_paths in game_groups:
                # Checkpoint between games — never inside a game's parallel batch,
                # so no half-written DLLs result.
                if self._cancel_requested:
                    cancelled = True
                    break

                # Call async update_dlss_versions per game (no thread pool)
                result = await update_dlss_versions(
                    {launcher: dll_paths},
                    settings,
                    make_progress_callback(processed_dlls, game_name)
                )

                raw_updated.extend(result.get("updated_games", []))
                raw_skipped.extend(result.get("skipped_games", []))
                errors.extend(result.get("errors", []))
                processed_dlls += len(dll_paths)
                processed_games += 1

            # Await any remaining pending progress tasks to ensure completion
            if _pending_progress_tasks:
                async def _drain(t: asyncio.Task) -> None:
                    try:
                        await t
                    except Exception:
                        pass

                async with anyio.create_task_group() as tg:
                    for t in _pending_progress_tasks:
                        tg.start_soon(_drain, t)

            # Parse aggregated results (update_dlss_versions returns dict with results)
            # updated_games: list of (dll_path, launcher, dll_type) tuples
            # skipped_games: list of (dll_path, launcher, reason, dll_type) tuples
            updated_games = [
                f"{extract_game_name(dll_path, launcher)} ({dll_type})"
                for dll_path, launcher, dll_type in raw_updated
            ]
            skipped_games = [
                f"{extract_game_name(dll_path, launcher)} ({dll_type}: {reason})"
                for dll_path, launcher, reason, dll_type in raw_skipped
            ]

            self.was_cancelled = cancelled
            self.cancel_processed = processed_games

            if cancelled:
                self.logger.info(
                    f"Update cancelled: processed {processed_games} of "
                    f"{len(game_groups)} games "
                    f"({len(updated_games)} updated, {len(skipped_games)} skipped)"
                )
            else:
                self.logger.info(
                    f"Update complete: {len(updated_games)} updated, "
                    f"{len(skipped_games)} skipped, {len(errors)} errors"
                )

            # Report completion / cancellation
            if self._progress_callback:
                await self._progress_callback(UpdateProgress(
                    current=processed_dlls,
                    total=total_dlls,
                    message="Update cancelled" if cancelled else "Update complete",
                    percentage=int((processed_dlls / total_dlls) * 100) if total_dlls > 0 else 100
                ))

            return UpdateResult(
                updated_games=updated_games,
                skipped_games=skipped_games,
                errors=errors,
                backup_created=backup_enabled,
                total_processed=processed_dlls
            )

        except Exception as e:
            self.logger.error(f"Update failed: {e}", exc_info=True)
            raise

    async def _filter_ignored_games(self, dll_dict: dict[str, list]) -> dict[str, list]:
        """Remove DLL paths belonging to personally-ignored games from the update set."""
        ignored_ids = await anyio.to_thread.run_sync(
            db_manager.batch_get_ignored_game_ids_sync, limiter=thread_io
        )
        if not ignored_ids:
            return dll_dict

        all_paths = []
        for paths in dll_dict.values():
            all_paths.extend(str(p) for p in paths)

        path_to_game_id = await anyio.to_thread.run_sync(
            db_manager.batch_get_game_ids_for_dll_paths_sync, all_paths, limiter=thread_io
        )

        filtered = {}
        skipped_count = 0
        for launcher, paths in dll_dict.items():
            kept = []
            for p in paths:
                game_id = path_to_game_id.get(str(p).lower())
                if game_id and game_id in ignored_ids:
                    skipped_count += 1
                    self.logger.info(f"Skipping ignored game (id={game_id}): {p}")
                else:
                    kept.append(p)
            if kept:
                filtered[launcher] = kept

        if skipped_count:
            self.logger.info(f"Filtered {skipped_count} DLLs from ignored games")

        return filtered

    async def update_single_game(
        self,
        game_id: int,
        game_name: str,
        dll_groups: list[str] | None = None,
        progress_callback: Callable[[UpdateProgress], None] | None = None,
        skip_dll_filenames: set[str] | None = None,
    ) -> dict[str, Any]:
        """
        Update DLLs for a single game

        Args:
            game_id: Database ID of the game to update
            game_name: Name of the game (for logging)
            dll_groups: Optional list of DLL groups to update (e.g., ["DLSS", "XeSS"]).
                       If None, updates all DLLs.
            progress_callback: Optional callback for progress updates
            skip_dll_filenames: Optional set of DLL filenames (lowercase) to skip —
                       used by rollback compatibility warning to skip user-flagged versions.

        Returns:
            Dict with 'updated', 'skipped', 'errors' lists and 'success' bool
        """
        groups_str = ", ".join(dll_groups) if dll_groups else "all"
        self.logger.info(f"Starting single-game update for: {game_name} (id: {game_id}, groups: {groups_str})")
        self._progress_callback = progress_callback
        self._cancel_requested = False
        self.was_cancelled = False

        results: dict[str, Any] = {
            'updated': [],
            'skipped': [],
            'errors': [],
            'success': False
        }

        try:
            # Check if game is in personal ignore list
            if await db_manager.is_game_ignored(game_id):
                self.logger.info(f"Game '{game_name}' (id={game_id}) is in personal ignore list, skipping")
                results['skipped'].append({
                    'dll_type': 'All',
                    'dll_path': '',
                    'reason': 'Game is in your personal ignore list'
                })
                return results

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

            # Apply rollback-compat skip filter (user chose "Skip flagged DLLs")
            if skip_dll_filenames:
                kept: list = []
                for game_dll in game_dlls:
                    fname_lower = (game_dll.dll_filename or "").lower()
                    if fname_lower in skip_dll_filenames:
                        self.logger.info(
                            f"Skipping flagged DLL {game_dll.dll_filename} for {game_name} (rollback compat)"
                        )
                        results['skipped'].append({
                            'dll_type': game_dll.dll_type,
                            'dll_path': str(game_dll.dll_path),
                            'reason': 'Skipped — flagged by rollback compatibility check'
                        })
                    else:
                        kept.append(game_dll)
                game_dlls = kept
                if not game_dlls:
                    self.logger.info(f"All DLLs flagged and skipped for {game_name}")
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
                # Checkpoint between DLLs — never mid-copy, so no half-written
                # DLLs result from a cancellation.
                if self._cancel_requested:
                    self.was_cancelled = True
                    self.logger.info(
                        f"Single-game update cancelled for {game_name} after "
                        f"{processed} of {total_dlls} DLL(s)"
                    )
                    break

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
                        new_version = await anyio.to_thread.run_sync(
                            get_dll_version, dll_path, limiter=thread_io
                        )
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

            # FSR 4 group install. Runs after the per-DLL pass because it is a
            # different operation: it ADDS the effect DLLs an FSR 3.1 game never
            # shipped and installs the loader under the monolith's name. Gated on
            # RDNA 3/4 hardware and applied all-or-nothing (see fsr4_installer).
            if not self._cancel_requested:
                await self._try_fsr4_upgrade(
                    game_id, game_name, dll_groups, skip_dll_filenames, results
                )

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

    async def _try_fsr4_upgrade(
        self,
        game_id: int,
        game_name: str,
        dll_groups: list[str] | None,
        skip_dll_filenames: set[str] | None,
        results: dict,
    ) -> None:
        """
        Offer the FSR 4 DLL set to a game that only has the FidelityFX monolith.

        Silent and harmless when it doesn't apply: no FidelityFX game, no capable
        GPU, FSR disabled in preferences, or the DLLs aren't cached yet. Failures
        are reported but never abort the surrounding update, which has already
        succeeded for everything else.
        """
        try:
            from dlss_updater.config import LATEST_DLL_PATHS, config_manager
            from dlss_updater.fsr4_installer import (
                FSR4_ANCHOR_DLL,
                apply_fsr4_upgrade,
                plan_fsr4_upgrade,
            )

            if dll_groups and "FSR" not in dll_groups:
                return
            if not config_manager.get_update_preference("FSR"):
                return
            if skip_dll_filenames and FSR4_ANCHOR_DLL in skip_dll_filenames:
                return

            # Locate the folder(s) holding this game's FidelityFX monolith.
            game_dlls = await db_manager.get_dlls_for_game(game_id)
            game_dirs = {
                str(Path(d.dll_path).parent)
                for d in game_dlls
                if (d.dll_filename or "").lower() == FSR4_ANCHOR_DLL
            }
            if not game_dirs:
                return

            for game_dir in sorted(game_dirs):
                plan = plan_fsr4_upgrade(game_dir, dict(LATEST_DLL_PATHS))
                if not plan.is_actionable:
                    self.logger.info(f"FSR 4 skipped for {game_name}: {plan.skipped_reason}")
                    continue
                if plan.added_count == 0:
                    # Already an SDK 2.x layout; the per-DLL pass handled it.
                    continue

                success, message, _ = await apply_fsr4_upgrade(plan, game_id)
                if success:
                    results['updated'].append({
                        'dll_type': f"FSR 4 upgrade ({plan.added_count} DLL(s) added)",
                        'dll_path': game_dir,
                        'backup_path': None,
                    })
                    self.logger.info(f"FSR 4 upgrade applied to {game_name}: {message}")
                else:
                    results['errors'].append({
                        'dll_type': 'FSR 4 upgrade',
                        'dll_path': game_dir,
                        'message': message,
                    })
        except Exception as e:
            # Never let this optional step fail an otherwise-successful update.
            self.logger.error(f"FSR 4 upgrade check failed for {game_name}: {e}", exc_info=True)

    def cancel(self):
        """Request cancellation of current operation"""
        self.logger.info("Update cancellation requested")
        self._cancel_requested = True
