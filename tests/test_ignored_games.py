"""Tests for how the personal ignore list interacts with counting and filtering.

Ignoring a game means "don't touch this one". AsyncUpdateCoordinator already
honours that — ``_filter_ignored_games()`` drops ignored games' DLLs from the
run — so any headline count that includes them promises more than the button
delivers ("Update all (11)" running on 8). These tests pin the two count paths
and the visibility pass that kept disagreeing with it (issue #299):

* ``count_card_statuses`` — the Games view's chips / subtitle / CTA.
* ``count_merged_games_needing_update`` — the hub pill/CTA and the app-bar
  pill, which share HubView._count_games_needing_update.
* ``GamesView._apply_visibility`` / ``_refresh_filters_and_counts`` — cards are
  born ``visible=True``, so every path that CREATES cards has to re-apply the
  active filters or a rebuild silently un-hides ignored games.

All of it is driven with plain stand-ins rather than Flet-backed controls, the
same approach as test_bulk_selection.py and test_grid_density.py. The stand-in
factories are annotated ``-> Any`` because they are duck types handed to
methods that declare GameCard / GamesView / GridView parameters — the fakes
carry only the handful of fields each method actually touches, which is the
point.
"""

from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from dlss_updater.models import GameDLL, UIPreferencesConfig
from dlss_updater.ui_flet.views import games_view as games_view_module
from dlss_updater.ui_flet.views.games_view import (
    GamesView,
    count_card_statuses,
    count_merged_games_needing_update,
)


def _card(
    name: str = "Game",
    *,
    outdated: bool = False,
    backups: bool = False,
    ignored: bool = False,
    launcher: str = "Steam",
    game_id: int = 1,
) -> Any:
    """A stand-in for a built GameCard: only the fields the counters read."""
    return SimpleNamespace(
        game=SimpleNamespace(
            id=game_id, name=name, display_name=name, launcher=launcher
        ),
        is_ignored=ignored,
        has_backups=backups,
        visible=True,
        _check_for_updates=lambda: outdated,
    )


def _dll(filename: str, version: str, game_id: int = 1) -> GameDLL:
    return GameDLL(
        id=1,
        game_id=game_id,
        dll_type="DLSS",
        dll_filename=filename,
        dll_path=f"C:/Games/{filename}",
        current_version=version,
    )


def _merged(game_ids: list[int]) -> Any:
    """A stand-in for MergedGame: the counter only reads all_game_ids."""
    return SimpleNamespace(all_game_ids=game_ids)


@pytest.fixture
def outdated_dll(monkeypatch):
    """A DLL filename whose installed version is behind the bundled latest."""
    monkeypatch.setattr(
        games_view_module,
        "count_outdated_dlls",
        lambda dlls: sum(1 for d in dlls if d.current_version == "1.0.0.0"),
    )


# ==================== Games view chip / subtitle / CTA counts ====================


def test_ignored_outdated_game_is_not_counted_as_needing_an_update():
    """The complaint in #299: an ignored game still read as "needs update"."""
    counts = count_card_statuses(
        [
            _card("Kept", outdated=True, game_id=1),
            _card("Ignored", outdated=True, ignored=True, game_id=2),
        ]
    )

    assert counts.needs_update == 1


def test_ignored_game_is_not_counted_as_up_to_date_either():
    """Excluding it from "needs update" must not park it in "up to date" —
    that just moves the wrong number to the other chip."""
    counts = count_card_statuses(
        [
            _card("Kept", outdated=False, game_id=1),
            _card("Ignored", outdated=False, ignored=True, game_id=2),
        ]
    )

    assert counts.up_to_date == 1


def test_ignored_games_still_count_towards_has_backups():
    """Backups are not update accounting: an ignored game can still be
    restored, so hiding it from that chip would hide a real, usable action."""
    counts = count_card_statuses(
        [
            _card("Kept", backups=True, game_id=1),
            _card("Ignored", backups=True, ignored=True, game_id=2),
        ]
    )

    assert counts.has_backups == 2


def test_ignored_total_is_reported_for_the_header_subtitle():
    """"14 games · 2 need updates · 3 ignored" — the note that explains why
    the chips no longer add up to the library size."""
    counts = count_card_statuses(
        [
            _card("Kept", game_id=1),
            _card("A", ignored=True, game_id=2),
            _card("B", ignored=True, game_id=3),
        ]
    )

    assert counts.ignored == 2


