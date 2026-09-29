"""Update-scoping tests for GamesView / GameCard (Flet 1.0.x).

Covers the flush discipline these two files follow:

- a theme toggle costs ONE GamesView update (not one per helper, not one per
  card), and none at all while the view is detached by the nav controller;
- background work (progressive loading, re-sorts, badge refreshes) skips its
  update() while detached - Flet does not raise there, it diffs the detached
  subtree and ships a patch the client drops - but still mutates the Python
  objects, which go out with the view's full re-add on its next attach;
- snackbars go through page.show_dialog() (the dialog stack), never
  page.overlay + page.update().

Stubs are SimpleNamespace objects driven through the unbound methods, the same
approach as test_bulk_selection.py - no Flet page or client is started.
"""

from types import SimpleNamespace

import anyio
import flet as ft
import pytest

from dlss_updater.ui_flet.components import game_card as game_card_module
from dlss_updater.ui_flet.components.game_card import GameCard
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin
from dlss_updater.ui_flet.views import games_view as games_view_module
from dlss_updater.ui_flet.views.games_view import (
    CARD_THEME_FLUSH_DEBOUNCE_S,
    GamesView,
)


class Counter:
    """A stand-in control that only counts update() calls."""

    def __init__(self, **fields):
        self.updates = 0
        self.__dict__.update(fields)

    def update(self):
        self.updates += 1


def _bind(view, *names):
    """Bind real GamesView methods onto a SimpleNamespace stub."""
    for name in names:
        method = getattr(GamesView, name)
        setattr(view, name, method.__get__(view))


# ==================== GamesView.apply_theme ====================


def _theme_view(attached: bool = True):
    view = Counter(
        _nav_attached=attached,
        header=SimpleNamespace(bgcolor=None),
        options_menu=Counter(items=None),
        sort_menu=Counter(items=None),
        steam_api_pill=Counter(bgcolor=None, border=None),
        _steam_pill_icon=SimpleNamespace(icon=None, color=None),
        _steam_pill_text=SimpleNamespace(color=None),
        select_mode_button=None,
        steam_api_card=SimpleNamespace(_api_key_valid=True),
        _registry=SimpleNamespace(is_dark=True),
    )
    view.get_themed_properties = lambda: {"header.bgcolor": ("#dark", "#light")}
    view._set_nested_property = ThemeAwareMixin._set_nested_property.__get__(view)
    view._build_options_menu_items = lambda: ["options"]
    view._build_sort_menu_items = lambda: ["sort"]
    view._refresh_update_all_button = lambda is_dark: None
    view._apply_filter_chip_theme = lambda is_dark: None
    view._refresh_selection_bar = lambda is_dark: None
    _bind(
        view,
        "_paint_themed_properties",
        "_refresh_steam_api_pill",
        "_steam_pill_style",
        "_steam_pill_border",
        "_get_is_dark",
    )
    return view


@pytest.mark.anyio
async def test_apply_theme_flushes_the_view_exactly_once():
    view = _theme_view()

    await GamesView.apply_theme(view, False)

    assert view.updates == 1
    # The helpers paint but no longer flush on their own.
    assert view.options_menu.updates == 0
    assert view.sort_menu.updates == 0
    assert view.steam_api_pill.updates == 0
    # ...and everything was actually painted.
    assert view.header.bgcolor == "#light"
    assert view.options_menu.items == ["options"]
    assert view.sort_menu.items == ["sort"]
    assert view.steam_api_pill.bgcolor is not None


@pytest.mark.anyio
async def test_apply_theme_while_detached_paints_but_does_not_update():
    view = _theme_view(attached=False)

    await GamesView.apply_theme(view, True)

    assert view.updates == 0
    assert view.header.bgcolor == "#dark"


def test_steam_pill_refresh_from_dialog_close_flushes_only_the_pill():
    view = _theme_view()

    GamesView._refresh_steam_api_pill(view)

    assert view.steam_api_pill.updates == 1
    assert view.updates == 0


# ==================== Card theme flush coalescing ====================


