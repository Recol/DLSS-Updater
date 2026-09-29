"""show_snackbar() must go through page.show_dialog - never page.snack_bar
(a dead field in Flet 1.0.x) or an overlay append that leaks."""

from types import SimpleNamespace

import flet as ft

from dlss_updater.ui_flet.components.snackbar import show_snackbar


def _page():
    shown = []
    page = SimpleNamespace(
        theme_mode=ft.ThemeMode.DARK,
        overlay=[],
        show_dialog=shown.append,
        update=lambda: (_ for _ in ()).throw(AssertionError("no page.update()")),
    )
    return page, shown


def test_shows_through_show_dialog_without_touching_the_overlay():
    page, shown = _page()

    bar = show_snackbar(page, "Saved")

    assert shown == [bar]
    assert isinstance(bar, ft.SnackBar)
    assert page.overlay == []


def test_error_tone_uses_the_error_colour():
    from dlss_updater.ui_flet.theme.colors import MD3Colors

    page, _ = _page()

    bar = show_snackbar(page, "Nope", tone="error", is_dark=True)

    assert bar.bgcolor == MD3Colors.get_error(True)


def test_no_page_is_a_no_op():
    assert show_snackbar(None, "x") is None


def test_a_snackbar_with_an_action_auto_dismisses_by_default():
    page, _ = _page()

    bar = show_snackbar(page, "Ignored", action="Undo", on_action=lambda e: None)

    assert bar.persist is False


def test_persist_can_be_forced_on():
    page, _ = _page()

    bar = show_snackbar(page, "Stay", action="OK", persist=True)

    assert bar.persist is True
