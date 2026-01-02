"""
Backend Infrastructure Scaffold Specification for DLSS Updater v3.2.0
=====================================================================

This file contains the complete backend infrastructure design for 5 new features:
1. DLL Version Dashboard
2. Favorites & Game Grouping
3. System Tray (window state persistence)
4. Hotkey Support (no backend needed - Flet handles)
5. Discord Rich Presence

Design Principles:
- Python 3.14 free-threaded (GIL disabled) compatibility
- Async/await for all I/O operations via asyncio.to_thread()
- msgspec for high-performance serialization (not raw JSON)
- Aggressive concurrency scaling (CPU*32 for I/O operations)
- Thread-safe singleton patterns with double-checked locking
- SQLite with aiosqlite for async operations

Library Versions (December 2025):
- aiosqlite 0.22.0+ (async SQLite)
- msgspec 0.19.0+ (high-performance serialization)
- pypresence 4.3.0+ (Discord Rich Presence)

Author: Backend Scaffold Engineer
Date: December 2025
"""

# =============================================================================
# SECTION 1: SQLite Schema Additions
# =============================================================================

SCHEMA_MIGRATION_SQL = """
-- =============================================================================
-- MIGRATION: Backend Features v3.2.0
-- Run this after the existing schema is created
-- =============================================================================

-- ============================================================================
-- FEATURE: Favorites & Game Grouping
-- ============================================================================

-- Favorites table - games marked as favorites with ordering
CREATE TABLE IF NOT EXISTS game_favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
);

-- Custom tags (user-created, colored)
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT '#6366f1',  -- Default indigo color (hex)
    icon TEXT DEFAULT NULL,  -- Optional icon name (e.g., 'star', 'gamepad')
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Game-tag associations (many-to-many)
CREATE TABLE IF NOT EXISTS game_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,
    UNIQUE(game_id, tag_id)
);

-- Indexes for favorites
CREATE INDEX IF NOT EXISTS idx_favorites_game_id ON game_favorites(game_id);
CREATE INDEX IF NOT EXISTS idx_favorites_sort_order ON game_favorites(sort_order);

-- Indexes for tags
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_game_tags_game_id ON game_tags(game_id);
CREATE INDEX IF NOT EXISTS idx_game_tags_tag_id ON game_tags(tag_id);

-- ============================================================================
-- FEATURE: DLL Version Dashboard (Statistics Cache)
-- ============================================================================

-- Dashboard statistics cache (pre-aggregated for performance)
CREATE TABLE IF NOT EXISTS dashboard_stats_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Singleton row
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
);

-- Latest DLL versions cache (from repository)
CREATE TABLE IF NOT EXISTS latest_dll_versions (
    dll_name TEXT PRIMARY KEY,
    latest_version TEXT NOT NULL,
    source_url TEXT,
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    check_failed BOOLEAN DEFAULT 0
);

-- DLL version distribution (for charts)
CREATE TABLE IF NOT EXISTS dll_version_distribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dll_type TEXT NOT NULL,  -- e.g., 'DLSS', 'FSR', 'XeSS'
    version TEXT NOT NULL,
    game_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dll_type, version)
);

-- Indexes for dashboard
CREATE INDEX IF NOT EXISTS idx_dll_version_dist_type ON dll_version_distribution(dll_type);
CREATE INDEX IF NOT EXISTS idx_dll_version_dist_version ON dll_version_distribution(version);
CREATE INDEX IF NOT EXISTS idx_latest_dll_versions_checked ON latest_dll_versions(last_checked);

-- ============================================================================
-- FEATURE: System Tray (Window State Persistence)
-- ============================================================================

-- Window state persistence
CREATE TABLE IF NOT EXISTS window_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Singleton row
    x_position INTEGER,
    y_position INTEGER,
    width INTEGER NOT NULL DEFAULT 1280,
    height INTEGER NOT NULL DEFAULT 720,
    is_maximized BOOLEAN NOT NULL DEFAULT 0,
    is_minimized_to_tray BOOLEAN NOT NULL DEFAULT 0,
    last_active_tab TEXT DEFAULT 'home',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- FEATURE: Discord Rich Presence
-- ============================================================================

-- Discord presence state (for persistence across restarts)
CREATE TABLE IF NOT EXISTS discord_presence_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Singleton row
    is_enabled BOOLEAN NOT NULL DEFAULT 0,
    current_state TEXT DEFAULT 'idle',  -- 'idle', 'scanning', 'updating', 'browsing'
    current_details TEXT,
    games_scanned INTEGER DEFAULT 0,
    dlls_updated INTEGER DEFAULT 0,
    session_start_timestamp INTEGER,  -- Unix timestamp for Discord
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Initialize singleton rows
INSERT OR IGNORE INTO dashboard_stats_cache (id) VALUES (1);
INSERT OR IGNORE INTO window_state (id) VALUES (1);
INSERT OR IGNORE INTO discord_presence_state (id) VALUES (1);
"""


# =============================================================================
# SECTION 2: msgspec Struct Models
# =============================================================================

import msgspec
from datetime import datetime
from typing import Optional, List, Dict


# -----------------------------------------------------------------------------
# Favorites & Tagging Models
# -----------------------------------------------------------------------------

class GameFavorite(msgspec.Struct):
    """
    Favorite game record with sort ordering.

    Attributes:
        id: Database ID
        game_id: Foreign key to games table
        sort_order: Manual sort position (lower = higher priority)
        added_at: When the game was favorited
    """
    id: int
    game_id: int
    sort_order: int = 0
    added_at: datetime = msgspec.field(default_factory=datetime.now)


class Tag(msgspec.Struct):
    """
    User-created tag for game organization.

    Attributes:
        id: Database ID
        name: Tag name (case-insensitive unique)
        color: Hex color code (e.g., '#6366f1')
        icon: Optional icon identifier
        created_at: When the tag was created
    """
    id: int
    name: str
    color: str = '#6366f1'
    icon: Optional[str] = None
    created_at: datetime = msgspec.field(default_factory=datetime.now)


class GameTag(msgspec.Struct):
    """
    Association between a game and a tag (many-to-many).

    Attributes:
        id: Database ID
        game_id: Foreign key to games table
        tag_id: Foreign key to tags table
        added_at: When the association was created
    """
    id: int
    game_id: int
    tag_id: int
    added_at: datetime = msgspec.field(default_factory=datetime.now)


class GameWithTags(msgspec.Struct):
    """
    Game data enriched with favorite status and tags.
    Used for UI display in game lists.
    """
    game_id: int
    game_name: str
    launcher: str
    is_favorite: bool = False
    favorite_sort_order: int = 0
    tags: List[Tag] = msgspec.field(default_factory=list)
    has_outdated_dlls: bool = False
    has_backups: bool = False
    last_updated: Optional[datetime] = None


# -----------------------------------------------------------------------------
# Dashboard Models
# -----------------------------------------------------------------------------

class DashboardStats(msgspec.Struct):
    """
    Pre-aggregated dashboard statistics.
    Cached in database for instant loading.
    """
    total_games: int = 0
    total_dlls: int = 0
    total_updates_performed: int = 0
    successful_updates: int = 0
    failed_updates: int = 0
    games_with_outdated_dlls: int = 0
    games_with_backups: int = 0
    total_backup_size_bytes: int = 0
    last_scan_timestamp: Optional[datetime] = None
    last_update_timestamp: Optional[datetime] = None
    cache_updated_at: Optional[datetime] = None

    @property
    def success_rate(self) -> float:
        """Calculate update success rate as percentage."""
        total = self.successful_updates + self.failed_updates
        if total == 0:
            return 100.0
        return round((self.successful_updates / total) * 100, 1)

    @property
    def total_backup_size_mb(self) -> float:
        """Get backup size in megabytes."""
        return round(self.total_backup_size_bytes / (1024 * 1024), 2)


class LatestDLLVersion(msgspec.Struct):
    """
    Cached latest DLL version from repository.
    """
    dll_name: str
    latest_version: str
    source_url: Optional[str] = None
    last_checked: datetime = msgspec.field(default_factory=datetime.now)
    check_failed: bool = False


class DLLVersionDistribution(msgspec.Struct):
    """
    Distribution of DLL versions across games for charts.
    """
    dll_type: str  # e.g., 'DLSS', 'FSR', 'XeSS'
    version: str
    game_count: int = 0
    updated_at: datetime = msgspec.field(default_factory=datetime.now)


