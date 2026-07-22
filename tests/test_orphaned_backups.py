"""
Tests for orphaned-backup surfacing (data layer).

Reproduces the production condition where a game is removed/rescanned without
the ON DELETE CASCADE firing, leaving an active ``dll_backups`` row with a
dangling ``game_dll_id``. Such a backup is counted by the global stats query
but dropped by the per-game INNER-JOIN view — invisible and unmanageable.

Verifies:
  * ``get_orphaned_backups_grouped_sync`` surfaces the orphan (grouped, with a
    recovered label + dll filename/type), and EXCLUDES linked backups.
  * ``get_backups_grouped_by_game_sync`` still returns only linked backups.
  * stats (raw) == linked + orphaned  (drift removed; every counted backup is
    reachable), asserted directly and via ``get_backup_overview_sync``.
  * ``_get_backup_by_id`` now resolves an orphan by id (so delete_backup works).
"""

import sqlite3
import threading

import pytest

from dlss_updater.database import db_manager


LINKED_BACKUP_PATH = (
    r"D:\SteamLibrary\steamapps\common\BaldursGate3\bin\nvngx_dlss.dlss"
)
ORPHAN_BACKUP_PATH = (
    r"D:\SteamLibrary\steamapps\common\MarvelRivals\MarvelGame\Engine"
    r"\Plugins\Marketplace\Streamline\Binaries\ThirdParty\Win64\sl.reflex.dlsss"
)
LINKED_SIZE = 12_000_000
ORPHAN_SIZE = 199_136


def _seed(db_path) -> None:
    """Seed a linked backup and an orphaned backup.

    The orphan is created exactly as it arises in production: with
    ``foreign_keys=OFF`` so a ``dll_backups`` row can reference a
    non-existent ``game_dlls`` id (dangling FK), mirroring a delete that
    ran without CASCADE enforcement.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    cur = conn.cursor()

    # Linked game -> game_dll -> backup (fully joinable).
    cur.execute(
        "INSERT INTO games (name, path, launcher) VALUES (?, ?, ?)",
        ("Baldurs Gate 3", r"D:\SteamLibrary\steamapps\common\BaldursGate3", "Steam"),
    )
    game_id = cur.lastrowid
    cur.execute(
        "INSERT INTO game_dlls (game_id, dll_type, dll_filename, dll_path) "
        "VALUES (?, ?, ?, ?)",
        (game_id, "DLSS DLL", "nvngx_dlss.dll",
         r"D:\SteamLibrary\steamapps\common\BaldursGate3\bin\nvngx_dlss.dll"),
    )
    linked_dll_id = cur.lastrowid
    cur.execute(
        "INSERT INTO dll_backups (game_dll_id, backup_path, original_version, "
        "backup_size, is_active) VALUES (?, ?, ?, ?, 1)",
        (linked_dll_id, LINKED_BACKUP_PATH, "3.7.0", LINKED_SIZE),
    )

    # Orphaned backup: dangling game_dll_id (no such game_dlls row).
    cur.execute(
        "INSERT INTO dll_backups (game_dll_id, backup_path, original_version, "
        "backup_size, is_active) VALUES (?, ?, ?, ?, 1)",
        (99999, ORPHAN_BACKUP_PATH, "2.5.1", ORPHAN_SIZE),
    )

    conn.commit()
    conn.close()


@pytest.fixture()
def temp_db(tmp_path):
    """Repoint the db_manager singleton at a fresh temp DB, then restore it."""
    db_path = tmp_path / "games.db"

    orig_path = db_manager.db_path
    orig_local = db_manager._thread_local

    db_manager.db_path = db_path
    db_manager._thread_local = threading.local()  # force reconnect to temp DB

    db_manager._create_schema()
    _seed(db_path)

    try:
        yield db_path
    finally:
        # Drop the temp thread-local connection and restore the real singleton.
        try:
            db_manager._close_thread_connection()
        except Exception:
            pass
        db_manager.db_path = orig_path
        db_manager._thread_local = orig_local


def test_orphaned_surfaced_and_isolated(temp_db):
    orphaned = db_manager.get_orphaned_backups_grouped_sync()

    # Exactly one orphan group, with the single orphan backup.
    assert len(orphaned) == 1
    (gid, backups), = orphaned.items()
    assert gid < 0, "orphan group id must be a synthetic NEGATIVE id"
    assert len(backups) == 1

    b = backups[0]
    assert b.backup_path == ORPHAN_BACKUP_PATH
    assert b.game_id == gid, "backup.game_id must equal its group key"
    # Label recovered + prettified from the Steam path.
    assert b.game_name == "Marvel Rivals"
    assert backups[0].game_name == b.game_name  # view uses backups[0].game_name
    # Original filename recovered from backup_path (.dlsss -> .dll), classified.
    assert b.dll_filename == "sl.reflex.dll"
    assert b.dll_type == "Streamline Reflex Low-Latency DLL"
    assert b.original_version == "2.5.1"
    assert b.backup_size == ORPHAN_SIZE


def test_linked_view_excludes_orphan(temp_db):
    linked = db_manager.get_backups_grouped_by_game_sync()

    all_paths = [bk.backup_path for grp in linked.values() for bk in grp]
    assert LINKED_BACKUP_PATH in all_paths
    assert ORPHAN_BACKUP_PATH not in all_paths, "linked view must NOT show orphans"
    # All linked group ids are real (positive) game ids.
    assert all(gid > 0 for gid in linked)


def test_stats_reconcile_no_drift(temp_db):
    total_count, total_bytes = db_manager.get_backup_summary_stats_sync()
    assert total_count == 2
    assert total_bytes == LINKED_SIZE + ORPHAN_SIZE

    linked = db_manager.get_backups_grouped_by_game_sync()
    orphaned = db_manager.get_orphaned_backups_grouped_sync()

    def count(groups):
        return sum(len(v) for v in groups.values())

    def size(groups):
        return sum(bk.backup_size for grp in groups.values() for bk in grp)

    # The header total must equal linked + orphaned (drift removed).
    assert count(linked) + count(orphaned) == total_count
    assert size(linked) + size(orphaned) == total_bytes


def test_overview_is_consistent(temp_db):
    from dlss_updater.services import backup_service as bs

    ov = bs.get_backup_overview_sync()
    assert ov.total_count == 2
    assert ov.total_bytes == LINKED_SIZE + ORPHAN_SIZE
    assert ov.displayed_count == ov.total_count
    assert ov.displayed_bytes == ov.total_bytes
    assert ov.is_consistent()
    assert len(ov.linked) == 1
    assert len(ov.orphaned) == 1


def test_get_backup_by_id_resolves_orphan(temp_db):
    """delete_backup / restore fetch the row via get_backup_by_id first;
    it must resolve orphans (LEFT JOIN) or delete is impossible."""
    orphaned = db_manager.get_orphaned_backups_grouped_sync()
    (_, backups), = orphaned.items()
    orphan_id = backups[0].id

    row = db_manager._get_backup_by_id(orphan_id)
    assert row is not None, "orphan must be resolvable by id (delete depends on it)"
    assert row.backup_path == ORPHAN_BACKUP_PATH
    assert row.game_name == "Marvel Rivals"      # recovered from path
    assert row.dll_filename == "sl.reflex.dll"   # recovered from path


def test_service_wrapper_delegates(temp_db):
    from dlss_updater.services import backup_service as bs

    assert bs.get_orphaned_backups_grouped_sync() == \
        db_manager.get_orphaned_backups_grouped_sync()
