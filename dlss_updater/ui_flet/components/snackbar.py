"""
Shared snackbar helper.

Snackbars go through ``page.show_dialog()``. In Flet 1.0.x ``SnackBar`` is a
``DialogControl``, so this updates only the dialog stack (no full
``page.update()``) and removes the snackbar from the stack when it is
dismissed. The two patterns this replaces were both broken:

- ``page.snack_bar = ...`` - there is no such field in 1.0.x, so the write is
  silently dead and the snackbar never appears.
- ``page.overlay.append(snackbar); snackbar.open = True; page.update()`` -
  never removed, so every later ``page.update()`` re-diffs a growing overlay,
  and each call serializes the whole page.
"""

from typing import Literal

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors

SnackbarTone = Literal["info", "success", "error"]


def _tone_bgcolor(tone: SnackbarTone, is_dark: bool) -> str:
    if tone == "error":
        return MD3Colors.get_error(is_dark)
    if tone == "success":
        return MD3Colors.get_success(is_dark)
    return MD3Colors.get_themed("snackbar_bg", is_dark)


def show_snackbar(
    page: ft.Page | None,
    message: str,
    *,
    tone: SnackbarTone = "info",
    is_dark: bool | None = None,
    duration_ms: int = 3000,
    action: str | None = None,
    on_action=None,
    persist: bool | None = None,
) -> ft.SnackBar | None:
    """Show a transient message. Returns the SnackBar, or None if there is no page.

    Safe to call from any handler; it never calls ``page.update()`` itself and
    callers must not add one after it.

    ``persist`` defaults to False whenever an ``action`` is given: Flutter keeps
    a SnackBar that has an action on screen until it is dismissed, so an
    "Undo" snackbar would otherwise never go away on its own.
    """
    if page is None:
        return None
    if is_dark is None:
        is_dark = page.theme_mode != ft.ThemeMode.LIGHT
    snackbar = ft.SnackBar(
        content=ft.Text(message, color=ft.Colors.WHITE),
        bgcolor=_tone_bgcolor(tone, is_dark),
        duration=duration_ms,
        action=action,
        on_action=on_action,
        persist=persist if persist is not None else (False if action else None),
    )
    page.show_dialog(snackbar)
    return snackbar
