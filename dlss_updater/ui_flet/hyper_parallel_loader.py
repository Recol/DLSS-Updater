"""
Hyper-Parallel Data Loading for UI Views

Uses ThreadPoolExecutor with as_completed() for truly parallel I/O operations.
Designed for loading multiple database queries and image fetches concurrently.

Key differences from asyncio.to_thread():
- asyncio.to_thread() serializes calls through the event loop
- ThreadPoolExecutor.submit() with as_completed() allows true parallelism
- Multiple queries run simultaneously on different threads

Usage:
    loader = HyperParallelLoader()
    results = loader.load_all([
        LoadTask("dlls", lambda: db.batch_get_dlls_for_games_sync(game_ids)),
        LoadTask("backups", lambda: db.batch_get_backups_grouped_sync(game_ids)),
        LoadTask("images", lambda: db.batch_get_cached_image_paths(app_ids)),
    ])

    dlls = results.get("dlls", {})
    backups = results.get("backups", {})
    images = results.get("images", {})
"""

from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass
from typing import Callable, Any
import threading
import time

from dlss_updater.config import Concurrency
from dlss_updater.logger import setup_logger

logger = setup_logger()


@dataclass
class LoadTask:
    """A single task for parallel loading."""
    id: str
    work_fn: Callable[[], Any]


class HyperParallelLoader:
    """
    Context manager for hyper-parallel data loading.

    Uses a shared ThreadPoolExecutor to avoid thread creation overhead.
    Supports cancellation via threading.Event for responsive UI shutdown.

    Performance characteristics:
    - First call creates shared executor (~5-10ms overhead)
    - Subsequent calls reuse executor (near-zero overhead)
    - as_completed() returns results as they finish (no waiting for slowest)
    - Cancellation stops pending work immediately
    """

    _shared_executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()

    @classmethod
    def get_shared_executor(cls) -> ThreadPoolExecutor:
        """
        Get the shared ThreadPoolExecutor instance.

        Thread-safe singleton pattern using double-checked locking.
        The executor is created lazily on first use.
        """
        if cls._shared_executor is None:
            with cls._executor_lock:
                if cls._shared_executor is None:
                    cls._shared_executor = ThreadPoolExecutor(
                        max_workers=Concurrency.THREADPOOL_IO,
                        thread_name_prefix="hyper_load"
                    )
                    logger.debug(
                        f"Created shared HyperParallelLoader executor "
                        f"(max_workers={Concurrency.THREADPOOL_IO})"
                    )
        return cls._shared_executor

    @classmethod
    def shutdown_shared_executor(cls):
        """
        Shutdown the shared executor during application cleanup.

        Call this from your application's shutdown handler to ensure
        clean thread termination.
        """
        with cls._executor_lock:
            if cls._shared_executor is not None:
                cls._shared_executor.shutdown(wait=False, cancel_futures=True)
                cls._shared_executor = None
                logger.debug("Shutdown shared HyperParallelLoader executor")

    def __init__(self):
        """Initialize the loader with a cancellation token."""
        self._cancel_event = threading.Event()
        self._executor = self.get_shared_executor()

    def load_all(self, tasks: list[LoadTask], timeout: float | None = 30.0) -> dict[str, Any]:
        """
        Execute all tasks in parallel, return results dict.

        Args:
            tasks: List of LoadTask objects to execute
            timeout: Maximum time to wait for all tasks (default 30s)

        Returns:
            Dict mapping task.id to result (or Exception if task failed)
        """
        if not tasks:
            return {}

        start_time = time.perf_counter()
        futures: dict[Future, LoadTask] = {}

        # Submit all tasks
        for task in tasks:
            if self._cancel_event.is_set():
                break
            future = self._executor.submit(task.work_fn)
            futures[future] = task

        # Collect results as they complete
        results: dict[str, Any] = {}
        try:
            for future in as_completed(futures, timeout=timeout):
                if self._cancel_event.is_set():
                    # Cancel remaining futures
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break

                task = futures[future]
                try:
                    results[task.id] = future.result()
                except Exception as e:
                    logger.warning(f"HyperParallelLoader task '{task.id}' failed: {e}")
                    results[task.id] = e

        except TimeoutError:
            logger.warning(f"HyperParallelLoader timed out after {timeout}s")
            # Cancel remaining futures
            for f in futures:
                if not f.done():
                    f.cancel()

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


class BatchedImageLoader:
    """
    Specialized loader for Steam game images.

    Handles the common pattern of:
    1. Check cache for existing images (single batch query)
    2. Fetch missing images in parallel
    3. Update cache with new images

    Designed to work with GameCard components without triggering
    N individual page.update() calls.
    """

    def __init__(self, executor: ThreadPoolExecutor | None = None):
        """
        Initialize the image loader.

        Args:
            executor: Optional custom executor. Uses shared executor if None.
        """
        self._executor = executor or HyperParallelLoader.get_shared_executor()
        self._cancel_event = threading.Event()

    def load_images_batch(
        self,
        steam_app_ids: list[int],
        fetch_fn: Callable[[int], str | None],
        timeout: float = 30.0
    ) -> dict[int, str]:
        """
        Load images for multiple Steam apps in parallel.

        Args:
            steam_app_ids: List of Steam app IDs to fetch images for
            fetch_fn: Function that takes app_id and returns local path or None
            timeout: Maximum time to wait for all fetches

        Returns:
            Dict mapping app_id to local_path for successfully fetched images
        """
        if not steam_app_ids:
            return {}

        start_time = time.perf_counter()
        futures: dict[Future, int] = {}

        # Submit all fetch tasks
        for app_id in steam_app_ids:
            if self._cancel_event.is_set():
                break
            future = self._executor.submit(fetch_fn, app_id)
            futures[future] = app_id

        # Collect results
        results: dict[int, str] = {}
        try:
            for future in as_completed(futures, timeout=timeout):
                if self._cancel_event.is_set():
                    break

                app_id = futures[future]
                try:
                    path = future.result()
                    if path:
                        results[app_id] = path
                except Exception as e:
                    logger.debug(f"Image fetch for app {app_id} failed: {e}")

        except TimeoutError:
            logger.warning(f"BatchedImageLoader timed out after {timeout}s")

        elapsed = time.perf_counter() - start_time
        logger.debug(
            f"[PERF] BatchedImageLoader fetched {len(results)}/{len(steam_app_ids)} images "
            f"in {elapsed*1000:.1f}ms"
        )

        return results

    def cancel(self):
        """Signal cancellation for pending fetches."""
        self._cancel_event.set()
