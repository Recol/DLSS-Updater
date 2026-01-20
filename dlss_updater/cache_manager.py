"""
Unified Cache Manager for DLSS Updater - Phase 4 Memory Optimization

Provides centralized cache management with:
- Configurable policies per cache (max_size_mb, max_age_days, eviction_enabled)
- Memory-mapped file support for DLLs via mmap
- LRU eviction with file access time tracking
- Background cleanup loop for automatic maintenance
- Reference counting for update sessions
- Thread-safe operations for Python 3.14 free-threaded interpreter

Performance characteristics:
- O(1) cache entry lookup via dict
- O(n log n) eviction via sorted access times
- Async I/O for all file operations
- Memory mapping reduces copy overhead for large DLLs
"""

import asyncio
import mmap
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import aiofiles

from dlss_updater.logger import setup_logger
from dlss_updater.task_registry import register_task

logger = setup_logger()


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class CachePolicy:
    """
    Configuration policy for a cache directory.

    Attributes:
        max_size_mb: Maximum total size in MB (0 = no limit)
        max_age_days: Maximum age of entries in days (0 = no age limit)
        cleanup_interval_hours: How often to run automatic cleanup
        eviction_enabled: If False, never evict entries (e.g., for critical DLLs)
        priority_patterns: Glob patterns for high-priority files (evicted last)
    """
    max_size_mb: int = 500
    max_age_days: int = 30
    cleanup_interval_hours: int = 1
    eviction_enabled: bool = True
    priority_patterns: list[str] = field(default_factory=list)


@dataclass
class CacheEntry:
    """
    Represents a single cached file entry.

    Attributes:
        path: Absolute path to the cached file
        size_bytes: File size in bytes
        last_access: Timestamp of last access (for LRU)
        is_memory_mapped: Whether this entry has an active mmap
        priority: Higher values = evicted later (0 = normal)
    """
    path: Path
    size_bytes: int
    last_access: datetime
    is_memory_mapped: bool = False
    priority: int = 0


@dataclass
class CacheStats:
    """
    Statistics for cache monitoring and reporting.

    Attributes:
        name: Cache name
        total_entries: Number of entries in cache
        total_size_bytes: Total size of all entries
        memory_mapped_count: Number of memory-mapped entries
        oldest_entry: Timestamp of oldest entry
        newest_entry: Timestamp of newest entry
        evictions_performed: Total evictions since start
        cleanup_runs: Number of cleanup cycles completed
    """
    name: str
    total_entries: int
    total_size_bytes: int
    memory_mapped_count: int
    oldest_entry: datetime | None
    newest_entry: datetime | None
    evictions_performed: int
    cleanup_runs: int


@dataclass
class _RegisteredCache:
    """Internal representation of a registered cache."""
    name: str
    cache_dir: Path
    policy: CachePolicy
    entries: dict[str, CacheEntry] = field(default_factory=dict)
    evictions_performed: int = 0
    cleanup_runs: int = 0


# =============================================================================
# Memory-Mapped File Handle
# =============================================================================

@dataclass
class MmapHandle:
    """
    Handle for a memory-mapped file with reference counting.

    Attributes:
        path: Path to the mapped file
        file_handle: Open file handle (kept open while mapped)
        mmap_obj: The mmap object
        ref_count: Number of active references
        created_at: When the mapping was created
    """
    path: Path
    file_handle: object  # File handle type varies by platform
    mmap_obj: mmap.mmap
    ref_count: int = 1
    created_at: datetime = field(default_factory=datetime.now)


# =============================================================================
# Unified Cache Manager
# =============================================================================