class DLLTypeStats(msgspec.Struct):
    """
    Aggregated statistics for a specific DLL type.
    Used in dashboard DLL breakdown section.
    """
    dll_type: str
    total_count: int = 0
    up_to_date_count: int = 0
    outdated_count: int = 0
    latest_version: Optional[str] = None
    version_distribution: List[DLLVersionDistribution] = msgspec.field(default_factory=list)


class UpdateHistoryEntry(msgspec.Struct):
    """
    Single update history entry for timeline display.
    """
    game_name: str
    dll_type: str
    dll_filename: str
    from_version: Optional[str]
    to_version: Optional[str]
    success: bool
    updated_at: datetime = msgspec.field(default_factory=datetime.now)


class DashboardData(msgspec.Struct):
    """
    Complete dashboard data structure for UI rendering.
    Combines all dashboard information in one struct.
    """
    stats: DashboardStats
    dll_type_breakdown: List[DLLTypeStats] = msgspec.field(default_factory=list)
    recent_updates: List[UpdateHistoryEntry] = msgspec.field(default_factory=list)
    games_needing_updates: int = 0


# -----------------------------------------------------------------------------
# Window State Models
# -----------------------------------------------------------------------------

class WindowState(msgspec.Struct):
    """
    Window state persistence for system tray feature.
    """
    x_position: Optional[int] = None
    y_position: Optional[int] = None
    width: int = 1280
    height: int = 720
    is_maximized: bool = False
    is_minimized_to_tray: bool = False
    last_active_tab: str = 'home'
    updated_at: datetime = msgspec.field(default_factory=datetime.now)


class TrayNotificationPrefs(msgspec.Struct):
    """
    Tray notification preferences (stored in config.ini).
    """
    show_update_complete: bool = True
    show_scan_complete: bool = True
    show_error_alerts: bool = True
    minimize_to_tray_on_close: bool = False
    start_minimized: bool = False


# -----------------------------------------------------------------------------
# Discord Rich Presence Models
# -----------------------------------------------------------------------------

class DiscordPresenceState(msgspec.Struct):
    """
    Discord presence state for persistence and state management.
    """
    is_enabled: bool = False
    current_state: str = 'idle'  # 'idle', 'scanning', 'updating', 'browsing'
    current_details: Optional[str] = None
    games_scanned: int = 0
    dlls_updated: int = 0
    session_start_timestamp: Optional[int] = None  # Unix timestamp
    updated_at: datetime = msgspec.field(default_factory=datetime.now)


class DiscordActivityUpdate(msgspec.Struct):
    """
    Data for updating Discord presence activity.
    Maps to pypresence update() parameters.
    """
    state: str  # Short status text
    details: str  # Longer description
    large_image: str = 'dlss_updater_logo'
    large_text: str = 'DLSS Updater'
    small_image: Optional[str] = None
    small_text: Optional[str] = None
    start_timestamp: Optional[int] = None
    buttons: Optional[List[Dict[str, str]]] = None


# -----------------------------------------------------------------------------
# Smart Group Filters
# -----------------------------------------------------------------------------

class SmartGroupType(msgspec.Struct):
    """
    Smart group definition for dynamic game filtering.
    """
    id: str  # e.g., 'needs_update', 'has_backups', 'recently_updated'
    name: str  # Display name
    description: str
    icon: str  # Icon identifier
    sql_filter: str  # SQL WHERE clause fragment


# Predefined smart groups (not stored in DB, computed dynamically)
SMART_GROUPS = [
    SmartGroupType(
        id='needs_update',
        name='Needs Update',
        description='Games with outdated DLLs',
        icon='update',
        sql_filter="""
            EXISTS (
                SELECT 1 FROM game_dlls gd
                JOIN latest_dll_versions ldv ON gd.dll_filename = ldv.dll_name
                WHERE gd.game_id = games.id
                AND gd.current_version IS NOT NULL
                AND gd.current_version != ldv.latest_version
            )
        """
    ),
    SmartGroupType(
        id='has_backups',
        name='Has Backups',
        description='Games with available backup files',
        icon='backup',
        sql_filter="""
            EXISTS (
                SELECT 1 FROM dll_backups b
                JOIN game_dlls gd ON b.game_dll_id = gd.id
                WHERE gd.game_id = games.id AND b.is_active = 1
            )
        """
    ),
    SmartGroupType(
        id='recently_updated',
        name='Recently Updated',
        description='Games updated in the last 7 days',
        icon='history',
        sql_filter="""
            EXISTS (
                SELECT 1 FROM update_history uh
                JOIN game_dlls gd ON uh.game_dll_id = gd.id
                WHERE gd.game_id = games.id
                AND uh.success = 1
                AND uh.updated_at >= datetime('now', '-7 days')
            )
        """
    ),
    SmartGroupType(
        id='favorites',
        name='Favorites',
        description='Your favorite games',
        icon='star',
        sql_filter="""
            EXISTS (
                SELECT 1 FROM game_favorites gf
                WHERE gf.game_id = games.id
            )
        """
    ),
]


# =============================================================================
# SECTION 3: DatabaseManager Method Extensions
# =============================================================================

# The following methods should be added to the DatabaseManager class in database.py

