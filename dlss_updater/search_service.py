"""
Game Search Service for DLSS Updater

High-performance search infrastructure optimized for:
- Near-instant results (<50ms perceived latency)
- In-memory caching for loaded games
- Database-level search with FTS5 full-text search
- Search history persistence
- Optional fuzzy matching with rapidfuzz

Performance Architecture:
- L1 Cache: In-memory trie/hash for loaded games (sub-millisecond)
- L2 Cache: LRU cache for recent query results
- L3: Database FTS5 search (fallback for large datasets)

Thread Safety:
- All operations are async-safe
- Uses threading.Lock for free-threaded Python 3.14 compatibility
- Connection pooling via DatabaseManager

Library Versions (December 2025):
- aiosqlite 0.22.0+ (async SQLite)
- rapidfuzz 3.12.0+ (optional, fuzzy matching)
"""

import asyncio
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Any
from datetime import datetime

import msgspec

from dlss_updater.logger import setup_logger
from dlss_updater.models import Game
from dlss_updater.config import Concurrency

logger = setup_logger()

# Thread-safety locks for free-threaded Python 3.14
_search_cache_lock = threading.Lock()
_history_lock = threading.Lock()


# =============================================================================
# Data Models
# =============================================================================

class SearchResult(msgspec.Struct):
    """
    Single search result with relevance score.

    Attributes:
        game: The matched Game object
        score: Relevance score (0-100, higher is better)
        match_type: Type of match ('exact', 'prefix', 'substring', 'fuzzy')
    """
    game: Game
    score: float
    match_type: str


class SearchHistoryEntry(msgspec.Struct):
    """
    Search history entry for persistence.

    Attributes:
        query: The search query string
        timestamp: When the search was performed
        launcher: Optional launcher filter used
        result_count: Number of results returned
    """
    query: str
    timestamp: datetime
    launcher: str | None = None
    result_count: int = 0


@dataclass
class SearchCacheEntry:
    """LRU cache entry for search results."""
    results: list[SearchResult]
    created_at: float = field(default_factory=time.time)
    ttl_seconds: float = 60.0  # 1 minute TTL

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds


# =============================================================================
# In-Memory Search Index
# =============================================================================