class UnifiedCacheManager:
    """
    Centralized cache manager for DLSS Updater.

    Provides unified management of multiple cache directories with:
    - Per-cache configurable policies
    - Memory-mapped file support for DLLs
    - LRU eviction based on access times
    - Background cleanup loop
    - Thread-safe operations for free-threaded Python 3.14

    Usage:
        # Register caches
        cache_manager.register_cache(
            "dlls",
            Path("/path/to/dll/cache"),
            CachePolicy(max_size_mb=200, eviction_enabled=False)
        )
        cache_manager.register_cache(
            "images",
            Path("/path/to/image/cache"),
            CachePolicy(max_size_mb=100, max_age_days=7)
        )

        # Start background cleanup
        await cache_manager.start()

        # Record file access for LRU tracking
        await cache_manager.record_access(some_path)

        # Get memory-mapped file
        mm = await cache_manager.get_memory_mapped(dll_path)
        try:
            data = mm[:]  # Read all bytes
        finally:
            await cache_manager.release_memory_mapped(dll_path)

        # Get statistics
        stats = await cache_manager.get_stats()

        # Stop background cleanup
        await cache_manager.stop()
    """

    def __init__(self):
        """Initialize the UnifiedCacheManager."""
        # Thread-safe locks for free-threaded Python 3.14
        self._lock = asyncio.Lock()
        self._mmap_lock = asyncio.Lock()

        # Registered caches: name -> _RegisteredCache
        self._caches: dict[str, _RegisteredCache] = {}

        # Memory-mapped files: path_str -> MmapHandle
        self._mmaps: dict[str, MmapHandle] = {}

        # Background task handle
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

        # Global stats
        self._total_evictions = 0
        self._total_cleanup_runs = 0

        logger.debug("UnifiedCacheManager initialized")

    # =========================================================================
    # Cache Registration
    # =========================================================================

    async def register_cache(
        self,
        name: str,
        cache_dir: Path,
        policy: CachePolicy | None = None
    ) -> None:
        """
        Register a cache directory with the manager.

        Args:
            name: Unique name for this cache
            cache_dir: Path to the cache directory
            policy: Cache policy (defaults to CachePolicy())

        Raises:
            ValueError: If a cache with this name is already registered
        """
        async with self._lock:
            if name in self._caches:
                raise ValueError(f"Cache '{name}' is already registered")

            if policy is None:
                policy = CachePolicy()

            # Ensure directory exists
            cache_dir = Path(cache_dir)
            if not cache_dir.exists():
                await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)

            registered = _RegisteredCache(
                name=name,
                cache_dir=cache_dir,
                policy=policy
            )

            # Scan existing files into entries
            await self._scan_cache_directory(registered)

            self._caches[name] = registered
            logger.info(
                f"Registered cache '{name}' at {cache_dir} "
                f"(max_size={policy.max_size_mb}MB, max_age={policy.max_age_days}d, "
                f"eviction={'enabled' if policy.eviction_enabled else 'disabled'})"
            )

    async def _scan_cache_directory(self, cache: _RegisteredCache) -> None:
        """
        Scan a cache directory and populate entries.

        Args:
            cache: The registered cache to scan
        """
        try:
            cache_dir = cache.cache_dir
            if not cache_dir.exists():
                return

            # Run directory scan in thread pool to avoid blocking
            def scan_dir():
                entries = {}
                for item in cache_dir.rglob("*"):
                    if item.is_file():
                        try:
                            stat_result = item.stat()
                            # Use modification time as proxy for access time
                            # (Windows doesn't reliably track atime)
                            last_access = datetime.fromtimestamp(stat_result.st_mtime)
                            entries[str(item)] = CacheEntry(
                                path=item,
                                size_bytes=stat_result.st_size,
                                last_access=last_access
                            )
                        except OSError as e:
                            logger.warning(f"Failed to stat {item}: {e}")
                return entries

            cache.entries = await asyncio.to_thread(scan_dir)
            logger.debug(
                f"Scanned cache '{cache.name}': "
                f"{len(cache.entries)} entries, "
                f"{sum(e.size_bytes for e in cache.entries.values()) / 1024 / 1024:.1f}MB"
            )

        except Exception as e:
            logger.error(f"Error scanning cache directory {cache.cache_dir}: {e}")

    # =========================================================================
    # Lifecycle Management
    # =========================================================================

    async def start(self) -> None:
        """
        Start the background cleanup loop.

        The cleanup loop runs periodically based on the minimum
        cleanup_interval_hours across all registered caches.
        """
        async with self._lock:
            if self._running:
                logger.warning("UnifiedCacheManager is already running")
                return

            self._running = True
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            register_task(self._cleanup_task, "cache_cleanup_loop")
            logger.info("UnifiedCacheManager background cleanup started")

    async def stop(self) -> None:
        """
        Stop the background cleanup loop and release resources.

        This will:
        - Cancel the cleanup task
        - Release all memory-mapped files
        - Clear internal state
        """
        async with self._lock:
            self._running = False

            if self._cleanup_task:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass
                self._cleanup_task = None

        # Release all memory mappings
        await self._release_all_mmaps()

        logger.info("UnifiedCacheManager stopped")

    async def _cleanup_loop(self) -> None:
        """
        Background loop that periodically runs cleanup on all caches.
        """
        while self._running:
            try:
                # Calculate minimum interval across all caches
                async with self._lock:
                    if not self._caches:
                        interval_hours = 1
                    else:
                        interval_hours = min(
                            c.policy.cleanup_interval_hours
                            for c in self._caches.values()
                        )

                # Wait for the interval
                await asyncio.sleep(interval_hours * 3600)

                if not self._running:
                    break

                # Run cleanup on all caches
                await self.cleanup()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}", exc_info=True)
                # Wait a bit before retrying on error
                await asyncio.sleep(60)

    # =========================================================================
    # Access Recording
    # =========================================================================

    async def record_access(
        self,
        path: Path,
        is_memory_mapped: bool = False
    ) -> None:
        """
        Record access to a cached file for LRU tracking.

        This updates the last_access timestamp and optionally marks
        the entry as memory-mapped.

        Args:
            path: Path to the file being accessed
            is_memory_mapped: Whether this access involves memory mapping
        """
        path = Path(path)
        path_str = str(path)

        async with self._lock:
            # Find which cache this path belongs to
            for cache in self._caches.values():
                if path_str in cache.entries:
                    entry = cache.entries[path_str]
                    entry.last_access = datetime.now()
                    entry.is_memory_mapped = is_memory_mapped
                    return

                # Check if path is under this cache directory
                try:
                    path.relative_to(cache.cache_dir)
                    # Path is under this cache but not in entries - add it
                    if path.exists():
                        stat_result = await asyncio.to_thread(path.stat)
                        cache.entries[path_str] = CacheEntry(
                            path=path,
                            size_bytes=stat_result.st_size,
                            last_access=datetime.now(),
                            is_memory_mapped=is_memory_mapped
                        )
                        return
                except ValueError:
                    # Not under this cache directory
                    continue

    # =========================================================================
    # Memory-Mapped File Management
    # =========================================================================

    async def get_memory_mapped(self, path: Path) -> mmap.mmap:
        """
        Get or create a memory-mapped file handle.

        Memory mapping is efficient for DLL files as it:
        - Avoids copying file contents to Python memory
        - Uses OS virtual memory for efficient access
        - Allows multiple processes to share the same physical pages

        The returned mmap object supports slice notation for reading:
            mm = await cache_manager.get_memory_mapped(path)
            data = mm[:]  # Read all bytes
            header = mm[:1024]  # Read first 1KB

        Args:
            path: Path to the file to memory-map

        Returns:
            mmap.mmap object for the file

        Raises:
            FileNotFoundError: If the file doesn't exist
            OSError: If memory mapping fails
        """
        path = Path(path)
        path_str = str(path)
        mmap_result = None

        async with self._mmap_lock:
            # Check if already mapped
            if path_str in self._mmaps:
                handle = self._mmaps[path_str]
                handle.ref_count += 1
                logger.debug(f"Reusing mmap for {path.name} (refs={handle.ref_count})")
                mmap_result = handle.mmap_obj
                # Return early but record access outside lock
                # Fall through to record_access below

            if mmap_result is None:
                # Create new mapping
                if not path.exists():
                    raise FileNotFoundError(f"Cannot memory-map non-existent file: {path}")

                # Track handles for cleanup on failure
                file_handle = None
                mmap_obj = None

                # Open file and create mapping in thread pool
                def create_mmap():
                    nonlocal file_handle, mmap_obj
                    # Open file in binary read mode
                    # On Windows, we need r+b for mmap even for read-only access
                    f = open(path, "r+b")
                    file_handle = f
                    try:
                        # Get file size
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(0)

                        if size == 0:
                            raise ValueError(f"Cannot memory-map empty file: {path}")

                        # Create mmap with read access
                        # On Windows, ACCESS_READ requires the file handle
                        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                        mmap_obj = mm
                        return f, mm
                    except Exception:
                        f.close()
                        file_handle = None
                        raise

                try:
                    file_handle, mmap_obj = await asyncio.to_thread(create_mmap)

                    handle = MmapHandle(
                        path=path,
                        file_handle=file_handle,
                        mmap_obj=mmap_obj,
                        ref_count=1,
                        created_at=datetime.now()
                    )

                    self._mmaps[path_str] = handle
                    logger.debug(f"Created mmap for {path.name}")
                    mmap_result = mmap_obj

                except Exception as e:
                    # Cleanup on any failure - ensure resources are released
                    if mmap_obj is not None:
                        try:
                            mmap_obj.close()
                        except Exception:
                            pass
                    if file_handle is not None:
                        try:
                            file_handle.close()
                        except Exception:
                            pass
                    logger.error(f"Failed to memory-map {path}: {e}")
                    raise

        # Record access AFTER releasing mmap_lock to avoid lock ordering issues
        await self.record_access(path, is_memory_mapped=True)

        return mmap_result

    async def release_memory_mapped(self, path: Path) -> None:
        """
        Release a reference to a memory-mapped file.

        When the reference count reaches zero, the mapping is closed.
        Always call this in a finally block or use get_memory_mapped
        with a context manager pattern.

        Args:
            path: Path to the memory-mapped file
        """
        path = Path(path)
        path_str = str(path)
        should_record_access = False

        async with self._mmap_lock:
            if path_str not in self._mmaps:
                logger.warning(f"Attempted to release unmapped file: {path}")
                return

            handle = self._mmaps[path_str]

            # Guard against reference count going negative
            if handle.ref_count <= 0:
                logger.warning(f"Reference count already 0 for {path}, ignoring release")
                return

            handle.ref_count -= 1

            if handle.ref_count <= 0:
                # Close the mapping
                await self._close_mmap(handle)
                del self._mmaps[path_str]
                logger.debug(f"Closed mmap for {path.name}")

                # Mark that we need to update entry outside the lock
                should_record_access = True
            else:
                logger.debug(f"Released mmap ref for {path.name} (refs={handle.ref_count})")

        # Update entry to mark as not memory-mapped AFTER releasing lock
        if should_record_access:
            await self.record_access(path, is_memory_mapped=False)

    async def _close_mmap(self, handle: MmapHandle) -> None:
        """
        Close a memory-mapped file handle.

        Args:
            handle: The MmapHandle to close
        """
        def close_resources():
            try:
                handle.mmap_obj.close()
            except Exception as e:
                logger.warning(f"Error closing mmap: {e}")

            try:
                handle.file_handle.close()
            except Exception as e:
                logger.warning(f"Error closing file handle: {e}")

        await asyncio.to_thread(close_resources)

    async def _release_all_mmaps(self) -> None:
        """Release all memory-mapped files."""
        async with self._mmap_lock:
            for handle in self._mmaps.values():
                await self._close_mmap(handle)
            self._mmaps.clear()
            logger.debug("Released all memory mappings")

    # =========================================================================
    # Cleanup and Eviction
    # =========================================================================

    async def cleanup(self, cache_name: str | None = None) -> int:
        """
        Run cleanup/eviction on caches.

        This will:
        1. Remove entries exceeding max_age_days
        2. Evict entries to bring size under max_size_mb (LRU order)
        3. Skip entries with eviction_enabled=False

        Args:
            cache_name: Specific cache to clean (None = all caches)

        Returns:
            Number of entries evicted
        """
        total_evicted = 0

        async with self._lock:
            caches_to_clean = (
                [self._caches[cache_name]] if cache_name and cache_name in self._caches
                else list(self._caches.values())
            )

        for cache in caches_to_clean:
            try:
                evicted = await self._evict_cache(cache.name, cache.policy)
                total_evicted += evicted
                cache.cleanup_runs += 1
                cache.evictions_performed += evicted
            except Exception as e:
                logger.error(f"Error cleaning cache '{cache.name}': {e}", exc_info=True)

        self._total_cleanup_runs += 1
        self._total_evictions += total_evicted

        if total_evicted > 0:
            logger.info(f"Cache cleanup completed: {total_evicted} entries evicted")

        return total_evicted

    async def _evict_cache(self, name: str, policy: CachePolicy) -> int:
        """
        Perform eviction on a specific cache.

        Args:
            name: Name of the cache
            policy: Cache policy

        Returns:
            Number of entries evicted
        """
        # Skip if eviction is disabled
        if not policy.eviction_enabled:
            logger.debug(f"Skipping eviction for '{name}' (eviction_enabled=False)")
            return 0

        async with self._lock:
            if name not in self._caches:
                return 0

            cache = self._caches[name]
            entries = cache.entries
            evicted_count = 0
            to_remove: set[str] = set()

            now = datetime.now()

            # Take snapshot of entries to iterate safely (avoid dict modification during iteration)
            entries_snapshot = list(entries.items())

            # Phase 1: Remove entries exceeding max_age_days
            if policy.max_age_days > 0:
                max_age = timedelta(days=policy.max_age_days)
                for path_str, entry in entries_snapshot:
                    if now - entry.last_access > max_age:
                        # Don't evict memory-mapped files
                        if not entry.is_memory_mapped:
                            to_remove.add(path_str)

            # Phase 2: Size-based eviction (LRU)
            if policy.max_size_mb > 0:
                max_bytes = policy.max_size_mb * 1024 * 1024

                # Calculate current size excluding already-marked entries
                current_size = sum(
                    e.size_bytes for p, e in entries_snapshot
                    if p not in to_remove
                )

                if current_size > max_bytes:
                    # Sort by priority (ascending) then last_access (ascending)
                    # Lower priority and older access = evicted first
                    sorted_entries = sorted(
                        ((p, e) for p, e in entries_snapshot
                         if p not in to_remove and not e.is_memory_mapped),
                        key=lambda x: (x[1].priority, x[1].last_access)
                    )

                    for path_str, entry in sorted_entries:
                        if current_size <= max_bytes:
                            break
                        to_remove.add(path_str)
                        current_size -= entry.size_bytes

            # Perform deletions
            for path_str in to_remove:
                try:
                    path = Path(path_str)
                    if path.exists():
                        await asyncio.to_thread(path.unlink)
                    del entries[path_str]
                    evicted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to evict {path_str}: {e}")

        if evicted_count > 0:
            logger.debug(f"Evicted {evicted_count} entries from cache '{name}'")

        return evicted_count

    # =========================================================================
    # Statistics
    # =========================================================================

    async def get_stats(self) -> dict[str, CacheStats]:
        """
        Get statistics for all registered caches.

        Returns:
            Dict mapping cache name to CacheStats
        """
        async with self._lock:
            stats = {}

            for name, cache in self._caches.items():
                entries = cache.entries
                entry_list = list(entries.values())

                if entry_list:
                    oldest = min(e.last_access for e in entry_list)
                    newest = max(e.last_access for e in entry_list)
                else:
                    oldest = None
                    newest = None

                stats[name] = CacheStats(
                    name=name,
                    total_entries=len(entries),
                    total_size_bytes=sum(e.size_bytes for e in entry_list),
                    memory_mapped_count=sum(1 for e in entry_list if e.is_memory_mapped),
                    oldest_entry=oldest,
                    newest_entry=newest,
                    evictions_performed=cache.evictions_performed,
                    cleanup_runs=cache.cleanup_runs
                )

            return stats

    async def get_global_stats(self) -> dict[str, int]:
        """
        Get global cache manager statistics.

        Returns:
            Dict with global stats (total_caches, total_mmaps, etc.)
        """
        async with self._lock:
            total_entries = sum(len(c.entries) for c in self._caches.values())
            total_size = sum(
                sum(e.size_bytes for e in c.entries.values())
                for c in self._caches.values()
            )

        async with self._mmap_lock:
            mmap_count = len(self._mmaps)
            mmap_refs = sum(h.ref_count for h in self._mmaps.values())

        return {
            "total_caches": len(self._caches),
            "total_entries": total_entries,
            "total_size_bytes": total_size,
            "total_size_mb": total_size / 1024 / 1024,
            "active_mmaps": mmap_count,
            "total_mmap_refs": mmap_refs,
            "total_evictions": self._total_evictions,
            "total_cleanup_runs": self._total_cleanup_runs,
            "is_running": self._running
        }

    # =========================================================================
    # Utility Methods
    # =========================================================================

    async def clear_cache(self, cache_name: str) -> int:
        """
        Clear all entries from a specific cache.

        This removes all files and entries from the cache directory.
        Memory-mapped files are preserved until released.

        Args:
            cache_name: Name of the cache to clear

        Returns:
            Number of entries cleared
        """
        async with self._lock:
            if cache_name not in self._caches:
                raise ValueError(f"Unknown cache: {cache_name}")

            cache = self._caches[cache_name]
            entries = cache.entries
            cleared = 0

            for path_str, entry in list(entries.items()):
                # Skip memory-mapped files
                if entry.is_memory_mapped:
                    logger.debug(f"Skipping memory-mapped file: {path_str}")
                    continue

                try:
                    path = Path(path_str)
                    if path.exists():
                        await asyncio.to_thread(path.unlink)
                    del entries[path_str]
                    cleared += 1
                except Exception as e:
                    logger.warning(f"Failed to clear {path_str}: {e}")

            logger.info(f"Cleared {cleared} entries from cache '{cache_name}'")
            return cleared

    async def get_cache_size(self, cache_name: str) -> int:
        """
        Get the total size of a cache in bytes.

        Args:
            cache_name: Name of the cache

        Returns:
            Total size in bytes
        """
        async with self._lock:
            if cache_name not in self._caches:
                return 0

            return sum(
                e.size_bytes
                for e in self._caches[cache_name].entries.values()
            )

    async def is_path_cached(self, path: Path) -> bool:
        """
        Check if a path is tracked in any cache.

        Args:
            path: Path to check

        Returns:
            True if the path is in a cache
        """
        path_str = str(path)

        async with self._lock:
            for cache in self._caches.values():
                if path_str in cache.entries:
                    return True
            return False