DATABASE_MANAGER_METHODS = '''
# =============================================================================
# Favorites Operations
# =============================================================================

async def add_favorite(self, game_id: int, sort_order: Optional[int] = None) -> bool:
    """
    Add a game to favorites.

    Args:
        game_id: Database ID of the game
        sort_order: Optional manual sort order. If None, appends to end.

    Returns:
        True if added, False if already exists or error
    """
    return await asyncio.to_thread(self._add_favorite, game_id, sort_order)

def _add_favorite(self, game_id: int, sort_order: Optional[int] = None) -> bool:
    """Add favorite (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        if sort_order is None:
            # Get max sort_order and add 1
            cursor.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM game_favorites")
            sort_order = cursor.fetchone()[0]

        cursor.execute("""
            INSERT INTO game_favorites (game_id, sort_order)
            VALUES (?, ?)
            ON CONFLICT(game_id) DO UPDATE SET sort_order = excluded.sort_order
        """, (game_id, sort_order))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding favorite: {e}", exc_info=True)
        conn.rollback()
        return False


async def remove_favorite(self, game_id: int) -> bool:
    """Remove a game from favorites."""
    return await asyncio.to_thread(self._remove_favorite, game_id)

def _remove_favorite(self, game_id: int) -> bool:
    """Remove favorite (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM game_favorites WHERE game_id = ?", (game_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error removing favorite: {e}", exc_info=True)
        conn.rollback()
        return False


async def get_favorites(self) -> List[GameFavorite]:
    """Get all favorites ordered by sort_order."""
    return await asyncio.to_thread(self._get_favorites)

def _get_favorites(self) -> List[GameFavorite]:
    """Get favorites (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT id, game_id, sort_order, added_at
            FROM game_favorites
            ORDER BY sort_order ASC
        """)

        favorites = []
        for row in cursor:
            favorites.append(GameFavorite(
                id=row[0],
                game_id=row[1],
                sort_order=row[2],
                added_at=datetime.fromisoformat(row[3])
            ))
        return favorites
    except Exception as e:
        logger.error(f"Error getting favorites: {e}", exc_info=True)
        return []


async def reorder_favorites(self, game_id_order: List[int]) -> bool:
    """
    Reorder favorites based on provided game ID list.

    Args:
        game_id_order: List of game IDs in desired order
    """
    return await asyncio.to_thread(self._reorder_favorites, game_id_order)

def _reorder_favorites(self, game_id_order: List[int]) -> bool:
    """Reorder favorites (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        # Update sort_order for each game in order
        update_data = [(idx, gid) for idx, gid in enumerate(game_id_order)]
        cursor.executemany("""
            UPDATE game_favorites SET sort_order = ? WHERE game_id = ?
        """, update_data)

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error reordering favorites: {e}", exc_info=True)
        conn.rollback()
        return False


async def is_favorite(self, game_id: int) -> bool:
    """Check if a game is favorited."""
    return await asyncio.to_thread(self._is_favorite, game_id)

def _is_favorite(self, game_id: int) -> bool:
    """Check if favorite (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT EXISTS(SELECT 1 FROM game_favorites WHERE game_id = ? LIMIT 1)
        """, (game_id,))
        return bool(cursor.fetchone()[0])
    except Exception as e:
        logger.error(f"Error checking favorite: {e}", exc_info=True)
        return False


async def batch_check_favorites(self, game_ids: List[int]) -> Dict[int, bool]:
    """
    Check multiple games for favorite status in a single query.

    Args:
        game_ids: List of game IDs to check

    Returns:
        Dict mapping game_id to is_favorite bool
    """
    if not game_ids:
        return {}
    return await asyncio.to_thread(self._batch_check_favorites, game_ids)

def _batch_check_favorites(self, game_ids: List[int]) -> Dict[int, bool]:
    """Batch check favorites (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        result = {gid: False for gid in game_ids}

        placeholders = ','.join('?' * len(game_ids))
        cursor.execute(f"""
            SELECT game_id FROM game_favorites WHERE game_id IN ({placeholders})
        """, game_ids)

        for row in cursor:
            result[row[0]] = True

        return result
    except Exception as e:
        logger.error(f"Error batch checking favorites: {e}", exc_info=True)
        return {gid: False for gid in game_ids}


# =============================================================================
# Tag Operations
# =============================================================================

async def create_tag(self, name: str, color: str = '#6366f1', icon: Optional[str] = None) -> Optional[Tag]:
    """Create a new tag."""
    return await asyncio.to_thread(self._create_tag, name, color, icon)

def _create_tag(self, name: str, color: str, icon: Optional[str]) -> Optional[Tag]:
    """Create tag (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO tags (name, color, icon)
            VALUES (?, ?, ?)
            RETURNING id, name, color, icon, created_at
        """, (name, color, icon))

        row = cursor.fetchone()
        conn.commit()

        if row:
            return Tag(
                id=row[0],
                name=row[1],
                color=row[2],
                icon=row[3],
                created_at=datetime.fromisoformat(row[4])
            )
        return None
    except Exception as e:
        logger.error(f"Error creating tag: {e}", exc_info=True)
        conn.rollback()
        return None


async def update_tag(self, tag_id: int, name: Optional[str] = None,
                     color: Optional[str] = None, icon: Optional[str] = None) -> bool:
    """Update an existing tag."""
    return await asyncio.to_thread(self._update_tag, tag_id, name, color, icon)

def _update_tag(self, tag_id: int, name: Optional[str],
                color: Optional[str], icon: Optional[str]) -> bool:
    """Update tag (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if color is not None:
            updates.append("color = ?")
            params.append(color)
        if icon is not None:
            updates.append("icon = ?")
            params.append(icon)

        if not updates:
            return True  # Nothing to update

        params.append(tag_id)
        cursor.execute(f"""
            UPDATE tags SET {', '.join(updates)} WHERE id = ?
        """, params)

        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error updating tag: {e}", exc_info=True)
        conn.rollback()
        return False


async def delete_tag(self, tag_id: int) -> bool:
    """Delete a tag (CASCADE removes game associations)."""
    return await asyncio.to_thread(self._delete_tag, tag_id)

def _delete_tag(self, tag_id: int) -> bool:
    """Delete tag (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting tag: {e}", exc_info=True)
        conn.rollback()
        return False


async def get_all_tags(self) -> List[Tag]:
    """Get all tags ordered by name."""
    return await asyncio.to_thread(self._get_all_tags)

def _get_all_tags(self) -> List[Tag]:
    """Get all tags (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT id, name, color, icon, created_at
            FROM tags
            ORDER BY name COLLATE NOCASE
        """)

        tags = []
        for row in cursor:
            tags.append(Tag(
                id=row[0],
                name=row[1],
                color=row[2],
                icon=row[3],
                created_at=datetime.fromisoformat(row[4])
            ))
        return tags
    except Exception as e:
        logger.error(f"Error getting tags: {e}", exc_info=True)
        return []


async def add_tag_to_game(self, game_id: int, tag_id: int) -> bool:
    """Associate a tag with a game."""
    return await asyncio.to_thread(self._add_tag_to_game, game_id, tag_id)

def _add_tag_to_game(self, game_id: int, tag_id: int) -> bool:
    """Add tag to game (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO game_tags (game_id, tag_id)
            VALUES (?, ?)
            ON CONFLICT(game_id, tag_id) DO NOTHING
        """, (game_id, tag_id))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding tag to game: {e}", exc_info=True)
        conn.rollback()
        return False


async def remove_tag_from_game(self, game_id: int, tag_id: int) -> bool:
    """Remove a tag association from a game."""
    return await asyncio.to_thread(self._remove_tag_from_game, game_id, tag_id)

def _remove_tag_from_game(self, game_id: int, tag_id: int) -> bool:
    """Remove tag from game (runs in thread)"""
    conn = self._get_thread_connection()
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


async def get_tags_for_game(self, game_id: int) -> List[Tag]:
    """Get all tags for a specific game."""
    return await asyncio.to_thread(self._get_tags_for_game, game_id)

def _get_tags_for_game(self, game_id: int) -> List[Tag]:
    """Get tags for game (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT t.id, t.name, t.color, t.icon, t.created_at
            FROM tags t
            JOIN game_tags gt ON t.id = gt.tag_id
            WHERE gt.game_id = ?
            ORDER BY t.name COLLATE NOCASE
        """, (game_id,))

        tags = []
        for row in cursor:
            tags.append(Tag(
                id=row[0],
                name=row[1],
                color=row[2],
                icon=row[3],
                created_at=datetime.fromisoformat(row[4])
            ))
        return tags
    except Exception as e:
        logger.error(f"Error getting tags for game: {e}", exc_info=True)
        return []


async def get_games_by_tag(self, tag_id: int) -> List[int]:
    """Get all game IDs that have a specific tag."""
    return await asyncio.to_thread(self._get_games_by_tag, tag_id)

def _get_games_by_tag(self, tag_id: int) -> List[int]:
    """Get games by tag (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT game_id FROM game_tags WHERE tag_id = ?
        """, (tag_id,))

        return [row[0] for row in cursor]
    except Exception as e:
        logger.error(f"Error getting games by tag: {e}", exc_info=True)
        return []


async def batch_get_tags_for_games(self, game_ids: List[int]) -> Dict[int, List[Tag]]:
    """
    Get tags for multiple games in a single query.

    Args:
        game_ids: List of game IDs

    Returns:
        Dict mapping game_id to list of Tag objects
    """
    if not game_ids:
        return {}
    return await asyncio.to_thread(self._batch_get_tags_for_games, game_ids)

def _batch_get_tags_for_games(self, game_ids: List[int]) -> Dict[int, List[Tag]]:
    """Batch get tags for games (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        result = {gid: [] for gid in game_ids}

        placeholders = ','.join('?' * len(game_ids))
        cursor.execute(f"""
            SELECT gt.game_id, t.id, t.name, t.color, t.icon, t.created_at
            FROM game_tags gt
            JOIN tags t ON gt.tag_id = t.id
            WHERE gt.game_id IN ({placeholders})
            ORDER BY t.name COLLATE NOCASE
        """, game_ids)

        for row in cursor:
            game_id = row[0]
            tag = Tag(
                id=row[1],
                name=row[2],
                color=row[3],
                icon=row[4],
                created_at=datetime.fromisoformat(row[5])
            )
            result[game_id].append(tag)

        return result
    except Exception as e:
        logger.error(f"Error batch getting tags: {e}", exc_info=True)
        return {gid: [] for gid in game_ids}


# =============================================================================
# Dashboard Statistics Operations
# =============================================================================

async def get_dashboard_stats(self) -> DashboardStats:
    """Get cached dashboard statistics."""
    return await asyncio.to_thread(self._get_dashboard_stats)

def _get_dashboard_stats(self) -> DashboardStats:
    """Get dashboard stats (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT total_games, total_dlls, total_updates_performed,
                   successful_updates, failed_updates, games_with_outdated_dlls,
                   games_with_backups, total_backup_size_bytes,
                   last_scan_timestamp, last_update_timestamp, cache_updated_at
            FROM dashboard_stats_cache
            WHERE id = 1
        """)

        row = cursor.fetchone()
        if row:
            return DashboardStats(
                total_games=row[0],
                total_dlls=row[1],
                total_updates_performed=row[2],
                successful_updates=row[3],
                failed_updates=row[4],
                games_with_outdated_dlls=row[5],
                games_with_backups=row[6],
                total_backup_size_bytes=row[7],
                last_scan_timestamp=datetime.fromisoformat(row[8]) if row[8] else None,
                last_update_timestamp=datetime.fromisoformat(row[9]) if row[9] else None,
                cache_updated_at=datetime.fromisoformat(row[10]) if row[10] else None
            )
        return DashboardStats()
    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}", exc_info=True)
        return DashboardStats()


async def refresh_dashboard_stats(self) -> DashboardStats:
    """
    Recalculate and cache dashboard statistics.
    Call this after scans or updates complete.
    """
    return await asyncio.to_thread(self._refresh_dashboard_stats)

def _refresh_dashboard_stats(self) -> DashboardStats:
    """Refresh dashboard stats (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        # Calculate statistics from source tables

        # Total games
        cursor.execute("SELECT COUNT(*) FROM games")
        total_games = cursor.fetchone()[0]

        # Total DLLs
        cursor.execute("SELECT COUNT(*) FROM game_dlls")
        total_dlls = cursor.fetchone()[0]

        # Update history stats
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed
            FROM update_history
        """)
        row = cursor.fetchone()
        total_updates = row[0] or 0
        successful_updates = row[1] or 0
        failed_updates = row[2] or 0

        # Games with backups
        cursor.execute("""
            SELECT COUNT(DISTINCT gd.game_id)
            FROM dll_backups b
            JOIN game_dlls gd ON b.game_dll_id = gd.id
            WHERE b.is_active = 1
        """)
        games_with_backups = cursor.fetchone()[0]

        # Total backup size
        cursor.execute("""
            SELECT COALESCE(SUM(backup_size), 0)
            FROM dll_backups
            WHERE is_active = 1
        """)
        total_backup_size = cursor.fetchone()[0]

        # Games with outdated DLLs (requires latest_dll_versions to be populated)
        cursor.execute("""
            SELECT COUNT(DISTINCT gd.game_id)
            FROM game_dlls gd
            JOIN latest_dll_versions ldv ON gd.dll_filename = ldv.dll_name
            WHERE gd.current_version IS NOT NULL
            AND gd.current_version != ldv.latest_version
        """)
        games_outdated = cursor.fetchone()[0]

        # Last scan timestamp (most recent game scan)
        cursor.execute("SELECT MAX(last_scanned) FROM games")
        last_scan = cursor.fetchone()[0]

        # Last update timestamp
        cursor.execute("SELECT MAX(updated_at) FROM update_history")
        last_update = cursor.fetchone()[0]

        # Update cache
        cursor.execute("""
            UPDATE dashboard_stats_cache SET
                total_games = ?,
                total_dlls = ?,
                total_updates_performed = ?,
                successful_updates = ?,
                failed_updates = ?,
                games_with_outdated_dlls = ?,
                games_with_backups = ?,
                total_backup_size_bytes = ?,
                last_scan_timestamp = ?,
                last_update_timestamp = ?,
                cache_updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (
            total_games, total_dlls, total_updates, successful_updates,
            failed_updates, games_outdated, games_with_backups, total_backup_size,
            last_scan, last_update
        ))

        conn.commit()

        return DashboardStats(
            total_games=total_games,
            total_dlls=total_dlls,
            total_updates_performed=total_updates,
            successful_updates=successful_updates,
            failed_updates=failed_updates,
            games_with_outdated_dlls=games_outdated,
            games_with_backups=games_with_backups,
            total_backup_size_bytes=total_backup_size,
            last_scan_timestamp=datetime.fromisoformat(last_scan) if last_scan else None,
            last_update_timestamp=datetime.fromisoformat(last_update) if last_update else None,
            cache_updated_at=datetime.now()
        )
    except Exception as e:
        logger.error(f"Error refreshing dashboard stats: {e}", exc_info=True)
        conn.rollback()
        return DashboardStats()


async def get_recent_update_history(self, limit: int = 20) -> List[UpdateHistoryEntry]:
    """Get recent update history for dashboard timeline."""
    return await asyncio.to_thread(self._get_recent_update_history, limit)

def _get_recent_update_history(self, limit: int) -> List[UpdateHistoryEntry]:
    """Get recent update history (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT g.name, gd.dll_type, gd.dll_filename,
                   uh.from_version, uh.to_version, uh.success, uh.updated_at
            FROM update_history uh
            JOIN game_dlls gd ON uh.game_dll_id = gd.id
            JOIN games g ON gd.game_id = g.id
            ORDER BY uh.updated_at DESC
            LIMIT ?
        """, (limit,))

        entries = []
        for row in cursor:
            entries.append(UpdateHistoryEntry(
                game_name=row[0],
                dll_type=row[1],
                dll_filename=row[2],
                from_version=row[3],
                to_version=row[4],
                success=bool(row[5]),
                updated_at=datetime.fromisoformat(row[6])
            ))
        return entries
    except Exception as e:
        logger.error(f"Error getting recent update history: {e}", exc_info=True)
        return []


async def get_dll_type_breakdown(self) -> List[DLLTypeStats]:
    """Get DLL statistics broken down by type for dashboard."""
    return await asyncio.to_thread(self._get_dll_type_breakdown)

def _get_dll_type_breakdown(self) -> List[DLLTypeStats]:
    """Get DLL type breakdown (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        # Get counts by DLL type
        cursor.execute("""
            SELECT dll_type, COUNT(*) as total
            FROM game_dlls
            GROUP BY dll_type
            ORDER BY total DESC
        """)

        type_counts = {row[0]: row[1] for row in cursor}

        # Get version distribution
        cursor.execute("""
            SELECT dll_type, current_version, COUNT(*) as count
            FROM game_dlls
            WHERE current_version IS NOT NULL
            GROUP BY dll_type, current_version
            ORDER BY dll_type, count DESC
        """)

        version_dist = {}
        for row in cursor:
            dll_type = row[0]
            if dll_type not in version_dist:
                version_dist[dll_type] = []
            version_dist[dll_type].append(DLLVersionDistribution(
                dll_type=dll_type,
                version=row[1],
                game_count=row[2]
            ))

        # Get latest versions
        cursor.execute("""
            SELECT dll_name, latest_version
            FROM latest_dll_versions
            WHERE check_failed = 0
        """)
        latest_versions = {row[0]: row[1] for row in cursor}

        # Build result
        stats = []
        for dll_type, total in type_counts.items():
            # Calculate up-to-date count
            up_to_date = 0
            for dist in version_dist.get(dll_type, []):
                # Check if this version matches any latest version
                if any(dist.version == lv for lv in latest_versions.values()):
                    up_to_date += dist.game_count

            stats.append(DLLTypeStats(
                dll_type=dll_type,
                total_count=total,
                up_to_date_count=up_to_date,
                outdated_count=total - up_to_date,
                latest_version=latest_versions.get(dll_type),
                version_distribution=version_dist.get(dll_type, [])
            ))

        return stats
    except Exception as e:
        logger.error(f"Error getting DLL type breakdown: {e}", exc_info=True)
        return []


async def cache_latest_dll_versions(self, versions: Dict[str, str]) -> bool:
    """
    Cache latest DLL versions from repository.

    Args:
        versions: Dict mapping dll_name to latest_version
    """
    return await asyncio.to_thread(self._cache_latest_dll_versions, versions)

def _cache_latest_dll_versions(self, versions: Dict[str, str]) -> bool:
    """Cache latest DLL versions (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        for dll_name, version in versions.items():
            cursor.execute("""
                INSERT INTO latest_dll_versions (dll_name, latest_version, last_checked)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(dll_name) DO UPDATE SET
                    latest_version = excluded.latest_version,
                    last_checked = CURRENT_TIMESTAMP,
                    check_failed = 0
            """, (dll_name, version))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error caching latest DLL versions: {e}", exc_info=True)
        conn.rollback()
        return False


# =============================================================================
# Window State Operations
# =============================================================================

async def get_window_state(self) -> WindowState:
    """Get persisted window state."""
    return await asyncio.to_thread(self._get_window_state)

def _get_window_state(self) -> WindowState:
    """Get window state (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT x_position, y_position, width, height,
                   is_maximized, is_minimized_to_tray, last_active_tab, updated_at
            FROM window_state
            WHERE id = 1
        """)

        row = cursor.fetchone()
        if row:
            return WindowState(
                x_position=row[0],
                y_position=row[1],
                width=row[2],
                height=row[3],
                is_maximized=bool(row[4]),
                is_minimized_to_tray=bool(row[5]),
                last_active_tab=row[6],
                updated_at=datetime.fromisoformat(row[7]) if row[7] else datetime.now()
            )
        return WindowState()
    except Exception as e:
        logger.error(f"Error getting window state: {e}", exc_info=True)
        return WindowState()


async def save_window_state(self, state: WindowState) -> bool:
    """Save window state."""
    return await asyncio.to_thread(self._save_window_state, state)

def _save_window_state(self, state: WindowState) -> bool:
    """Save window state (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE window_state SET
                x_position = ?,
                y_position = ?,
                width = ?,
                height = ?,
                is_maximized = ?,
                is_minimized_to_tray = ?,
                last_active_tab = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (
            state.x_position,
            state.y_position,
            state.width,
            state.height,
            1 if state.is_maximized else 0,
            1 if state.is_minimized_to_tray else 0,
            state.last_active_tab
        ))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving window state: {e}", exc_info=True)
        conn.rollback()
        return False


# =============================================================================
# Discord Presence State Operations
# =============================================================================

async def get_discord_presence_state(self) -> DiscordPresenceState:
    """Get Discord presence state."""
    return await asyncio.to_thread(self._get_discord_presence_state)

def _get_discord_presence_state(self) -> DiscordPresenceState:
    """Get Discord presence state (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT is_enabled, current_state, current_details,
                   games_scanned, dlls_updated, session_start_timestamp, updated_at
            FROM discord_presence_state
            WHERE id = 1
        """)

        row = cursor.fetchone()
        if row:
            return DiscordPresenceState(
                is_enabled=bool(row[0]),
                current_state=row[1],
                current_details=row[2],
                games_scanned=row[3],
                dlls_updated=row[4],
                session_start_timestamp=row[5],
                updated_at=datetime.fromisoformat(row[6]) if row[6] else datetime.now()
            )
        return DiscordPresenceState()
    except Exception as e:
        logger.error(f"Error getting Discord presence state: {e}", exc_info=True)
        return DiscordPresenceState()


async def save_discord_presence_state(self, state: DiscordPresenceState) -> bool:
    """Save Discord presence state."""
    return await asyncio.to_thread(self._save_discord_presence_state, state)

def _save_discord_presence_state(self, state: DiscordPresenceState) -> bool:
    """Save Discord presence state (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE discord_presence_state SET
                is_enabled = ?,
                current_state = ?,
                current_details = ?,
                games_scanned = ?,
                dlls_updated = ?,
                session_start_timestamp = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (
            1 if state.is_enabled else 0,
            state.current_state,
            state.current_details,
            state.games_scanned,
            state.dlls_updated,
            state.session_start_timestamp
        ))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving Discord presence state: {e}", exc_info=True)
        conn.rollback()
        return False


# =============================================================================
# Smart Group Queries
# =============================================================================

async def get_games_by_smart_group(self, group_id: str, limit: int = 100) -> List[Game]:
    """
    Get games matching a smart group filter.

    Args:
        group_id: Smart group ID ('needs_update', 'has_backups', 'recently_updated', 'favorites')
        limit: Maximum games to return
    """
    return await asyncio.to_thread(self._get_games_by_smart_group, group_id, limit)

def _get_games_by_smart_group(self, group_id: str, limit: int) -> List[Game]:
    """Get games by smart group (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    # Find the smart group definition
    smart_group = None
    for sg in SMART_GROUPS:
        if sg.id == group_id:
            smart_group = sg
            break

    if not smart_group:
        logger.warning(f"Unknown smart group: {group_id}")
        return []

    try:
        query = f"""
            SELECT id, name, path, launcher, steam_app_id, last_scanned, created_at
            FROM games
            WHERE {smart_group.sql_filter}
            ORDER BY name COLLATE NOCASE
            LIMIT ?
        """

        cursor.execute(query, (limit,))

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
        logger.error(f"Error getting games by smart group {group_id}: {e}", exc_info=True)
        return []


async def get_smart_group_counts(self) -> Dict[str, int]:
    """
    Get counts for all smart groups (for sidebar badges).

    Returns:
        Dict mapping group_id to count
    """
    return await asyncio.to_thread(self._get_smart_group_counts)

def _get_smart_group_counts(self) -> Dict[str, int]:
    """Get smart group counts (runs in thread)"""
    conn = self._get_thread_connection()
    cursor = conn.cursor()

    counts = {}

    for sg in SMART_GROUPS:
        try:
            query = f"""
                SELECT COUNT(*) FROM games WHERE {sg.sql_filter}
            """
            cursor.execute(query)
            counts[sg.id] = cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"Error counting smart group {sg.id}: {e}", exc_info=True)
            counts[sg.id] = 0

    return counts
'''