def test_counts_are_unchanged_when_nothing_is_ignored():
    counts = count_card_statuses(
        [
            _card("A", outdated=True, backups=True, game_id=1),
            _card("B", outdated=False, game_id=2),
        ]
    )

    assert (counts.needs_update, counts.up_to_date, counts.has_backups, counts.ignored) == (1, 1, 1, 0)


def test_empty_library_counts_to_zeroes():
    counts = count_card_statuses([])

    assert (counts.needs_update, counts.up_to_date, counts.has_backups, counts.ignored) == (0, 0, 0, 0)


# ==================== Hub pill / app-bar pill count ====================


def test_hub_count_skips_ignored_games(outdated_dll):
    """HubView._count_games_needing_update feeds BOTH the hub pill/CTA and the
    app-bar status pill, so one exclusion here fixes both surfaces."""
    merged = [_merged([1]), _merged([2])]
    dlls_by_game = {1: [_dll("nvngx_dlss.dll", "1.0.0.0", 1)], 2: [_dll("nvngx_dlss.dll", "1.0.0.0", 2)]}

    assert count_merged_games_needing_update(merged, dlls_by_game, {2}) == 1


def test_hub_count_skips_a_merged_game_ignored_by_any_of_its_ids():
    """A merged card covers several rows (same game across launchers); the
    ignore toggle writes only the primary id, so membership is an intersection
    — matching how GamesView.create_card decides ``is_ignored``."""
    merged = [_merged([10, 11, 12])]
    dlls_by_game = {11: [_dll("nvngx_dlss.dll", "1.0.0.0", 11)]}

    assert count_merged_games_needing_update(merged, dlls_by_game, {12}) == 0


def test_hub_count_is_unchanged_with_an_empty_ignore_list(outdated_dll):
    merged = [_merged([1]), _merged([2])]
    dlls_by_game = {1: [_dll("nvngx_dlss.dll", "1.0.0.0", 1)], 2: [_dll("nvngx_dlss.dll", "1.0.0.0", 2)]}

    assert count_merged_games_needing_update(merged, dlls_by_game, set()) == 2


def test_hub_count_ignores_a_game_with_no_outdated_dlls(outdated_dll):
    merged = [_merged([1])]
    dlls_by_game = {1: [_dll("nvngx_dlss.dll", "9.9.9.9", 1)]}

    assert count_merged_games_needing_update(merged, dlls_by_game, set()) == 0


# ==================== Visibility survives a rebuild ====================


def _view(*, show_ignored: bool = True, cards=None, launcher: str | None = None) -> Any:
    """A stand-in for GamesView holding only what the filter pass reads."""
    view: Any = SimpleNamespace(
        game_cards={c.game.id: c for c in (cards or [])},
        game_card_containers={},
        search_query="",
        _filter_needs_update=False,
        _filter_up_to_date=False,
        _filter_has_backups=False,
        _show_ignored_games=show_ignored,
        _ignored_game_ids=set(),
        _total_games=len(cards or []),
        _sort_preference="name_asc",
        _sort_applied_at_build="name_asc",
        logger=SimpleNamespace(debug=lambda *a, **k: None, error=lambda *a, **k: None),
        update=lambda: None,
        _update_filter_chip_counts=lambda: None,
        _apply_sort_to_grids=lambda: None,
        _on_ignore_changed=None,
    )
    view._get_current_launcher = lambda: launcher
    view._card_passes_filters = lambda card: GamesView._card_passes_filters(view, card)
    view._apply_visibility = lambda: GamesView._apply_visibility(view)
    view._refresh_filters_and_counts = lambda: GamesView._refresh_filters_and_counts(view)
    return view


def test_apply_visibility_hides_ignored_games_when_the_filter_is_active():
    kept, hidden = _card("Kept", game_id=1), _card("Hidden", ignored=True, game_id=2)
    view = _view(show_ignored=False, cards=[kept, hidden])

    GamesView._apply_visibility(view)

    assert (kept.visible, hidden.visible) == (True, False)