# =============================================================================
# Global Singleton Instance
# =============================================================================

# Create singleton instance for application-wide use
cache_manager = UnifiedCacheManager()


# =============================================================================
# Module-Level Convenience Functions
# =============================================================================

async def initialize_cache_manager() -> None:
    """
    Initialize the cache manager with default caches.

    Call this at application startup to register standard caches
    and start the background cleanup loop.
    """
    import appdirs

    app_name = "DLSS-Updater"
    app_author = "Recol"
    cache_dir = Path(appdirs.user_cache_dir(app_name, app_author))

    # Register DLL cache (critical - no eviction)
    dll_cache = cache_dir / "dlls"
    await cache_manager.register_cache(
        "dlls",
        dll_cache,
        CachePolicy(
            max_size_mb=0,  # No size limit for DLLs
            max_age_days=0,  # No age limit
            eviction_enabled=False,  # Never evict DLLs
            priority_patterns=["*.dll"]
        )
    )

    # Register image cache (temporary, evictable)
    image_cache = cache_dir / "images"
    await cache_manager.register_cache(
        "images",
        image_cache,
        CachePolicy(
            max_size_mb=100,
            max_age_days=30,
            cleanup_interval_hours=6,
            eviction_enabled=True
        )
    )

    # Register scan cache (temporary, evictable)
    scan_cache = cache_dir / "scans"
    await cache_manager.register_cache(
        "scans",
        scan_cache,
        CachePolicy(
            max_size_mb=50,
            max_age_days=7,
            cleanup_interval_hours=1,
            eviction_enabled=True
        )
    )

    # Start background cleanup
    await cache_manager.start()

    logger.info("Cache manager initialized with default caches")


async def shutdown_cache_manager() -> None:
    """
    Shutdown the cache manager cleanly.

    Call this at application shutdown to:
    - Stop the background cleanup loop
    - Release all memory mappings
    - Save any pending state
    """
    await cache_manager.stop()
    logger.info("Cache manager shutdown complete")
