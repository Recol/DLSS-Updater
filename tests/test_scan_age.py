"""Tests for the Games header's "scanned Xd ago" label.

Two things kept this label wrong, and they are independent:

* ``games.last_scanned`` is UTC (SQLite's ``CURRENT_TIMESTAMP``) and was
  compared against a local ``datetime.now()``, so every age was inflated by the
  local UTC offset. That is fixed at the database boundary now
  (``database.db_timestamp`` - see test_db_timestamps.py), which is why the
  fixtures here are LOCAL: by the time a Game reaches the view, it has been
  converted.
* Only the scan upserts wrote ``last_scanned``, so pressing refresh - which
  re-reads every known DLL from disk - could never move the counter. The
  refresh path now stamps it, and tells MainView so the Hub's copy of the same
  label (read from scan_cache.json, not the database) does not fall behind.

Driven with plain stand-ins rather than Flet-backed controls, the same approach
as test_ignored_games.py.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from dlss_updater.ui_flet.views import games_view as games_view_module
from dlss_updater.ui_flet.views.games_view import GamesView


def _db_now(**delta) -> datetime:
    """A Game.last_scanned as the view receives it: naive LOCAL time."""
    return datetime.now().replace(microsecond=0) - timedelta(**delta)


def _view(*timestamps) -> Any:
    games = [SimpleNamespace(last_scanned=ts) for ts in timestamps]
    return SimpleNamespace(games_by_launcher={"Steam": games})


def test_a_scan_that_just_finished_reads_as_minutes_not_hours():
    """The regression: in any timezone east of Greenwich this read "scanned Nh ago"."""
    assert GamesView._scan_age_str(_view(_db_now(seconds=5))) == "scanned 0m ago"


def test_a_utc_timestamp_would_still_be_caught_here():
    """Guard the boundary conversion from being removed further upstream.

    If a Game ever reaches the view carrying a raw UTC value again, this is the
    label that goes wrong first - so assert on what the view is handed rather
    than trusting the layer below.
    """
    from datetime import UTC

    raw_utc = datetime.now(UTC).replace(tzinfo=None)
    rendered = GamesView._scan_age_str(_view(raw_utc))
    offset = datetime.now().astimezone().utcoffset()

    if offset:      # a machine running in UTC cannot observe the difference
        assert rendered != "scanned 0m ago", (
            "an unconverted UTC timestamp must not read as a fresh scan - "
            "database.db_timestamp() is missing from the read path"
        )


def test_ages_are_reported_in_the_largest_fitting_unit():
    assert GamesView._scan_age_str(_view(_db_now(minutes=42))) == "scanned 42m ago"
    assert GamesView._scan_age_str(_view(_db_now(hours=5))) == "scanned 5h ago"
    assert GamesView._scan_age_str(_view(_db_now(days=3))) == "scanned 3d ago"


def test_a_future_timestamp_never_renders_a_negative_count():
    """A clock adjustment between the scan and now must not print "-300m ago"."""
    future = datetime.now() + timedelta(hours=6)
    assert GamesView._scan_age_str(_view(future)) == "scanned 0m ago"


def test_the_most_recent_scan_across_launchers_wins():
    view = SimpleNamespace(
        games_by_launcher={
            "Steam": [SimpleNamespace(last_scanned=_db_now(days=9))],
            "Epic": [SimpleNamespace(last_scanned=_db_now(minutes=3))],
        }
    )
    assert GamesView._scan_age_str(view) == "scanned 3m ago"


def test_no_usable_timestamps_yields_no_label():
    assert GamesView._scan_age_str(SimpleNamespace(games_by_launcher={})) is None
    assert GamesView._scan_age_str(_view(None)) is None


# =============================================================================
# Refresh counts as a scan
# =============================================================================


def _refresh_stub(rescanned_calls: list, **overrides) -> Any:
    """A GamesView stand-in carrying only what _on_refresh_clicked touches."""
    calls = []

    async def _noop(*args, **kwargs):
        calls.append("badges")

    async def _load(force=False):
        calls.append(f"load(force={force})")

    async def _rescanned():
        rescanned_calls.append(True)

    stub = SimpleNamespace(
        refresh_button_ref=SimpleNamespace(current=None),
        game_cards={7: object(), 9: object()},
        refresh_all_badges=_noop,
        load_games=_load,
        _on_rescanned=_rescanned,
        logger=SimpleNamespace(warning=lambda *a, **k: None),
        update=lambda: None,
        calls=calls,
    )
    stub.__dict__.update(overrides)
    return stub


@pytest.mark.anyio
async def test_refresh_stamps_last_scanned_before_reloading(monkeypatch):
    """Order matters: stamp, then reload, or the reload shows the old timestamps."""
    order = []
    stamped = {}

    async def fake_mark(game_ids):
        stamped["ids"] = game_ids
        order.append("stamp")
        return len(game_ids)

    monkeypatch.setattr(
        games_view_module.db_manager, "mark_games_scanned", fake_mark, raising=False
    )

    rescanned = []
    stub = _refresh_stub(rescanned)

    async def _load(force=False):
        order.append(f"load(force={force})")

    stub.load_games = _load

    await GamesView._on_refresh_clicked(stub, None)

    assert stamped["ids"] == [7, 9]
    assert order == ["stamp", "load(force=True)"]
    assert rescanned == [True], "MainView must be told, or the Hub label falls behind"


@pytest.mark.anyio
async def test_refresh_survives_a_failed_stamp(monkeypatch):
    """A refresh that cannot write the timestamp must still refresh the view."""
    async def boom(game_ids):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(
        games_view_module.db_manager, "mark_games_scanned", boom, raising=False
    )

    rescanned = []
    stub = _refresh_stub(rescanned)
    await GamesView._on_refresh_clicked(stub, None)

    assert "load(force=True)" in stub.calls


@pytest.mark.anyio
async def test_refresh_with_an_empty_library_stamps_nothing(monkeypatch):
    called = []

    async def fake_mark(game_ids):
        called.append(game_ids)
        return 0

    monkeypatch.setattr(
        games_view_module.db_manager, "mark_games_scanned", fake_mark, raising=False
    )

    stub = _refresh_stub([], game_cards={})
    await GamesView._on_refresh_clicked(stub, None)

    assert called == [], "no games means no UPDATE ... IN () to issue"