# =============================================================================
# SECTION 4: Service Classes
# =============================================================================

SERVICE_CLASSES_CODE = '''
"""
Service Classes for DLSS Updater v3.2.0
High-level business logic encapsulating database operations
"""

import asyncio
import threading
import time
from typing import Optional, List, Dict, Callable, Any
from datetime import datetime

from dlss_updater.logger import setup_logger
from dlss_updater.config import Concurrency
from dlss_updater.models import Game

logger = setup_logger()


# =============================================================================
# Dashboard Service
# =============================================================================

class DashboardService:
    """
    High-level service for dashboard data aggregation and caching.

    Provides:
    - Efficient stats retrieval with caching
    - Background refresh capabilities
    - Thread-safe operations for free-threaded Python 3.14

    Usage:
        service = DashboardService()
        await service.initialize()
        data = await service.get_dashboard_data()
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern with double-checked locking."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        self._db_manager = None
        self._cache_lock = threading.Lock()
        self._cached_stats: Optional[DashboardStats] = None
        self._cache_timestamp: float = 0
        self._cache_ttl_seconds: float = 60.0  # 1 minute cache

        self._initialized = True

    async def _get_db(self):
        """Lazy load database manager."""
        if self._db_manager is None:
            from dlss_updater.database import db_manager
            self._db_manager = db_manager
        return self._db_manager

    async def initialize(self):
        """Initialize the service and populate latest DLL versions."""
        db = await self._get_db()

        # Populate latest DLL versions from config
        from dlss_updater.config import LATEST_DLL_VERSIONS
        await db.cache_latest_dll_versions(LATEST_DLL_VERSIONS)

        logger.info("DashboardService initialized")

    async def get_dashboard_data(self, force_refresh: bool = False) -> DashboardData:
        """
        Get complete dashboard data.

        Args:
            force_refresh: If True, bypass cache and recalculate stats

        Returns:
            DashboardData with all dashboard information
        """
        db = await self._get_db()

        # Check cache
        with self._cache_lock:
            cache_valid = (
                not force_refresh
                and self._cached_stats is not None
                and (time.time() - self._cache_timestamp) < self._cache_ttl_seconds
            )

        if cache_valid:
            stats = self._cached_stats
        else:
            # Refresh stats
            stats = await db.refresh_dashboard_stats()
            with self._cache_lock:
                self._cached_stats = stats
                self._cache_timestamp = time.time()

        # Get additional data in parallel
        dll_breakdown_task = db.get_dll_type_breakdown()
        recent_updates_task = db.get_recent_update_history(20)

        dll_breakdown, recent_updates = await asyncio.gather(
            dll_breakdown_task,
            recent_updates_task
        )

        return DashboardData(
            stats=stats,
            dll_type_breakdown=dll_breakdown,
            recent_updates=recent_updates,
            games_needing_updates=stats.games_with_outdated_dlls
        )

    async def refresh_stats(self) -> DashboardStats:
        """Force refresh dashboard statistics."""
        db = await self._get_db()
        stats = await db.refresh_dashboard_stats()

        with self._cache_lock:
            self._cached_stats = stats
            self._cache_timestamp = time.time()

        logger.info("Dashboard stats refreshed")
        return stats

    def invalidate_cache(self):
        """Invalidate the stats cache (call after updates/scans)."""
        with self._cache_lock:
            self._cache_timestamp = 0
        logger.debug("Dashboard cache invalidated")


# Singleton instance
dashboard_service = DashboardService()


# =============================================================================
# Discord Presence Manager
# =============================================================================

class DiscordPresenceManager:
    """
    Manages Discord Rich Presence integration.

    Features:
    - Async-safe presence updates
    - State persistence across restarts
    - Automatic reconnection handling
    - Thread-safe for free-threaded Python 3.14

    Library: pypresence 4.3.0+
    Discord Application ID required for Rich Presence

    Usage:
        manager = DiscordPresenceManager()
        await manager.connect()
        await manager.update_presence(state='scanning', details='Scanning 150 games...')
        await manager.disconnect()
    """

    # Discord Application ID (replace with actual ID)
    # Create at https://discord.com/developers/applications
    DISCORD_CLIENT_ID = "YOUR_DISCORD_CLIENT_ID"  # TODO: Replace with actual ID

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern with double-checked locking."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        self._rpc = None
        self._connected = False
        self._enabled = False
        self._connect_lock = asyncio.Lock()
        self._state = DiscordPresenceState()
        self._db_manager = None

        # Rate limiting (Discord limits updates to every 15 seconds)
        self._last_update_time: float = 0
        self._update_cooldown: float = 15.0

        self._initialized = True
        logger.info("DiscordPresenceManager initialized")

    async def _get_db(self):
        """Lazy load database manager."""
        if self._db_manager is None:
            from dlss_updater.database import db_manager
            self._db_manager = db_manager
        return self._db_manager

    async def _load_state(self):
        """Load persisted state from database."""
        db = await self._get_db()
        self._state = await db.get_discord_presence_state()
        self._enabled = self._state.is_enabled

    async def _save_state(self):
        """Save current state to database."""
        db = await self._get_db()
        await db.save_discord_presence_state(self._state)

    async def connect(self) -> bool:
        """
        Connect to Discord.

        Returns:
            True if connected successfully, False otherwise
        """
        async with self._connect_lock:
            if self._connected:
                return True

            try:
                # Import pypresence (optional dependency)
                from pypresence import Presence

                # Run connection in thread pool (blocking operation)
                self._rpc = Presence(self.DISCORD_CLIENT_ID)
                await asyncio.to_thread(self._rpc.connect)

                self._connected = True
                self._state.session_start_timestamp = int(time.time())
                await self._save_state()

                logger.info("Connected to Discord Rich Presence")
                return True

            except ImportError:
                logger.warning("pypresence not installed, Discord integration disabled")
                return False
            except Exception as e:
                logger.error(f"Failed to connect to Discord: {e}")
                self._connected = False
                return False

    async def disconnect(self):
        """Disconnect from Discord."""
        async with self._connect_lock:
            if not self._connected or self._rpc is None:
                return

            try:
                await asyncio.to_thread(self._rpc.close)
                logger.info("Disconnected from Discord Rich Presence")
            except Exception as e:
                logger.debug(f"Error disconnecting from Discord: {e}")
            finally:
                self._connected = False
                self._rpc = None

    async def update_presence(
        self,
        state: str,
        details: str,
        large_image: str = 'dlss_updater_logo',
        large_text: str = 'DLSS Updater',
        small_image: Optional[str] = None,
        small_text: Optional[str] = None
    ) -> bool:
        """
        Update Discord Rich Presence.

        Respects Discord's 15-second rate limit.

        Args:
            state: Short status text (e.g., 'Scanning games...')
            details: Longer description (e.g., 'Found 150 games with DLSS')
            large_image: Large image key (set in Discord Developer Portal)
            large_text: Tooltip for large image
            small_image: Small image key (optional)
            small_text: Tooltip for small image (optional)

        Returns:
            True if update succeeded, False otherwise
        """
        if not self._enabled or not self._connected:
            return False

        # Rate limiting
        current_time = time.time()
        if (current_time - self._last_update_time) < self._update_cooldown:
            logger.debug("Discord update rate limited, skipping")
            return False

        try:
            # Build update kwargs
            kwargs = {
                'state': state[:128],  # Discord limit
                'details': details[:128],
                'large_image': large_image,
                'large_text': large_text,
            }

            if small_image:
                kwargs['small_image'] = small_image
            if small_text:
                kwargs['small_text'] = small_text

            # Add session start time if available
            if self._state.session_start_timestamp:
                kwargs['start'] = self._state.session_start_timestamp

            # Run update in thread pool
            await asyncio.to_thread(self._rpc.update, **kwargs)

            self._last_update_time = current_time

            # Update state
            self._state.current_state = state
            self._state.current_details = details
            await self._save_state()

            logger.debug(f"Discord presence updated: {state}")
            return True

        except Exception as e:
            logger.error(f"Failed to update Discord presence: {e}")
            # Try to reconnect on failure
            self._connected = False
            return False

    async def set_enabled(self, enabled: bool):
        """Enable or disable Discord presence."""
        self._enabled = enabled
        self._state.is_enabled = enabled

        if enabled:
            await self.connect()
        else:
            await self.disconnect()

        await self._save_state()
        logger.info(f"Discord presence {'enabled' if enabled else 'disabled'}")

    def is_enabled(self) -> bool:
        """Check if Discord presence is enabled."""
        return self._enabled

    def is_connected(self) -> bool:
        """Check if connected to Discord."""
        return self._connected

    # =========================================================================
    # Convenience Methods for Common States
    # =========================================================================

    async def set_idle(self):
        """Set presence to idle state."""
        await self.update_presence(
            state='Idle',
            details='Ready to update DLLs'
        )

    async def set_scanning(self, games_found: int = 0):
        """Set presence to scanning state."""
        self._state.games_scanned = games_found
        await self.update_presence(
            state='Scanning for games...',
            details=f'Found {games_found} games' if games_found > 0 else 'Searching...',
            small_image='scanning',
            small_text='Scanning'
        )

    async def set_updating(self, dlls_updated: int = 0, total: int = 0):
        """Set presence to updating state."""
        self._state.dlls_updated = dlls_updated
        progress = f'{dlls_updated}/{total}' if total > 0 else str(dlls_updated)
        await self.update_presence(
            state='Updating DLLs...',
            details=f'Updated {progress} DLLs',
            small_image='updating',
            small_text='Updating'
        )

    async def set_browsing(self, game_count: int = 0):
        """Set presence to browsing state."""
        await self.update_presence(
            state='Browsing library',
            details=f'Managing {game_count} games'
        )


# Singleton instance
discord_presence = DiscordPresenceManager()


# =============================================================================
# Game Organization Service
# =============================================================================

class GameOrganizationService:
    """
    Service for managing favorites and tags.

    Provides high-level operations for:
    - Favorite management with drag-drop reordering
    - Tag CRUD operations
    - Batch operations for UI efficiency
    - Smart group filtering

    Thread-safe for free-threaded Python 3.14.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern with double-checked locking."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        self._db_manager = None
        self._initialized = True

    async def _get_db(self):
        """Lazy load database manager."""
        if self._db_manager is None:
            from dlss_updater.database import db_manager
            self._db_manager = db_manager
        return self._db_manager

    # =========================================================================
    # Favorites
    # =========================================================================

    async def toggle_favorite(self, game_id: int) -> bool:
        """
        Toggle favorite status for a game.

        Returns:
            True if game is now favorited, False if unfavorited
        """
        db = await self._get_db()

        if await db.is_favorite(game_id):
            await db.remove_favorite(game_id)
            return False
        else:
            await db.add_favorite(game_id)
            return True

    async def get_favorite_games(self) -> List[Game]:
        """Get all favorite games in sort order."""
        db = await self._get_db()
        favorites = await db.get_favorites()

        if not favorites:
            return []

        # Get game details
        games = await db.get_games_by_smart_group('favorites')

        # Sort by favorite sort_order
        fav_order = {f.game_id: f.sort_order for f in favorites}
        games.sort(key=lambda g: fav_order.get(g.id, 999999))

        return games

    async def reorder_favorites(self, game_ids: List[int]) -> bool:
        """
        Reorder favorites based on new order (e.g., after drag-drop).

        Args:
            game_ids: List of game IDs in new order
        """
        db = await self._get_db()
        return await db.reorder_favorites(game_ids)

    # =========================================================================
    # Tags
    # =========================================================================

    async def create_tag(
        self,
        name: str,
        color: str = '#6366f1',
        icon: Optional[str] = None
    ) -> Optional[Tag]:
        """Create a new tag."""
        db = await self._get_db()
        return await db.create_tag(name, color, icon)

    async def delete_tag(self, tag_id: int) -> bool:
        """Delete a tag and all its associations."""
        db = await self._get_db()
        return await db.delete_tag(tag_id)

    async def update_tag(
        self,
        tag_id: int,
        name: Optional[str] = None,
        color: Optional[str] = None,
        icon: Optional[str] = None
    ) -> bool:
        """Update tag properties."""
        db = await self._get_db()
        return await db.update_tag(tag_id, name, color, icon)

    async def get_all_tags(self) -> List[Tag]:
        """Get all available tags."""
        db = await self._get_db()
        return await db.get_all_tags()

    async def tag_game(self, game_id: int, tag_id: int) -> bool:
        """Add a tag to a game."""
        db = await self._get_db()
        return await db.add_tag_to_game(game_id, tag_id)

    async def untag_game(self, game_id: int, tag_id: int) -> bool:
        """Remove a tag from a game."""
        db = await self._get_db()
        return await db.remove_tag_from_game(game_id, tag_id)

    async def get_game_tags(self, game_id: int) -> List[Tag]:
        """Get all tags for a game."""
        db = await self._get_db()
        return await db.get_tags_for_game(game_id)

    async def get_games_with_tag(self, tag_id: int) -> List[int]:
        """Get all game IDs that have a specific tag."""
        db = await self._get_db()
        return await db.get_games_by_tag(tag_id)

    # =========================================================================
    # Batch Operations (for UI efficiency)
    # =========================================================================

    async def get_games_with_metadata(
        self,
        game_ids: List[int]
    ) -> List[GameWithTags]:
        """
        Get games enriched with favorite status and tags.
        Optimized for batch operations in game lists.

        Args:
            game_ids: List of game IDs to enrich

        Returns:
            List of GameWithTags objects
        """
        if not game_ids:
            return []

        db = await self._get_db()

        # Batch fetch in parallel
        favorites_task = db.batch_check_favorites(game_ids)
        tags_task = db.batch_get_tags_for_games(game_ids)
        backups_task = db.batch_check_games_have_backups(game_ids)

        favorites, tags_by_game, has_backups = await asyncio.gather(
            favorites_task, tags_task, backups_task
        )

        # TODO: Fetch game details and outdated status
        # This requires additional database queries

        results = []
        for game_id in game_ids:
            results.append(GameWithTags(
                game_id=game_id,
                game_name='',  # TODO: Populate from games table
                launcher='',  # TODO: Populate from games table
                is_favorite=favorites.get(game_id, False),
                tags=tags_by_game.get(game_id, []),
                has_backups=has_backups.get(game_id, False)
            ))

        return results

    # =========================================================================
    # Smart Groups
    # =========================================================================

    async def get_smart_group_games(
        self,
        group_id: str,
        limit: int = 100
    ) -> List[Game]:
        """Get games matching a smart group filter."""
        db = await self._get_db()
        return await db.get_games_by_smart_group(group_id, limit)

    async def get_smart_group_counts(self) -> Dict[str, int]:
        """Get counts for all smart groups (for sidebar badges)."""
        db = await self._get_db()
        return await db.get_smart_group_counts()


# Singleton instance
game_organization = GameOrganizationService()
'''


