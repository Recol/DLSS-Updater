"""Tests for the UTC->local boundary conversion on stored timestamps.

Every timestamp column in the schema is written by SQLite's
``CURRENT_TIMESTAMP`` - named explicitly in the statement, or supplied by the
column ``DEFAULT``. Nothing binds a Python datetime, so every stored value is
UTC while reading back as a naive datetime that looks local. Parsing those
straight into the models handed the application UTC values it then compared
against a local ``datetime.now()``, adding the local UTC offset to every age
and displayed time: the Games header could never read fresher than "scanned 1h
ago" in BST, and west of Greenwich the difference went negative.

``database.db_timestamp()`` is the single boundary that fixes it, so these
pin both the helper and the round trip through a real database.

The assertions hold in any machine timezone by construction - none of them
hard-code an offset.
"""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta, timezone

import pytest

from dlss_updater.database import db_manager, db_timestamp


@pytest.fixture()
def temp_db(tmp_path):
    """Repoint the db_manager singleton at a fresh temp DB, then restore it."""
    db_path = tmp_path / "games.db"

    orig_path = db_manager.db_path
    orig_local = db_manager._thread_local

    db_manager.db_path = db_path
    db_manager._thread_local = threading.local()  # force reconnect to temp DB
    db_manager._create_schema()

    try:
        yield db_path
    finally:
        try:
            db_manager._close_thread_connection()
        except Exception:
            pass
        db_manager.db_path = orig_path
        db_manager._thread_local = orig_local


# =============================================================================
# The helper
# =============================================================================


def test_a_stored_string_is_read_as_local_time():
    """The exact bug: a UTC string parsed as if it were already local."""
    stored = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    converted = db_timestamp(stored.strftime("%Y-%m-%d %H:%M:%S"))

    drift = abs((datetime.now() - converted).total_seconds())
    assert drift < 5, f"a timestamp written now should read as now, not {converted}"


def test_the_shift_equals_the_local_utc_offset():
    stored = datetime(2026, 1, 15, 12, 0, 0)          # noon UTC, as SQLite wrote it
    # The offset that applies on that date, not today's - the two differ either
    # side of a DST boundary.
    offset = stored.replace(tzinfo=UTC).astimezone().utcoffset()

    assert db_timestamp("2026-01-15 12:00:00") == stored + offset


def test_iso_and_space_separated_forms_both_parse():
    assert db_timestamp("2026-01-15 12:00:00") == db_timestamp("2026-01-15T12:00:00")


def test_a_datetime_passes_through_the_same_conversion():
    """sqlite3 converters can hand back a datetime rather than a string."""
    naive = datetime(2026, 1, 15, 12, 0, 0)
    assert db_timestamp(naive) == db_timestamp("2026-01-15 12:00:00")


def test_an_aware_value_is_trusted_rather_than_assumed_utc():
    aware = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    assert db_timestamp(aware) == aware.astimezone().replace(tzinfo=None)


def test_the_result_is_naive():
    """Models and every consumer compare against a naive datetime.now()."""
    assert db_timestamp("2026-01-15 12:00:00").tzinfo is None


def test_a_corrupt_value_still_raises():
    """Previous behaviour: an unparseable row is a bug, not a silent default."""
    with pytest.raises(ValueError):
        db_timestamp("not-a-timestamp")


# =============================================================================
# Round trip through a real database
# =============================================================================


def _only_game(db_path):
    """The single seeded game, read back through the normal row->model path."""
    by_launcher = db_manager._get_all_games_by_launcher()
    games = [g for gs in by_launcher.values() for g in gs]
    assert len(games) == 1
    return games[0]


def _insert_game(db_path, name="Baldurs Gate 3", path=r"D:\Games\BG3") -> int:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO games (name, path, launcher) VALUES (?, ?, ?)",
        (name, path, "Steam"),
    )
    game_id = cur.lastrowid
    conn.commit()
    conn.close()
    return game_id


def test_a_game_written_now_reads_back_as_now(temp_db):
    """last_scanned comes from the column DEFAULT here - still UTC, still converted."""
    _insert_game(temp_db)

    game = _only_game(temp_db)

    drift = abs((datetime.now() - game.last_scanned).total_seconds())
    assert drift < 60, (
        f"last_scanned read back as {game.last_scanned}, "
        f"local now is {datetime.now()} - the UTC offset is leaking through"
    )
    assert abs((datetime.now() - game.created_at).total_seconds()) < 60


# =============================================================================
# Refresh counts as a scan
# =============================================================================


@pytest.mark.anyio
async def test_mark_games_scanned_stamps_last_scanned(temp_db):
    """The refresh button re-reads every DLL from disk, so it is a scan."""
    game_id = _insert_game(temp_db)

    # Backdate the row to three days ago, as a library scanned last week has.
    conn = sqlite3.connect(str(temp_db))
    old = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE games SET last_scanned = ? WHERE id = ?", (old, game_id))
    conn.commit()
    conn.close()

    before = _only_game(temp_db).last_scanned
    assert (datetime.now() - before).days == 3

    stamped = await db_manager.mark_games_scanned([game_id])
    assert stamped == 1

    after = _only_game(temp_db).last_scanned
    assert abs((datetime.now() - after).total_seconds()) < 60, "should read as just now"


@pytest.mark.anyio
async def test_mark_games_scanned_with_nothing_to_stamp(temp_db):
    """An empty library must not build an `IN ()` clause."""
    assert await db_manager.mark_games_scanned([]) == 0
