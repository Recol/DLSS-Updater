"""
High-Performance DLL Update Pipeline

This module implements a memory-mapped source caching system and batch backup
operations for maximum update throughput. It provides:

1. MemoryPressureMonitor - Adaptive memory management based on system RAM usage
2. SourceDLLMemoryCache - Memory-mapped source DLLs for zero-copy reads
3. BackupManifest - Atomic batch backup tracking with rollback support
4. HighPerformanceUpdateManager - 4-phase pipeline orchestration

The 4-phase pipeline:
- Phase 0: Load all source DLLs into memory cache
- Phase 1: Create all backups in parallel
- Phase 2: Write updates in parallel from cache
- Phase 3: Verify updates and cleanup resources

Thread-safety: All classes use threading.Lock for Python 3.14 free-threading
compatibility where the GIL may be disabled.

Performance characteristics:
- Memory-mapped I/O eliminates redundant disk reads
- Parallel backup creation maximizes disk throughput
- Batch writes from cache reduce I/O latency
- Adaptive memory management prevents OOM conditions
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import mmap
import os
import shutil
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any

import psutil

from .config import LATEST_DLL_PATHS, Concurrency, config_manager
from .constants import DLL_TYPE_MAP
from .logger import setup_logger
from .models import (
    BackupEntry,
    BatchUpdateResult,
    CacheStats,
    MemoryStatus,
    ProcessedDLLResult,
)
from .updater import (
    create_backup,
    get_dll_version,
    is_file_in_use,
    parse_version,
    remove_read_only,
    restore_permissions,
)

logger = setup_logger()

# =============================================================================
# Check for free-threaded Python (GIL disabled)
# =============================================================================
try:
    GIL_DISABLED = not sys._is_gil_enabled()
except AttributeError:
    GIL_DISABLED = False


# =============================================================================
# Custom Exceptions
# =============================================================================


class MemoryPressureError(Exception):
    """
    Raised when memory pressure exceeds critical threshold (>90%).

    This signals the caller to fall back to standard (non-cached) update mode
    to prevent out-of-memory conditions.
    """

    def __init__(self, message: str, percent_used: float):
        super().__init__(message)
        self.percent_used = percent_used


class BackupFailedError(Exception):
    """
    Raised when backup creation fails for any DLL in the batch.

    Batch operations should abort and cleanup partial backups when this occurs.
    """

    def __init__(self, message: str, failed_path: str, partial_backups: List[str]):
        super().__init__(message)
        self.failed_path = failed_path
        self.partial_backups = partial_backups


class UpdateAbortedError(Exception):
    """
    Raised when update pipeline is aborted due to critical errors.
    """
    pass


# =============================================================================
# Memory Pressure Levels
# =============================================================================


class MemoryPressureLevel(Enum):
    """
    Memory pressure levels that determine caching strategy.

    AGGRESSIVE: < 60% used - Load all source DLLs into memory
    NORMAL: 60-80% used - Load source DLLs on-demand
    CONSERVATIVE: 80-90% used - Limit cache size
    CRITICAL: > 90% used - Fallback to standard mode (no caching)
    """
    AGGRESSIVE = auto()    # < 60% - full caching
    NORMAL = auto()        # 60-80% - on-demand caching
    CONSERVATIVE = auto()  # 80-90% - limited caching
    CRITICAL = auto()      # > 90% - no caching, fallback mode


# =============================================================================
# MemoryPressureMonitor
# =============================================================================


class MemoryPressureMonitor:
    """
    Monitor system memory pressure and provide adaptive caching recommendations.

    Uses psutil.virtual_memory() to track RAM usage and provides:
    - Current memory status
    - Recommended cache sizes based on available memory
    - Allocation permission checks

    Thread-safe for Python 3.14 free-threading compatibility.

    Thresholds:
    - AGGRESSIVE (< 60% used): Load all source DLLs into cache
    - NORMAL (60-80% used): Load source DLLs on-demand
    - CONSERVATIVE (80-90% used): Limit cache size to 256MB
    - CRITICAL (> 90% used): No caching, raise MemoryPressureError

    Example:
        monitor = MemoryPressureMonitor()
        status = monitor.get_memory_status()
        if status.can_use_aggressive_mode:
            # Load all DLLs into cache
            pass
        elif not monitor.can_allocate(file_size):
            raise MemoryPressureError("Not enough memory", status.percent_used)
    """

    # Memory pressure thresholds (percentage of total RAM used)
    THRESHOLD_AGGRESSIVE = 60.0    # < 60% - full caching
    THRESHOLD_NORMAL = 80.0        # 60-80% - on-demand caching
    THRESHOLD_CONSERVATIVE = 90.0  # 80-90% - limited caching
    # > 90% = CRITICAL - no caching

    # Cache size limits by pressure level (MB)
    CACHE_LIMIT_AGGRESSIVE = 1024   # 1GB max cache
    CACHE_LIMIT_NORMAL = 512        # 512MB max cache
    CACHE_LIMIT_CONSERVATIVE = 256  # 256MB max cache
    CACHE_LIMIT_CRITICAL = 0        # No caching

    # Minimum free memory to maintain (MB) - safety buffer
    MIN_FREE_MEMORY_MB = 512

    def __init__(self):
        """Initialize the memory pressure monitor."""
        self._lock = threading.Lock()
        self._last_check_time: float = 0.0
        self._cached_status: Optional[MemoryStatus] = None
        self._cache_ttl_seconds: float = 1.0  # Cache status for 1 second

    def get_pressure_level(self) -> MemoryPressureLevel:
        """
        Get current memory pressure level.

        Returns:
            MemoryPressureLevel enum value based on current RAM usage
        """
        mem = psutil.virtual_memory()
        percent_used = mem.percent

        if percent_used < self.THRESHOLD_AGGRESSIVE:
            return MemoryPressureLevel.AGGRESSIVE
        elif percent_used < self.THRESHOLD_NORMAL:
            return MemoryPressureLevel.NORMAL
        elif percent_used < self.THRESHOLD_CONSERVATIVE:
            return MemoryPressureLevel.CONSERVATIVE
        else:
            return MemoryPressureLevel.CRITICAL

    def get_memory_status(self) -> MemoryStatus:
        """
        Get current memory status with caching recommendations.

        Uses a short TTL cache (1 second) to avoid excessive psutil calls
        during rapid sequential checks.

        Returns:
            MemoryStatus with current usage and recommendations
        """
        current_time = time.monotonic()

        with self._lock:
            # Return cached status if still valid
            if (self._cached_status is not None and
                current_time - self._last_check_time < self._cache_ttl_seconds):
                return self._cached_status

            # Get fresh memory info
            mem = psutil.virtual_memory()
            pressure_level = self.get_pressure_level()

            # Calculate recommended cache size based on pressure level
            if pressure_level == MemoryPressureLevel.AGGRESSIVE:
                recommended_cache_mb = self.CACHE_LIMIT_AGGRESSIVE
            elif pressure_level == MemoryPressureLevel.NORMAL:
                recommended_cache_mb = self.CACHE_LIMIT_NORMAL
            elif pressure_level == MemoryPressureLevel.CONSERVATIVE:
                recommended_cache_mb = self.CACHE_LIMIT_CONSERVATIVE
            else:
                recommended_cache_mb = self.CACHE_LIMIT_CRITICAL

            # Create status object
            status = MemoryStatus(
                total_bytes=mem.total,
                available_bytes=mem.available,
                percent_used=mem.percent,
                can_use_aggressive_mode=(pressure_level == MemoryPressureLevel.AGGRESSIVE),
                recommended_cache_mb=recommended_cache_mb
            )

            # Cache the result
            self._cached_status = status
            self._last_check_time = current_time

            return status

    def can_allocate(self, bytes_needed: int) -> bool:
        """
        Check if it's safe to allocate the specified amount of memory.

        Considers:
        - Current available memory
        - Minimum safety buffer (512MB)
        - Current memory pressure level

        Args:
            bytes_needed: Number of bytes to potentially allocate

        Returns:
            True if allocation is safe, False otherwise
        """
        status = self.get_memory_status()

        # Never allocate in critical mode
        pressure_level = self.get_pressure_level()
        if pressure_level == MemoryPressureLevel.CRITICAL:
            return False

        # Ensure minimum safety buffer remains
        min_free_bytes = self.MIN_FREE_MEMORY_MB * 1024 * 1024
        available_for_cache = status.available_bytes - min_free_bytes

        return bytes_needed <= available_for_cache

    def get_recommended_cache_size_mb(self) -> int:
        """
        Get recommended maximum cache size in megabytes.

        Returns:
            Maximum recommended cache size in MB based on current memory pressure
        """
        return self.get_memory_status().recommended_cache_mb

    def check_critical_and_raise(self) -> None:
        """
        Check memory pressure and raise MemoryPressureError if critical.

        Call this at key points in the update pipeline to abort early
        if memory pressure becomes critical.

        Raises:
            MemoryPressureError: If memory pressure exceeds 90%
        """
        status = self.get_memory_status()
        if self.get_pressure_level() == MemoryPressureLevel.CRITICAL:
            raise MemoryPressureError(
                f"Memory pressure critical: {status.percent_used:.1f}% used. "
                f"Falling back to standard update mode.",
                status.percent_used
            )


# =============================================================================
# SourceDLLMemoryCache
# =============================================================================


class SourceDLLMemoryCache:
    """
    Memory-mapped cache for source DLL files.

    Uses mmap module to memory-map source DLL files, enabling:
    - Zero-copy reads for parallel update writes
    - Automatic memory management by the OS
    - Efficient handling of large DLL files

    Thread-safe for Python 3.14 free-threading compatibility.

    Example:
        cache = SourceDLLMemoryCache(memory_monitor)

        # Load all source DLLs
        await cache.load_all_sources(LATEST_DLL_PATHS)

        # Get cached data for writing
        data = cache.get_source_data("nvngx_dlss.dll")
        if data:
            with open(target_path, "wb") as f:
                f.write(data)

        # Cleanup
        cache.release_all()
    """

    def __init__(self, memory_monitor: Optional[MemoryPressureMonitor] = None):
        """
        Initialize the source DLL cache.

        Args:
            memory_monitor: Optional memory monitor for adaptive caching.
                            If not provided, a new one is created.
        """
        self._lock = threading.Lock()
        self._cache: Dict[str, Tuple[mmap.mmap, int]] = {}  # dll_name -> (mmap_obj, file_handle)
        self._file_handles: Dict[str, int] = {}  # dll_name -> file descriptor
        self._sizes: Dict[str, int] = {}  # dll_name -> size in bytes
        self._memory_monitor = memory_monitor or MemoryPressureMonitor()
        self._stats = CacheStats(
            dlls_cached=0,
            total_size_bytes=0,
            cache_hits=0,
            cache_misses=0
        )

    def load_source(self, dll_name: str, dll_path: str) -> bool:
        """
        Load a source DLL into the memory-mapped cache.

        Args:
            dll_name: Name of the DLL (e.g., "nvngx_dlss.dll")
            dll_path: Full path to the DLL file

        Returns:
            True if successfully loaded, False otherwise
        """
        path = Path(dll_path)

        if not path.exists():
            logger.warning(f"[CACHE] Source DLL not found: {dll_path}")
            return False

        try:
            file_size = path.stat().st_size

            # Check if we can allocate this much memory
            if not self._memory_monitor.can_allocate(file_size):
                logger.warning(
                    f"[CACHE] Insufficient memory to cache {dll_name} "
                    f"({file_size / (1024*1024):.1f}MB)"
                )
                return False

            with self._lock:
                # Already cached?
                if dll_name in self._cache:
                    logger.debug(f"[CACHE] {dll_name} already cached")
                    return True

                # Open file and create memory map
                # On Windows, use ACCESS_READ for read-only memory mapping
                fd = os.open(dll_path, os.O_RDONLY | os.O_BINARY)
                try:
                    mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)

                    self._cache[dll_name] = (mm, fd)
                    self._file_handles[dll_name] = fd
                    self._sizes[dll_name] = file_size

                    # Update stats
                    self._stats = CacheStats(
                        dlls_cached=len(self._cache),
                        total_size_bytes=sum(self._sizes.values()),
                        cache_hits=self._stats.cache_hits,
                        cache_misses=self._stats.cache_misses
                    )

                    logger.info(
                        f"[CACHE] Loaded {dll_name} ({file_size / (1024*1024):.2f}MB)"
                    )
                    return True

                except Exception as e:
                    os.close(fd)
                    raise e

        except Exception as e:
            logger.error(f"[CACHE] Failed to load {dll_name}: {e}", exc_info=True)
            return False

    def load_all_sources(self, dll_paths_dict: Dict[str, str]) -> int:
        """
        Load multiple source DLLs into cache.

        Args:
            dll_paths_dict: Dictionary mapping DLL names to file paths

        Returns:
            Number of DLLs successfully loaded
        """
        loaded_count = 0

        for dll_name, dll_path in dll_paths_dict.items():
            if dll_path and self.load_source(dll_name, dll_path):
                loaded_count += 1

        logger.info(
            f"[CACHE] Loaded {loaded_count}/{len(dll_paths_dict)} source DLLs "
            f"({self.total_size_bytes / (1024*1024):.1f}MB total)"
        )
        return loaded_count

    def get_source_data(self, dll_name: str) -> Optional[bytes]:
        """
        Get cached source DLL data as bytes.

        This returns a copy of the data (not a view) to ensure thread safety
        during parallel writes.

        Args:
            dll_name: Name of the DLL to retrieve

        Returns:
            DLL file contents as bytes, or None if not cached
        """
        with self._lock:
            if dll_name not in self._cache:
                # Update miss count
                self._stats = CacheStats(
                    dlls_cached=self._stats.dlls_cached,
                    total_size_bytes=self._stats.total_size_bytes,
                    cache_hits=self._stats.cache_hits,
                    cache_misses=self._stats.cache_misses + 1
                )
                return None

            mm, _ = self._cache[dll_name]

            # Update hit count
            self._stats = CacheStats(
                dlls_cached=self._stats.dlls_cached,
                total_size_bytes=self._stats.total_size_bytes,
                cache_hits=self._stats.cache_hits + 1,
                cache_misses=self._stats.cache_misses
            )

            # Return a copy of the data for thread safety
            mm.seek(0)
            return mm.read()

    def get_source_view(self, dll_name: str) -> Optional[memoryview]:
        """
        Get a memory view of the cached source DLL.

        WARNING: The returned memoryview is only valid while the cache entry
        exists. Do not use after calling release_all().

        For thread-safe parallel writes, prefer get_source_data() instead.

        Args:
            dll_name: Name of the DLL to retrieve

        Returns:
            Memory view of the DLL data, or None if not cached
        """
        with self._lock:
            if dll_name not in self._cache:
                return None

            mm, _ = self._cache[dll_name]
            return memoryview(mm)

    def is_cached(self, dll_name: str) -> bool:
        """Check if a DLL is in the cache."""
        with self._lock:
            return dll_name in self._cache

    def release(self, dll_name: str) -> bool:
        """
        Release a single cached DLL.

        Args:
            dll_name: Name of the DLL to release

        Returns:
            True if released, False if not found
        """
        with self._lock:
            if dll_name not in self._cache:
                return False

            mm, fd = self._cache[dll_name]

            try:
                mm.close()
                os.close(fd)
            except Exception as e:
                logger.warning(f"[CACHE] Error releasing {dll_name}: {e}")

            del self._cache[dll_name]
            del self._file_handles[dll_name]
            size = self._sizes.pop(dll_name, 0)

            # Update stats
            self._stats = CacheStats(
                dlls_cached=len(self._cache),
                total_size_bytes=sum(self._sizes.values()),
                cache_hits=self._stats.cache_hits,
                cache_misses=self._stats.cache_misses
            )

            logger.debug(f"[CACHE] Released {dll_name} ({size / (1024*1024):.2f}MB)")
            return True

    def release_all(self) -> None:
        """
        Release all cached DLLs and free resources.

        Should be called in a finally block or cleanup phase.
        """
        with self._lock:
            for dll_name, (mm, fd) in list(self._cache.items()):
                try:
                    mm.close()
                    os.close(fd)
                except Exception as e:
                    logger.warning(f"[CACHE] Error releasing {dll_name}: {e}")

            total_released = self._stats.total_size_bytes
            self._cache.clear()
            self._file_handles.clear()
            self._sizes.clear()

            # Reset stats
            self._stats = CacheStats(
                dlls_cached=0,
                total_size_bytes=0,
                cache_hits=self._stats.cache_hits,
                cache_misses=self._stats.cache_misses
            )

            logger.info(
                f"[CACHE] Released all cached DLLs ({total_released / (1024*1024):.1f}MB)"
            )

    @property
    def total_size_bytes(self) -> int:
        """Total size of all cached DLLs in bytes."""
        with self._lock:
            return sum(self._sizes.values())

    @property
    def stats(self) -> CacheStats:
        """Get current cache statistics."""
        with self._lock:
            return self._stats

    def __del__(self):
        """Ensure resources are released on garbage collection."""
        self.release_all()


# =============================================================================
# BackupManifest
# =============================================================================


class BackupManifest:
    """
    Track all backups created during a batch update operation.

    Provides:
    - Thread-safe backup registration
    - Batch verification of all backups
    - Atomic rollback of all backups on failure

    Thread-safe for Python 3.14 free-threading compatibility.

    Example:
        manifest = BackupManifest()

        # During backup phase
        manifest.add_backup(original_path, backup_path, size)

        # Before update phase - verify all backups
        success, errors = manifest.verify_all()
        if not success:
            manifest.rollback_all()
            raise BackupFailedError(...)

        # After successful update
        manifest.clear()
    """

    def __init__(self):
        """Initialize the backup manifest."""
        self._lock = threading.Lock()
        self._entries: List[BackupEntry] = []

    def add_backup(
        self,
        original_path: str,
        backup_path: str,
        size: int
    ) -> None:
        """
        Add a backup entry to the manifest.

        Args:
            original_path: Path to the original DLL file
            backup_path: Path to the backup file
            size: Size of the backup file in bytes
        """
        with self._lock:
            entry = BackupEntry(
                original_path=str(original_path),
                backup_path=str(backup_path),
                original_size=size,
                verified=False
            )
            self._entries.append(entry)
            logger.debug(f"[MANIFEST] Added backup: {Path(original_path).name}")

    def get_entries(self) -> List[BackupEntry]:
        """
        Get all backup entries.

        Returns:
            Copy of the entries list (thread-safe)
        """
        with self._lock:
            return list(self._entries)

    def get_backup_path(self, original_path: str) -> Optional[str]:
        """
        Get the backup path for an original file.

        Args:
            original_path: Path to the original file

        Returns:
            Backup path if found, None otherwise
        """
        normalized = str(Path(original_path).resolve())

        with self._lock:
            for entry in self._entries:
                if str(Path(entry.original_path).resolve()) == normalized:
                    return entry.backup_path
        return None

    def verify_all(self) -> Tuple[bool, List[str]]:
        """
        Verify all backups in the manifest.

        Checks:
        - Backup file exists
        - Backup file size matches expected size
        - Backup file is readable

        Returns:
            Tuple of (all_valid: bool, error_messages: List[str])
        """
        errors: List[str] = []

        with self._lock:
            for i, entry in enumerate(self._entries):
                backup_path = Path(entry.backup_path)

                if not backup_path.exists():
                    errors.append(f"Backup not found: {backup_path}")
                    continue

                try:
                    actual_size = backup_path.stat().st_size
                    if actual_size != entry.original_size:
                        errors.append(
                            f"Size mismatch for {backup_path.name}: "
                            f"expected {entry.original_size}, got {actual_size}"
                        )
                        continue

                    # Verify readable
                    if not os.access(backup_path, os.R_OK):
                        errors.append(f"Backup not readable: {backup_path}")
                        continue

                    # Mark as verified
                    self._entries[i] = BackupEntry(
                        original_path=entry.original_path,
                        backup_path=entry.backup_path,
                        original_size=entry.original_size,
                        verified=True
                    )

                except Exception as e:
                    errors.append(f"Error verifying {backup_path}: {e}")

        all_valid = len(errors) == 0

        if all_valid:
            logger.info(f"[MANIFEST] All {len(self._entries)} backups verified")
        else:
            logger.error(f"[MANIFEST] Backup verification failed: {len(errors)} errors")
            for error in errors:
                logger.error(f"  - {error}")

        return all_valid, errors

    def rollback_all(self) -> Dict[str, Any]:
        """
        Rollback all backups by restoring original files.

        This is called when the update operation fails and we need to
        restore all DLLs to their original state.

        Returns:
            Dict with 'success_count', 'failure_count', and 'errors' keys
        """
        results = {
            "success_count": 0,
            "failure_count": 0,
            "errors": []
        }

        with self._lock:
            for entry in self._entries:
                try:
                    original_path = Path(entry.original_path)
                    backup_path = Path(entry.backup_path)

                    if not backup_path.exists():
                        results["errors"].append(
                            f"Backup not found for rollback: {backup_path}"
                        )
                        results["failure_count"] += 1
                        continue

                    # Remove any partial update
                    if original_path.exists():
                        try:
                            os.chmod(original_path, stat.S_IWRITE)
                            os.remove(original_path)
                        except Exception:
                            pass

                    # Restore from backup
                    shutil.copy2(backup_path, original_path)

                    logger.info(f"[ROLLBACK] Restored {original_path.name}")
                    results["success_count"] += 1

                except Exception as e:
                    error_msg = f"Failed to rollback {entry.original_path}: {e}"
                    logger.error(f"[ROLLBACK] {error_msg}")
                    results["errors"].append(error_msg)
                    results["failure_count"] += 1

        logger.info(
            f"[ROLLBACK] Complete: {results['success_count']} succeeded, "
            f"{results['failure_count']} failed"
        )
        return results

    def cleanup_backups(self) -> int:
        """
        Delete all backup files after successful update.

        Note: This should only be called after a successful update.
        The backup metadata is retained in the database for restore capability.

        Returns:
            Number of backup files deleted
        """
        deleted_count = 0

        with self._lock:
            for entry in self._entries:
                try:
                    backup_path = Path(entry.backup_path)
                    if backup_path.exists():
                        os.remove(backup_path)
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"[MANIFEST] Failed to delete backup {entry.backup_path}: {e}")

        logger.debug(f"[MANIFEST] Cleaned up {deleted_count} backup files")
        return deleted_count

    def clear(self) -> None:
        """Clear all entries from the manifest."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        """Number of entries in the manifest."""
        with self._lock:
            return len(self._entries)


# =============================================================================
# HighPerformanceUpdateManager
# =============================================================================


@dataclass
class DLLTask:
    """Represents a single DLL update task."""
    target_path: str
    source_dll_name: str
    game_name: str = "Unknown Game"
    dll_type: str = "Unknown"

    def __post_init__(self):
        if not self.dll_type or self.dll_type == "Unknown":
            self.dll_type = DLL_TYPE_MAP.get(
                Path(self.target_path).name.lower(),
                "Unknown DLL type"
            )
        # Try to extract game name from path if not provided
        if self.game_name == "Unknown Game":
            try:
                path = Path(self.target_path)
                # Look for common game directory patterns
                parts = path.parts
                for i, part in enumerate(parts):
                    if part.lower() in ("common", "games", "steamapps"):
                        if i + 1 < len(parts):
                            self.game_name = parts[i + 1]
                            break
            except Exception:
                pass


class HighPerformanceUpdateManager:
    """
    Orchestrates the 4-phase high-performance update pipeline.

    The pipeline phases:
    1. Phase 0 - Load Sources: Memory-map all source DLLs
    2. Phase 1 - Create Backups: Parallel backup creation for all targets
    3. Phase 2 - Parallel Updates: Write updates from cache to targets
    4. Phase 3 - Verify & Cleanup: Verify updates and release resources

    Thread-safe for Python 3.14 free-threading compatibility.

    Example:
        manager = HighPerformanceUpdateManager()

        # Prepare tasks
        tasks = [
            DLLTask(target_path="/path/to/game/nvngx_dlss.dll",
                    source_dll_name="nvngx_dlss.dll"),
            ...
        ]

        # Execute update pipeline
        result = await manager.execute(tasks, settings, progress_callback)

        if result.mode_used == "fallback":
            print("Fell back to standard mode due to memory pressure")
    """

    def __init__(self):
        """Initialize the high-performance update manager."""
        self._lock = threading.Lock()
        self._memory_monitor = MemoryPressureMonitor()
        self._source_cache: Optional[SourceDLLMemoryCache] = None
        self._backup_manifest: Optional[BackupManifest] = None
        self._start_time: float = 0.0
        self._peak_memory_mb: float = 0.0
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None

    async def execute(
        self,
        dll_tasks: List[DLLTask],
        settings: Dict[str, Any],
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> BatchUpdateResult:
        """
        Execute the high-performance update pipeline.

        Args:
            dll_tasks: List of DLL update tasks to execute
            settings: Update settings (e.g., CreateBackups preference)
            progress_callback: Optional callback(current, total, message)

        Returns:
            BatchUpdateResult with pipeline execution results

        Raises:
            MemoryPressureError: If memory pressure exceeds critical threshold
                                (caller should fall back to standard mode)
        """
        if not dll_tasks:
            return BatchUpdateResult(
                mode_used="high_performance",
                backups_created=0,
                updates_succeeded=0,
                updates_failed=0,
                updates_skipped=0,
                memory_peak_mb=0.0,
                duration_seconds=0.0,
                errors=[]
            )

        self._start_time = time.monotonic()
        self._peak_memory_mb = 0.0
        errors: List[Dict[str, str]] = []
        detailed_updates: List[Dict[str, str]] = []
        detailed_skipped: List[Dict[str, str]] = []
        mode_used = "high_performance"

        total_steps = len(dll_tasks) * 3  # backup + update + verify
        current_step = 0

        # Progress helper - handles both sync and async callbacks
        # Note: For calls from sync contexts (thread pool), we use fire-and-forget
        def _progress_sync(message: str):
            """Sync progress update (for thread pool contexts)"""
            nonlocal current_step
            current_step += 1
            if progress_callback:
                result = progress_callback(current_step, total_steps, message)
                if asyncio.iscoroutine(result):
                    # Schedule the coroutine but don't block
                    asyncio.create_task(result)

        async def _progress(message: str):
            """Async progress update (for async contexts)"""
            nonlocal current_step
            current_step += 1
            if progress_callback:
                result = progress_callback(current_step, total_steps, message)
                if asyncio.iscoroutine(result):
                    await result

        # Initialize components
        self._source_cache = SourceDLLMemoryCache(self._memory_monitor)
        self._backup_manifest = BackupManifest()

        # Create thread pool executor
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=Concurrency.THREADPOOL_IO,
            thread_name_prefix="hp_update"
        )

        backups_created = 0
        updates_succeeded = 0
        updates_failed = 0
        updates_skipped = 0

        try:
            # Check initial memory status
            try:
                self._memory_monitor.check_critical_and_raise()
            except MemoryPressureError:
                mode_used = "fallback"
                raise

            # ========== PHASE 0: Load Source DLLs ==========
            await _progress("Loading source DLLs into cache...")

            try:
                loaded_count = await self._phase0_load_sources(dll_tasks)
                logger.info(f"[PHASE 0] Loaded {loaded_count} source DLLs into cache")
            except MemoryPressureError:
                mode_used = "fallback"
                raise

            # ========== PRE-FILTER: Check which DLLs need updates ==========
            await _progress("Checking versions...")

            filtered_tasks, pre_skipped = await self._filter_tasks_needing_update(
                dll_tasks,
                _progress_sync
            )
            updates_skipped += pre_skipped

            if not filtered_tasks:
                logger.info("[PRE-FILTER] No DLLs need updating")
                # Return early with all DLLs skipped
                duration = time.monotonic() - self._start_time
                return BatchUpdateResult(
                    mode_used=mode_used,
                    backups_created=0,
                    updates_succeeded=0,
                    updates_failed=0,
                    updates_skipped=updates_skipped,
                    memory_peak_mb=self._peak_memory_mb,
                    duration_seconds=duration,
                    errors=[],
                    detailed_updates=[],
                    detailed_skipped=[]
                )

            # Update total steps based on filtered count
            total_steps = len(filtered_tasks) * 3  # backup + update + verify
            current_step = 0

            # ========== PHASE 1: Create All Backups ==========
            if settings.get("CreateBackups", True):
                await _progress("Creating backups...")

                try:
                    backups_created = await self._phase1_create_all_backups(
                        filtered_tasks,  # Use filtered list
                        _progress_sync  # Use sync version for thread pool context
                    )
                    logger.info(f"[PHASE 1] Created {backups_created} backups")
                except BackupFailedError as e:
                    # Abort - cleanup partial backups
                    logger.error(f"[PHASE 1] Backup failed: {e}")
                    self._backup_manifest.rollback_all()
                    errors.append({
                        "phase": "backup",
                        "path": e.failed_path,
                        "error": str(e)
                    })
                    raise UpdateAbortedError(f"Backup creation failed: {e}")

            # Check memory pressure before heavy phase
            try:
                self._memory_monitor.check_critical_and_raise()
            except MemoryPressureError:
                mode_used = "fallback"
                raise

            # ========== PHASE 2: Parallel Updates ==========
            await _progress("Applying updates...")

            update_results = await self._phase2_parallel_updates(
                filtered_tasks,  # Use filtered list
                _progress_sync  # Use sync version for thread pool context
            )

            # Collect detailed results for UI display
            for result in update_results:
                if result["success"]:
                    updates_succeeded += 1
                    # Track detailed update info
                    detailed_updates.append({
                        "game_name": result.get("game_name", "Unknown Game"),
                        "dll_name": result.get("dll_name", "Unknown DLL"),
                        "dll_type": result.get("dll_type", ""),
                        "old_version": result.get("old_version", ""),
                        "new_version": result.get("new_version", "")
                    })
                elif result.get("skipped", False):
                    updates_skipped += 1
                    # Track detailed skip info
                    detailed_skipped.append({
                        "game_name": result.get("game_name", "Unknown Game"),
                        "dll_name": result.get("dll_name", "Unknown DLL"),
                        "dll_type": result.get("dll_type", ""),
                        "reason": result.get("error", "Already up-to-date")
                    })
                else:
                    updates_failed += 1
                    errors.append({
                        "phase": "update",
                        "path": result["path"],
                        "error": result.get("error", "Unknown error")
                    })

            logger.info(
                f"[PHASE 2] Updates: {updates_succeeded} succeeded, "
                f"{updates_failed} failed, {updates_skipped} skipped"
            )

            # ========== PHASE 3: Verify & Cleanup ==========
            await _progress("Verifying updates...")

            await self._phase3_verify_cleanup(update_results)

        except (MemoryPressureError, UpdateAbortedError):
            # Re-raise for caller to handle
            raise

        except Exception as e:
            logger.error(f"[PIPELINE] Unexpected error: {e}", exc_info=True)
            errors.append({
                "phase": "pipeline",
                "path": "",
                "error": str(e)
            })

        finally:
            # Always cleanup resources
            if self._source_cache:
                self._source_cache.release_all()

            if self._executor:
                self._executor.shutdown(wait=False)

            # Update peak memory
            self._update_peak_memory()

        # Calculate duration
        duration = time.monotonic() - self._start_time

        return BatchUpdateResult(
            mode_used=mode_used,
            backups_created=backups_created,
            updates_succeeded=updates_succeeded,
            updates_failed=updates_failed,
            updates_skipped=updates_skipped,
            memory_peak_mb=self._peak_memory_mb,
            duration_seconds=duration,
            errors=errors,
            detailed_updates=detailed_updates,
            detailed_skipped=detailed_skipped
        )

    async def _phase0_load_sources(self, dll_tasks: List[DLLTask]) -> int:
        """
        Phase 0: Load all required source DLLs into memory cache.

        Identifies unique source DLLs needed and loads them into the cache.

        Args:
            dll_tasks: List of DLL update tasks

        Returns:
            Number of source DLLs loaded

        Raises:
            MemoryPressureError: If memory pressure becomes critical
        """
        # Identify unique source DLLs needed
        unique_sources: Dict[str, str] = {}

        for task in dll_tasks:
            if task.source_dll_name not in unique_sources:
                source_path = LATEST_DLL_PATHS.get(task.source_dll_name)
                if source_path:
                    unique_sources[task.source_dll_name] = source_path

        logger.info(f"[PHASE 0] Loading {len(unique_sources)} unique source DLLs")

        # Load sources (run in thread pool to avoid blocking)
        loaded = await asyncio.to_thread(
            self._source_cache.load_all_sources,
            unique_sources
        )

        # Check memory after loading
        self._memory_monitor.check_critical_and_raise()
        self._update_peak_memory()

        return loaded

    def _check_needs_update(self, task: DLLTask) -> Tuple[bool, str]:
        """
        Check if a single DLL needs updating (runs in thread pool).

        Compares existing version vs latest version to determine if update needed.

        Args:
            task: DLL update task to check

        Returns:
            Tuple of (needs_update: bool, reason: str)
        """
        target_path = Path(task.target_path)

        if not target_path.exists():
            return False, "Target not found"

        source_path = LATEST_DLL_PATHS.get(task.source_dll_name)
        if not source_path:
            return False, "No source DLL"

        existing_version = get_dll_version(target_path)
        latest_version = get_dll_version(source_path)

        if not existing_version or not latest_version:
            # Can't determine versions - include for update
            return True, "Version unknown"

        if parse_version(existing_version) >= parse_version(latest_version):
            return False, f"Up-to-date ({existing_version})"

        return True, f"Update available ({existing_version} -> {latest_version})"

    async def _filter_tasks_needing_update(
        self,
        dll_tasks: List[DLLTask],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Tuple[List[DLLTask], int]:
        """
        Filter DLL tasks to only those that need updates.

        Compares existing version vs latest version for each target.
        Runs version checks in parallel using thread pool.

        Args:
            dll_tasks: All DLL update tasks
            progress_callback: Optional progress callback

        Returns:
            Tuple of (tasks_needing_update, skipped_count)
        """
        logger.info(f"[PRE-FILTER] Checking versions for {len(dll_tasks)} DLLs")

        tasks_needing_update: List[DLLTask] = []
        skipped_count = 0

        # Submit version checks in parallel
        futures: Dict[concurrent.futures.Future, DLLTask] = {}

        for task in dll_tasks:
            future = self._executor.submit(
                self._check_needs_update,
                task
            )
            futures[future] = task

        # Collect results
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            try:
                needs_update, reason = future.result()
                if needs_update:
                    tasks_needing_update.append(task)
                    logger.debug(f"[PRE-FILTER] {Path(task.target_path).name}: {reason}")
                else:
                    skipped_count += 1
                    logger.debug(f"[PRE-FILTER] Skipped {Path(task.target_path).name}: {reason}")
            except Exception as e:
                logger.warning(f"[PRE-FILTER] Error checking {task.target_path}: {e}")
                # Include in update list on error (let Phase 2 handle it)
                tasks_needing_update.append(task)

        logger.info(
            f"[PRE-FILTER] {len(tasks_needing_update)} need updates, "
            f"{skipped_count} already up-to-date"
        )
        return tasks_needing_update, skipped_count

    async def _phase1_create_all_backups(
        self,
        dll_tasks: List[DLLTask],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> int:
        """
        Phase 1: Create backups for all target DLLs in parallel.

        Uses ThreadPoolExecutor for parallel backup creation.
        If ANY backup fails, raises BackupFailedError for rollback.

        Args:
            dll_tasks: List of DLL update tasks
            progress_callback: Optional callback for progress updates

        Returns:
            Number of backups created

        Raises:
            BackupFailedError: If any backup creation fails
        """
        logger.info(f"[PHASE 1] Creating backups for {len(dll_tasks)} DLLs")

        # Submit all backup tasks
        loop = asyncio.get_event_loop()
        futures: Dict[concurrent.futures.Future, DLLTask] = {}

        for task in dll_tasks:
            target_path = Path(task.target_path)
            if not target_path.exists():
                logger.warning(f"[PHASE 1] Target not found, skipping: {target_path}")
                continue

            future = self._executor.submit(
                self._create_single_backup,
                str(target_path)
            )
            futures[future] = task

        # Wait for all backups to complete
        backups_created = 0
        partial_backups: List[str] = []

        for future in concurrent.futures.as_completed(futures):
            task = futures[future]

            try:
                result = future.result()

                if result["success"]:
                    # Add to manifest
                    self._backup_manifest.add_backup(
                        result["original_path"],
                        result["backup_path"],
                        result["size"]
                    )
                    partial_backups.append(result["backup_path"])
                    backups_created += 1

                    if progress_callback:
                        progress_callback(f"Backed up {Path(task.target_path).name}")
                else:
                    # Backup failed - abort entire operation
                    raise BackupFailedError(
                        f"Failed to create backup for {task.target_path}: {result.get('error')}",
                        task.target_path,
                        partial_backups
                    )

            except BackupFailedError:
                raise
            except Exception as e:
                raise BackupFailedError(
                    f"Backup error for {task.target_path}: {e}",
                    task.target_path,
                    partial_backups
                )

        # Verify all backups before proceeding
        success, errors = self._backup_manifest.verify_all()
        if not success:
            raise BackupFailedError(
                f"Backup verification failed: {'; '.join(errors)}",
                "",
                partial_backups
            )

        self._update_peak_memory()
        return backups_created

    def _create_single_backup(self, target_path: str) -> Dict[str, Any]:
        """
        Create a single backup (runs in thread pool).

        Args:
            target_path: Path to the DLL to back up

        Returns:
            Dict with 'success', 'backup_path', 'original_path', 'size', 'error'
        """
        try:
            path = Path(target_path)
            backup_path = create_backup(path)

            if backup_path:
                return {
                    "success": True,
                    "backup_path": str(backup_path),
                    "original_path": target_path,
                    "size": path.stat().st_size,
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "backup_path": None,
                    "original_path": target_path,
                    "size": 0,
                    "error": "create_backup returned None"
                }

        except Exception as e:
            return {
                "success": False,
                "backup_path": None,
                "original_path": target_path,
                "size": 0,
                "error": str(e)
            }

    async def _phase2_parallel_updates(
        self,
        dll_tasks: List[DLLTask],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        Phase 2: Apply updates in parallel from memory cache.

        Reads source data from cache and writes to all targets in parallel.
        Continues processing even if individual updates fail.

        Args:
            dll_tasks: List of DLL update tasks
            progress_callback: Optional callback for progress updates

        Returns:
            List of result dicts with 'success', 'path', 'error', 'skipped'
        """
        logger.info(f"[PHASE 2] Applying {len(dll_tasks)} updates from cache")

        results: List[Dict[str, Any]] = []

        # Submit all update tasks
        futures: Dict[concurrent.futures.Future, DLLTask] = {}

        for task in dll_tasks:
            future = self._executor.submit(
                self._apply_single_update,
                task
            )
            futures[future] = task

        # Collect results as they complete
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]

            try:
                result = future.result()
                results.append(result)

                if progress_callback:
                    status = "Updated" if result["success"] else (
                        "Skipped" if result.get("skipped") else "Failed"
                    )
                    progress_callback(f"{status} {Path(task.target_path).name}")

            except Exception as e:
                logger.error(f"[PHASE 2] Update error for {task.target_path}: {e}")
                results.append({
                    "success": False,
                    "path": task.target_path,
                    "error": str(e),
                    "skipped": False
                })

        self._update_peak_memory()
        return results

    def _apply_single_update(self, task: DLLTask) -> Dict[str, Any]:
        """
        Apply a single DLL update from cache (runs in thread pool).

        Args:
            task: DLL update task

        Returns:
            Dict with 'success', 'path', 'error', 'skipped', 'game_name', 'dll_name',
            'old_version', 'new_version'
        """
        target_path = Path(task.target_path)
        dll_name = target_path.name

        # Base result dict with common fields
        def make_result(success: bool, error: str = None, skipped: bool = False,
                       old_version: str = None, new_version: str = None) -> Dict[str, Any]:
            return {
                "success": success,
                "path": str(target_path),
                "error": error,
                "skipped": skipped,
                "game_name": task.game_name,
                "dll_name": dll_name,
                "dll_type": task.dll_type,
                "old_version": old_version or "",
                "new_version": new_version or ""
            }

        try:
            # Check if target exists
            if not target_path.exists():
                return make_result(False, "Target file not found", skipped=True)

            # Get source data from cache
            source_data = self._source_cache.get_source_data(task.source_dll_name)

            if source_data is None:
                # Source not in cache - try direct file copy
                source_path = LATEST_DLL_PATHS.get(task.source_dll_name)
                if not source_path or not Path(source_path).exists():
                    return make_result(False, f"Source DLL not found: {task.source_dll_name}", skipped=True)

                # Fall back to standard copy
                logger.debug(f"[PHASE 2] Cache miss for {task.source_dll_name}, using file copy")

                # Get versions for comparison
                existing_version = get_dll_version(target_path)
                latest_version = get_dll_version(source_path)

                if existing_version and latest_version:
                    if parse_version(existing_version) >= parse_version(latest_version):
                        return make_result(
                            False, f"Already up-to-date ({existing_version})",
                            skipped=True, old_version=existing_version, new_version=latest_version
                        )

                # Check if file is in use
                if is_file_in_use(str(target_path)):
                    return make_result(
                        False, "File is in use", skipped=False,
                        old_version=existing_version, new_version=latest_version
                    )

                # Perform update using shutil
                original_permissions = os.stat(target_path).st_mode
                remove_read_only(target_path)

                os.remove(target_path)
                shutil.copyfile(source_path, target_path)
                restore_permissions(target_path, original_permissions)

                return make_result(
                    True, old_version=existing_version, new_version=latest_version
                )

            # Use cached source data
            source_path = LATEST_DLL_PATHS.get(task.source_dll_name)

            # Get versions for comparison
            existing_version = get_dll_version(target_path)
            latest_version = get_dll_version(source_path) if source_path else None

            if existing_version and latest_version:
                if parse_version(existing_version) >= parse_version(latest_version):
                    return make_result(
                        False, f"Already up-to-date ({existing_version})",
                        skipped=True, old_version=existing_version, new_version=latest_version
                    )

            # Check if file is in use
            if is_file_in_use(str(target_path)):
                return make_result(
                    False, "File is in use", skipped=False,
                    old_version=existing_version, new_version=latest_version
                )

            # Perform update from cache
            original_permissions = os.stat(target_path).st_mode
            remove_read_only(target_path)

            os.remove(target_path)

            # Write from cache
            with open(target_path, "wb") as f:
                f.write(source_data)

            restore_permissions(target_path, original_permissions)

            # Verify update
            new_version = get_dll_version(target_path)
            if new_version == latest_version:
                logger.info(
                    f"[PHASE 2] Updated {target_path.name}: "
                    f"{existing_version} -> {latest_version}"
                )

                # Record update history
                try:
                    from .updater import record_update_history_sync
                    record_update_history_sync(target_path, existing_version, latest_version, True)
                except Exception as e:
                    logger.warning(f"Failed to record update history: {e}")

                return make_result(
                    True, old_version=existing_version, new_version=latest_version
                )
            else:
                logger.error(
                    f"[PHASE 2] Version mismatch after update: "
                    f"expected {latest_version}, got {new_version}"
                )
                return make_result(
                    False, f"Version mismatch: expected {latest_version}, got {new_version}",
                    skipped=False, old_version=existing_version, new_version=new_version
                )

        except Exception as e:
            logger.error(f"[PHASE 2] Error updating {target_path}: {e}", exc_info=True)
            return make_result(False, str(e), skipped=False)

    async def _phase3_verify_cleanup(
        self,
        update_results: List[Dict[str, Any]]
    ) -> None:
        """
        Phase 3: Verify updates and cleanup resources.

        Verifies successful updates and releases cached resources.

        Args:
            update_results: Results from Phase 2
        """
        logger.info("[PHASE 3] Verifying updates and cleaning up")

        # Count successful updates
        success_count = sum(1 for r in update_results if r["success"])

        if success_count > 0:
            logger.info(f"[PHASE 3] {success_count} updates verified successfully")

        # Release cache resources
        if self._source_cache:
            stats = self._source_cache.stats
            logger.info(
                f"[PHASE 3] Cache stats: {stats.cache_hits} hits, "
                f"{stats.cache_misses} misses, "
                f"{stats.total_size_bytes / (1024*1024):.1f}MB cached"
            )
            self._source_cache.release_all()

        self._update_peak_memory()

    def _update_peak_memory(self) -> None:
        """Update peak memory usage tracking."""
        try:
            process = psutil.Process()
            current_mb = process.memory_info().rss / (1024 * 1024)
            self._peak_memory_mb = max(self._peak_memory_mb, current_mb)
        except Exception:
            pass