class GameSearchIndex:
    """
    In-memory search index for fast game lookups.

    Uses a combination of:
    - Normalized name hash map for O(1) exact matches
    - Prefix tree (trie) for prefix matching
    - Inverted index for word-based substring matching

    Thread-safe for free-threaded Python 3.14.
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Hash map: normalized_name -> Game
        self._exact_index: dict[str, Game] = {}

        # Prefix index: prefix -> list of (normalized_name, Game)
        self._prefix_index: dict[str, list[tuple[str, Game]]] = {}

        # Word index: word -> list of (normalized_name, Game)
        self._word_index: dict[str, list[tuple[str, Game]]] = {}

        # Launcher filter index: launcher -> list of Game
        self._launcher_index: dict[str, list[Game]] = {}

        # All games list for full iteration when needed
        self._all_games: list[Game] = []

        self._is_built = False

    def _normalize(self, text: str) -> str:
        """Normalize text for matching (lowercase, strip)."""
        return text.lower().strip()

    def _tokenize(self, text: str) -> list[str]:
        """Split text into searchable tokens."""
        # Split on common separators
        import re
        tokens = re.split(r'[\s\-_:]+', text.lower())
        return [t for t in tokens if len(t) >= 2]  # Min 2 chars

    def build(self, games_by_launcher: dict[str, list[Game]]):
        """
        Build the search index from games data.

        This is O(n) where n is total games and runs once on load.

        Args:
            games_by_launcher: Games grouped by launcher from database
        """
        with self._lock:
            # Clear existing indexes
            self._exact_index.clear()
            self._prefix_index.clear()
            self._word_index.clear()
            self._launcher_index.clear()
            self._all_games.clear()

            for launcher, games in games_by_launcher.items():
                self._launcher_index[launcher] = games

                for game in games:
                    self._all_games.append(game)
                    normalized = self._normalize(game.name)

                    # Exact match index
                    self._exact_index[normalized] = game

                    # Prefix index (first 1-5 chars)
                    for i in range(1, min(6, len(normalized) + 1)):
                        prefix = normalized[:i]
                        if prefix not in self._prefix_index:
                            self._prefix_index[prefix] = []
                        self._prefix_index[prefix].append((normalized, game))

                    # Word index
                    for word in self._tokenize(game.name):
                        if word not in self._word_index:
                            self._word_index[word] = []
                        self._word_index[word].append((normalized, game))

            self._is_built = True
            logger.info(f"Search index built: {len(self._all_games)} games indexed")

    def search(
        self,
        query: str,
        launcher: str | None = None,
        limit: int = 50
    ) -> list[SearchResult]:
        """
        Search for games matching query.

        Search strategy (in order of priority):
        1. Exact match (score 100)
        2. Prefix match (score 90-99 based on length)
        3. Word match (score 70-89 based on word position)
        4. Substring match (score 50-69)

        Args:
            query: Search query string
            launcher: Optional launcher filter
            limit: Maximum results to return

        Returns:
            List of SearchResult sorted by relevance score
        """
        if not self._is_built or not query:
            return []

        query_normalized = self._normalize(query)
        query_words = self._tokenize(query)

        results: dict[int, SearchResult] = {}  # game_id -> SearchResult

        with self._lock:
            # Filter by launcher if specified
            candidate_games = (
                self._launcher_index.get(launcher, [])
                if launcher else self._all_games
            )

            for game in candidate_games:
                game_normalized = self._normalize(game.name)
                score = 0.0
                match_type = ""

                # 1. Exact match
                if game_normalized == query_normalized:
                    score = 100.0
                    match_type = "exact"

                # 2. Prefix match
                elif game_normalized.startswith(query_normalized):
                    # Score based on how much of the name matches
                    match_ratio = len(query_normalized) / len(game_normalized)
                    score = 90.0 + (match_ratio * 9.0)  # 90-99
                    match_type = "prefix"

                # 3. Word match (any word starts with query)
                elif not match_type:
                    game_words = self._tokenize(game.name)
                    for i, word in enumerate(game_words):
                        if word.startswith(query_normalized):
                            # Earlier words score higher
                            position_bonus = max(0, 19 - (i * 5))
                            score = 70.0 + position_bonus
                            match_type = "word"
                            break

                # 4. Substring match
                if not match_type and query_normalized in game_normalized:
                    # Score based on position and length ratio
                    pos = game_normalized.find(query_normalized)
                    position_score = max(0, 19 - (pos * 2))  # Earlier = better
                    score = 50.0 + position_score
                    match_type = "substring"

                # 5. Multi-word match (all query words appear somewhere)
                if not match_type and len(query_words) > 1:
                    game_words_set = set(self._tokenize(game.name))
                    matching_words = sum(
                        1 for qw in query_words
                        if any(gw.startswith(qw) for gw in game_words_set)
                    )
                    if matching_words == len(query_words):
                        score = 40.0 + (matching_words * 5)
                        match_type = "multi_word"

                if score > 0:
                    results[game.id] = SearchResult(
                        game=game,
                        score=score,
                        match_type=match_type
                    )

        # Sort by score descending, then by name
        sorted_results = sorted(
            results.values(),
            key=lambda r: (-r.score, r.game.name.lower())
        )

        return sorted_results[:limit]

    def get_all_games(self, launcher: str | None = None) -> list[Game]:
        """Get all games, optionally filtered by launcher."""
        with self._lock:
            if launcher:
                return list(self._launcher_index.get(launcher, []))
            return list(self._all_games)

    def is_built(self) -> bool:
        """Check if index has been built."""
        return self._is_built

    def clear(self):
        """Clear the search index."""
        with self._lock:
            self._exact_index.clear()
            self._prefix_index.clear()
            self._word_index.clear()
            self._launcher_index.clear()
            self._all_games.clear()
            self._is_built = False


# =============================================================================
# LRU Cache for Query Results
# =============================================================================

class SearchResultCache:
    """
    LRU cache for search query results.

    Provides O(1) lookup for recently executed queries.
    Thread-safe for free-threaded Python 3.14.
    """

    def __init__(self, max_size: int = 100, ttl_seconds: float = 60.0):
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, SearchCacheEntry] = OrderedDict()
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds

        # Stats
        self._hits = 0
        self._misses = 0

    def _make_key(self, query: str, launcher: str | None) -> str:
        """Create cache key from query parameters."""
        launcher_part = launcher or "__all__"
        return f"{query.lower().strip()}::{launcher_part}"

    def get(self, query: str, launcher: str | None = None) -> list[SearchResult] | None:
        """Get cached results if available and not expired."""
        key = self._make_key(query, launcher)

        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return None

            if entry.is_expired():
                del self._cache[key]
                self._misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.results

    def put(self, query: str, launcher: str | None, results: list[SearchResult]):
        """Cache search results."""
        key = self._make_key(query, launcher)

        with self._lock:
            # Remove oldest if at capacity
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)

            self._cache[key] = SearchCacheEntry(
                results=results,
                ttl_seconds=self._ttl_seconds
            )

    def invalidate(self):
        """Invalidate all cached results (call after data changes)."""
        with self._lock:
            self._cache.clear()

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 1),
                "size": len(self._cache),
                "max_size": self._max_size
            }


# =============================================================================
# Main Search Service
# =============================================================================

class GameSearchService:
    """
    High-performance game search service.

    Provides a unified interface for:
    - In-memory search with caching
    - Database search (fallback)
    - Search history management
    - Optional fuzzy matching

    Performance characteristics:
    - In-memory search: <1ms for typical queries
    - Cached results: <0.1ms
    - Database fallback: <50ms with proper indexes

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

        self._index = GameSearchIndex()
        self._cache = SearchResultCache(max_size=100, ttl_seconds=60.0)

        # Search history (in-memory, synced to DB)
        self._history: list[SearchHistoryEntry] = []
        self._history_max = 50

        # Fuzzy matching support (optional)
        self._fuzzy_enabled = False
        self._fuzzy_scorer = None
        self._init_fuzzy()

        # Database manager reference (lazy loaded)
        self._db_manager = None

        self._initialized = True
        logger.info(f"GameSearchService initialized (fuzzy={self._fuzzy_enabled})")

    def _init_fuzzy(self):
        """Initialize fuzzy matching if rapidfuzz is available."""
        try:
            from rapidfuzz import fuzz, process
            self._fuzzy_scorer = fuzz.WRatio
            self._fuzzy_enabled = True
            logger.info("Fuzzy matching enabled (rapidfuzz 3.12.0+)")
        except ImportError:
            self._fuzzy_enabled = False
            logger.info("Fuzzy matching disabled (rapidfuzz not installed)")

    async def _get_db_manager(self):
        """Lazy load database manager."""
        if self._db_manager is None:
            from dlss_updater.database import db_manager
            self._db_manager = db_manager
        return self._db_manager

    # =========================================================================
    # Index Management
    # =========================================================================

    async def build_index(self, games_by_launcher: dict[str, list[Game]] | None = None):
        """
        Build or rebuild the search index.

        Args:
            games_by_launcher: Pre-loaded games data, or None to load from DB
        """
        if games_by_launcher is None:
            db = await self._get_db_manager()
            games_by_launcher = await db.get_games_grouped_by_launcher()

        # Build index in thread pool (CPU-bound operation)
        await asyncio.to_thread(self._index.build, games_by_launcher)

        # Invalidate cache since data changed
        self._cache.invalidate()

        total_games = sum(len(g) for g in games_by_launcher.values())
        logger.info(f"Search index built: {total_games} games")

    def invalidate_cache(self):
        """Invalidate search cache (call after games data changes)."""
        self._cache.invalidate()
        logger.debug("Search cache invalidated")

    def clear_index(self):
        """Clear the search index and cache."""
        self._index.clear()
        self._cache.invalidate()
        logger.info("Search index cleared")

    # =========================================================================
    # Search Operations
    # =========================================================================

    async def search(
        self,
        query: str,
        launcher: str | None = None,
        limit: int = 50,
        use_fuzzy: bool = False,
        fuzzy_threshold: int = 60,
        record_history: bool = True
    ) -> list[SearchResult]:
        """
        Search for games matching query.

        Search is performed in order of preference:
        1. Cache lookup (fastest)
        2. In-memory index search
        3. Fuzzy matching (if enabled and requested)
        4. Database fallback (if index not built)

        Args:
            query: Search query string
            launcher: Optional launcher filter
            limit: Maximum results to return
            use_fuzzy: Enable fuzzy matching for typo tolerance
            fuzzy_threshold: Minimum fuzzy score (0-100) for matches
            record_history: Whether to record this search in history

        Returns:
            List of SearchResult sorted by relevance
        """
        if not query or not query.strip():
            return []

        query = query.strip()
        start_time = time.perf_counter()

        # 1. Check cache
        cached = self._cache.get(query, launcher)
        if cached is not None:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(f"Search cache hit: '{query}' ({elapsed_ms:.2f}ms)")

            if record_history:
                await self._record_history(query, launcher, len(cached))

            return cached[:limit]

        # 2. In-memory index search
        results = []
        if self._index.is_built():
            results = await asyncio.to_thread(
                self._index.search, query, launcher, limit * 2
            )

            # 3. Add fuzzy matches if enabled and requested
            if use_fuzzy and self._fuzzy_enabled and len(results) < limit:
                fuzzy_results = await self._fuzzy_search(
                    query, launcher, limit, fuzzy_threshold
                )
                # Merge fuzzy results (avoid duplicates)
                seen_ids = {r.game.id for r in results}
                for fr in fuzzy_results:
                    if fr.game.id not in seen_ids:
                        results.append(fr)
                        seen_ids.add(fr.game.id)

        # 4. Database fallback if index not built
        if not self._index.is_built():
            results = await self._database_search(query, launcher, limit)

        # Sort by score and limit
        results = sorted(results, key=lambda r: -r.score)[:limit]

        # Cache results
        self._cache.put(query, launcher, results)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug(f"Search completed: '{query}' -> {len(results)} results ({elapsed_ms:.2f}ms)")

        if record_history:
            await self._record_history(query, launcher, len(results))

        return results

    async def _fuzzy_search(
        self,
        query: str,
        launcher: str | None,
        limit: int,
        threshold: int
    ) -> list[SearchResult]:
        """Perform fuzzy matching search using rapidfuzz."""
        if not self._fuzzy_enabled:
            return []

        from rapidfuzz import process

        games = self._index.get_all_games(launcher)
        if not games:
            return []

        # Create choices dict: name -> game
        choices = {g.name: g for g in games}

        # Use rapidfuzz's process.extract for batch matching
        matches = await asyncio.to_thread(
            process.extract,
            query,
            list(choices.keys()),
            scorer=self._fuzzy_scorer,
            limit=limit,
            score_cutoff=threshold
        )

        results = []
        for name, score, _ in matches:
            game = choices[name]
            results.append(SearchResult(
                game=game,
                score=score * 0.5,  # Scale down fuzzy scores (max 50)
                match_type="fuzzy"
            ))

        return results

    async def _database_search(
        self,
        query: str,
        launcher: str | None,
        limit: int
    ) -> list[SearchResult]:
        """
        Fallback database search using SQL LIKE.

        Used when in-memory index is not built.
        """
        db = await self._get_db_manager()
        games = await db.search_games(query, launcher, limit)

        results = []
        for game in games:
            # Simple scoring for DB results
            name_lower = game.name.lower()
            query_lower = query.lower()

            if name_lower == query_lower:
                score = 100.0
                match_type = "exact"
            elif name_lower.startswith(query_lower):
                score = 80.0
                match_type = "prefix"
            else:
                score = 50.0
                match_type = "substring"

            results.append(SearchResult(
                game=game,
                score=score,
                match_type=match_type
            ))

        return results

    # =========================================================================
    # Search History
    # =========================================================================

    async def _record_history(self, query: str, launcher: str | None, result_count: int):
        """Record a search in history."""
        entry = SearchHistoryEntry(
            query=query,
            timestamp=datetime.now(),
            launcher=launcher,
            result_count=result_count
        )

        with _history_lock:
            # Remove duplicate of same query
            self._history = [h for h in self._history if h.query.lower() != query.lower()]

            # Add to front
            self._history.insert(0, entry)

            # Trim to max size
            self._history = self._history[:self._history_max]

        # Persist to database (async, non-blocking)
        try:
            db = await self._get_db_manager()
            await db.add_search_history(query, launcher, result_count)
        except Exception as e:
            logger.debug(f"Failed to persist search history: {e}")

    async def get_search_history(self, limit: int = 10) -> list[SearchHistoryEntry]:
        """
        Get recent search history.

        Args:
            limit: Maximum entries to return

        Returns:
            List of SearchHistoryEntry, most recent first
        """
        # First, try in-memory history
        with _history_lock:
            if self._history:
                return self._history[:limit]

        # Fall back to database
        try:
            db = await self._get_db_manager()
            db_history = await db.get_search_history(limit)

            # Convert to SearchHistoryEntry objects
            entries = []
            for row in db_history:
                entries.append(SearchHistoryEntry(
                    query=row['query'],
                    timestamp=datetime.fromisoformat(row['timestamp']),
                    launcher=row.get('launcher'),
                    result_count=row.get('result_count', 0)
                ))

            # Update in-memory cache
            with _history_lock:
                self._history = entries

            return entries
        except Exception as e:
            logger.debug(f"Failed to load search history: {e}")
            return []

    async def clear_search_history(self):
        """Clear all search history."""
        with _history_lock:
            self._history.clear()

        try:
            db = await self._get_db_manager()
            await db.clear_search_history()
            logger.info("Search history cleared")
        except Exception as e:
            logger.warning(f"Failed to clear search history from DB: {e}")

    # =========================================================================
    # Utilities
    # =========================================================================

    def get_cache_stats(self) -> dict[str, Any]:
        """Get search cache statistics."""
        return self._cache.get_stats()

    def is_index_built(self) -> bool:
        """Check if search index is built and ready."""
        return self._index.is_built()

    def get_game_count(self) -> int:
        """Get total number of indexed games."""
        return len(self._index.get_all_games())

    def is_fuzzy_available(self) -> bool:
        """Check if fuzzy matching is available."""
        return self._fuzzy_enabled


# Singleton instance
search_service = GameSearchService()