# =============================================================================
# SECTION 5: Config.py Additions
# =============================================================================

CONFIG_ADDITIONS = '''
# =============================================================================
# CONFIG.PY ADDITIONS - Add to ConfigManager class
# =============================================================================

# In __init__(), add these sections after existing initialization:

# Initialize SystemTray section
if not self.has_section("SystemTray"):
    self.add_section("SystemTray")
    self["SystemTray"]["MinimizeToTray"] = "false"
    self["SystemTray"]["StartMinimized"] = "false"
    self["SystemTray"]["ShowUpdateNotifications"] = "true"
    self["SystemTray"]["ShowScanNotifications"] = "true"
    self["SystemTray"]["ShowErrorNotifications"] = "true"
    self.save()

# Initialize DiscordPresence section
if not self.has_section("DiscordPresence"):
    self.add_section("DiscordPresence")
    self["DiscordPresence"]["Enabled"] = "false"
    self.save()

# =============================================================================
# New Methods to Add
# =============================================================================

# ----- System Tray Preferences -----

def get_minimize_to_tray(self) -> bool:
    """Get whether app minimizes to system tray on close."""
    return self["SystemTray"].getboolean("MinimizeToTray", False)

def set_minimize_to_tray(self, enabled: bool):
    """Set minimize to tray on close preference."""
    self["SystemTray"]["MinimizeToTray"] = str(enabled).lower()
    self.save()

def get_start_minimized(self) -> bool:
    """Get whether app starts minimized to tray."""
    return self["SystemTray"].getboolean("StartMinimized", False)

def set_start_minimized(self, enabled: bool):
    """Set start minimized preference."""
    self["SystemTray"]["StartMinimized"] = str(enabled).lower()
    self.save()

def get_tray_notification_prefs(self) -> TrayNotificationPrefs:
    """Get all tray notification preferences as struct."""
    return TrayNotificationPrefs(
        show_update_complete=self["SystemTray"].getboolean("ShowUpdateNotifications", True),
        show_scan_complete=self["SystemTray"].getboolean("ShowScanNotifications", True),
        show_error_alerts=self["SystemTray"].getboolean("ShowErrorNotifications", True),
        minimize_to_tray_on_close=self.get_minimize_to_tray(),
        start_minimized=self.get_start_minimized()
    )

def save_tray_notification_prefs(self, prefs: TrayNotificationPrefs):
    """Save tray notification preferences from struct."""
    if not self.has_section("SystemTray"):
        self.add_section("SystemTray")

    self["SystemTray"]["ShowUpdateNotifications"] = str(prefs.show_update_complete).lower()
    self["SystemTray"]["ShowScanNotifications"] = str(prefs.show_scan_complete).lower()
    self["SystemTray"]["ShowErrorNotifications"] = str(prefs.show_error_alerts).lower()
    self["SystemTray"]["MinimizeToTray"] = str(prefs.minimize_to_tray_on_close).lower()
    self["SystemTray"]["StartMinimized"] = str(prefs.start_minimized).lower()
    self.save()


# ----- Discord Rich Presence -----

def get_discord_presence_enabled(self) -> bool:
    """Get whether Discord Rich Presence is enabled."""
    if not self.has_section("DiscordPresence"):
        return False
    return self["DiscordPresence"].getboolean("Enabled", False)

def set_discord_presence_enabled(self, enabled: bool):
    """Set Discord Rich Presence enabled state."""
    if not self.has_section("DiscordPresence"):
        self.add_section("DiscordPresence")
    self["DiscordPresence"]["Enabled"] = str(enabled).lower()
    self.save()
'''


