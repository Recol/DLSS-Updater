"""Tests for the two pure helpers behind "Update selected (N)".

build_dll_dict_for_selection() maps the selected cards onto the shape
AsyncUpdateCoordinator.update_games() already accepts — {launcher: [dll_path]}
— so a hand-picked subset runs through the same high-performance pipeline,
cancellable overlay and summary dialog as the library-wide update.

collect_flagged_dlls() is the rollback-compatibility check, extracted from
GamesView._perform_game_update_with_warning() so the per-game path and the
selection path cross-reference flagged versions identically. Flagged DLLs are
vendor-signed, so a bad version is bad regardless of which game carries it —
the check is over the UNION of the selection's DLL filenames, one warning entry
per DLL rather than one per game.

Both take plain data, so they are called directly rather than constructing
Flet-backed controls (same approach as test_game_card_tech_version.py).
"""

from types import SimpleNamespace

from dlss_updater.models import Game, GameDLL
from dlss_updater.ui_flet.components.game_card import GameCard
from dlss_updater.ui_flet.views.games_view import (
    GamesView,
    build_dll_dict_for_selection,
    collect_flagged_dlls,
    filter_dll_dict,
)


def _card(name: str, launcher: str, dll_paths: list[str], game_id: int = 1):
    """A stand-in for a selected GameCard: only .game and .dlls are read."""
    game = Game(id=game_id, name=name, path=f"C:/Games/{name}", launcher=launcher)
    dlls = [
        GameDLL(
            id=i,
            game_id=game_id,
            dll_type="DLSS",
            dll_filename=path.rsplit("/", 1)[-1],
            dll_path=path,
        )
        for i, path in enumerate(dll_paths)
    ]
    return SimpleNamespace(game=game, dlls=dlls)


# ==================== build_dll_dict_for_selection ====================


def test_groups_dll_paths_under_their_launcher():
    cards = [_card("Cyberpunk 2077", "Steam", ["C:/Games/CP/nvngx_dlss.dll"])]

    assert build_dll_dict_for_selection(cards) == {
        "Steam": ["C:/Games/CP/nvngx_dlss.dll"]
    }


def test_merges_several_games_from_the_same_launcher():
    cards = [
        _card("A", "Steam", ["C:/A/nvngx_dlss.dll"], game_id=1),
        _card("B", "Steam", ["C:/B/nvngx_dlss.dll"], game_id=2),
    ]

    assert build_dll_dict_for_selection(cards) == {
        "Steam": ["C:/A/nvngx_dlss.dll", "C:/B/nvngx_dlss.dll"]
    }


def test_keeps_launchers_separate():
    cards = [
        _card("A", "Steam", ["C:/A/nvngx_dlss.dll"], game_id=1),
        _card("B", "Epic Games Store", ["C:/B/nvngx_dlss.dll"], game_id=2),
    ]

    result = build_dll_dict_for_selection(cards)

    assert result == {
        "Steam": ["C:/A/nvngx_dlss.dll"],
        "Epic Games Store": ["C:/B/nvngx_dlss.dll"],
    }


def test_empty_selection_produces_an_empty_dict():
    assert build_dll_dict_for_selection([]) == {}


def test_a_game_with_no_dlls_contributes_no_launcher_key():
    """An empty launcher list would make update_games() report a launcher it
    then does nothing for."""
    cards = [_card("No DLLs", "GOG Galaxy", [])]

    assert build_dll_dict_for_selection(cards) == {}


def test_duplicate_paths_are_collapsed_preserving_order():
    """A MergedGame aggregates DLLs across every merged game id, so the same
    path can legitimately appear twice; updating it twice would back it up
    twice."""
    cards = [
        _card("A", "Steam", ["C:/A/nvngx_dlss.dll", "C:/A/nvngx_dlssg.dll"], game_id=1),
        _card("A dupe", "Steam", ["C:/A/nvngx_dlss.dll"], game_id=2),
    ]

    assert build_dll_dict_for_selection(cards) == {
        "Steam": ["C:/A/nvngx_dlss.dll", "C:/A/nvngx_dlssg.dll"]
    }


