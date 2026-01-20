"""
Database Manager for DLSS Updater
SQLite-based persistent storage for games, DLLs, backups, and update history

Performance optimizations:
- Connection pooling with aiosqlite for true async operations
- Batch upsert operations for games and DLLs
- Thread-local connection reuse for sync operations
"""

import sqlite3
import asyncio
import logging
import threading
from pathlib import Path
from typing import Any
from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite

from dlss_updater.logger import setup_logger
from dlss_updater.platform_utils import APP_CONFIG_DIR
from dlss_updater.models import (
    Game, GameDLL, DLLBackup, UpdateHistory, SteamImage,
    GameDLLBackup, GameBackupSummary, GameWithBackupCount, MergedGame
)

logger = setup_logger()

# Thread-safety lock for singleton pattern (free-threading Python 3.14+)
_db_manager_lock = threading.Lock()


def merge_games_by_name(games: list[Game]) -> list[MergedGame]:
    """Merge games with same name (case-insensitive) into MergedGame entries.

    Args:
        games: List of Game objects to merge

    Returns:
        List of MergedGame objects, each representing one or more games
    """
    from collections import defaultdict

    # Group by lowercase name
    name_groups: dict[str, list[Game]] = defaultdict(list)
    for game in games:
        name_groups[game.name.lower()].append(game)

    merged = []
    for name_lower, group in name_groups.items():
        if len(group) == 1:
            # Single game, wrap in MergedGame for consistency
            game = group[0]
            merged.append(MergedGame(
                primary_game=game,
                all_game_ids=[game.id],
                all_paths=[game.path],
            ))
        else:
            # Multiple games with same name - merge
            primary = group[0]  # Use first as primary
            merged.append(MergedGame(
                primary_game=primary,
                all_game_ids=[g.id for g in group],
                all_paths=[g.path for g in group],
            ))

    return merged