# =============================================================================
# SECTION 6: Concurrency Patterns
# =============================================================================

CONCURRENCY_PATTERNS = '''
# =============================================================================
# CONCURRENCY PATTERNS FOR FREE-THREADED PYTHON 3.14
# =============================================================================

"""
Design Principles for Free-Threaded Python 3.14:

1. THREAD SAFETY
   - Use threading.Lock for mutable shared state
   - Double-checked locking for singleton patterns
   - Thread-local storage for connection reuse
   - asyncio.Lock for async-specific synchronization

2. CONCURRENCY SCALING
   - I/O-bound: CPU * 32 (IO_HEAVY in Concurrency class)
   - CPU-bound: CPU * 0.9 (CPU_BOUND in Concurrency class)
   - Network: CPU * 64 (IO_EXTREME for mostly-waiting operations)

3. ASYNC PATTERNS
   - All I/O operations use async/await
   - Sync operations run in thread pool via asyncio.to_thread()
   - Connection pooling for database access
   - Bounded semaphores for rate limiting

4. DATA STRUCTURES
   - msgspec.Struct for immutable data transfer objects
   - Thread-safe caches with LRU eviction
   - Atomic updates where possible
"""

import asyncio
import threading
from typing import TypeVar, Generic, Optional
from collections import OrderedDict

T = TypeVar('T')


class ThreadSafeCache(Generic[T]):
    """
    Thread-safe LRU cache for free-threaded Python 3.14.

    Features:
    - O(1) get/put operations
    - LRU eviction when at capacity
    - TTL-based expiration
    - Thread-safe with fine-grained locking
    """

    def __init__(self, max_size: int = 100, ttl_seconds: float = 60.0):
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[T, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds

    def get(self, key: str) -> Optional[T]:
        """Get item from cache if not expired."""
        import time

        with self._lock:
            if key not in self._cache:
                return None

            value, timestamp = self._cache[key]

            # Check expiration
            if (time.time() - timestamp) > self._ttl_seconds:
                del self._cache[key]
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return value

    def put(self, key: str, value: T):
        """Put item in cache."""
        import time

        with self._lock:
            # Remove oldest if at capacity
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)

            self._cache[key] = (value, time.time())

    def invalidate(self, key: Optional[str] = None):
        """Invalidate specific key or all keys."""
        with self._lock:
            if key is None:
                self._cache.clear()
            elif key in self._cache:
                del self._cache[key]


class BoundedConcurrentExecutor:
    """
    Executor for bounded concurrent async operations.

    Uses semaphore to limit concurrent operations while
    maximizing throughput within the limit.

    Usage:
        async with BoundedConcurrentExecutor(max_concurrent=32) as executor:
            results = await executor.map(process_item, items)
    """

    def __init__(self, max_concurrent: int = None):
        from dlss_updater.config import Concurrency
        self._max_concurrent = max_concurrent or Concurrency.IO_HEAVY
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def __aenter__(self):
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def map(self, func, items):
        """
        Map async function over items with bounded concurrency.

        Args:
            func: Async function to call for each item
            items: Iterable of items to process

        Returns:
            List of results in original order
        """
        async def bounded_call(item):
            async with self._semaphore:
                return await func(item)

        tasks = [bounded_call(item) for item in items]
        return await asyncio.gather(*tasks)


# Example usage in services:
"""
async def batch_process_games(game_ids: List[int]) -> List[GameResult]:
    async with BoundedConcurrentExecutor(Concurrency.IO_HEAVY) as executor:
        return await executor.map(process_single_game, game_ids)
"""
'''


