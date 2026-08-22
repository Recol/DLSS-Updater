"""
Tests for DLL records whose file has vanished (issue #281).

When a game patch relocates its DLLs, the old ``game_dlls`` rows must stop
being update targets. Post-scan cleanup used to delete such rows outright --
except that it skipped every row still holding an active backup, to protect
that backup from the ON DELETE CASCADE. The consequence was that any DLL the
user had ever successfully updated stayed pinned in the DB at a dead path and
was retried on every subsequent run, surfacing as an "Error" row.

The fix separates the two concerns: a backup-protected row whose file is gone
is MARKED (``missing_at``) rather than deleted, and marked rows are excluded
from the DLL enumeration the updater and the game cards read. The backup stays
reachable, the dead path stops being retried, and re-discovery clears the mark.

Verifies:
  * backup-protected orphan -> marked, NOT deleted; its backup survives
  * marked rows are invisible to get_dlls_for_game / batch_get_dlls_for_games_sync
  * unprotected orphan -> still deleted (no behaviour regression)
  * a DLL that still exists on disk is neither marked nor deleted
  * re-discovery (upsert) clears the mark
  * update_dll / update_dll_with_backup skip a vanished target instead of raising
"""

import sqlite3
import threading

import pytest

from dlss_updater.database import db_manager

GONE_DIR = (
    r"H:\SteamLibrary\steamapps\common\Mafia The Old Country"
    r"\Plugins\Nvidia-DLSS\4.8.1.0\StreamlineCore"
)
PROTECTED_DLL_PATH = GONE_DIR + r"\sl.common.dll"
PROTECTED_BACKUP_PATH = GONE_DIR + r"\sl.common.dlsss"
UNPROTECTED_DLL_PATH = GONE_DIR + r"\nvngx_dlssg.dll"


