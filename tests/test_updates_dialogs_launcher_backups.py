"""Update-scope fixes in LauncherCard, BackupsView and DLSSOverlayDialog.

- Snackbars go through page.show_dialog() (page.snack_bar does not exist in
  Flet 1.0.x, so the old writes never showed anything) with no page.update().
- BackupsView background work skips update() while the view is nav-detached.
- The already-loaded fast path flushes loading_indicator.visible=False (it
  used to be set after the only update and then returned).
"""

import asyncio
import os
from types import SimpleNamespace

import flet as ft
import pytest

from dlss_updater.ui_flet.components import launcher_card as lc_mod
from dlss_updater.ui_flet.components.launcher_card import LauncherCard
from dlss_updater.ui_flet.dialogs.dlss_overlay_dialog import DLSSOverlayDialog
from dlss_updater.ui_flet.views.backups_view import BackupsView


class _Page:
    """Page stub: __slots__ makes any page.snack_bar write raise, and there is
    deliberately no update() so a stray page.update() fails the test."""

    __slots__ = ("shown", "theme_mode")

    def __init__(self):
        self.shown = []
        self.theme_mode = ft.ThemeMode.DARK

    def show_dialog(self, dialog):
        self.shown.append(dialog)


def _card_stub(paths=None):
    card = SimpleNamespace(
        current_paths=list(paths or []),
        _page_ref=_Page(),
        _registry=SimpleNamespace(is_dark=True),
        logger=SimpleNamespace(warning=lambda *a: None, error=lambda *a: None, info=lambda *a: None),
        launcher_enum=object(),
        _attached=True,
    )
    return card


def _snack_texts(page):
    assert all(isinstance(d, ft.SnackBar) for d in page.shown)
    return [d.content.value for d in page.shown]


# ---------------------------------------------------------------- LauncherCard


@pytest.mark.parametrize("fails", [False, True])
def test_copy_paths_shows_snackbar_via_show_dialog(monkeypatch, fails):
    class _Clipboard:
        async def set(self, _text):
            if fails:
                raise RuntimeError("no clipboard")

    monkeypatch.setattr(ft, "Clipboard", _Clipboard)
    card = _card_stub(["C:/Games"])

    asyncio.run(LauncherCard._on_copy_paths(card, None))

    expected = "Failed to copy to clipboard" if fails else "Paths copied to clipboard"
    assert _snack_texts(card._page_ref) == [expected]


def test_open_explorer_failure_shows_snackbar(monkeypatch):
    def _boom(_path):
        raise OSError("nope")

    monkeypatch.setattr(lc_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(lc_mod, "IS_LINUX", False)
    monkeypatch.setattr(os, "startfile", _boom, raising=False)
    card = _card_stub(["C:/Missing"])

    asyncio.run(LauncherCard._on_open_explorer(card, None))

    assert _snack_texts(card._page_ref) == ["Could not open: C:/Missing"]


@pytest.mark.parametrize(
    "detected, added, expected",
    [
        ("C:/Epic", True, "Detected: C:/Epic"),
        ("C:/Epic", False, "Path already configured or at limit"),
        (None, False, "Could not auto-detect path"),
    ],
)
def test_auto_detect_shows_snackbar_and_flushes_card_once(monkeypatch, detected, added, expected):
    import dlss_updater.scanner as scanner

    monkeypatch.setattr(scanner, "auto_detect_launcher_path", lambda _enum: detected)
    monkeypatch.setattr(lc_mod.config_manager, "add_launcher_path", lambda _enum, _p: added)
    card = _card_stub()
    calls = []

    def _apply(_is_dark):
        calls.append("apply")

    async def _display():
        calls.append("display")  # the real one ends with the card's self.update()

    def _update():
        calls.append("update")

    card._apply_configured_state = _apply
    card._update_paths_display = _display
    card.update = _update

    asyncio.run(LauncherCard._on_auto_detect(card, None))

    assert _snack_texts(card._page_ref) == [expected]
    # Configured-state before the display refresh (whose update flushes both),
    # and no extra card update on top.
    assert calls == (["apply", "display"] if detected and added else [])


# ------------------------------------------------------------ DLSSOverlayDialog


@pytest.mark.parametrize("fails", [False, True])
def test_overlay_copy_shows_snackbar_via_show_dialog(monkeypatch, fails):
    class _Clipboard:
        async def set(self, _text):
            if fails:
                raise RuntimeError("no clipboard")

    monkeypatch.setattr(ft, "Clipboard", _Clipboard)
    dlg = SimpleNamespace(
        _page_ref=_Page(),
        _registry=SimpleNamespace(is_dark=False),
        logger=SimpleNamespace(warning=lambda *a: None, info=lambda *a: None),
        LINUX_OVERLAY_ENV=DLSSOverlayDialog.LINUX_OVERLAY_ENV,
    )

    asyncio.run(DLSSOverlayDialog._on_copy_clicked(dlg, None))

    expected = "Failed to copy to clipboard" if fails else "Launch options copied to clipboard!"
    assert _snack_texts(dlg._page_ref) == [expected]


# ------------------------------------------------------------------ BackupsView


class _ViewStub:
    _update_if_attached = BackupsView._update_if_attached
    _animate_groups_in = BackupsView._animate_groups_in

    def __init__(self, attached=True):
        self._nav_attached = attached
        self.updates = 0
        self.logger = SimpleNamespace(debug=lambda *a: None)

    def update(self):
        self.updates += 1


@pytest.mark.parametrize("attached, expected", [(True, 1), (False, 0)])
def test_update_if_attached_honours_nav_detachment(attached, expected):
    view = _ViewStub(attached)
    view._update_if_attached()
    assert view.updates == expected


def test_group_fade_in_skips_updates_while_detached(monkeypatch):
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr("dlss_updater.ui_flet.views.backups_view.anyio.sleep", _no_sleep)
    groups = [ft.Container(opacity=0) for _ in range(5)]

    detached = _ViewStub(attached=False)
    asyncio.run(detached._animate_groups_in(groups))
    assert detached.updates == 0
    # The final state is still on the objects, for the re-attach to serialize.
    assert all(g.opacity == 1 for g in groups)

    attached = _ViewStub(attached=True)
    asyncio.run(attached._animate_groups_in([ft.Container(opacity=0) for _ in range(5)]))
    assert attached.updates == 2  # batches of 3


@pytest.mark.parametrize("has_backups", [False, True])
def test_fast_path_flushes_hidden_loading_indicator(has_backups):
    view = _ViewStub()
    view.is_loading = False
    view._backups_loaded = True
    view.backups = [object()] if has_backups else []
    view.loading_indicator = ft.Container(visible=True)
    view.empty_state = ft.Container(visible=False)
    view.backups_list_container = ft.Container(visible=False)
    view.backups_list = SimpleNamespace(controls=[ft.Container(), ft.Container()])
    flushed = []
    view.update = lambda: flushed.append(view.loading_indicator.visible)

    async def _noop(_groups):
        return None

    view._animate_groups_in = _noop

    asyncio.run(BackupsView.load_backups(view))

    # Exactly one update, and the indicator was already hidden when it ran.
    assert flushed == [False]
