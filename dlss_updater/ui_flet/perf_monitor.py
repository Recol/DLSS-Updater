"""
Performance Monitoring Utilities for UI Operations

Provides timing decorators and context managers for measuring UI performance.
All measurements are logged at DEBUG level for minimal overhead in production.
"""

import asyncio
import functools
import logging
import time
from contextlib import contextmanager
from typing import Callable, Any

# Performance logger - separate from main app logger for easy filtering
perf_logger = logging.getLogger("DLSSUpdater.Perf")


@contextmanager
def measure_sync(operation_name: str, threshold_ms: float = 50.0):
    """
    Context manager for measuring synchronous operations.

    Args:
        operation_name: Name of the operation being measured
        threshold_ms: Log as WARNING if operation exceeds this threshold

    Usage:
        with measure_sync("button_click"):
            do_something()
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms >= threshold_ms:
            perf_logger.warning(f"[SLOW] {operation_name}: {elapsed_ms:.1f}ms (threshold: {threshold_ms}ms)")
        else:
            perf_logger.debug(f"[PERF] {operation_name}: {elapsed_ms:.1f}ms")


class measure_async:
    """
    Context manager for measuring async operations.

    Args:
        operation_name: Name of the operation being measured
        threshold_ms: Log as WARNING if operation exceeds this threshold

    Usage:
        async with measure_async("load_data"):
            await fetch_data()
    """

    def __init__(self, operation_name: str, threshold_ms: float = 100.0):
        self.operation_name = operation_name
        self.threshold_ms = threshold_ms
        self.start = None

    async def __aenter__(self):
        self.start = time.perf_counter()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.perf_counter() - self.start) * 1000
        if elapsed_ms >= self.threshold_ms:
            perf_logger.warning(f"[SLOW] {self.operation_name}: {elapsed_ms:.1f}ms (threshold: {self.threshold_ms}ms)")
        else:
            perf_logger.debug(f"[PERF] {self.operation_name}: {elapsed_ms:.1f}ms")
        return False


def timed_sync(threshold_ms: float = 50.0):
    """
    Decorator for timing synchronous functions.

    Args:
        threshold_ms: Log as WARNING if function exceeds this threshold

    Usage:
        @timed_sync(threshold_ms=100)
        def my_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                if elapsed_ms >= threshold_ms:
                    perf_logger.warning(f"[SLOW] {func.__name__}: {elapsed_ms:.1f}ms (threshold: {threshold_ms}ms)")
                else:
                    perf_logger.debug(f"[PERF] {func.__name__}: {elapsed_ms:.1f}ms")
        return wrapper
    return decorator


def timed_async(threshold_ms: float = 100.0):
    """
    Decorator for timing async functions.

    Args:
        threshold_ms: Log as WARNING if function exceeds this threshold

    Usage:
        @timed_async(threshold_ms=200)
        async def my_async_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                if elapsed_ms >= threshold_ms:
                    perf_logger.warning(f"[SLOW] {func.__name__}: {elapsed_ms:.1f}ms (threshold: {threshold_ms}ms)")
                else:
                    perf_logger.debug(f"[PERF] {func.__name__}: {elapsed_ms:.1f}ms")
        return wrapper
    return decorator


def measure_page_update(page, operation_name: str = "page.update"):
    """
    Measure a page.update() call.

    Usage:
        measure_page_update(self._page_ref, "menu_toggle")
    """
    start = time.perf_counter()
    try:
        page.update()
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms >= 50:
            perf_logger.warning(f"[SLOW] {operation_name} page.update(): {elapsed_ms:.1f}ms")
        else:
            perf_logger.debug(f"[PERF] {operation_name} page.update(): {elapsed_ms:.1f}ms")


class PerformanceTracker:
    """
    Track performance metrics over time for identifying patterns.

    Usage:
        tracker = PerformanceTracker("search")
        tracker.record(150.5)  # Record 150.5ms
        tracker.report()  # Log summary statistics
    """

    def __init__(self, name: str, max_samples: int = 100):
        self.name = name
        self.max_samples = max_samples
        self.samples: list[float] = []

    def record(self, elapsed_ms: float):
        """Record a timing sample."""
        self.samples.append(elapsed_ms)
        if len(self.samples) > self.max_samples:
            self.samples.pop(0)

    def report(self):
        """Log summary statistics."""
        if not self.samples:
            perf_logger.info(f"[TRACKER] {self.name}: No samples recorded")
            return

        avg = sum(self.samples) / len(self.samples)
        min_val = min(self.samples)
        max_val = max(self.samples)

        perf_logger.info(
            f"[TRACKER] {self.name}: "
            f"avg={avg:.1f}ms, min={min_val:.1f}ms, max={max_val:.1f}ms, "
            f"samples={len(self.samples)}"
        )

    def clear(self):
        """Clear all recorded samples."""
        self.samples.clear()
