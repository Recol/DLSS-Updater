"""
Task Registry for graceful shutdown management.

Thread-safe for Python 3.14 free-threaded interpreter.
Tracks background tasks and cancels them during application shutdown.

register_task() deliberately keeps accepting plain asyncio.Task objects
(created via asyncio.create_task()) rather than being rearchitected around
anyio's TaskGroup.start_soon(). Several call sites (debounce timers in
search_bar.py/main.py, per-card animations in games_view.py) rely on
externally cancelling ONE specific task while leaving its siblings running
via task.cancel()/task.done() -- a pattern structured concurrency does not
support at the TaskGroup level (you cancel a whole scope, not an arbitrary
child). Reworking every one of those ~35 call sites to each open its own
nested anyio.CancelScope for this would be a much larger, riskier rewrite
for no correctness gain over their current simple coroutine bodies.

Where anyio genuinely helps is right here in the shutdown path: instead of a
single asyncio.wait_for() (which can let a task that swallows CancelledError
in a finally-block outlive the timeout), cancel_all_tasks() below uses
anyio.move_on_after() plus a bounded repeated-cancel retry loop, which is
more deterministic about actually reclaiming stuck tasks within the deadline.
"""

import asyncio
import threading

import anyio

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

    # Re-request cancellation on every pass rather than once: a task that
    # swallows CancelledError (e.g. in a finally-block that awaits more
    # work) gets cancelled again instead of being allowed to linger silently
    # past the deadline. move_on_after() bounds the whole loop deterministically.
    with anyio.move_on_after(timeout) as scope:
        while True:
            pending = [t for t in tasks if not t.done()]
            if not pending:
                break
            for task in pending:
                task.cancel()
            await anyio.sleep(0.1)

    if scope.cancelled_caught:
        still_pending = sum(1 for t in tasks if not t.done())
        logger.warning(f"{still_pending} task(s) did not complete within {timeout}s timeout")

    cancelled = sum(1 for t in tasks if t.cancelled())
    logger.info(f"Cancelled {cancelled}/{len(tasks)} background tasks")
    return cancelled