# ==================== collect_flagged_dlls ====================

LATEST = {"nvngx_dlss.dll": "310.7.0.0", "nvngx_dlssg.dll": "310.4.0.0"}
FLAGGED = {
    ("nvngx_dlss.dll", "310.7.0.0"): {
        "count": 4,
        "games": ["Hogwarts Legacy", "Alan Wake 2"],
        "from_versions": ["310.2.1.0"],
    }
}


def test_flags_a_dll_whose_target_version_is_flagged():
    entries = collect_flagged_dlls({"nvngx_dlss.dll"}, FLAGGED, LATEST)

    assert entries == [
        {
            "dll_filename": "nvngx_dlss.dll",
            "target_version": "310.7.0.0",
            "event_count": 4,
            "affected_games": ["Hogwarts Legacy", "Alan Wake 2"],
            "from_versions": ["310.2.1.0"],
        }
    ]


def test_returns_nothing_when_no_target_version_is_flagged():
    assert collect_flagged_dlls({"nvngx_dlssg.dll"}, FLAGGED, LATEST) == []


def test_ignores_dlls_with_no_known_latest_version():
    """No bundled source DLL means the update would skip it anyway."""
    assert collect_flagged_dlls({"unknown.dll"}, FLAGGED, LATEST) == []


def test_one_entry_per_dll_however_many_games_carry_it():
    """The union of the selection's filenames — not one warning per game."""
    entries = collect_flagged_dlls(
        ["nvngx_dlss.dll", "nvngx_dlss.dll", "nvngx_dlssg.dll"], FLAGGED, LATEST
    )

    assert len(entries) == 1
    assert entries[0]["dll_filename"] == "nvngx_dlss.dll"


def test_filenames_are_matched_case_insensitively():
    """DLL filenames reach this from the DB and from DLL_GROUPS with
    inconsistent case."""
    assert collect_flagged_dlls({"NVNGX_DLSS.DLL"}, FLAGGED, LATEST) != []


def test_empty_selection_flags_nothing():
    assert collect_flagged_dlls(set(), FLAGGED, LATEST) == []


# ==================== filter_dll_dict ====================
#
# update_games() has no skip_dll_filenames parameter (only update_single_game
# does), so the rollback dialog's "Skip flagged" outcome is applied by
# narrowing the dict before it reaches the coordinator.

DICT = {
    "Steam": ["C:/A/nvngx_dlss.dll", "C:/A/nvngx_dlssg.dll"],
    "GOG Galaxy": ["C:/B/nvngx_dlss.dll"],
}


def test_drops_paths_whose_filename_is_skipped():
    assert filter_dll_dict(DICT, {"nvngx_dlss.dll"}) == {
        "Steam": ["C:/A/nvngx_dlssg.dll"]
    }


def test_drops_a_launcher_left_with_no_paths():
    """GOG Galaxy's only DLL was skipped, so it must not survive as an empty
    list the coordinator would report a launcher for."""
    result = filter_dll_dict(DICT, {"nvngx_dlss.dll"})

    assert "GOG Galaxy" not in result


def test_skipping_nothing_returns_the_dict_unchanged():
    assert filter_dll_dict(DICT, set()) == DICT


def test_skip_matching_is_case_insensitive():
    assert filter_dll_dict(DICT, {"NVNGX_DLSS.DLL"}) == {
        "Steam": ["C:/A/nvngx_dlssg.dll"]
    }


def test_skipping_every_dll_produces_an_empty_dict():
    assert filter_dll_dict(DICT, {"nvngx_dlss.dll", "nvngx_dlssg.dll"}) == {}


def test_does_not_mutate_the_input():
    original = {"Steam": ["C:/A/nvngx_dlss.dll", "C:/A/nvngx_dlssg.dll"]}

    filter_dll_dict(original, {"nvngx_dlss.dll"})

    assert original == {"Steam": ["C:/A/nvngx_dlss.dll", "C:/A/nvngx_dlssg.dll"]}