# =============================================================================
# SECTION 7: Migration Script
# =============================================================================

MIGRATION_SCRIPT = '''
"""
Database Migration Script for v3.2.0 Features
Run this to add new tables for:
- Favorites & Tags
- Dashboard Stats Cache
- Window State
- Discord Presence State
"""

import asyncio
import sqlite3
from pathlib import Path
import appdirs


def get_db_path() -> Path:
    """Get database path matching existing DatabaseManager."""
    app_name = "DLSS-Updater"
    app_author = "Recol"
    config_dir = appdirs.user_config_dir(app_name, app_author)
    return Path(config_dir) / "games.db"


def run_migration():
    """Run the v3.2.0 schema migration."""
    db_path = get_db_path()

    if not db_path.exists():
        print(f"Database not found at {db_path}, skipping migration")
        return

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    try:
        print("Running v3.2.0 schema migration...")

        # Execute migration SQL (from SCHEMA_MIGRATION_SQL above)
        migration_sql = """
        -- Favorites table
        CREATE TABLE IF NOT EXISTS game_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
        );

        -- Tags table
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            color TEXT NOT NULL DEFAULT '#6366f1',
            icon TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Game-tag associations
        CREATE TABLE IF NOT EXISTS game_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,
            UNIQUE(game_id, tag_id)
        );

        -- Dashboard stats cache
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
        );

        -- Latest DLL versions cache
        CREATE TABLE IF NOT EXISTS latest_dll_versions (
            dll_name TEXT PRIMARY KEY,
            latest_version TEXT NOT NULL,
            source_url TEXT,
            last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            check_failed BOOLEAN DEFAULT 0
        );

        -- DLL version distribution
        CREATE TABLE IF NOT EXISTS dll_version_distribution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dll_type TEXT NOT NULL,
            version TEXT NOT NULL,
            game_count INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(dll_type, version)
        );

        -- Window state
        CREATE TABLE IF NOT EXISTS window_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            x_position INTEGER,
            y_position INTEGER,
            width INTEGER NOT NULL DEFAULT 1280,
            height INTEGER NOT NULL DEFAULT 720,
            is_maximized BOOLEAN NOT NULL DEFAULT 0,
            is_minimized_to_tray BOOLEAN NOT NULL DEFAULT 0,
            last_active_tab TEXT DEFAULT 'home',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Discord presence state
        CREATE TABLE IF NOT EXISTS discord_presence_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_enabled BOOLEAN NOT NULL DEFAULT 0,
            current_state TEXT DEFAULT 'idle',
            current_details TEXT,
            games_scanned INTEGER DEFAULT 0,
            dlls_updated INTEGER DEFAULT 0,
            session_start_timestamp INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_favorites_game_id ON game_favorites(game_id);
        CREATE INDEX IF NOT EXISTS idx_favorites_sort_order ON game_favorites(sort_order);
        CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_game_tags_game_id ON game_tags(game_id);
        CREATE INDEX IF NOT EXISTS idx_game_tags_tag_id ON game_tags(tag_id);
        CREATE INDEX IF NOT EXISTS idx_dll_version_dist_type ON dll_version_distribution(dll_type);
        CREATE INDEX IF NOT EXISTS idx_latest_dll_versions_checked ON latest_dll_versions(last_checked);

        -- Initialize singleton rows
        INSERT OR IGNORE INTO dashboard_stats_cache (id) VALUES (1);
        INSERT OR IGNORE INTO window_state (id) VALUES (1);
        INSERT OR IGNORE INTO discord_presence_state (id) VALUES (1);
        """

        cursor.executescript(migration_sql)
        conn.commit()

        print("Migration completed successfully!")
        print("New tables created:")
        print("  - game_favorites")
        print("  - tags")
        print("  - game_tags")
        print("  - dashboard_stats_cache")
        print("  - latest_dll_versions")
        print("  - dll_version_distribution")
        print("  - window_state")
        print("  - discord_presence_state")

    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
'''


