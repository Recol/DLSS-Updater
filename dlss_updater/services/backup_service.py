"""
Backup service — thin data-access wrappers for the Backups view.

UI code imports these functions instead of touching ``db_manager`` /
``backup_manager`` directly (CLAUDE.md service-layer rule). Every wrapper is a
one-line delegate; all real work stays in the managers. The ``*_sync`` wrappers
are safe to drive from a ThreadPoolExecutor / ``HyperParallelLoader`` LoadTask
(they call the thread-local sync database methods).
"""

import msgspec

from dlss_updater.database import db_manager, DLLBackup
from dlss_updater.models import GameDLLBackup
from dlss_updater.backup_manager import (
    restore_dll_from_backup,
    restore_group_for_game,
    delete_backup,
)


# ===== Grouped-backup reads (sync, for HyperParallelLoader LoadTask) =====

def get_backups_grouped_by_game_sync(
    game_id: int | None = None,
) -> dict[int, list[GameDLLBackup]]:
    """Active backups grouped by game id for the per-game view (LINKED games).

    Returns only backups whose game still exists in the library (INNER JOIN).
    Pair with :func:`get_orphaned_backups_grouped_sync` for full coverage.
    """
    return db_manager.get_backups_grouped_by_game_sync(game_id)


def get_orphaned_backups_grouped_sync() -> dict[int, list[GameDLLBackup]]:
    """Active backups whose owning game is gone, grouped for the "Unlinked
    games" section.

    Complement of :func:`get_backups_grouped_by_game_sync`. Keyed by a synthetic
    NEGATIVE game id (stable per derived label, never colliding with real game
    ids); each ``GameDLLBackup`` carries a display-ready ``game_name`` recovered
    from ``backup_path`` and its real ``id`` / ``backup_path`` so the existing
    restore/delete callbacks work unchanged. Drop-in shape for ``BackupGroup``.
    """
    return db_manager.get_orphaned_backups_grouped_sync()


def batch_get_backups_grouped_sync(
    game_ids: list[int],
) -> dict[int, dict[str, list[DLLBackup]]]:
    """Backups grouped by DLL type for many games in one query."""
    return db_manager.batch_get_backups_grouped_sync(game_ids)


def get_backup_summary_stats_sync() -> tuple[int, int]:
    """Global backup stats over ALL active backups: ``(count, total_bytes)``.

    Reads raw ``dll_backups`` (linked AND orphaned), so this is the header total
    that must reconcile with linked + orphaned groups combined.
    """
    return db_manager.get_backup_summary_stats_sync()


async def get_backups_grouped_by_dll_type(
    game_id: int,
) -> dict[str, list[DLLBackup]]:
    """Backups for one game grouped by DLL type (async)."""
    return await db_manager.get_backups_grouped_by_dll_type(game_id)


# ===== Consistent overview (removes header/groups drift) =====

class BackupOverview(msgspec.Struct):
    """Consistent snapshot tying the header total to the displayed groups.

    ``total_count`` / ``total_bytes`` come from the same raw-``dll_backups``
    query the header tiles use. ``linked`` and ``orphaned`` are the two grouped
    partitions the view renders. By construction their combined count/bytes
    equal the totals (``displayed_count`` / ``displayed_bytes``), so a header
    that shows ``total_*`` can never disagree with the sum of what is on screen.
    Use :meth:`is_consistent` to assert the invariant.
    """
    total_count: int
    total_bytes: int
    linked: dict[int, list[GameDLLBackup]]
    orphaned: dict[int, list[GameDLLBackup]]

    @property
    def displayed_count(self) -> int:
        return sum(len(v) for v in self.linked.values()) + sum(
            len(v) for v in self.orphaned.values()
        )

    @property
    def displayed_bytes(self) -> int:
        total = 0
        for group in self.linked.values():
            total += sum(b.backup_size for b in group)
        for group in self.orphaned.values():
            total += sum(b.backup_size for b in group)
        return total

    def is_consistent(self) -> bool:
        """True when the header totals match the sum of all displayed groups."""
        return (
            self.displayed_count == self.total_count
            and self.displayed_bytes == self.total_bytes
        )


def get_backup_overview_sync() -> BackupOverview:
    """One call returning header totals + both grouped partitions, consistently.

    SYNC; safe for a ``HyperParallelLoader`` LoadTask. Lets a caller render the
    header total and the on-screen groups from a single reconciled snapshot
    (``overview.is_consistent()`` holds whenever no rows change mid-read).
    """
    total_count, total_bytes = db_manager.get_backup_summary_stats_sync()
    linked = db_manager.get_backups_grouped_by_game_sync()
    orphaned = db_manager.get_orphaned_backups_grouped_sync()
    return BackupOverview(
        total_count=total_count,
        total_bytes=total_bytes,
        linked=linked,
        orphaned=orphaned,
    )


__all__ = [
    "get_backups_grouped_by_game_sync",
    "get_orphaned_backups_grouped_sync",
    "batch_get_backups_grouped_sync",
    "get_backup_summary_stats_sync",
    "get_backups_grouped_by_dll_type",
    "get_backup_overview_sync",
    "BackupOverview",
    "restore_dll_from_backup",
    "restore_group_for_game",
    "delete_backup",
]