def test_refresh_filters_and_counts_reapplies_the_ignore_filter():
    """The seam every card-creating path ends on. Cards are born visible=True,
    so a rebuild that only recounted the chips left ignored games on screen
    with "Hide ignored games" still active (issue #299)."""
    fresh = _card("Freshly rebuilt", ignored=True, game_id=1)
    view = _view(show_ignored=False, cards=[fresh])

    GamesView._refresh_filters_and_counts(view)

    assert fresh.visible is False


@pytest.mark.anyio
async def test_background_batches_are_filtered_as_they_land():
    """Progressive loading appends cards AFTER the initial paint. Toggling
    "Hide ignored games" filters what exists at that moment; the batches that
    land later have to arrive filtered or the ignored games reappear."""
    ignored_card = _card("Late arrival", ignored=True, game_id=7)
    view = _view(show_ignored=False, cards=[])

    grid: Any = SimpleNamespace(controls=[])
    merged: Any = SimpleNamespace(primary_game=SimpleNamespace(id=7, effective_steam_app_id=None))
    ignored_card.game.effective_steam_app_id = None
    ignored_card._image_loaded = False

    await GamesView._load_remaining_cards_progressive(
        view,
        [("Steam", merged, [], {})],
        {"Steam": grid},
        {},
        lambda mg, dlls, backups: ignored_card,
    )

    assert grid.controls == [ignored_card]
    assert ignored_card.visible is False


def test_ignoring_a_game_recounts_immediately():
    """Excluding ignored games from the count is worthless if the count only
    catches up on the next reload — the card's own ignore button and the
    Settings ignore-list panel both land here."""
    recounts: list[int] = []
    card = _card("Newly ignored", outdated=True, game_id=1)
    view = _view(cards=[card])
    view._update_filter_chip_counts = lambda: recounts.append(
        count_card_statuses(view.game_cards.values()).needs_update
    )
    view._refresh_filters_and_counts = lambda: GamesView._refresh_filters_and_counts(view)
    card.set_ignored = lambda value: setattr(card, "is_ignored", value)

    GamesView._sync_card_ignore_state(view, card, True)

    assert card.is_ignored is True
    assert recounts == [0]


def test_unignoring_a_game_brings_it_back_into_the_count():
    recounts: list[int] = []
    card = _card("Restored", outdated=True, ignored=True, game_id=1)
    view = _view(cards=[card])
    view._update_filter_chip_counts = lambda: recounts.append(
        count_card_statuses(view.game_cards.values()).needs_update
    )
    view._refresh_filters_and_counts = lambda: GamesView._refresh_filters_and_counts(view)
    card.set_ignored = lambda value: setattr(card, "is_ignored", value)

    GamesView._sync_card_ignore_state(view, card, False)

    assert recounts == [1]


@pytest.mark.anyio
async def test_ignoring_a_game_asks_the_app_bar_pill_to_recount():
    """The pill counts the library straight from the database and is hidden
    while the Games view is on screen, so nothing else would tell it that a
    game just left the count before the user navigates away."""
    fired: list[bool] = []

    async def on_ignore_changed():
        fired.append(True)

    card = _card("Newly ignored", outdated=True, game_id=1)
    view = _view(cards=[card])
    view._on_ignore_changed = on_ignore_changed
    view._refresh_filters_and_counts = lambda: GamesView._refresh_filters_and_counts(view)
    card.set_ignored = lambda value: setattr(card, "is_ignored", value)

    GamesView._sync_card_ignore_state(view, card, True)
    await anyio.sleep(0)  # let the fire-and-forget task run

    assert fired == [True]


# ==================== Toggle persistence ====================


def test_show_ignored_games_defaults_to_showing_them():
    """A fresh install still lists ignored games, dimmed — hiding them by
    default would make an ignored game look deleted."""
    assert UIPreferencesConfig().show_ignored_games is True


def test_toggling_the_ignore_filter_persists_the_choice(monkeypatch):
    """sort_preference and grid_density — the two neighbours in the same
    options menu — are both persisted; this one silently reset on restart."""
    saved: list[bool] = []
    monkeypatch.setattr(
        games_view_module.config_manager,
        "set_show_ignored_games",
        lambda value: saved.append(value),
    )

    view = _view(show_ignored=True, cards=[])
    view.options_menu = None

    GamesView._on_ignore_filter_toggle(view, None)

    assert view._show_ignored_games is False
    assert saved == [False]