# =============================================================================
# SECTION 8: Summary and Integration Notes
# =============================================================================

INTEGRATION_NOTES = """
# =============================================================================
# INTEGRATION NOTES FOR DLSS UPDATER v3.2.0
# =============================================================================

## Implementation Steps

1. **Run Database Migration**
   - Execute the migration script to create new tables
   - This is safe to run multiple times (uses IF NOT EXISTS)

2. **Add msgspec Models to models.py**
   - Copy the struct definitions from Section 2
   - Import datetime and Optional from typing

3. **Extend DatabaseManager in database.py**
   - Add methods from Section 3 (DATABASE_MANAGER_METHODS)
   - Update _create_schema() to include new tables
   - Import new models

4. **Create Service Classes**
   - Create new file: services.py
   - Copy service classes from Section 4
   - Import from database.py and config.py

5. **Update config.py**
   - Add new sections in ConfigManager.__init__()
   - Add getter/setter methods from Section 5

6. **Install Optional Dependencies**
   - pypresence 4.3.0+ for Discord Rich Presence
   - Already have: aiosqlite, msgspec


## Concurrency Guidelines

### For I/O Operations (file/network/database):
```python
# Use Concurrency.IO_HEAVY (CPU * 32)
async with BoundedConcurrentExecutor(Concurrency.IO_HEAVY) as executor:
    results = await executor.map(io_operation, items)
```

### For CPU Operations (parsing/computation):
```python
# Use ThreadPoolExecutor with Concurrency.CPU_BOUND
with ThreadPoolExecutor(max_workers=Concurrency.CPU_BOUND) as executor:
    futures = [executor.submit(cpu_task, item) for item in items]
```

### For Database Operations:
```python
# Always use async methods which internally use asyncio.to_thread()
stats = await db_manager.get_dashboard_stats()
```


## Testing Checklist

- [ ] Run migration on fresh database
- [ ] Run migration on existing database
- [ ] Test favorites add/remove/reorder
- [ ] Test tag CRUD operations
- [ ] Test dashboard stats refresh
- [ ] Test window state persistence
- [ ] Test Discord presence (if client ID configured)
- [ ] Verify thread safety under concurrent access
- [ ] Performance test with large game collections


## Library Versions (December 2025)

| Library    | Version | Purpose                    |
|------------|---------|----------------------------|
| aiosqlite  | 0.22.0+ | Async SQLite operations    |
| msgspec    | 0.19.0+ | High-performance JSON      |
| pypresence | 4.3.0+  | Discord Rich Presence      |
| Python     | 3.14    | Free-threaded interpreter  |
| Flet       | 0.28.3  | UI framework               |


## Discord Rich Presence Setup

1. Create application at https://discord.com/developers/applications
2. Get Client ID from application settings
3. Replace DISCORD_CLIENT_ID in DiscordPresenceManager
4. Upload images (large_image, small_image) in Rich Presence Assets
5. Test with Discord client running


## Performance Expectations

| Operation                    | Target Latency |
|------------------------------|----------------|
| Dashboard stats (cached)     | < 1ms          |
| Dashboard stats (refresh)    | < 100ms        |
| Toggle favorite              | < 10ms         |
| Get games with metadata      | < 50ms         |
| Smart group query            | < 50ms         |
| Window state save            | < 5ms          |
| Discord presence update      | < 50ms         |

"""

# Print summary when module is imported
if __name__ == "__main__":
    print("=" * 70)
    print("DLSS Updater v3.2.0 Backend Infrastructure Specification")
    print("=" * 70)
    print()
    print("This file contains complete backend infrastructure for:")
    print("  1. DLL Version Dashboard")
    print("  2. Favorites & Game Grouping")
    print("  3. System Tray (window state persistence)")
    print("  4. Discord Rich Presence")
    print()
    print("Sections included:")
    print("  - SCHEMA_MIGRATION_SQL: SQLite schema additions")
    print("  - msgspec Struct models for all new data types")
    print("  - DATABASE_MANAGER_METHODS: Full method implementations")
    print("  - SERVICE_CLASSES_CODE: High-level service classes")
    print("  - CONFIG_ADDITIONS: ConfigManager extensions")
    print("  - CONCURRENCY_PATTERNS: Thread-safe patterns")
    print("  - MIGRATION_SCRIPT: Database migration code")
    print("  - INTEGRATION_NOTES: Implementation guide")
    print()
    print("See INTEGRATION_NOTES at the end of this file for implementation steps.")
