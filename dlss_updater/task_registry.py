"""
Task Registry for graceful shutdown management.

Thread-safe for Python 3.14 free-threaded interpreter.
Tracks background tasks and cancels them during application shutdown.
"""

import asyncio
import threading
from dlss_updater.logger import setup_logger

logger = setup_logger()

# Thread-safe lock for task registry (Python 3.14 free-threading)
_registry_lock = threading.Lock()
_background_tasks: list[asyncio.Task] = []
_shutdown_in_progress = False


def register_task(task: asyncio.Task, name: str = "") -> asyncio.Task:
    """Register a background task for tracking.

    Thread-safe for Python 3.14 free-threaded interpreter. The lock is held
    during both the shutdown check and the append operation to prevent TOCTOU
    race conditions.

    Args:
        task: The asyncio.Task to track
        name: Optional name for logging

    Returns:
        The same task (for chaining)
    """
    global _shutdown_in_progress

    with _registry_lock:
        if _shutdown_in_progress:
            logger.warning(f"Task '{name}' created during shutdown - cancelling immediately")
            task.cancel()
            return task

        # Clean up done tasks to prevent unbounded growth
        _background_tasks[:] = [t for t in _background_tasks if not t.done()]

        _background_tasks.append(task)
        logger.debug(f"Registered background task: {name or task.get_name()}")
    return task


def reset_shutdown_state() -> None:
    """Reset shutdown state (useful for testing or restart scenarios).

    Thread-safe: clears all tasks and resets the shutdown flag atomically.
    """
    global _shutdown_in_progress
    with _registry_lock:
        _shutdown_in_progress = False
        _background_tasks.clear()
        logger.debug("Task registry reset: shutdown state cleared")


def get_active_task_count() -> int:
    """Get count of active (non-done) tasks.

    Thread-safe: provides visibility into task registry state.

    Returns:
        Number of tasks that are not yet done.
    """
    with _registry_lock:
        return sum(1 for t in _background_tasks if not t.done())


def get_task_names() -> list[str]:
    """Get names of all registered tasks.

    Thread-safe: returns a snapshot of task names.

    Returns:
        List of task names (may include auto-generated names).
    """
    with _registry_lock:
        return [t.get_name() for t in _background_tasks]


async def cancel_all_tasks(timeout: float = 3.0) -> int:
    """Cancel all registered background tasks.

    Args:
        timeout: Maximum time to wait for task cancellation

    Returns:
        Number of tasks cancelled
    """
    global _shutdown_in_progress

    with _registry_lock:
        _shutdown_in_progress = True
        tasks = _background_tasks.copy()
        _background_tasks.clear()

    if not tasks:
        logger.debug("No background tasks to cancel")
        return 0

    logger.info(f"Cancelling {len(tasks)} background tasks...")

    # Request cancellation for all tasks
    for task in tasks:
        if not task.done():
            task.cancel()

    # Wait for tasks to complete with timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"Some tasks did not complete within {timeout}s timeout")

    cancelled = sum(1 for t in tasks if t.cancelled())
    logger.info(f"Cancelled {cancelled}/{len(tasks)} background tasks")
    return cancelled