class DatabaseManager:
    """
    Singleton database manager for DLSS Updater
    Handles all database operations with async wrappers
    """
    _instance = None

    def __new__(cls):
        # Thread-safe singleton for free-threaded Python 3.14+
        # Always acquire lock first - outer check is NOT safe without GIL
        with _db_manager_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.db_path = APP_CONFIG_DIR / "games.db"
            self.initialized = False

            # Connection pool for async operations (aiosqlite)
            self._async_pool: list[Any] = []  # List of aiosqlite.Connection
            self._pool_size = 5
            self._pool_lock = asyncio.Lock()
            self._pool_semaphore: asyncio.Semaphore | None = None
            self._pool_active = False  # Track pool lifecycle state

            # Thread-local storage for sync operations (connection reuse)
            self._thread_local = threading.local()

            logger.info(f"Database path: {self.db_path}")

    async def initialize(self):
        """Initialize database schema only (pool creation is lazy via ensure_pool)"""
        if self.initialized:
            return

        await asyncio.to_thread(self._create_schema)

        self.initialized = True
        logger.info("Database schema initialized successfully")

    async def _create_pool(self):
        """
        Create the async connection pool.

        Thread-safe: Uses _pool_lock to prevent concurrent pool creation.
        Called lazily by ensure_pool() when first database operation occurs.
        """
        async with self._pool_lock:
            # Double-check after acquiring lock (another coroutine may have created pool)
            if self._pool_active:
                return

            # Initialize semaphore for connection pool
            if self._pool_semaphore is None:
                self._pool_semaphore = asyncio.Semaphore(self._pool_size)

            # Create async connection pool
            try:
                for _ in range(self._pool_size):
                    conn = await aiosqlite.connect(str(self.db_path))
                    conn.row_factory = aiosqlite.Row
                    self._async_pool.append(conn)

                self._pool_active = True
                logger.info(f"Connection pool created ({len(self._async_pool)} connections)")
            except Exception as e:
                logger.error(f"Failed to create async pool: {e}")
                raise

    async def ensure_pool(self):
        """
        Ensure the connection pool is ready before database operations.

        This method implements lazy pool initialization - the pool is only
        created when first needed, not at application startup. This improves
        startup performance and resource utilization.

        Thread-safe for free-threaded Python 3.14: Pool state check and
        creation are both done under the same lock to prevent TOCTOU races.
        """
        # Check and create pool atomically under lock to prevent TOCTOU race
        async with self._pool_lock:
            if self._pool_active:
                return

            # Initialize semaphore for connection pool
            if self._pool_semaphore is None:
                self._pool_semaphore = asyncio.Semaphore(self._pool_size)

            # Create async connection pool
            try:
                for _ in range(self._pool_size):
                    conn = await aiosqlite.connect(str(self.db_path))
                    conn.row_factory = aiosqlite.Row
                    self._async_pool.append(conn)

                self._pool_active = True
                logger.info(f"Connection pool created ({len(self._async_pool)} connections)")
            except Exception as e:
                logger.error(f"Failed to create async pool: {e}")
                raise

    @asynccontextmanager
    async def get_async_connection(self):
        """
        Get an async connection from the pool.

        Thread-safe for free-threaded Python 3.14: Validates connections
        before returning to pool to prevent leaking broken connections.

        Usage:
            async with db_manager.get_async_connection() as conn:
                await conn.execute(...)
        """
        await self._pool_semaphore.acquire()
        conn = None
        conn_valid = True

        try:
            async with self._pool_lock:
                if self._async_pool:
                    conn = self._async_pool.pop()

            # Validate pooled connection or create new one
            if conn is not None:
                try:
                    # Quick validation query
                    await conn.execute("SELECT 1")
                except Exception:
                    # Connection is broken, create fresh one
                    conn_valid = False
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    conn = await aiosqlite.connect(str(self.db_path))
                    conn.row_factory = aiosqlite.Row
                    conn_valid = True
            else:
                # Pool exhausted, create new connection
                conn = await aiosqlite.connect(str(self.db_path))
                conn.row_factory = aiosqlite.Row

            yield conn

        except Exception:
            # Mark connection as invalid on any exception during use
            conn_valid = False
            raise
        finally:
            if conn is not None:
                if conn_valid:
                    # Return valid connection to pool
                    async with self._pool_lock:
                        self._async_pool.append(conn)
                else:
                    # Close invalid connection instead of returning to pool
                    try:
                        await conn.close()
                    except Exception:
                        pass
            self._pool_semaphore.release()

    def _get_thread_connection(self) -> sqlite3.Connection:
        """
        Get a thread-local reusable connection for sync operations.
        Reuses connection within the same thread to reduce overhead.
        """
        if not hasattr(self._thread_local, 'connection') or self._thread_local.connection is None:
            self._thread_local.connection = sqlite3.connect(str(self.db_path))
            self._thread_local.connection.row_factory = sqlite3.Row
        return self._thread_local.connection

    def _close_thread_connection(self):
        """
        Close thread-local connection if it exists.

        Thread-safe: Only affects the calling thread's connection.
        Should be called when a thread is about to exit or during cleanup.
        """
        if hasattr(self._thread_local, 'connection') and self._thread_local.connection:
            try:
                self._thread_local.connection.close()
            except Exception as e:
                logger.debug(f"Error closing thread-local connection: {e}")
            finally:
                self._thread_local.connection = None

    async def close(self):
        """
        Close all pooled connections and reset pool state.

        This method safely closes all async connections in the pool,
        clears the pool list, resets the pool state to inactive, and
        closes any thread-local connection for the calling thread.

        The pool can be recreated by calling ensure_pool() again.

        Thread-safe for free-threaded Python 3.14: Uses asyncio.Lock for
        async pool and handles thread-local connections separately.
        """
        # Close thread-local connection first (sync, thread-specific)
        self._close_thread_connection()

        async with self._pool_lock:
            if not self._pool_active and not self._async_pool:
                logger.debug("Pool already closed or never initialized")
                return

            closed_count = 0
            for conn in self._async_pool:
                try:
                    await conn.close()
                    closed_count += 1
                except Exception as e:
                    logger.debug(f"Error closing pooled connection: {e}")

            self._async_pool.clear()
            self._pool_active = False

            logger.info(f"Database pool closed ({closed_count} connections released)")

    def _create_schema(self):
        """Create database schema (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))  # New connection for schema setup
        cursor = conn.cursor()

        try:
            # Enable WAL mode for better write performance
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")

            # Games table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    launcher TEXT NOT NULL,
                    steam_app_id INTEGER,
                    last_scanned TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # DLLs found in games
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS game_dlls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    dll_type TEXT NOT NULL,
                    dll_filename TEXT NOT NULL,
                    dll_path TEXT NOT NULL UNIQUE,
                    current_version TEXT,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                )
            """)

            # Backup metadata
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dll_backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_dll_id INTEGER NOT NULL,
                    backup_path TEXT NOT NULL,
                    original_version TEXT,
                    backup_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    backup_size INTEGER,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY (game_dll_id) REFERENCES game_dlls(id) ON DELETE CASCADE
                )
            """)

            # Update history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS update_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_dll_id INTEGER NOT NULL,
                    from_version TEXT,
                    to_version TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    success BOOLEAN NOT NULL,
                    FOREIGN KEY (game_dll_id) REFERENCES game_dlls(id) ON DELETE CASCADE
                )
            """)

            # Steam image cache
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS steam_images (
                    steam_app_id INTEGER PRIMARY KEY,
                    image_url TEXT NOT NULL,
                    local_path TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    fetch_failed BOOLEAN DEFAULT 0
                )
            """)

            # Steam app list cache (for name-based lookup) - LEGACY table, kept for migration
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS steam_app_list (
                    app_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Steam apps table with FTS5 for high-performance name search
            # Eliminates ~20-30 MB in-memory index (207K+ entries)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS steam_apps (
                    appid INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_normalized TEXT NOT NULL
                )
            """)

            # FTS5 virtual table for full-text search on Steam app names
            # content='steam_apps' means FTS5 stores only the index, not the content
            # content_rowid='appid' links FTS5 rows to steam_apps.appid
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS steam_apps_fts USING fts5(
                    name,
                    name_normalized,
                    content='steam_apps',
                    content_rowid='appid'
                )
            """)

            # Triggers to keep FTS5 index in sync with steam_apps table
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS steam_apps_ai AFTER INSERT ON steam_apps BEGIN
                    INSERT INTO steam_apps_fts(rowid, name, name_normalized)
                    VALUES (new.appid, new.name, new.name_normalized);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS steam_apps_ad AFTER DELETE ON steam_apps BEGIN
                    INSERT INTO steam_apps_fts(steam_apps_fts, rowid, name, name_normalized)
                    VALUES ('delete', old.appid, old.name, old.name_normalized);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS steam_apps_au AFTER UPDATE ON steam_apps BEGIN
                    INSERT INTO steam_apps_fts(steam_apps_fts, rowid, name, name_normalized)
                    VALUES ('delete', old.appid, old.name, old.name_normalized);
                    INSERT INTO steam_apps_fts(rowid, name, name_normalized)
                    VALUES (new.appid, new.name, new.name_normalized);
                END
            """)

            # Search history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    launcher TEXT,
                    result_count INTEGER DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_launcher ON games(launcher)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_steam_app_id ON games(steam_app_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_dlls_game_id ON game_dlls(game_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_dlls_dll_type ON game_dlls(dll_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dll_backups_game_dll_id ON dll_backups(game_dll_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dll_backups_active ON dll_backups(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_update_history_game_dll_id ON update_history(game_dll_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_steam_name ON steam_app_list(name COLLATE NOCASE)")

            # Index for fast exact match lookups on normalized names (spaceless)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_steam_apps_normalized ON steam_apps(name_normalized)")

            # Search-related indexes for fast game name lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_name ON games(name COLLATE NOCASE)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_name_launcher ON games(name COLLATE NOCASE, launcher)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_history_timestamp ON search_history(timestamp DESC)")

            conn.commit()
            logger.info("Database schema created successfully")

        except Exception as e:
            logger.error(f"Error creating database schema: {e}", exc_info=True)
            conn.rollback()
            raise
        finally:
            conn.close()

    # ===== Game Operations =====

    async def upsert_game(self, game_data: dict[str, Any]) -> Game | None:
        """Insert or update game record"""
        return await asyncio.to_thread(self._upsert_game, game_data)

    def _upsert_game(self, game_data: dict[str, Any]) -> Game | None:
        """Upsert game (runs in thread) - uses thread-local connection"""
        conn = self._get_thread_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO games (name, path, launcher, steam_app_id, last_scanned)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    name = excluded.name,
                    steam_app_id = COALESCE(excluded.steam_app_id, games.steam_app_id),
                    last_scanned = CURRENT_TIMESTAMP
                RETURNING *
            """, (
                game_data['name'],
                game_data['path'],
                game_data['launcher'],
                game_data.get('steam_app_id')
            ))

            row = cursor.fetchone()
            conn.commit()

            if row:
                return Game(
                    id=row[0],
                    name=row[1],
                    path=row[2],
                    launcher=row[3],
                    steam_app_id=row[4],
                    last_scanned=datetime.fromisoformat(row[5]),
                    created_at=datetime.fromisoformat(row[6])
                )
            return None

        except Exception as e:
            logger.error(f"Error upserting game: {e}", exc_info=True)
            conn.rollback()
            return None
        # Note: Don't close thread-local connection - it's reused

    async def get_games_grouped_by_launcher(self) -> dict[str, list[Game]]:
        """Get all games grouped by launcher"""
        return await asyncio.to_thread(self._get_games_grouped_by_launcher)

    def _get_games_grouped_by_launcher(self) -> dict[str, list[Game]]:
        """Get games grouped by launcher (runs in thread) - uses thread-local connection"""
        conn = self._get_thread_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    MIN(id) as id,
                    name,
                    MIN(path) as path,
                    launcher,
                    steam_app_id,
                    MAX(last_scanned) as last_scanned,
                    MIN(created_at) as created_at
                FROM games
                GROUP BY name, launcher, steam_app_id
                ORDER BY launcher, name
            """)

            games_by_launcher = {}
            for row in cursor:  # Direct iteration instead of fetchall()
                game = Game(
                    id=row[0],
                    name=row[1],
                    path=row[2],
                    launcher=row[3],
                    steam_app_id=row[4],
                    last_scanned=datetime.fromisoformat(row[5]),
                    created_at=datetime.fromisoformat(row[6])
                )

                if game.launcher not in games_by_launcher:
                    games_by_launcher[game.launcher] = []
                games_by_launcher[game.launcher].append(game)

            return games_by_launcher

        except Exception as e:
            logger.error(f"Error getting games by launcher: {e}", exc_info=True)
            return {}

    async def get_all_games_by_launcher(self) -> dict[str, list[Game]]:
        """Get all games grouped by launcher without merging duplicates.

        Unlike get_games_grouped_by_launcher(), this returns ALL game records
        even if they have the same name. Use merge_games_by_name() in UI layer
        to properly merge while preserving all paths.
        """
        return await asyncio.to_thread(self._get_all_games_by_launcher)

    def _get_all_games_by_launcher(self) -> dict[str, list[Game]]:
        """Get all games by launcher (no GROUP BY) - uses thread-local connection"""
        conn = self._get_thread_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, name, path, launcher, steam_app_id, last_scanned, created_at
                FROM games
                ORDER BY launcher, name
            """)

            games_by_launcher = {}
            for row in cursor:
                game = Game(
                    id=row[0],
                    name=row[1],
                    path=row[2],
                    launcher=row[3],
                    steam_app_id=row[4],
                    last_scanned=datetime.fromisoformat(row[5]),
                    created_at=datetime.fromisoformat(row[6])
                )

                if game.launcher not in games_by_launcher:
                    games_by_launcher[game.launcher] = []
                games_by_launcher[game.launcher].append(game)

            return games_by_launcher

        except Exception as e:
            logger.error(f"Error getting all games by launcher: {e}", exc_info=True)
            return {}


    async def delete_all_games(self):
        """
        Delete all games from database (CASCADE deletes game_dlls, backups, update_history)
        Keeps Steam cache data (steam_images, steam_app_list)

        Returns:
            Number of games deleted
        """
        return await asyncio.to_thread(self._delete_all_games)

    def _delete_all_games(self):
        """Delete all games (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Count games before deletion
            cursor.execute("SELECT COUNT(*) FROM games")
            count = cursor.fetchone()[0]

            # Delete all games (CASCADE will delete game_dlls, dll_backups, update_history)
            cursor.execute("DELETE FROM games")

            conn.commit()
            logger.info(f"Deleted {count} games from database")
            return count

        except Exception as e:
            logger.error(f"Error deleting all games: {e}", exc_info=True)
            conn.rollback()
            return 0
        finally:
            conn.close()

    async def cleanup_duplicate_games(self):
        """
        Clean up duplicate game entries by merging games with the same name and launcher
        Keeps the entry with the shortest path (most likely the true root)

        Returns:
            Number of duplicate entries removed
        """
        return await asyncio.to_thread(self._cleanup_duplicate_games)

    def _cleanup_duplicate_games(self):
        """Cleanup duplicate games (runs in thread) - optimized with batched SQL"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Find duplicate games (same name + launcher)
            cursor.execute("""
                SELECT name, launcher, GROUP_CONCAT(id) as ids, GROUP_CONCAT(path) as paths
                FROM games
                GROUP BY name, launcher
                HAVING COUNT(*) > 1
            """)

            duplicates = cursor.fetchall()

            # Collect all mappings for batched operations
            dll_migrations = []  # List of (keep_id, remove_id) tuples
            all_remove_ids = []  # All IDs to delete

            for name, launcher, ids_str, paths_str in duplicates:
                ids = [int(x) for x in ids_str.split(',')]
                paths = paths_str.split(',')

                # Find the shortest path (most likely the true root)
                shortest_idx = min(range(len(paths)), key=lambda i: len(paths[i]))
                keep_id = ids[shortest_idx]
                remove_ids = [id for id in ids if id != keep_id]

                # Collect migrations: (keep_id, remove_id) for batch update
                dll_migrations.extend((keep_id, remove_id) for remove_id in remove_ids)
                all_remove_ids.extend(remove_ids)

                logger.debug(f"Queued merge: {len(remove_ids)} duplicate entries for '{name}' ({launcher})")

            if not all_remove_ids:
                logger.info("No duplicate games found")
                return 0

            # Batch migrate DLLs from duplicate games to kept games
            cursor.executemany("""
                UPDATE game_dlls
                SET game_id = ?
                WHERE game_id = ?
            """, dll_migrations)

            # Batch delete duplicate game entries
            placeholders = ','.join('?' * len(all_remove_ids))
            cursor.execute(f"""
                DELETE FROM games
                WHERE id IN ({placeholders})
            """, all_remove_ids)

            conn.commit()
            merged_count = len(all_remove_ids)
            logger.info(f"Cleaned up {merged_count} duplicate game entries")
            return merged_count

        except sqlite3.OperationalError as e:
            if "readonly database" in str(e):
                logger.warning(f"Database is read-only. "
                              f"To fix: chown $USER:$USER {self.db_path}")
            else:
                logger.error(f"Error cleaning up duplicate games: {e}", exc_info=True)
            conn.rollback()
            return 0
        except Exception as e:
            logger.error(f"Error cleaning up duplicate games: {e}", exc_info=True)
            conn.rollback()
            return 0
        finally:
            conn.close()

    # ===== GameDLL Operations =====

    async def upsert_game_dll(self, dll_data: dict[str, Any]) -> GameDLL | None:
        """Insert or update game DLL record"""
        return await asyncio.to_thread(self._upsert_game_dll, dll_data)

    def _upsert_game_dll(self, dll_data: dict[str, Any]) -> GameDLL | None:
        """Upsert game DLL (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO game_dlls (game_id, dll_type, dll_filename, dll_path, current_version)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(dll_path) DO UPDATE SET
                    game_id = excluded.game_id,
                    dll_type = excluded.dll_type,
                    dll_filename = excluded.dll_filename,
                    current_version = excluded.current_version,
                    detected_at = CURRENT_TIMESTAMP
                RETURNING *
            """, (
                dll_data['game_id'],
                dll_data['dll_type'],
                dll_data['dll_filename'],
                dll_data['dll_path'],
                dll_data.get('current_version')
            ))

            row = cursor.fetchone()
            conn.commit()

            if row:
                return GameDLL(
                    id=row[0],
                    game_id=row[1],
                    dll_type=row[2],
                    dll_filename=row[3],
                    dll_path=row[4],
                    current_version=row[5],
                    detected_at=datetime.fromisoformat(row[6])
                )
            return None

        except Exception as e:
            logger.error(f"Error upserting game DLL: {e}", exc_info=True)
            conn.rollback()
            return None
        finally:
            conn.close()

    async def get_game_dll_by_path(self, dll_path: str) -> GameDLL | None:
        """Get game DLL by path"""
        return await asyncio.to_thread(self._get_game_dll_by_path, dll_path)

    def _get_game_dll_by_path(self, dll_path: str) -> GameDLL | None:
        """Get game DLL by path (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, game_id, dll_type, dll_filename, dll_path, current_version, detected_at
                FROM game_dlls
                WHERE dll_path = ?
            """, (dll_path,))

            row = cursor.fetchone()
            if row:
                return GameDLL(
                    id=row[0],
                    game_id=row[1],
                    dll_type=row[2],
                    dll_filename=row[3],
                    dll_path=row[4],
                    current_version=row[5],
                    detected_at=datetime.fromisoformat(row[6])
                )
            return None

        except Exception as e:
            logger.error(f"Error getting game DLL by path: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    async def get_dlls_for_game(self, game_id: int) -> list[GameDLL]:
        """Get all DLLs for a specific game"""
        return await asyncio.to_thread(self._get_dlls_for_game, game_id)

    def _get_dlls_for_game(self, game_id: int) -> list[GameDLL]:
        """Get DLLs for game (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, game_id, dll_type, dll_filename, dll_path, current_version, detected_at
                FROM game_dlls
                WHERE game_id = ?
            """, (game_id,))

            dlls = []
            for row in cursor.fetchall():
                dlls.append(GameDLL(
                    id=row[0],
                    game_id=row[1],
                    dll_type=row[2],
                    dll_filename=row[3],
                    dll_path=row[4],
                    current_version=row[5],
                    detected_at=datetime.fromisoformat(row[6])
                ))

            return dlls

        except Exception as e:
            logger.error(f"Error getting DLLs for game: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def get_game_dll_by_path(self, dll_path: str) -> GameDLL | None:
        """Get DLL record by file path"""
        return await asyncio.to_thread(self._get_game_dll_by_path, dll_path)

    def _get_game_dll_by_path(self, dll_path: str) -> GameDLL | None:
        """Get DLL by path (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, game_id, dll_type, dll_filename, dll_path, current_version, detected_at
                FROM game_dlls
                WHERE dll_path = ?
            """, (dll_path,))

            row = cursor.fetchone()
            if row:
                return GameDLL(
                    id=row[0],
                    game_id=row[1],
                    dll_type=row[2],
                    dll_filename=row[3],
                    dll_path=row[4],
                    current_version=row[5],
                    detected_at=datetime.fromisoformat(row[6])
                )
            return None

        except Exception as e:
            logger.error(f"Error getting DLL by path: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    async def update_game_dll_version(self, dll_id: int, new_version: str):
        """Update DLL version"""
        return await asyncio.to_thread(self._update_game_dll_version, dll_id, new_version)

    def _update_game_dll_version(self, dll_id: int, new_version: str):
        """Update DLL version (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE game_dlls
                SET current_version = ?
                WHERE id = ?
            """, (new_version, dll_id))

            conn.commit()

        except Exception as e:
            logger.error(f"Error updating DLL version: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    # ===== Batch Operations (Performance Optimized) =====

    async def batch_upsert_games(self, games: list[dict[str, Any]]) -> dict[str, Game]:
        """
        Batch upsert multiple games in a single transaction.

        Performance: O(1) transaction vs O(n) individual transactions
        Reduces database roundtrips from N to 1.

        Args:
            games: List of game dicts with keys: name, path, launcher, steam_app_id

        Returns:
            Dict mapping path to Game object
        """
        if not games:
            return {}

        return await asyncio.to_thread(self._batch_upsert_games, games)

    def _batch_upsert_games(self, games: list[dict[str, Any]]) -> dict[str, Game]:
        """Batch upsert games (runs in thread) - uses thread-local connection"""
        conn = self._get_thread_connection()
        cursor = conn.cursor()
        result = {}

        try:
            # Use executemany for batch insert
            game_data = [
                (g['name'], g['path'], g['launcher'], g.get('steam_app_id'))
                for g in games
            ]

            cursor.executemany("""
                INSERT INTO games (name, path, launcher, steam_app_id, last_scanned)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    name = excluded.name,
                    steam_app_id = COALESCE(excluded.steam_app_id, games.steam_app_id),
                    last_scanned = CURRENT_TIMESTAMP
            """, game_data)

            conn.commit()

            # Fetch all inserted/updated records
            paths = [g['path'] for g in games]
            placeholders = ','.join('?' * len(paths))

            cursor.execute(f"""
                SELECT id, name, path, launcher, steam_app_id, last_scanned, created_at
                FROM games
                WHERE path IN ({placeholders})
            """, paths)

            for row in cursor:  # Direct iteration
                game = Game(
                    id=row[0],
                    name=row[1],
                    path=row[2],
                    launcher=row[3],
                    steam_app_id=row[4],
                    last_scanned=datetime.fromisoformat(row[5]),
                    created_at=datetime.fromisoformat(row[6])
                )
                result[game.path] = game

            logger.info(f"Batch upserted {len(result)} games")
            return result

        except Exception as e:
            logger.error(f"Error batch upserting games: {e}", exc_info=True)
            conn.rollback()
            return {}

    async def batch_upsert_dlls(self, dlls: list[dict[str, Any]]) -> int:
        """
        Batch upsert multiple DLLs in a single transaction.

        Performance: O(1) transaction vs O(n) individual transactions

        Args:
            dlls: List of DLL dicts with keys: game_id, dll_type, dll_filename, dll_path, current_version

        Returns:
            Number of DLLs upserted
        """
        if not dlls:
            return 0

        return await asyncio.to_thread(self._batch_upsert_dlls, dlls)

    def _batch_upsert_dlls(self, dlls: list[dict[str, Any]]) -> int:
        """Batch upsert DLLs (runs in thread) - uses thread-local connection"""
        conn = self._get_thread_connection()
        cursor = conn.cursor()

        try:
            dll_data = [
                (d['game_id'], d['dll_type'], d['dll_filename'], d['dll_path'], d.get('current_version'))
                for d in dlls
            ]

            cursor.executemany("""
                INSERT INTO game_dlls (game_id, dll_type, dll_filename, dll_path, current_version)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(dll_path) DO UPDATE SET
                    game_id = excluded.game_id,
                    dll_type = excluded.dll_type,
                    dll_filename = excluded.dll_filename,
                    current_version = excluded.current_version,
                    detected_at = CURRENT_TIMESTAMP
            """, dll_data)

            conn.commit()
            count = len(dlls)
            logger.info(f"Batch upserted {count} DLLs")
            return count

        except Exception as e:
            logger.error(f"Error batch upserting DLLs: {e}", exc_info=True)
            conn.rollback()
            return 0

    # ===== Backup Operations =====

    async def insert_backup(self, backup_data: dict[str, Any]) -> int | None:
        """Insert backup record"""
        return await asyncio.to_thread(self._insert_backup, backup_data)

    def _insert_backup(self, backup_data: dict[str, Any]) -> int | None:
        """Insert backup (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO dll_backups (game_dll_id, backup_path, original_version, backup_size)
                VALUES (?, ?, ?, ?)
                RETURNING id
            """, (
                backup_data['game_dll_id'],
                backup_data['backup_path'],
                backup_data.get('original_version'),
                backup_data.get('backup_size', 0)
            ))

            backup_id = cursor.fetchone()[0]
            conn.commit()
            return backup_id

        except Exception as e:
            logger.error(f"Error inserting backup: {e}", exc_info=True)
            conn.rollback()
            return None
        finally:
            conn.close()

    async def get_all_backups(self) -> list[DLLBackup]:
        """Get all active backups"""
        return await asyncio.to_thread(self._get_all_backups)

    def _get_all_backups(self) -> list[DLLBackup]:
        """Get all backups (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    b.id, b.game_dll_id, g.name, d.dll_filename,
                    b.backup_path, b.original_version, b.backup_created_at,
                    b.backup_size, b.is_active
                FROM dll_backups b
                JOIN game_dlls d ON b.game_dll_id = d.id
                JOIN games g ON d.game_id = g.id
                WHERE b.is_active = 1
                ORDER BY b.backup_created_at DESC
            """)

            backups = []
            for row in cursor.fetchall():
                backups.append(DLLBackup(
                    id=row[0],
                    game_dll_id=row[1],
                    game_name=row[2],
                    dll_filename=row[3],
                    backup_path=row[4],
                    original_version=row[5],
                    backup_created_at=datetime.fromisoformat(row[6]),
                    backup_size=row[7],
                    is_active=bool(row[8])
                ))

            return backups

        except Exception as e:
            logger.error(f"Error getting backups: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def get_backup_by_id(self, backup_id: int) -> DLLBackup | None:
        """Get backup by ID"""
        return await asyncio.to_thread(self._get_backup_by_id, backup_id)

    def _get_backup_by_id(self, backup_id: int) -> DLLBackup | None:
        """Get backup by ID (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    b.id, b.game_dll_id, g.name, d.dll_filename,
                    b.backup_path, b.original_version, b.backup_created_at,
                    b.backup_size, b.is_active
                FROM dll_backups b
                JOIN game_dlls d ON b.game_dll_id = d.id
                JOIN games g ON d.game_id = g.id
                WHERE b.id = ?
            """, (backup_id,))

            row = cursor.fetchone()
            if row:
                return DLLBackup(
                    id=row[0],
                    game_dll_id=row[1],
                    game_name=row[2],
                    dll_filename=row[3],
                    backup_path=row[4],
                    original_version=row[5],
                    backup_created_at=datetime.fromisoformat(row[6]),
                    backup_size=row[7],
                    is_active=bool(row[8])
                )
            return None

        except Exception as e:
            logger.error(f"Error getting backup by ID: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    async def mark_backup_inactive(self, backup_id: int):
        """Mark backup as inactive"""
        return await asyncio.to_thread(self._mark_backup_inactive, backup_id)

    def _mark_backup_inactive(self, backup_id: int):
        """Mark backup inactive (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE dll_backups
                SET is_active = 0
                WHERE id = ?
            """, (backup_id,))

            conn.commit()

        except Exception as e:
            logger.error(f"Error marking backup inactive: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    async def mark_old_backups_inactive(self, game_dll_id: int):
        """Mark all existing backups for a game DLL as inactive"""
        return await asyncio.to_thread(self._mark_old_backups_inactive, game_dll_id)

    def _mark_old_backups_inactive(self, game_dll_id: int):
        """Mark old backups inactive (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE dll_backups
                SET is_active = 0
                WHERE game_dll_id = ? AND is_active = 1
            """, (game_dll_id,))

            affected_rows = cursor.rowcount
            conn.commit()

            if affected_rows > 0:
                logger.info(f"Marked {affected_rows} old backup(s) as inactive for game_dll_id {game_dll_id}")

        except Exception as e:
            logger.error(f"Error marking old backups inactive: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    async def cleanup_duplicate_backups(self):
        """
        Clean up duplicate backup entries by keeping only the most recent backup for each DLL
        This is a one-time cleanup for existing databases with duplicates
        """
        return await asyncio.to_thread(self._cleanup_duplicate_backups)

    def _cleanup_duplicate_backups(self):
        """Cleanup duplicate backups (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # For each game_dll_id, keep only the most recent active backup
            cursor.execute("""
                UPDATE dll_backups
                SET is_active = 0
                WHERE is_active = 1
                AND id NOT IN (
                    SELECT MAX(id)
                    FROM dll_backups
                    WHERE is_active = 1
                    GROUP BY game_dll_id
                )
            """)

            affected_rows = cursor.rowcount
            conn.commit()

            if affected_rows > 0:
                logger.info(f"Cleaned up {affected_rows} duplicate backup entries")
            else:
                logger.info("No duplicate backups found")

            return affected_rows

        except sqlite3.OperationalError as e:
            if "readonly database" in str(e):
                logger.warning(f"Database is read-only. "
                              f"To fix: chown $USER:$USER {self.db_path}")
            else:
                logger.error(f"Error cleaning up duplicate backups: {e}", exc_info=True)
            conn.rollback()
            return 0
        except Exception as e:
            logger.error(f"Error cleaning up duplicate backups: {e}", exc_info=True)
            conn.rollback()
            return 0
        finally:
            conn.close()

    async def delete_all_backups(self):
        """Mark all active backups as inactive"""
        return await asyncio.to_thread(self._delete_all_backups)

    def _delete_all_backups(self):
        """Delete all backups (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Mark all active backups as inactive
            cursor.execute("""
                UPDATE dll_backups
                SET is_active = 0
                WHERE is_active = 1
            """)

            affected_rows = cursor.rowcount
            conn.commit()

            logger.info(f"Marked {affected_rows} backup(s) as inactive")
            return affected_rows

        except Exception as e:
            logger.error(f"Error deleting all backups: {e}", exc_info=True)
            conn.rollback()
            return 0
        finally:
            conn.close()

    # ===== Update History Operations =====

    async def record_update_history(self, history_data: dict[str, Any]):
        """Record update history"""
        return await asyncio.to_thread(self._record_update_history, history_data)

    def _record_update_history(self, history_data: dict[str, Any]):
        """Record update history (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO update_history (game_dll_id, from_version, to_version, success)
                VALUES (?, ?, ?, ?)
            """, (
                history_data['game_dll_id'],
                history_data.get('from_version'),
                history_data.get('to_version'),
                history_data['success']
            ))

            conn.commit()

        except Exception as e:
            logger.error(f"Error recording update history: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    # ===== Steam Integration Operations =====

    async def upsert_steam_app(self, app_id: int, name: str):
        """Insert or update Steam app list entry"""
        return await asyncio.to_thread(self._upsert_steam_app, app_id, name)

    def _upsert_steam_app(self, app_id: int, name: str):
        """Upsert Steam app (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO steam_app_list (app_id, name, last_updated)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(app_id) DO UPDATE SET
                    name = excluded.name,
                    last_updated = CURRENT_TIMESTAMP
            """, (app_id, name))

            conn.commit()

        except Exception as e:
            logger.error(f"Error upserting Steam app: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    async def find_steam_app_by_name(self, game_name: str) -> int | None:
        """Find Steam app ID by game name"""
        return await asyncio.to_thread(self._find_steam_app_by_name, game_name)

    def _find_steam_app_by_name(self, game_name: str) -> int | None:
        """Find Steam app by name (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Normalize name for comparison
            normalized_name = game_name.lower().strip()

            # Try exact match first
            cursor.execute("""
                SELECT app_id FROM steam_app_list
                WHERE LOWER(name) = ?
                LIMIT 1
            """, (normalized_name,))

            row = cursor.fetchone()
            if row:
                return row[0]

            # Try partial match
            cursor.execute("""
                SELECT app_id FROM steam_app_list
                WHERE LOWER(name) LIKE ?
                LIMIT 1
            """, (f"%{normalized_name}%",))

            row = cursor.fetchone()
            if row:
                return row[0]

            return None

        except Exception as e:
            logger.error(f"Error finding Steam app by name: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    async def get_steam_app_list_timestamp(self) -> datetime | None:
        """Get timestamp of last Steam app list update"""
        return await asyncio.to_thread(self._get_steam_app_list_timestamp)

    def _get_steam_app_list_timestamp(self) -> datetime | None:
        """Get Steam app list timestamp (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT MAX(last_updated) FROM steam_app_list
            """)

            row = cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
            return None

        except Exception as e:
            logger.error(f"Error getting Steam app list timestamp: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    async def cache_steam_image(self, app_id: int, local_path: str):
        """Cache Steam image metadata"""
        return await asyncio.to_thread(self._cache_steam_image, app_id, local_path)

    def _cache_steam_image(self, app_id: int, local_path: str):
        """Cache Steam image (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO steam_images (steam_app_id, image_url, local_path, fetch_failed)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(steam_app_id) DO UPDATE SET
                    local_path = excluded.local_path,
                    cached_at = CURRENT_TIMESTAMP,
                    fetch_failed = 0
            """, (
                app_id,
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/header.jpg",
                local_path
            ))

            conn.commit()

        except Exception as e:
            logger.error(f"Error caching Steam image: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    async def get_cached_image_path(self, app_id: int) -> str | None:
        """Get cached image path for Steam app"""
        return await asyncio.to_thread(self._get_cached_image_path, app_id)

    def _get_cached_image_path(self, app_id: int) -> str | None:
        """Get cached image path (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT local_path FROM steam_images
                WHERE steam_app_id = ? AND fetch_failed = 0
            """, (app_id,))

            row = cursor.fetchone()
            return row[0] if row else None

        except Exception as e:
            logger.error(f"Error getting cached image path: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    async def mark_image_fetch_failed(self, app_id: int):
        """Mark image fetch as failed"""
        return await asyncio.to_thread(self._mark_image_fetch_failed, app_id)

    def _mark_image_fetch_failed(self, app_id: int):
        """Mark image fetch failed (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO steam_images (steam_app_id, image_url, fetch_failed)
                VALUES (?, ?, 1)
                ON CONFLICT(steam_app_id) DO UPDATE SET
                    fetch_failed = 1
            """, (
                app_id,
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/header.jpg"
            ))

            conn.commit()

        except Exception as e:
            logger.error(f"Error marking image fetch failed: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    async def is_image_fetch_failed(self, app_id: int) -> bool:
        """Check if image fetch has already failed for this app"""
        return await asyncio.to_thread(self._is_image_fetch_failed, app_id)

    def _is_image_fetch_failed(self, app_id: int) -> bool:
        """Check if image fetch failed (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT fetch_failed FROM steam_images
                WHERE steam_app_id = ?
            """, (app_id,))

            row = cursor.fetchone()
            return row[0] == 1 if row else False

        except Exception as e:
            logger.error(f"Error checking image fetch status: {e}", exc_info=True)
            return False
        finally:
            conn.close()

    async def clear_steam_images_cache(self):
        """
        Clear all steam image cache entries (for migration).

        Non-blocking: runs in thread pool via asyncio.to_thread().
        """
        return await asyncio.to_thread(self._clear_steam_images_cache)

    def _clear_steam_images_cache(self):
        """Clear all steam image cache entries (runs in thread)."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("DELETE FROM steam_images")
            conn.commit()
            logger.info(f"Cleared {cursor.rowcount} steam image cache entries")
        except Exception as e:
            logger.error(f"Error clearing steam images cache: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    # ===== Steam Apps FTS5 Operations (Phase 3) =====

    async def upsert_steam_apps(self, apps: list[tuple[int, str, str]]) -> int:
        """
        Bulk insert/update Steam apps with FTS5 indexing.

        Uses INSERT OR REPLACE with batching for optimal performance.
        Batch size of 1000 balances memory usage and transaction overhead.

        Performance: ~207K apps in ~2-3 seconds with proper batching.

        Args:
            apps: List of tuples (appid, name, name_normalized)

        Returns:
            Number of apps upserted
        """
        if not apps:
            return 0

        return await asyncio.to_thread(self._upsert_steam_apps, apps)

    def _upsert_steam_apps(self, apps: list[tuple[int, str, str]]) -> int:
        """Bulk upsert Steam apps (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Batch size for optimal performance
            BATCH_SIZE = 1000
            total_upserted = 0

            # Process in batches to avoid memory pressure
            for i in range(0, len(apps), BATCH_SIZE):
                batch = apps[i:i + BATCH_SIZE]

                # Use INSERT OR REPLACE which triggers the FTS5 update triggers
                cursor.executemany("""
                    INSERT OR REPLACE INTO steam_apps (appid, name, name_normalized)
                    VALUES (?, ?, ?)
                """, batch)

                total_upserted += len(batch)

                # Commit after each batch to avoid holding locks too long
                if i + BATCH_SIZE < len(apps):
                    conn.commit()

            conn.commit()
            logger.info(f"Upserted {total_upserted} Steam apps to database")
            return total_upserted

        except Exception as e:
            logger.error(f"Error upserting Steam apps: {e}", exc_info=True)
            conn.rollback()
            return 0
        finally:
            conn.close()

    async def search_steam_app(self, query: str, limit: int = 10) -> list[tuple[int, str]]:
        """
        FTS5 full-text search for Steam apps by name.

        Uses FTS5 MATCH for high-performance prefix and fuzzy matching.

        Args:
            query: Search query string
            limit: Maximum results to return (default 10)

        Returns:
            List of tuples (appid, name) matching the query
        """
        return await asyncio.to_thread(self._search_steam_app, query, limit)

    def _search_steam_app(self, query: str, limit: int) -> list[tuple[int, str]]:
        """FTS5 search for Steam app (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Clean and prepare query for FTS5
            # Escape special FTS5 characters and add prefix matching
            clean_query = query.strip().lower()
            if not clean_query:
                return []

            # Escape special characters for FTS5
            for char in ['"', "'", '-', '+', '*', '(', ')', ':']:
                clean_query = clean_query.replace(char, ' ')

            # Split into words and add prefix matching
            words = clean_query.split()
            if not words:
                return []

            # Build FTS5 query with prefix matching on last word
            # e.g., "black myth" -> "black myth*" for prefix search
            fts_query = ' '.join(words[:-1]) + ' ' + words[-1] + '*' if words else ''
            fts_query = fts_query.strip()

            if not fts_query:
                return []

            # Use FTS5 MATCH for search, join with steam_apps for data
            cursor.execute("""
                SELECT s.appid, s.name
                FROM steam_apps_fts fts
                JOIN steam_apps s ON fts.rowid = s.appid
                WHERE steam_apps_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit))

            results = [(row[0], row[1]) for row in cursor.fetchall()]
            return results

        except Exception as e:
            # FTS5 MATCH can fail on malformed queries - fall back to LIKE
            logger.debug(f"FTS5 search failed, falling back to LIKE: {e}")
            try:
                cursor.execute("""
                    SELECT appid, name
                    FROM steam_apps
                    WHERE name_normalized LIKE ?
                    LIMIT ?
                """, (f"%{query.strip().lower()}%", limit))

                return [(row[0], row[1]) for row in cursor.fetchall()]
            except Exception as e2:
                logger.error(f"Error searching Steam apps: {e2}", exc_info=True)
                return []
        finally:
            conn.close()

    async def get_steam_app_by_name(self, name_normalized: str) -> int | None:
        """
        Exact match lookup for Steam app by normalized name.

        Uses the idx_steam_apps_normalized index for O(log n) lookup.

        Args:
            name_normalized: Normalized game name (lowercase, no spaces)

        Returns:
            Steam app ID if found, None otherwise
        """
        return await asyncio.to_thread(self._get_steam_app_by_name, name_normalized)

    def _get_steam_app_by_name(self, name_normalized: str) -> int | None:
        """Get Steam app by normalized name (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT appid FROM steam_apps
                WHERE name_normalized = ?
                LIMIT 1
            """, (name_normalized,))

            row = cursor.fetchone()
            return row[0] if row else None

        except Exception as e:
            logger.error(f"Error getting Steam app by name: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    async def get_steam_apps_count(self) -> int:
        """
        Get the count of Steam apps in the database.

        Useful for checking if the database needs to be populated.

        Returns:
            Number of Steam apps in the database
        """
        return await asyncio.to_thread(self._get_steam_apps_count)

    def _get_steam_apps_count(self) -> int:
        """Get Steam apps count (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT COUNT(*) FROM steam_apps")
            row = cursor.fetchone()
            return row[0] if row else 0

        except Exception as e:
            logger.error(f"Error getting Steam apps count: {e}", exc_info=True)
            return 0
        finally:
            conn.close()

    async def clear_steam_apps(self):
        """
        Clear all Steam apps from the database.

        This also clears the FTS5 index via the DELETE trigger.
        """
        return await asyncio.to_thread(self._clear_steam_apps)

    def _clear_steam_apps(self):
        """Clear all Steam apps (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("DELETE FROM steam_apps")
            conn.commit()
            logger.info(f"Cleared {cursor.rowcount} Steam apps from database")
        except Exception as e:
            logger.error(f"Error clearing Steam apps: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    # ===== Per-Game Backup Operations =====

    async def get_backups_for_game(self, game_id: int) -> list[GameDLLBackup]:
        """
        Get all active backups for a specific game.

        Args:
            game_id: Database ID of the game

        Returns:
            List of GameDLLBackup objects for the game, ordered by creation date (newest first)
        """
        return await asyncio.to_thread(self._get_backups_for_game, game_id)

    def _get_backups_for_game(self, game_id: int) -> list[GameDLLBackup]:
        """Get backups for game (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT b.id, b.game_dll_id, gd.game_id, g.name,
                       gd.dll_type, gd.dll_filename, b.backup_path,
                       b.original_version, b.backup_created_at,
                       b.backup_size, b.is_active
                FROM dll_backups b
                INNER JOIN game_dlls gd ON b.game_dll_id = gd.id
                INNER JOIN games g ON gd.game_id = g.id
                WHERE gd.game_id = ? AND b.is_active = 1
                ORDER BY b.backup_created_at DESC
            """, (game_id,))

            backups = []
            for row in cursor.fetchall():
                backups.append(GameDLLBackup(
                    id=row[0],
                    game_dll_id=row[1],
                    game_id=row[2],
                    game_name=row[3],
                    dll_type=row[4],
                    dll_filename=row[5],
                    backup_path=row[6],
                    original_version=row[7],
                    backup_created_at=datetime.fromisoformat(row[8]),
                    backup_size=row[9],
                    is_active=bool(row[10])
                ))

            return backups

        except Exception as e:
            logger.error(f"Error getting backups for game {game_id}: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def game_has_backups(self, game_id: int) -> bool:
        """
        Check if a game has any active backups.

        Uses EXISTS + LIMIT 1 for optimal performance - stops at first match.

        Args:
            game_id: Database ID of the game

        Returns:
            True if game has at least one active backup, False otherwise
        """
        return await asyncio.to_thread(self._game_has_backups, game_id)

    def _game_has_backups(self, game_id: int) -> bool:
        """Check if game has backups (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT EXISTS(
                    SELECT 1
                    FROM dll_backups b
                    INNER JOIN game_dlls gd ON b.game_dll_id = gd.id
                    WHERE gd.game_id = ? AND b.is_active = 1
                    LIMIT 1
                )
            """, (game_id,))

            result = cursor.fetchone()
            return bool(result[0]) if result else False

        except Exception as e:
            logger.error(f"Error checking backups for game {game_id}: {e}", exc_info=True)
            return False
        finally:
            conn.close()

    async def get_game_backup_summary(self, game_id: int) -> GameBackupSummary | None:
        """
        Get summary of backups for a specific game.

        Aggregates backup information including count, total size, and date range.

        Args:
            game_id: Database ID of the game

        Returns:
            GameBackupSummary if game has backups, None otherwise
        """
        return await asyncio.to_thread(self._get_game_backup_summary, game_id)

    def _get_game_backup_summary(self, game_id: int) -> GameBackupSummary | None:
        """Get game backup summary (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Get aggregated backup info
            cursor.execute("""
                SELECT
                    g.id,
                    g.name,
                    COUNT(b.id) as backup_count,
                    COALESCE(SUM(b.backup_size), 0) as total_size,
                    MIN(b.backup_created_at) as oldest_backup,
                    MAX(b.backup_created_at) as newest_backup
                FROM games g
                INNER JOIN game_dlls gd ON g.id = gd.game_id
                INNER JOIN dll_backups b ON gd.id = b.game_dll_id
                WHERE g.id = ? AND b.is_active = 1
                GROUP BY g.id, g.name
            """, (game_id,))

            row = cursor.fetchone()
            if not row or row[2] == 0:  # No backups
                return None

            # Get distinct DLL types for this game's backups
            cursor.execute("""
                SELECT DISTINCT gd.dll_type
                FROM game_dlls gd
                INNER JOIN dll_backups b ON gd.id = b.game_dll_id
                WHERE gd.game_id = ? AND b.is_active = 1
                ORDER BY gd.dll_type
            """, (game_id,))

            dll_types = [r[0] for r in cursor.fetchall()]

            return GameBackupSummary(
                game_id=row[0],
                game_name=row[1],
                backup_count=row[2],
                total_backup_size=row[3],
                dll_types=dll_types,
                oldest_backup=datetime.fromisoformat(row[4]) if row[4] else None,
                newest_backup=datetime.fromisoformat(row[5]) if row[5] else None
            )

        except Exception as e:
            logger.error(f"Error getting backup summary for game {game_id}: {e}", exc_info=True)
            return None
        finally:
            conn.close()

    async def get_backups_grouped_by_dll_type(self, game_id: int) -> dict[str, list[DLLBackup]]:
        """
        Get backups for a game grouped by DLL type.

        Useful for restore operations where user wants to restore specific DLL types.

        Args:
            game_id: Database ID of the game

        Returns:
            Dict mapping dll_type (e.g., "DLSS", "FSR") to list of DLLBackup objects
        """
        return await asyncio.to_thread(self._get_backups_grouped_by_dll_type, game_id)

    def _get_backups_grouped_by_dll_type(self, game_id: int) -> dict[str, list[DLLBackup]]:
        """Get backups grouped by DLL type (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    b.id, b.game_dll_id, g.name, gd.dll_filename,
                    b.backup_path, b.original_version, b.backup_created_at,
                    b.backup_size, b.is_active, gd.dll_type
                FROM dll_backups b
                INNER JOIN game_dlls gd ON b.game_dll_id = gd.id
                INNER JOIN games g ON gd.game_id = g.id
                WHERE gd.game_id = ? AND b.is_active = 1
                ORDER BY gd.dll_type, b.backup_created_at DESC
            """, (game_id,))

            grouped: dict[str, list[DLLBackup]] = {}
            for row in cursor.fetchall():
                dll_type = row[9]
                backup = DLLBackup(
                    id=row[0],
                    game_dll_id=row[1],
                    game_name=row[2],
                    dll_filename=row[3],
                    backup_path=row[4],
                    original_version=row[5],
                    backup_created_at=datetime.fromisoformat(row[6]),
                    backup_size=row[7],
                    is_active=bool(row[8])
                )

                if dll_type not in grouped:
                    grouped[dll_type] = []
                grouped[dll_type].append(backup)

            return grouped

        except Exception as e:
            logger.error(f"Error getting grouped backups for game {game_id}: {e}", exc_info=True)
            return {}
        finally:
            conn.close()

    async def get_games_with_backups(self) -> list[GameWithBackupCount]:
        """
        Get all games that have active backups with their backup counts.

        Useful for populating filter dropdowns in the UI.

        Returns:
            List of GameWithBackupCount objects, ordered by game name
        """
        return await asyncio.to_thread(self._get_games_with_backups)

    def _get_games_with_backups(self) -> list[GameWithBackupCount]:
        """Get games with backups (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    g.id,
                    g.name,
                    g.launcher,
                    COUNT(b.id) as backup_count
                FROM games g
                INNER JOIN game_dlls gd ON g.id = gd.game_id
                INNER JOIN dll_backups b ON gd.id = b.game_dll_id
                WHERE b.is_active = 1
                GROUP BY g.id, g.name, g.launcher
                HAVING backup_count > 0
                ORDER BY g.name
            """)

            games = []
            for row in cursor.fetchall():
                games.append(GameWithBackupCount(
                    game_id=row[0],
                    game_name=row[1],
                    launcher=row[2],
                    backup_count=row[3]
                ))

            return games

        except Exception as e:
            logger.error(f"Error getting games with backups: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def get_all_backups_filtered(
        self,
        game_id: int | None = None
    ) -> list[GameDLLBackup]:
        """
        Get all active backups, optionally filtered by game.

        Args:
            game_id: Optional game ID to filter by. If None, returns all backups.

        Returns:
            List of GameDLLBackup objects, ordered by creation date (newest first)
        """
        return await asyncio.to_thread(self._get_all_backups_filtered, game_id)

    def _get_all_backups_filtered(
        self,
        game_id: int | None = None
    ) -> list[GameDLLBackup]:
        """Get all backups with optional game filter (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            if game_id is not None:
                cursor.execute("""
                    SELECT b.id, b.game_dll_id, gd.game_id, g.name,
                           gd.dll_type, gd.dll_filename, b.backup_path,
                           b.original_version, b.backup_created_at,
                           b.backup_size, b.is_active
                    FROM dll_backups b
                    INNER JOIN game_dlls gd ON b.game_dll_id = gd.id
                    INNER JOIN games g ON gd.game_id = g.id
                    WHERE b.is_active = 1 AND gd.game_id = ?
                    ORDER BY b.backup_created_at DESC
                """, (game_id,))
            else:
                cursor.execute("""
                    SELECT b.id, b.game_dll_id, gd.game_id, g.name,
                           gd.dll_type, gd.dll_filename, b.backup_path,
                           b.original_version, b.backup_created_at,
                           b.backup_size, b.is_active
                    FROM dll_backups b
                    INNER JOIN game_dlls gd ON b.game_dll_id = gd.id
                    INNER JOIN games g ON gd.game_id = g.id
                    WHERE b.is_active = 1
                    ORDER BY b.backup_created_at DESC
                """)

            backups = []
            for row in cursor.fetchall():
                backups.append(GameDLLBackup(
                    id=row[0],
                    game_dll_id=row[1],
                    game_id=row[2],
                    game_name=row[3],
                    dll_type=row[4],
                    dll_filename=row[5],
                    backup_path=row[6],
                    original_version=row[7],
                    backup_created_at=datetime.fromisoformat(row[8]),
                    backup_size=row[9],
                    is_active=bool(row[10])
                ))

            return backups

        except Exception as e:
            logger.error(f"Error getting filtered backups: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def batch_check_games_have_backups(
        self,
        game_ids: list[int]
    ) -> dict[int, bool]:
        """
        Check multiple games for backup existence in a single query.

        Optimized for UI state checks where we need to know if restore
        buttons should be enabled for multiple games at once.

        Args:
            game_ids: List of game IDs to check

        Returns:
            Dict mapping game_id to bool (True if has backups)
        """
        if not game_ids:
            return {}
        return await asyncio.to_thread(self._batch_check_games_have_backups, game_ids)

    def _batch_check_games_have_backups(
        self,
        game_ids: list[int]
    ) -> dict[int, bool]:
        """Batch check games for backups (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Initialize all game_ids to False
            result = {gid: False for gid in game_ids}

            # Query for games that DO have active backups
            placeholders = ','.join('?' * len(game_ids))
            cursor.execute(f"""
                SELECT DISTINCT gd.game_id
                FROM dll_backups b
                INNER JOIN game_dlls gd ON b.game_dll_id = gd.id
                WHERE gd.game_id IN ({placeholders}) AND b.is_active = 1
            """, game_ids)

            # Mark games with backups as True
            for row in cursor.fetchall():
                result[row[0]] = True

            return result

        except Exception as e:
            logger.error(f"Error batch checking games for backups: {e}", exc_info=True)
            return {gid: False for gid in game_ids}
        finally:
            conn.close()

    # ===== Search Operations =====

    async def search_games(
        self,
        query: str,
        launcher: str | None = None,
        limit: int = 50
    ) -> list[Game]:
        """
        Search games by name using case-insensitive LIKE matching.

        Performance: Uses idx_games_name_launcher index for fast lookups.
        Typical query time: <10ms for databases up to 10,000 games.

        Args:
            query: Search query string
            launcher: Optional launcher filter
            limit: Maximum results to return

        Returns:
            List of Game objects matching the query
        """
        return await asyncio.to_thread(self._search_games, query, launcher, limit)

    def _search_games(
        self,
        query: str,
        launcher: str | None,
        limit: int
    ) -> list[Game]:
        """Search games (runs in thread) - uses thread-local connection"""
        conn = self._get_thread_connection()
        cursor = conn.cursor()

        try:
            # Normalize query for LIKE matching
            search_pattern = f"%{query.strip()}%"

            if launcher:
                cursor.execute("""
                    SELECT id, name, path, launcher, steam_app_id, last_scanned, created_at
                    FROM games
                    WHERE name LIKE ? COLLATE NOCASE AND launcher = ?
                    ORDER BY
                        CASE
                            WHEN LOWER(name) = LOWER(?) THEN 1
                            WHEN LOWER(name) LIKE LOWER(?) || '%' THEN 2
                            ELSE 3
                        END,
                        name COLLATE NOCASE
                    LIMIT ?
                """, (search_pattern, launcher, query.strip(), query.strip(), limit))
            else:
                cursor.execute("""
                    SELECT id, name, path, launcher, steam_app_id, last_scanned, created_at
                    FROM games
                    WHERE name LIKE ? COLLATE NOCASE
                    ORDER BY
                        CASE
                            WHEN LOWER(name) = LOWER(?) THEN 1
                            WHEN LOWER(name) LIKE LOWER(?) || '%' THEN 2
                            ELSE 3
                        END,
                        name COLLATE NOCASE
                    LIMIT ?
                """, (search_pattern, query.strip(), query.strip(), limit))

            games = []
            for row in cursor:
                games.append(Game(
                    id=row[0],
                    name=row[1],
                    path=row[2],
                    launcher=row[3],
                    steam_app_id=row[4],
                    last_scanned=datetime.fromisoformat(row[5]),
                    created_at=datetime.fromisoformat(row[6])
                ))

            return games

        except Exception as e:
            logger.error(f"Error searching games: {e}", exc_info=True)
            return []

    async def get_game_count(self) -> int:
        """Get total number of games in database."""
        return await asyncio.to_thread(self._get_game_count)

    def _get_game_count(self) -> int:
        """Get game count (runs in thread)"""
        conn = self._get_thread_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT COUNT(*) FROM games")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Error getting game count: {e}", exc_info=True)
            return 0

    # ===== Search History Operations =====

    async def add_search_history(
        self,
        query: str,
        launcher: str | None = None,
        result_count: int = 0
    ):
        """
        Add a search query to history.

        Deduplicates by query (case-insensitive), updating timestamp if exists.

        Args:
            query: The search query
            launcher: Optional launcher filter used
            result_count: Number of results returned
        """
        return await asyncio.to_thread(
            self._add_search_history, query, launcher, result_count
        )

    def _add_search_history(
        self,
        query: str,
        launcher: str | None,
        result_count: int
    ):
        """Add search history (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # First, delete any existing entry with same query (case-insensitive)
            cursor.execute("""
                DELETE FROM search_history
                WHERE LOWER(query) = LOWER(?)
            """, (query.strip(),))

            # Insert new entry
            cursor.execute("""
                INSERT INTO search_history (query, launcher, result_count, timestamp)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (query.strip(), launcher, result_count))

            # Trim to max 50 entries
            cursor.execute("""
                DELETE FROM search_history
                WHERE id NOT IN (
                    SELECT id FROM search_history
                    ORDER BY timestamp DESC
                    LIMIT 50
                )
            """)

            conn.commit()

        except Exception as e:
            logger.error(f"Error adding search history: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    async def get_search_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        Get recent search history.

        Args:
            limit: Maximum entries to return

        Returns:
            List of dicts with query, launcher, result_count, timestamp
        """
        return await asyncio.to_thread(self._get_search_history, limit)

    def _get_search_history(self, limit: int) -> list[dict[str, Any]]:
        """Get search history (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT query, launcher, result_count, timestamp
                FROM search_history
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            history = []
            for row in cursor.fetchall():
                history.append({
                    'query': row[0],
                    'launcher': row[1],
                    'result_count': row[2],
                    'timestamp': row[3]
                })

            return history

        except Exception as e:
            logger.error(f"Error getting search history: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def clear_search_history(self):
        """Clear all search history."""
        return await asyncio.to_thread(self._clear_search_history)

    def _clear_search_history(self):
        """Clear search history (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("DELETE FROM search_history")
            conn.commit()
            logger.info("Search history cleared")
        except Exception as e:
            logger.error(f"Error clearing search history: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()


# Singleton instance
db_manager = DatabaseManager()
