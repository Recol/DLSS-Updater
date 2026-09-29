"""Slide panels must not crash on dead Flet APIs or over-scope their updates.

- SlidePanel's validation-failure path used ``page.show_snack_bar``, which does
  not exist in Flet 1.0 (AttributeError on every failed validation).
- PanelContentBase._show_snackbar appended to ``page.overlay`` (never removed)
  and then serialized the whole page.
- The search handlers did a full ``page.update()`` per keystroke when only the
  results list changed.
"""

import asyncio
import logging
from types import SimpleNamespace

import flet as ft
import pytest

from dlss_updater.ui_flet.components.slide_panel.panel_content_base import (
    PanelContentBase,
)
from dlss_updater.ui_flet.components.slide_panel.slide_panel import SlidePanel
from dlss_updater.ui_flet.theme.colors import MD3Colors


def _no_page_update(*_args):
    raise AssertionError("page.update() must not be called")


def _fake_page(shown):
    # Deliberately no show_snack_bar attribute - Flet 1.0's Page has none.
    return SimpleNamespace(
        theme_mode=ft.ThemeMode.DARK,
        overlay=[],
        show_dialog=shown.append,
        update=_no_page_update,
    )


def test_page_has_no_show_snack_bar_in_this_flet():
    assert not hasattr(ft.Page, "show_snack_bar")


def test_validation_failure_shows_error_snackbar_instead_of_crashing():
    shown = []
    saved = []

    async def on_save():
        saved.append(True)
        return True

    panel = object.__new__(SlidePanel)
    panel._page_ref = _fake_page(shown)
    panel._registry = SimpleNamespace(is_dark=True)
    panel.logger = logging.getLogger("test")
    panel.content = SimpleNamespace(
        validate=lambda: (False, "Pick a preset first"), on_save=on_save
    )

    asyncio.run(panel._handle_save())

    assert len(shown) == 1
    bar = shown[0]
    assert isinstance(bar, ft.SnackBar)
    assert bar.content.value == "Pick a preset first"
    assert bar.bgcolor == MD3Colors.get_error(True)
    assert saved == []  # save never runs when validation fails


def _concrete_content_class():
    stubs = {name: None for name in PanelContentBase.__abstractmethods__}
    return type("_Content", (PanelContentBase,), stubs)


@pytest.mark.parametrize(
    "bgcolor, expected",
    [
        ("#2D6E88", lambda d: MD3Colors.get_themed("snackbar_bg", d)),
        ("#4CAF50", MD3Colors.get_success),
        ("#F44336", MD3Colors.get_error),
    ],
)
def test_show_snackbar_uses_show_dialog_not_the_overlay(bgcolor, expected):
    from dlss_updater.ui_flet.theme.theme_aware import get_theme_registry

    shown = []
    page = _fake_page(shown)
    content = _concrete_content_class()(page, logging.getLogger("test"))

    content._show_snackbar("Saved", bgcolor)

    assert page.overlay == []
    assert len(shown) == 1 and isinstance(shown[0], ft.SnackBar)
    assert shown[0].bgcolor == expected(get_theme_registry().is_dark)


def test_show_snackbar_keeps_a_custom_colour():
    shown = []
    page = _fake_page(shown)
    content = _concrete_content_class()(page, logging.getLogger("test"))

    content._show_snackbar("Restart needed", "#FFB74D")

    assert page.overlay == []
    assert [s.bgcolor for s in shown] == ["#FFB74D"]


class _FakeList:
    def __init__(self):
        self.controls = []
        self.updates = 0
        self.page = object()  # "attached"

    def update(self):
        self.updates += 1


def test_blacklist_search_updates_only_the_list():
    from dlss_updater.ui_flet.panels.blacklist_panel import BlacklistPanel

    panel = object.__new__(BlacklistPanel)
    panel._page_ref = SimpleNamespace(update=_no_page_update)
    panel.blacklisted_games = ["Foo Game", "Bar Game"]
    panel.games_column = _FakeList()
    panel._build_game_cards = lambda: list(panel.filtered_games)

    panel._on_search_change(SimpleNamespace(control=SimpleNamespace(value="foo")))

    assert panel.filtered_games == ["Foo Game"]
    assert panel.games_column.controls == ["Foo Game"]
    assert panel.games_column.updates == 1


def test_ignore_list_search_patches_only_the_list():
    from dlss_updater.ui_flet.panels.ignore_list_panel import IgnoreListPanel

    calls = []
    panel = object.__new__(IgnoreListPanel)
    panel._page_ref = SimpleNamespace(update=lambda *c: calls.append(c))
    panel._all_games = [SimpleNamespace(name="Foo"), SimpleNamespace(name="Bar")]
    panel.games_column = _FakeList()
    panel._build_game_rows = lambda: [g.name for g in panel._filtered_games]

    panel._on_search_change(SimpleNamespace(control=SimpleNamespace(value="bar")))

    assert panel.games_column.controls == ["Bar"]
    # Exactly one targeted patch - never the argument-less full page update.
    assert calls == [(panel.games_column,)]