# ==================== "Update all" vs. an active selection ====================
#
# The two bulk actions are mutually exclusive in intent: while games are picked
# out, "Update all (N)" sits right beside "Update selected (N)" offering to
# update the whole library instead — one misclick away from updating 11 games
# when 2 were chosen. _set_update_all_state() owns the button's visibility, so
# the selection check belongs there rather than in a second place that could
# disagree.


def _view(needs_update: int = 11, selected: set[int] | None = None):
    """A stand-in for GamesView: only the fields _set_update_all_state reads."""
    return SimpleNamespace(
        update_all_button=SimpleNamespace(visible=False),
        _update_all_text=SimpleNamespace(value=""),
        _on_update_all=lambda: None,
        _needs_update_count=needs_update,
        _selected_game_ids=selected if selected is not None else set(),
    )


def test_update_all_shows_when_games_are_outdated_and_nothing_is_selected():
    view = _view(needs_update=11)

    GamesView._set_update_all_state(view, 11)

    assert view.update_all_button.visible is True
    assert view._update_all_text.value == "Update all (11)"


def test_update_all_is_hidden_while_a_selection_exists():
    view = _view(needs_update=11, selected={1, 2})

    GamesView._set_update_all_state(view, 11)

    assert view.update_all_button.visible is False


def test_update_all_returns_when_the_selection_is_cleared():
    view = _view(needs_update=11, selected={1, 2})
    GamesView._set_update_all_state(view, 11)

    view._selected_game_ids.clear()
    GamesView._set_update_all_state(view, view._needs_update_count)

    assert view.update_all_button.visible is True


def test_update_all_stays_hidden_when_nothing_is_outdated():
    view = _view(needs_update=0)

    GamesView._set_update_all_state(view, 0)

    assert view.update_all_button.visible is False


def test_set_update_all_state_records_the_count_for_later_reevaluation():
    """_sync_selection_ui() re-runs this with the remembered count when the
    selection clears, so the count has to survive the hidden state."""
    view = _view(needs_update=0)

    GamesView._set_update_all_state(view, 7)

    assert view._needs_update_count == 7


# ==================== Card checkbox: selected vs. unselected ====================
#
# At 18px on top of box art the two states have to differ in more than a glyph
# outline — the plate itself changes colour and the glyph becomes a tick.


def _card_stub():
    return SimpleNamespace(
        _selected=False,
        _select_icon=SimpleNamespace(name=None, color=None),
        _select_button=SimpleNamespace(bgcolor=None, border=None),
        _registry=SimpleNamespace(is_dark=True),
    )


def test_selected_checkbox_shows_a_tick():
    import flet as ft

    card = _card_stub()

    GameCard.set_selected(card, True)

    assert card._select_icon.name == ft.Icons.CHECK


def test_unselected_checkbox_shows_an_empty_box():
    import flet as ft

    card = _card_stub()

    GameCard.set_selected(card, False)

    assert card._select_icon.name == ft.Icons.CHECK_BOX_OUTLINE_BLANK


def test_selected_and_unselected_plates_are_different_colours():
    """The plate carries the state, not just the glyph — this is what makes a
    selected card readable across a grid of box art."""
    selected, unselected = _card_stub(), _card_stub()

    GameCard.set_selected(selected, True)
    GameCard.set_selected(unselected, False)

    assert selected._select_button.bgcolor != unselected._select_button.bgcolor


def test_selected_plate_is_opaque_not_a_translucent_scrim():
    """A translucent plate over bright box art was exactly what made the two
    states hard to tell apart. Flet renders a faded colour as "<colour>,<alpha>"
    (e.g. "black,0.5"), so a solid fill carries no comma."""
    card = _card_stub()

    GameCard.set_selected(card, True)

    bgcolor = card._select_button.bgcolor
    assert bgcolor is not None
    assert "," not in str(bgcolor)


def test_selected_plate_repaints_for_a_theme_change():
    """apply_theme() passes the incoming is_dark rather than reading the
    registry, which has not necessarily flipped yet when the cascade runs."""
    card = _card_stub()  # registry reports dark
    GameCard.set_selected(card, True)
    dark_plate = card._select_button.bgcolor

    GameCard.set_selected(card, True, is_dark=False)

    assert card._select_button.bgcolor != dark_plate