def _card_stub(owner):
    card = Counter(
        parent=SimpleNamespace(parent=owner),  # e.g. the GridView between them
        update_button=None,
        restore_button=None,
        status_dot=None,
        dlls=[],
        _selected=False,
        _card_body=None,
        title=SimpleNamespace(color=None),
    )
    card._refresh_context_menu = lambda: None
    card.set_selected = lambda selected, is_dark=None: None
    card.get_themed_properties = lambda: {"title.color": ("#d", "#l")}
    card._set_nested_property = ThemeAwareMixin._set_nested_property.__get__(card)
    card._find_theme_flush_owner = GameCard._find_theme_flush_owner.__get__(card)
    return card


@pytest.mark.anyio
async def test_game_card_theme_defers_to_its_games_view():
    owner = SimpleNamespace(parent=None, requests=0)
    owner.request_card_theme_flush = lambda: setattr(owner, "requests", owner.requests + 1)
    card = _card_stub(owner)

    await GameCard.apply_theme(card, False)

    assert card.updates == 0
    assert owner.requests == 1
    assert card.title.color == "#l"


@pytest.mark.anyio
async def test_game_card_outside_a_games_view_still_self_updates():
    card = _card_stub(owner=None)

    await GameCard.apply_theme(card, True)

    assert card.updates == 1


@pytest.mark.anyio
async def test_many_card_flush_requests_coalesce_into_one_view_update():
    view = Counter(_nav_attached=True)
    _bind(view, "request_card_theme_flush", "_flush_card_theme", "_flush_card_theme_now")

    for _ in range(40):
        view.request_card_theme_flush()
    await anyio.sleep(CARD_THEME_FLUSH_DEBOUNCE_S * 4)

    assert view.updates == 1


@pytest.mark.anyio
async def test_card_theme_flush_is_skipped_while_detached():
    view = Counter(_nav_attached=False)
    _bind(view, "request_card_theme_flush", "_flush_card_theme", "_flush_card_theme_now")

    view.request_card_theme_flush()
    await anyio.sleep(CARD_THEME_FLUSH_DEBOUNCE_S * 4)

    assert view.updates == 0


# ==================== Background work while detached ====================


def _progressive_view(attached: bool):
    view = Counter(
        _nav_attached=attached,
        game_cards={},
        game_card_containers={},
        _sort_preference="name_asc",
        _sort_applied_at_build="name_asc",
        logger=SimpleNamespace(debug=lambda *a, **k: None, error=lambda *a, **k: None),
    )
    view._apply_visibility = lambda: 0
    view._refresh_filters_and_counts = lambda: None
    return view


def _remaining(n: int):
    items = []
    for i in range(n):
        game = SimpleNamespace(id=i, effective_steam_app_id=None)
        items.append(("Steam", SimpleNamespace(primary_game=game), [], {}))
    return items


def _make_card(mg, dlls, backups):
    return SimpleNamespace(
        is_ignored=False, _image_loaded=True, opacity=0,
        game=SimpleNamespace(effective_steam_app_id=None),
    )


@pytest.mark.parametrize("attached, expected_updates", [(True, 3), (False, 0)])
@pytest.mark.anyio
async def test_progressive_loading_skips_updates_while_detached(attached, expected_updates):
    view = _progressive_view(attached)
    grid = SimpleNamespace(controls=[])
    count = games_view_module.GAMES_BACKGROUND_BATCH_SIZE * 2  # two batches

    await GamesView._load_remaining_cards_progressive(
        view, _remaining(count), {"Steam": grid}, {}, _make_card
    )

    # Two batch flushes + the final one when attached; none when detached...
    assert view.updates == expected_updates
    # ...but the cards land in the grid either way, for the re-add on attach.
    assert len(grid.controls) == count
    assert len(view.game_cards) == count


@pytest.mark.parametrize("attached, expected_updates", [(True, 2), (False, 0)])
def test_sort_two_phase_swap_skipped_while_detached(attached, expected_updates):
    grid = SimpleNamespace(controls=["a", "b", "c"])
    view = Counter(
        _nav_attached=attached,
        _grids_by_launcher={"Steam": grid},
        _card_sort_fields={},
        _sort_preference="name_desc",
        _sort_applied_at_build="name_asc",
    )
    view._sort_entries = lambda entries, fields: list(reversed(entries))

    assert GamesView._apply_sort_to_grids(view) is True

    assert view.updates == expected_updates
    assert grid.controls == ["c", "b", "a"]
    assert view._sort_applied_at_build == "name_desc"