# =============================================================================
# Module-level convenience functions
# =============================================================================


async def execute_high_performance_update(
    dll_tasks: List[DLLTask],
    settings: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> BatchUpdateResult:
    """
    Execute a high-performance batch DLL update.

    Convenience function that creates a HighPerformanceUpdateManager
    and executes the update pipeline.

    Args:
        dll_tasks: List of DLL update tasks
        settings: Update settings
        progress_callback: Optional callback(current, total, message)

        Returns:
        BatchUpdateResult with execution results

    Raises:
        MemoryPressureError: If memory pressure exceeds critical threshold
    """
    manager = HighPerformanceUpdateManager()
    return await manager.execute(dll_tasks, settings, progress_callback)


def check_memory_for_high_performance_mode() -> Tuple[bool, str]:
    """
    Check if high-performance mode can be used.

    Returns:
        Tuple of (can_use: bool, reason: str)
    """
    monitor = MemoryPressureMonitor()
    level = monitor.get_pressure_level()
    status = monitor.get_memory_status()

    if level == MemoryPressureLevel.CRITICAL:
        return False, f"Memory pressure critical ({status.percent_used:.1f}% used)"
    elif level == MemoryPressureLevel.CONSERVATIVE:
        return True, f"Memory pressure high ({status.percent_used:.1f}% used), using limited cache"
    elif level == MemoryPressureLevel.NORMAL:
        return True, f"Memory pressure normal ({status.percent_used:.1f}% used)"
    else:
        return True, f"Memory available ({status.percent_used:.1f}% used), using full cache"
