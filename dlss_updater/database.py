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
from dlss_updater.models import Game, GameDLL, DLLBackup, UpdateHistory, SteamImage

logger = setup_logger()


class DatabaseManager:
    """
    Singleton database manager for DLSS Updater
    Handles all database operations with async wrappers
    """
    _instance = None

    def __new__(cls):
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
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
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

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_launcher ON games(launcher)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_steam_app_id ON games(steam_app_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_dlls_game_id ON game_dlls(game_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_dlls_dll_type ON game_dlls(dll_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dll_backups_game_dll_id ON dll_backups(game_dll_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_dll_backups_active ON dll_backups(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_update_history_game_dll_id ON update_history(game_dll_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_steam_name ON steam_app_list(name COLLATE NOCASE)")

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
        """Upsert game (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
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
        finally:
            conn.close()

    async def get_games_grouped_by_launcher(self) -> Dict[str, List[Game]]:
        """Get all games grouped by launcher"""
        return await asyncio.to_thread(self._get_games_grouped_by_launcher)

    def _get_games_grouped_by_launcher(self) -> Dict[str, List[Game]]:
        """Get games grouped by launcher (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
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
            for row in cursor.fetchall():
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
        finally:
            conn.close()

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
        """Cleanup duplicate games (runs in thread)"""
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
            merged_count = 0

            for name, launcher, ids_str, paths_str in duplicates:
                ids = [int(x) for x in ids_str.split(',')]
                paths = paths_str.split(',')

                # Find the shortest path (most likely the true root)
                shortest_idx = min(range(len(paths)), key=lambda i: len(paths[i]))
                keep_id = ids[shortest_idx]
                remove_ids = [id for id in ids if id != keep_id]

                # Migrate DLLs from duplicate games to the kept game
                for remove_id in remove_ids:
                    cursor.execute("""
                        UPDATE game_dlls
                        SET game_id = ?
                        WHERE game_id = ?
                    """, (keep_id, remove_id))

                # Delete duplicate game entries
                cursor.execute("""
                    DELETE FROM games
                    WHERE id IN ({})
                """.format(','.join('?' * len(remove_ids))), remove_ids)

                merged_count += len(remove_ids)
                logger.info(f"Merged {len(remove_ids)} duplicate entries for '{name}' ({launcher})")

            conn.commit()
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
        """Batch upsert games (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
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

            for row in cursor.fetchall():
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
        finally:
            conn.close()

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
        """Batch upsert DLLs (runs in thread)"""
        conn = sqlite3.connect(str(self.db_path))
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
        finally:
            conn.close()

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


# Singleton instance
db_manager = DatabaseManager()