class _BadgeCard:
    def __init__(self):
        self.update_kwargs = []

    async def refresh_dlls(self, dlls, update=True):
        self.update_kwargs.append(update)

    async def refresh_restore_button(self, groups, update=True):
        self.update_kwargs.append(update)


def _badge_view(monkeypatch, attached: bool = True):
    async def refresh(gid):
        return ["dll"]

    monkeypatch.setattr(games_view_module.db_manager, "refresh_dll_versions_for_game", refresh)
    monkeypatch.setattr(
        games_view_module.db_manager, "batch_get_backups_grouped_sync", lambda ids: {}
    )
    view = Counter(
        _nav_attached=attached,
        game_cards={1: _BadgeCard(), 2: _BadgeCard()},
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a, **k: None),
    )
    view._update_filter_chip_counts = lambda: None
    return view


@pytest.mark.parametrize(
    "attached, flush, expected_updates",
    [(True, True, 1), (True, False, 0), (False, True, 0)],
)
@pytest.mark.anyio
async def test_refresh_all_badges_flushes_once_not_per_card(
    monkeypatch, attached, flush, expected_updates
):
    view = _badge_view(monkeypatch, attached)

    await GamesView.refresh_all_badges(view, flush=flush)

    assert view.updates == expected_updates
    for card in view.game_cards.values():
        assert card.update_kwargs == [False, False]


def test_update_progress_updates_only_the_two_texts():
    page = Counter()
    view = SimpleNamespace(
        _page_ref=page,
        _progress_text=Counter(value=""),
        _progress_detail=Counter(value=""),
    )
    progress = SimpleNamespace(message="Copying", current=1, total=3)

    GamesView._update_progress_dialog(view, None, progress)

    assert page.updates == 0
    assert view._progress_text.updates == 1
    assert view._progress_detail.updates == 1
    assert view._progress_detail.value == "1/3 DLLs processed"


# ==================== Snackbars ====================


class _Page(Counter):
    def __init__(self):
        super().__init__(overlay=[], dialogs=[], theme_mode=ft.ThemeMode.DARK)

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)


@pytest.mark.anyio
async def test_ignore_toggle_snackbar_uses_the_dialog_stack(monkeypatch):
    async def set_ignored(game_id, ignored):
        return True

    monkeypatch.setattr(games_view_module.db_manager, "set_game_ignored", set_ignored)
    page = _Page()
    view = Counter(
        _page_ref=page,
        _ignored_game_ids=set(),
        game_cards={},
        logger=SimpleNamespace(info=lambda *a: None, error=lambda *a: None),
        _registry=SimpleNamespace(is_dark=True),
    )
    _bind(view, "_get_is_dark")
    game = SimpleNamespace(id=7, name="Half-Life")

    await GamesView._perform_ignore_toggle(view, game, True)

    assert page.overlay == []  # nothing leaked into page.overlay
    assert page.updates == 0  # no full page.update()
    assert len(page.dialogs) == 1
    snackbar = page.dialogs[0]
    assert isinstance(snackbar, ft.SnackBar)
    # With an action a SnackBar persists unless told otherwise.
    assert snackbar.persist is False


@pytest.mark.anyio
async def test_copy_path_snackbar_goes_through_show_snackbar(monkeypatch):
    calls = []

    class _Clipboard:
        async def set(self, value):
            calls.append(value)

    monkeypatch.setattr(ft, "Clipboard", _Clipboard)
    page = _Page()
    card = SimpleNamespace(
        _page_ref=page,
        _registry=SimpleNamespace(is_dark=False),
        all_paths=["C:/Games/HL"],
        logger=SimpleNamespace(warning=lambda *a: None),
    )

    await GameCard._on_copy_path_clicked(card, None)

    assert calls == ["C:/Games/HL"]
    assert page.updates == 0
    assert page.overlay == []
    assert len(page.dialogs) == 1
    assert isinstance(page.dialogs[0], ft.SnackBar)
    assert game_card_module.show_snackbar is not None
