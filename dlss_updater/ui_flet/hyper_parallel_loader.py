"""
Hyper-Parallel Data Loading for UI Views

Runs multiple blocking (sync) I/O operations — database queries, image fetches,
file stats — truly in parallel on separate OS worker threads, then collects the
results keyed by task id.

Implementation (anyio structured concurrency)
---------------------------------------------
Historically this module maintained its own ``ThreadPoolExecutor`` +
``as_completed()`` because a naive ``asyncio.to_thread()`` fan-out serialised
work through the event loop's single default executor. That workaround is no
longer needed: the whole codebase now dispatches thread work through
``anyio.to_thread.run_sync(func, limiter=thread_io)``. Several such calls awaited
concurrently inside an ``anyio.create_task_group()`` genuinely run on distinct
OS worker threads, up to the shared ``thread_io`` capacity limiter's cap — the
same true parallelism the old ThreadPoolExecutor provided, but consolidated onto
the one shared limiter instead of a second competing thread pool.

Usage:
    loader = HyperParallelLoader()
    results = await loader.load_all([
        LoadTask("dlls", lambda: db.batch_get_dlls_for_games_sync(game_ids)),
        LoadTask("backups", lambda: db.batch_get_backups_grouped_sync(game_ids)),
        LoadTask("images", lambda: db.batch_get_cached_image_paths(app_ids)),
    ])

    dlls = results.get("dlls", {})
    backups = results.get("backups", {})
    images = results.get("images", {})
"""

from dataclasses import dataclass
from typing import Callable, Any
import threading
import time

import anyio

from dlss_updater.concurrency_limiters import thread_io
from dlss_updater.logger import setup_logger

logger = setup_logger()


@dataclass
class LoadTask:
    """A single task for parallel loading."""
    id: str
    work_fn: Callable[[], Any]


class HyperParallelLoader:
    """
    Coordinator for hyper-parallel data loading.

    Dispatches each LoadTask's blocking ``work_fn`` onto a real OS worker thread
    via ``anyio.to_thread.run_sync(..., limiter=thread_io)`` inside a single
    ``anyio.create_task_group()``, so all tasks run concurrently on separate
    threads (bounded by the shared ``thread_io`` capacity limiter).

    Supports cooperative cancellation via ``threading.Event`` for responsive UI
    shutdown: tasks that have not yet started are skipped, and an in-progress
    fan-out that hits its timeout is abandoned (the underlying thread runs to
    completion in the background, exactly as the previous implementation did).

    Performance characteristics:
    - Threads are reused from anyio's worker-thread pool (no per-call spawn).
    - Concurrency is bounded by ``thread_io`` (shared app-wide), not a private pool.
    - The slowest task bounds total time; faster tasks do not wait for others to
      *start*, only for the group to finish.
    """

    def __init__(self):
        """Initialize the loader with a cancellation token."""
        self._cancel_event = threading.Event()

    async def load_all(
        self, tasks: list[LoadTask], timeout: float | None = 30.0
    ) -> dict[str, Any]:
        """
        Execute all tasks in parallel on worker threads, return results dict.

        Args:
            tasks: List of LoadTask objects to execute
            timeout: Maximum time to wait for all tasks (default 30s). When the
                deadline is hit, whatever has completed is returned and the
                remaining thread work is abandoned (runs to completion in the
                background — threads cannot be force-cancelled).

        Returns:
            Dict mapping task.id to result (or the Exception if that task failed).
        """
        if not tasks:
            return {}

        start_time = time.perf_counter()
        results: dict[str, Any] = {}

        async def _run(task: LoadTask) -> None:
            if self._cancel_event.is_set():
                return
            try:
                # abandon_on_cancel=True so a timeout can free the event loop
                # without waiting for an in-flight thread (matches the old
                # as_completed(timeout=...) behaviour, which also left running
                # threads to finish in the background).
                results[task.id] = await anyio.to_thread.run_sync(
                    task.work_fn, limiter=thread_io, abandon_on_cancel=True
                )
            except Exception as e:
                logger.warning(f"HyperParallelLoader task '{task.id}' failed: {e}")
                results[task.id] = e

        with anyio.move_on_after(timeout) as scope:
            async with anyio.create_task_group() as tg:
                for task in tasks:
                    if self._cancel_event.is_set():
                        break
                    tg.start_soon(_run, task)

        if scope.cancelled_caught:
            logger.warning(f"HyperParallelLoader timed out after {timeout}s")

        elapsed = time.perf_counter() - start_time
        logger.debug(
            f"[PERF] HyperParallelLoader completed {len(results)}/{len(tasks)} tasks "
            f"in {elapsed*1000:.1f}ms"
        )

        return results

    def cancel(self):
        """
        Signal cancellation for pending tasks.

        Already-running tasks will complete, but new results won't be collected.
        """
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancel_event.is_set()
