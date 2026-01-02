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
from typing import Optional, List, Dict, Tuple, Any
from contextlib import asynccontextmanager
from datetime import datetime
import appdirs

import aiosqlite

from dlss_updater.logger import setup_logger
from dlss_updater.models import (
    Game, GameDLL, DLLBackup, UpdateHistory, SteamImage,
    GameDLLBackup, GameBackupSummary, GameWithBackupCount
)

logger = setup_logger()

# Thread-safety lock for singleton pattern (free-threading Python 3.14+)
_db_manager_lock = threading.Lock()


class DatabaseManager:
    """
    Singleton database manager for DLSS Updater
    Handles all database operations with async wrappers
    """
    _instance = None

    def __new__(cls):
        # Double-checked locking pattern for free-threading safety
        if cls._instance is None:
            with _db_manager_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            app_name = "DLSS-Updater"
            app_author = "Recol"
            config_dir = appdirs.user_config_dir(app_name, app_author)
            self.db_path = Path(config_dir) / "games.db"
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.initialized = False

            # Connection pool for async operations (aiosqlite)
            self._async_pool: List[Any] = []  # List of aiosqlite.Connection
            self._pool_size = 5
            self._pool_lock = asyncio.Lock()
            self._pool_semaphore: Optional[asyncio.Semaphore] = None

            # Thread-local storage for sync operations (connection reuse)
            self._thread_local = threading.local()

            logger.info(f"Database path: {self.db_path}")

    async def initialize(self):
        """Initialize database schema and connection pool"""
        if self.initialized:
            return

        await asyncio.to_thread(self._create_schema)

        # Initialize semaphore for connection pool
        self._pool_semaphore = asyncio.Semaphore(self._pool_size)

        # Pre-warm async connection pool
        try:
            for _ in range(self._pool_size):
                conn = await aiosqlite.connect(str(self.db_path))
                conn.row_factory = aiosqlite.Row
                self._async_pool.append(conn)
            logger.info(f"Connection pool initialized ({len(self._async_pool)} connections)")
        except Exception as e:
            logger.error(f"Failed to initialize async pool: {e}")
            raise

        self.initialized = True
        logger.info("Database initialized successfully")

    @asynccontextmanager
    async def get_async_connection(self):
        """
        Get an async connection from the pool.

        Usage:
            async with db_manager.get_async_connection() as conn:
                await conn.execute(...)
        """
        await self._pool_semaphore.acquire()
        conn = None

        try:
            async with self._pool_lock:
                if self._async_pool:
                    conn = self._async_pool.pop()
                else:
                    # Pool exhausted, create new connection
                    conn = await aiosqlite.connect(str(self.db_path))
                    conn.row_factory = aiosqlite.Row

            yield conn

        finally:
            if conn is not None:
                async with self._pool_lock:
                    self._async_pool.append(conn)
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

    async def close(self):
        """Close all pooled connections"""
        async with self._pool_lock:
            for conn in self._async_pool:
                try:
                    await conn.close()
                except Exception as e:
                    logger.debug(f"Error closing pooled connection: {e}")
            self._async_pool.clear()
        logger.info("Database connections closed")

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

            # Steam app list cache (for name-based lookup)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS steam_app_list (
                    app_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
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

            # ================================================================
            # FEATURE: Favorites & Game Grouping (v3.2.0)
            # ================================================================

            # Favorites table - games marked as favorites with ordering
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS game_favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL UNIQUE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
                )
            """)

            # Custom tags (user-created, colored)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    color TEXT NOT NULL DEFAULT '#6366f1',
                    icon TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Game-tag associations (many-to-many)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS game_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
                    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,
                    UNIQUE(game_id, tag_id)
                )
            """)

            # ================================================================
            # FEATURE: DLL Dashboard Statistics Cache (v3.2.0)
            # ================================================================

            # Dashboard statistics cache (pre-aggregated for performance)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_stats_cache (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    total_games INTEGER NOT NULL DEFAULT 0,
                    total_dlls INTEGER NOT NULL DEFAULT 0,
                    total_updates_performed INTEGER NOT NULL DEFAULT 0,
                    successful_updates INTEGER NOT NULL DEFAULT 0,
                    failed_updates INTEGER NOT NULL DEFAULT 0,
                    games_with_outdated_dlls INTEGER NOT NULL DEFAULT 0,
                    games_with_backups INTEGER NOT NULL DEFAULT 0,
                    total_backup_size_bytes INTEGER NOT NULL DEFAULT 0,
                    last_scan_timestamp TIMESTAMP,
                    last_update_timestamp TIMESTAMP,
                    cache_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Initialize dashboard stats singleton row
            cursor.execute("INSERT OR IGNORE INTO dashboard_stats_cache (id) VALUES (1)")

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_launcher ON games(launcher)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_steam_app_id ON games(steam_app_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_dlls_game_id ON game_dlls(game_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_dlls_dll_type ON game_dlls(dll_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dll_backups_game_dll_id ON dll_backups(game_dll_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dll_backups_active ON dll_backups(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_update_history_game_dll_id ON update_history(game_dll_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_steam_name ON steam_app_list(name COLLATE NOCASE)")

            # Search-related indexes for fast game name lookups
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_name ON games(name COLLATE NOCASE)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_name_launcher ON games(name COLLATE NOCASE, launcher)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_history_timestamp ON search_history(timestamp DESC)")

            # Favorites & Tags indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_favorites_game_id ON game_favorites(game_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_favorites_sort_order ON game_favorites(sort_order)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name COLLATE NOCASE)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_tags_game_id ON game_tags(game_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_tags_tag_id ON game_tags(tag_id)")

            conn.commit()
            logger.info("Database schema created successfully")

        except Exception as e:
            logger.error(f"Error creating database schema: {e}", exc_info=True)
            conn.rollback()
            raise
        finally:
            conn.close()

    # ===== Game Operations =====

    async def upsert_game(self, game_data: Dict[str, Any]) -> Optional[Game]:
        """Insert or update game record"""
        return await asyncio.to_thread(self._upsert_game, game_data)

    def _upsert_game(self, game_data: Dict[str, Any]) -> Optional[Game]:
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

    async def get_games_grouped_by_launcher(self) -> Dict[str, List[Game]]:
        """Get all games grouped by launcher"""
        return await asyncio.to_thread(self._get_games_grouped_by_launcher)

    def _get_games_grouped_by_launcher(self) -> Dict[str, List[Game]]:
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

        except Exception as e:
            logger.error(f"Error cleaning up duplicate games: {e}", exc_info=True)
            conn.rollback()
            return 0
        finally:
            conn.close()

    # ===== GameDLL Operations =====

    async def upsert_game_dll(self, dll_data: Dict[str, Any]) -> Optional[GameDLL]:
        """Insert or update game DLL record"""
        return await asyncio.to_thread(self._upsert_game_dll, dll_data)

    def _upsert_game_dll(self, dll_data: Dict[str, Any]) -> Optional[GameDLL]:
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

    async def get_game_dll_by_path(self, dll_path: str) -> Optional[GameDLL]:
        """Get game DLL by path"""
        return await asyncio.to_thread(self._get_game_dll_by_path, dll_path)

    def _get_game_dll_by_path(self, dll_path: str) -> Optional[GameDLL]:
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

    async def get_dlls_for_game(self, game_id: int) -> List[GameDLL]:
        """Get all DLLs for a specific game"""
        return await asyncio.to_thread(self._get_dlls_for_game, game_id)

    def _get_dlls_for_game(self, game_id: int) -> List[GameDLL]:
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

    async def get_game_dll_by_path(self, dll_path: str) -> Optional[GameDLL]:
        """Get DLL record by file path"""
        return await asyncio.to_thread(self._get_game_dll_by_path, dll_path)

    def _get_game_dll_by_path(self, dll_path: str) -> Optional[GameDLL]:
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

    async def batch_upsert_games(self, games: List[Dict[str, Any]]) -> Dict[str, Game]:
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

    def _batch_upsert_games(self, games: List[Dict[str, Any]]) -> Dict[str, Game]:
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

    async def batch_upsert_dlls(self, dlls: List[Dict[str, Any]]) -> int:
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

    def _batch_upsert_dlls(self, dlls: List[Dict[str, Any]]) -> int:
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

    async def insert_backup(self, backup_data: Dict[str, Any]) -> Optional[int]:
        """Insert backup record"""
        return await asyncio.to_thread(self._insert_backup, backup_data)

    def _insert_backup(self, backup_data: Dict[str, Any]) -> Optional[int]:
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

    async def get_all_backups(self) -> List[DLLBackup]:
        """Get all active backups"""
        return await asyncio.to_thread(self._get_all_backups)

    def _get_all_backups(self) -> List[DLLBackup]:
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

    async def get_backup_by_id(self, backup_id: int) -> Optional[DLLBackup]:
        """Get backup by ID"""
        return await asyncio.to_thread(self._get_backup_by_id, backup_id)

    def _get_backup_by_id(self, backup_id: int) -> Optional[DLLBackup]:
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

    async def record_update_history(self, history_data: Dict[str, Any]):
        """Record update history"""
        return await asyncio.to_thread(self._record_update_history, history_data)

    def _record_update_history(self, history_data: Dict[str, Any]):
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

    async def find_steam_app_by_name(self, game_name: str) -> Optional[int]:
        """Find Steam app ID by game name"""
        return await asyncio.to_thread(self._find_steam_app_by_name, game_name)

    def _find_steam_app_by_name(self, game_name: str) -> Optional[int]:
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

    async def get_steam_app_list_timestamp(self) -> Optional[datetime]:
        """Get timestamp of last Steam app list update"""
        return await asyncio.to_thread(self._get_steam_app_list_timestamp)

    def _get_steam_app_list_timestamp(self) -> Optional[datetime]:
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

    async def get_cached_image_path(self, app_id: int) -> Optional[str]:
        """Get cached image path for Steam app"""
        return await asyncio.to_thread(self._get_cached_image_path, app_id)

    def _get_cached_image_path(self, app_id: int) -> Optional[str]:
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

    # ===== Per-Game Backup Operations =====

    async def get_backups_for_game(self, game_id: int) -> List[GameDLLBackup]:
        """
        Get all active backups for a specific game.

        Args:
            game_id: Database ID of the game

        Returns:
            List of GameDLLBackup objects for the game, ordered by creation date (newest first)
        """
        return await asyncio.to_thread(self._get_backups_for_game, game_id)

    def _get_backups_for_game(self, game_id: int) -> List[GameDLLBackup]:
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

    async def get_game_backup_summary(self, game_id: int) -> Optional[GameBackupSummary]:
        """
        Get summary of backups for a specific game.

        Aggregates backup information including count, total size, and date range.

        Args:
            game_id: Database ID of the game

        Returns:
            GameBackupSummary if game has backups, None otherwise
        """
        return await asyncio.to_thread(self._get_game_backup_summary, game_id)

    def _get_game_backup_summary(self, game_id: int) -> Optional[GameBackupSummary]:
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

    async def get_backups_grouped_by_dll_type(self, game_id: int) -> Dict[str, List[DLLBackup]]:
        """
        Get backups for a game grouped by DLL type.

        Useful for restore operations where user wants to restore specific DLL types.

        Args:
            game_id: Database ID of the game

        Returns:
            Dict mapping dll_type (e.g., "DLSS", "FSR") to list of DLLBackup objects
        """
        return await asyncio.to_thread(self._get_backups_grouped_by_dll_type, game_id)

    def _get_backups_grouped_by_dll_type(self, game_id: int) -> Dict[str, List[DLLBackup]]:
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

            grouped: Dict[str, List[DLLBackup]] = {}
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

    async def get_games_with_backups(self) -> List[GameWithBackupCount]:
        """
        Get all games that have active backups with their backup counts.

        Useful for populating filter dropdowns in the UI.

        Returns:
            List of GameWithBackupCount objects, ordered by game name
        """
        return await asyncio.to_thread(self._get_games_with_backups)

    def _get_games_with_backups(self) -> List[GameWithBackupCount]:
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
        game_id: Optional[int] = None
    ) -> List[GameDLLBackup]:
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
        game_id: Optional[int] = None
    ) -> List[GameDLLBackup]:
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
        game_ids: List[int]
    ) -> Dict[int, bool]:
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
        game_ids: List[int]
    ) -> Dict[int, bool]:
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
        launcher: Optional[str] = None,
        limit: int = 50
    ) -> List[Game]:
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
        launcher: Optional[str],
        limit: int
    ) -> List[Game]:
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
        launcher: Optional[str] = None,
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
        launcher: Optional[str],
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

    async def get_search_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent search history.

        Args:
            limit: Maximum entries to return

        Returns:
            List of dicts with query, launcher, result_count, timestamp
        """
        return await asyncio.to_thread(self._get_search_history, limit)

    def _get_search_history(self, limit: int) -> List[Dict[str, Any]]:
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

    # ===== Favorites Operations =====

    async def set_game_favorite(self, game_id: int, is_favorite: bool) -> bool:
        """
        Set or remove a game as favorite.

        Args:
            game_id: Database ID of the game
            is_favorite: True to add to favorites, False to remove

        Returns:
            True if operation succeeded
        """
        return await asyncio.to_thread(self._set_game_favorite, game_id, is_favorite)

    def _set_game_favorite(self, game_id: int, is_favorite: bool) -> bool:
        """Set game favorite (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            if is_favorite:
                # Add to favorites with sort_order = max + 1
                cursor.execute("""
                    INSERT OR REPLACE INTO game_favorites (game_id, sort_order)
                    SELECT ?, COALESCE(MAX(sort_order), 0) + 1 FROM game_favorites
                """, (game_id,))
            else:
                # Remove from favorites
                cursor.execute("DELETE FROM game_favorites WHERE game_id = ?", (game_id,))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error setting game favorite: {e}", exc_info=True)
            conn.rollback()
            return False
        finally:
            conn.close()

    async def is_game_favorite(self, game_id: int) -> bool:
        """Check if a game is in favorites."""
        return await asyncio.to_thread(self._is_game_favorite, game_id)

    def _is_game_favorite(self, game_id: int) -> bool:
        """Check if game is favorite (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT 1 FROM game_favorites WHERE game_id = ?", (game_id,))
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Error checking game favorite: {e}", exc_info=True)
            return False
        finally:
            conn.close()

    async def get_favorite_game_ids(self) -> List[int]:
        """Get all favorite game IDs ordered by sort_order."""
        return await asyncio.to_thread(self._get_favorite_game_ids)

    def _get_favorite_game_ids(self) -> List[int]:
        """Get favorite game IDs (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT game_id FROM game_favorites ORDER BY sort_order
            """)
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting favorite game IDs: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def batch_check_favorites(self, game_ids: List[int]) -> Dict[int, bool]:
        """Check favorite status for multiple games in one query."""
        if not game_ids:
            return {}
        return await asyncio.to_thread(self._batch_check_favorites, game_ids)

    def _batch_check_favorites(self, game_ids: List[int]) -> Dict[int, bool]:
        """Batch check favorites (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            result = {gid: False for gid in game_ids}
            placeholders = ','.join('?' * len(game_ids))
            cursor.execute(f"""
                SELECT game_id FROM game_favorites WHERE game_id IN ({placeholders})
            """, game_ids)
            for row in cursor.fetchall():
                result[row[0]] = True
            return result
        except Exception as e:
            logger.error(f"Error batch checking favorites: {e}", exc_info=True)
            return {gid: False for gid in game_ids}
        finally:
            conn.close()

    # ===== Tags Operations =====

    async def create_tag(self, name: str, color: str = '#6366f1', icon: str = None) -> Optional[int]:
        """
        Create a new tag.

        Args:
            name: Tag name (case-insensitive unique)
            color: Hex color code
            icon: Optional icon identifier

        Returns:
            Tag ID if created, None if failed
        """
        return await asyncio.to_thread(self._create_tag, name, color, icon)

    def _create_tag(self, name: str, color: str, icon: str) -> Optional[int]:
        """Create tag (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO tags (name, color, icon) VALUES (?, ?, ?)
            """, (name.strip(), color, icon))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.warning(f"Tag '{name}' already exists")
            return None
        except Exception as e:
            logger.error(f"Error creating tag: {e}", exc_info=True)
            conn.rollback()
            return None
        finally:
            conn.close()

    async def delete_tag(self, tag_id: int) -> bool:
        """Delete a tag and all its game associations."""
        return await asyncio.to_thread(self._delete_tag, tag_id)

    def _delete_tag(self, tag_id: int) -> bool:
        """Delete tag (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # CASCADE will handle game_tags cleanup
            cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting tag: {e}", exc_info=True)
            conn.rollback()
            return False
        finally:
            conn.close()

    async def get_all_tags(self) -> List[Dict]:
        """Get all tags with game counts."""
        return await asyncio.to_thread(self._get_all_tags)

    def _get_all_tags(self) -> List[Dict]:
        """Get all tags (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT t.id, t.name, t.color, t.icon, COUNT(gt.id) as game_count
                FROM tags t
                LEFT JOIN game_tags gt ON t.id = gt.tag_id
                GROUP BY t.id, t.name, t.color, t.icon
                ORDER BY t.name COLLATE NOCASE
            """)

            tags = []
            for row in cursor.fetchall():
                tags.append({
                    'id': row[0],
                    'name': row[1],
                    'color': row[2],
                    'icon': row[3],
                    'game_count': row[4]
                })
            return tags
        except Exception as e:
            logger.error(f"Error getting tags: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def get_game_tags(self, game_id: int) -> List[Dict]:
        """Get all tags for a specific game."""
        return await asyncio.to_thread(self._get_game_tags, game_id)

    def _get_game_tags(self, game_id: int) -> List[Dict]:
        """Get game tags (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT t.id, t.name, t.color, t.icon
                FROM tags t
                JOIN game_tags gt ON t.id = gt.tag_id
                WHERE gt.game_id = ?
                ORDER BY t.name COLLATE NOCASE
            """, (game_id,))

            tags = []
            for row in cursor.fetchall():
                tags.append({
                    'id': row[0],
                    'name': row[1],
                    'color': row[2],
                    'icon': row[3]
                })
            return tags
        except Exception as e:
            logger.error(f"Error getting game tags: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    async def set_game_tags(self, game_id: int, tag_ids: List[int]) -> bool:
        """
        Set tags for a game (replaces existing).

        Args:
            game_id: Database ID of the game
            tag_ids: List of tag IDs to assign

        Returns:
            True if operation succeeded
        """
        return await asyncio.to_thread(self._set_game_tags, game_id, tag_ids)

    def _set_game_tags(self, game_id: int, tag_ids: List[int]) -> bool:
        """Set game tags (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Remove existing tags
            cursor.execute("DELETE FROM game_tags WHERE game_id = ?", (game_id,))

            # Add new tags
            for tag_id in tag_ids:
                cursor.execute("""
                    INSERT INTO game_tags (game_id, tag_id) VALUES (?, ?)
                """, (game_id, tag_id))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error setting game tags: {e}", exc_info=True)
            conn.rollback()
            return False
        finally:
            conn.close()

    async def add_tag_to_game(self, game_id: int, tag_id: int) -> bool:
        """Add a single tag to a game."""
        return await asyncio.to_thread(self._add_tag_to_game, game_id, tag_id)

    def _add_tag_to_game(self, game_id: int, tag_id: int) -> bool:
        """Add tag to game (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO game_tags (game_id, tag_id) VALUES (?, ?)
            """, (game_id, tag_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error adding tag to game: {e}", exc_info=True)
            conn.rollback()
            return False
        finally:
            conn.close()

    async def remove_tag_from_game(self, game_id: int, tag_id: int) -> bool:
        """Remove a single tag from a game."""
        return await asyncio.to_thread(self._remove_tag_from_game, game_id, tag_id)

    def _remove_tag_from_game(self, game_id: int, tag_id: int) -> bool:
        """Remove tag from game (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                DELETE FROM game_tags WHERE game_id = ? AND tag_id = ?
            """, (game_id, tag_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error removing tag from game: {e}", exc_info=True)
            conn.rollback()
            return False
        finally:
            conn.close()

    async def get_games_by_tag(self, tag_id: int) -> List[int]:
        """Get all game IDs that have a specific tag."""
        return await asyncio.to_thread(self._get_games_by_tag, tag_id)

    def _get_games_by_tag(self, tag_id: int) -> List[int]:
        """Get games by tag (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT game_id FROM game_tags WHERE tag_id = ?
            """, (tag_id,))
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting games by tag: {e}", exc_info=True)
            return []
        finally:
            conn.close()

    # ===== Dashboard Statistics =====

    async def get_dashboard_stats(self) -> Dict:
        """Get cached dashboard statistics."""
        return await asyncio.to_thread(self._get_dashboard_stats)

    def _get_dashboard_stats(self) -> Dict:
        """Get dashboard stats (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # First, update the cache with live data
            cursor.execute("""
                UPDATE dashboard_stats_cache SET
                    total_games = (SELECT COUNT(*) FROM games),
                    total_dlls = (SELECT COUNT(*) FROM game_dlls),
                    games_with_backups = (
                        SELECT COUNT(DISTINCT gd.game_id)
                        FROM game_dlls gd
                        JOIN dll_backups b ON gd.id = b.game_dll_id
                        WHERE b.is_active = 1
                    ),
                    cache_updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
            """)
            conn.commit()

            # Now get the stats
            cursor.execute("""
                SELECT total_games, total_dlls, total_updates_performed,
                       successful_updates, failed_updates, games_with_outdated_dlls,
                       games_with_backups, total_backup_size_bytes,
                       last_scan_timestamp, last_update_timestamp
                FROM dashboard_stats_cache WHERE id = 1
            """)

            row = cursor.fetchone()
            if row:
                return {
                    'total_games': row[0],
                    'total_dlls': row[1],
                    'total_updates_performed': row[2],
                    'successful_updates': row[3],
                    'failed_updates': row[4],
                    'games_with_outdated_dlls': row[5],
                    'games_with_backups': row[6],
                    'total_backup_size_bytes': row[7],
                    'last_scan_timestamp': row[8],
                    'last_update_timestamp': row[9],
                }
            return {}
        except Exception as e:
            logger.error(f"Error getting dashboard stats: {e}", exc_info=True)
            return {}
        finally:
            conn.close()

    async def increment_update_stats(self, success: bool) -> None:
        """Increment update statistics."""
        return await asyncio.to_thread(self._increment_update_stats, success)

    def _increment_update_stats(self, success: bool) -> None:
        """Increment update stats (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            if success:
                cursor.execute("""
                    UPDATE dashboard_stats_cache SET
                        total_updates_performed = total_updates_performed + 1,
                        successful_updates = successful_updates + 1,
                        last_update_timestamp = CURRENT_TIMESTAMP
                    WHERE id = 1
                """)
            else:
                cursor.execute("""
                    UPDATE dashboard_stats_cache SET
                        total_updates_performed = total_updates_performed + 1,
                        failed_updates = failed_updates + 1,
                        last_update_timestamp = CURRENT_TIMESTAMP
                    WHERE id = 1
                """)
            conn.commit()
        except Exception as e:
            logger.error(f"Error incrementing update stats: {e}", exc_info=True)
            conn.rollback()
        finally:
            conn.close()

    # ===== Dashboard Query Methods =====

    async def get_total_games_count(self) -> int:
        """Get total number of games."""
        return await asyncio.to_thread(self._get_total_games_count)

    def _get_total_games_count(self) -> int:
        """Get total games count (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM games")
            return cursor.fetchone()[0] or 0
        except Exception as e:
            logger.error(f"Error getting total games count: {e}")
            return 0
        finally:
            conn.close()

    async def get_total_dlls_count(self) -> int:
        """Get total number of tracked DLLs."""
        return await asyncio.to_thread(self._get_total_dlls_count)

    def _get_total_dlls_count(self) -> int:
        """Get total DLLs count (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM game_dlls")
            return cursor.fetchone()[0] or 0
        except Exception as e:
            logger.error(f"Error getting total DLLs count: {e}")
            return 0
        finally:
            conn.close()

    async def get_updated_dlls_count(self) -> int:
        """Get count of DLLs that have been updated (have backup)."""
        return await asyncio.to_thread(self._get_updated_dlls_count)

    def _get_updated_dlls_count(self) -> int:
        """Get updated DLLs count (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT COUNT(DISTINCT game_dll_id)
                FROM dll_backups WHERE is_active = 1
            """)
            return cursor.fetchone()[0] or 0
        except Exception as e:
            logger.error(f"Error getting updated DLLs count: {e}")
            return 0
        finally:
            conn.close()

    async def get_dlls_needing_update_count(self) -> int:
        """Get count of DLLs that need updates (placeholder - returns 0 for now)."""
        # Note: This would require comparing versions to latest, which is complex
        # For now, we return 0 as a placeholder
        return 0

    async def get_version_distribution(self) -> Dict[str, Dict[str, int]]:
        """Get version distribution by DLL type."""
        return await asyncio.to_thread(self._get_version_distribution)

    def _get_version_distribution(self) -> Dict[str, Dict[str, int]]:
        """Get version distribution (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT dll_type, current_version, COUNT(*) as count
                FROM game_dlls
                WHERE current_version IS NOT NULL AND current_version != ''
                GROUP BY dll_type, current_version
                ORDER BY dll_type, count DESC
            """)

            distribution = {}
            for row in cursor.fetchall():
                dll_type = row[0]
                version = row[1]
                count = row[2]

                if dll_type not in distribution:
                    distribution[dll_type] = {}
                distribution[dll_type][version] = count

            return distribution
        except Exception as e:
            logger.error(f"Error getting version distribution: {e}")
            return {}
        finally:
            conn.close()

    async def get_update_timeline(self, start_date, end_date) -> List[Tuple]:
        """Get update counts by date for timeline chart, filling all days in range."""
        return await asyncio.to_thread(self._get_update_timeline, start_date, end_date)

    def _get_update_timeline(self, start_date, end_date) -> List[Tuple]:
        """
        Get update timeline with all days filled (runs in thread).

        Returns a list of (date, count) tuples for every day in the range,
        with 0 for days that had no updates. This ensures the chart shows
        a continuous timeline without gaps.
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        try:
            # Query only days that have updates
            cursor.execute("""
                SELECT DATE(updated_at) as update_date, COUNT(*) as count
                FROM update_history
                WHERE updated_at BETWEEN ? AND ?
                GROUP BY DATE(updated_at)
            """, (start_date.isoformat(), end_date.isoformat()))

            # Build a dict of date -> count for quick lookup
            update_counts = {}
            for row in cursor.fetchall():
                date_str = row[0]
                count = row[1]
                update_counts[date_str] = count

            # Generate all days from start_date to end_date (going backward from end_date)
            from datetime import timedelta
            timeline = []
            current_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date_normalized = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

            while current_date <= end_date_normalized:
                date_str = current_date.strftime("%Y-%m-%d")
                count = update_counts.get(date_str, 0)
                timeline.append((current_date, count))
                current_date = current_date + timedelta(days=1)

            return timeline
        except Exception as e:
            logger.error(f"Error getting update timeline: {e}")
            return []
        finally:
            conn.close()

    async def get_technology_distribution(self) -> Dict[str, int]:
        """Get DLL count by technology category (DLSS, XeSS, FSR, DirectStorage, Streamline)."""
        return await asyncio.to_thread(self._get_technology_distribution)

    def _get_technology_distribution(self) -> Dict[str, int]:
        """
        Get technology distribution grouped by category (runs in thread).

        Maps individual dll_type values to their parent categories:
        - DLSS: contains "DLSS" or "Streamline" (NVIDIA ecosystem)
        - XeSS: contains "XeSS"
        - FSR: contains "FSR" or "FidelityFX"
        - DirectStorage: contains "DirectStorage"
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT dll_type, COUNT(*) as count
                FROM game_dlls
                GROUP BY dll_type
            """)

            # Category mapping based on dll_type content
            # Streamline is merged with DLSS as it's part of NVIDIA's SDK
            category_counts = {
                "DLSS": 0,
                "XeSS": 0,
                "FSR": 0,
                "DirectStorage": 0,
            }

            for row in cursor.fetchall():
                dll_type = row[0]
                count = row[1]

                # Map to category based on content
                # Streamline is part of NVIDIA ecosystem, merge with DLSS
                if "DLSS" in dll_type or "Streamline" in dll_type:
                    category_counts["DLSS"] += count
                elif "XeSS" in dll_type:
                    category_counts["XeSS"] += count
                elif "FSR" in dll_type or "FidelityFX" in dll_type:
                    category_counts["FSR"] += count
                elif "DirectStorage" in dll_type:
                    category_counts["DirectStorage"] += count

            # Remove categories with 0 count and sort by count descending
            distribution = {k: v for k, v in category_counts.items() if v > 0}
            distribution = dict(sorted(distribution.items(), key=lambda x: x[1], reverse=True))

            return distribution
        except Exception as e:
            logger.error(f"Error getting technology distribution: {e}")
            return {}
        finally:
            conn.close()

    async def get_recent_updates(self, limit: int = 10) -> List[Dict]:
        """Get recent update history."""
        return await asyncio.to_thread(self._get_recent_updates, limit)

    def _get_recent_updates(self, limit: int) -> List[Dict]:
        """Get recent updates (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT g.name, gd.dll_type, uh.from_version, uh.to_version, uh.updated_at
                FROM update_history uh
                JOIN game_dlls gd ON uh.game_dll_id = gd.id
                JOIN games g ON gd.game_id = g.id
                WHERE uh.success = 1
                ORDER BY uh.updated_at DESC
                LIMIT ?
            """, (limit,))

            updates = []
            for row in cursor.fetchall():
                updates.append({
                    'game_name': row[0],
                    'dll_type': row[1],
                    'old_version': row[2] or 'Unknown',
                    'new_version': row[3] or 'Unknown',
                    'timestamp': row[4],
                })

            return updates
        except Exception as e:
            logger.error(f"Error getting recent updates: {e}")
            return []
        finally:
            conn.close()


# Singleton instance
db_manager = DatabaseManager()