def _seed(db_path, live_dll_path: str) -> int:
    """Seed one game with three DLLs:

    * a vanished path that holds an ACTIVE backup (must be marked, not deleted)
    * a vanished path with no backup            (must still be deleted)
    * a path that really exists on disk         (must be left alone)
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO games (name, path, launcher) VALUES (?, ?, ?)",
        ("Mafia The Old Country",
         r"H:\SteamLibrary\steamapps\common\Mafia The Old Country", "Steam"),
    )
    game_id = cur.lastrowid

    cur.execute(
        "INSERT INTO game_dlls (game_id, dll_type, dll_filename, dll_path) "
        "VALUES (?, ?, ?, ?)",
        (game_id, "Streamline Shared Library DLL", "sl.common.dll", PROTECTED_DLL_PATH),
    )
    protected_id = cur.lastrowid
    cur.execute(
        "INSERT INTO dll_backups (game_dll_id, backup_path, original_version, "
        "backup_size, is_active) VALUES (?, ?, ?, ?, 1)",
        (protected_id, PROTECTED_BACKUP_PATH, "2.7.1", 1_234_567),
    )

    cur.execute(
        "INSERT INTO game_dlls (game_id, dll_type, dll_filename, dll_path) "
        "VALUES (?, ?, ?, ?)",
        (game_id, "DLSS Frame Generation DLL", "nvngx_dlssg.dll", UNPROTECTED_DLL_PATH),
    )

    cur.execute(
        "INSERT INTO game_dlls (game_id, dll_type, dll_filename, dll_path) "
        "VALUES (?, ?, ?, ?)",
        (game_id, "DLSS DLL", "nvngx_dlss.dll", live_dll_path),
    )

    conn.commit()
    conn.close()
    return game_id


@pytest.fixture()
def seeded(tmp_path):
    """Repoint the db_manager singleton at a fresh temp DB, then restore it."""
    db_path = tmp_path / "games.db"
    live_dll = tmp_path / "nvngx_dlss.dll"
    live_dll.write_bytes(b"MZ")  # a DLL that genuinely exists

    orig_path = db_manager.db_path
    orig_local = db_manager._thread_local

    db_manager.db_path = db_path
    db_manager._thread_local = threading.local()  # force reconnect to temp DB

    db_manager._create_schema()
    game_id = _seed(db_path, str(live_dll))

    try:
        yield game_id, str(live_dll)
    finally:
        try:
            db_manager._close_thread_connection()
        except Exception:
            pass
        db_manager.db_path = orig_path
        db_manager._thread_local = orig_local


def _rows():
    """Return {dll_path: missing_at} straight from the DB."""
    conn = sqlite3.connect(str(db_manager.db_path))
    try:
        return {
            r[0]: r[1]
            for r in conn.execute("SELECT dll_path, missing_at FROM game_dlls")
        }
    finally:
        conn.close()


def _active_backup_count():
    conn = sqlite3.connect(str(db_manager.db_path))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM dll_backups WHERE is_active = 1"
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------- cleanup ---

def test_backup_protected_orphan_is_marked_not_deleted(seeded):
    _game_id, live_dll = seeded

    db_manager._cleanup_orphan_dlls({live_dll})

    rows = _rows()
    assert PROTECTED_DLL_PATH in rows, "backup-protected row must survive the cleanup"
    assert rows[PROTECTED_DLL_PATH] is not None, "vanished path must be marked missing"
    assert _active_backup_count() == 1, "the protected backup must not be cascaded away"


def test_unprotected_orphan_is_still_deleted(seeded):
    _game_id, live_dll = seeded

    db_manager._cleanup_orphan_dlls({live_dll})

    assert UNPROTECTED_DLL_PATH not in _rows(), (
        "a vanished path with no backup must still be pruned outright"
    )


def test_live_dll_is_left_alone(seeded):
    _game_id, live_dll = seeded

    db_manager._cleanup_orphan_dlls(set())  # not even in the scan set

    rows = _rows()
    assert live_dll in rows, "a DLL that exists on disk must never be pruned"
    assert rows[live_dll] is None, "a DLL that exists on disk must never be marked"


def test_cleanup_counts_only_deletions_as_orphans(seeded):
    _game_id, live_dll = seeded

    deleted = db_manager._cleanup_orphan_dlls({live_dll})

    assert deleted == 1, "only the unprotected orphan counts as deleted"


# ------------------------------------------------------------ enumeration ---

def test_marked_dll_is_excluded_from_update_enumeration(seeded):
    game_id, live_dll = seeded

    db_manager._cleanup_orphan_dlls({live_dll})

    paths = {d.dll_path for d in db_manager._get_dlls_for_game(game_id)}
    assert PROTECTED_DLL_PATH not in paths, (
        "a missing DLL must not be handed to the updater"
    )
    assert live_dll in paths

    batched = db_manager.batch_get_dlls_for_games_sync([game_id])
    batch_paths = {d.dll_path for d in batched[game_id]}
    assert PROTECTED_DLL_PATH not in batch_paths
    assert live_dll in batch_paths


# ----------------------------------------------------------------- healing ---

def test_rediscovery_clears_the_missing_mark(seeded):
    game_id, live_dll = seeded

    db_manager._cleanup_orphan_dlls({live_dll})
    assert _rows()[PROTECTED_DLL_PATH] is not None

    # The scanner finds the path again (drive back online / patch reverted).
    db_manager._batch_upsert_dlls([{
        'game_id': game_id,
        'dll_type': "Streamline Shared Library DLL",
        'dll_filename': "sl.common.dll",
        'dll_path': PROTECTED_DLL_PATH,
        'current_version': "2.7.1",
    }])

    assert _rows()[PROTECTED_DLL_PATH] is None, "re-discovery must clear missing_at"
    paths = {d.dll_path for d in db_manager._get_dlls_for_game(game_id)}
    assert PROTECTED_DLL_PATH in paths, "a healed DLL is an update target again"


def test_single_upsert_also_clears_the_missing_mark(seeded):
    game_id, live_dll = seeded

    db_manager._cleanup_orphan_dlls({live_dll})
    assert _rows()[PROTECTED_DLL_PATH] is not None

    db_manager._upsert_game_dll({
        'game_id': game_id,
        'dll_type': "Streamline Shared Library DLL",
        'dll_filename': "sl.common.dll",
        'dll_path': PROTECTED_DLL_PATH,
        'current_version': "2.7.1",
    })

    assert _rows()[PROTECTED_DLL_PATH] is None


# ----------------------------------------------------------- migration ------

def test_missing_at_is_migrated_onto_a_pre_existing_database(tmp_path):
    """Every existing install arrives here, not through CREATE TABLE."""
    db_path = tmp_path / "games.db"

    orig_path = db_manager.db_path
    orig_local = db_manager._thread_local
    db_manager.db_path = db_path
    db_manager._thread_local = threading.local()
    try:
        # Build the real, current schema, then take the new column back out to
        # reproduce a pre-4.7.1 database faithfully (rather than hand-rolling a
        # historic schema that the other migrations would then trip over).
        db_manager._create_schema()
        db_manager._close_thread_connection()

        conn = sqlite3.connect(str(db_path))
        conn.execute("ALTER TABLE game_dlls DROP COLUMN missing_at")
        conn.execute(
            "INSERT INTO games (name, path, launcher) VALUES ('G', 'C:\\g', 'Steam')"
        )
        conn.execute(
            "INSERT INTO game_dlls (game_id, dll_type, dll_filename, dll_path) "
            "VALUES (1, 'DLSS DLL', 'nvngx_dlss.dll', ?)",
            (PROTECTED_DLL_PATH,),
        )
        conn.commit()
        assert "missing_at" not in {
            r[1] for r in conn.execute("PRAGMA table_info(game_dlls)")
        }
        conn.close()

        db_manager._thread_local = threading.local()
        db_manager._create_schema()

        cols = {
            r[1]
            for r in sqlite3.connect(str(db_path)).execute(
                "PRAGMA table_info(game_dlls)"
            )
        }
        assert "missing_at" in cols, "migration must add the column to old DBs"

        # And the pre-existing row defaults to "not missing".
        assert _rows()[PROTECTED_DLL_PATH] is None
    finally:
        try:
            db_manager._close_thread_connection()
        except Exception:
            pass
        db_manager.db_path = orig_path
        db_manager._thread_local = orig_local


# ------------------------------------------------------------- updater ------

def test_update_dll_skips_vanished_target_without_raising(tmp_path):
    """The guard for this already existed but sat below an os.stat() that
    raised WinError 2/3 first, turning a clean skip into a logged 'Error'."""
    from dlss_updater.updater import update_dll

    gone = tmp_path / "gone" / "sl.common.dll"
    latest = tmp_path / "cache" / "sl.common.dll"

    result = update_dll(str(gone), str(latest))

    assert result.success is False
    assert result.skip_reason is not None
    assert "no longer exists" in result.skip_reason.lower()


def test_update_dll_with_backup_skips_vanished_target_without_raising(tmp_path):
    from dlss_updater.updater import update_dll_with_backup

    gone = tmp_path / "gone" / "sl.common.dll"
    latest = tmp_path / "cache" / "sl.common.dll"

    result = update_dll_with_backup(str(gone), str(latest))

    assert result.success is False
    assert result.skip_reason is not None
    assert "no longer exists" in result.skip_reason.lower()
