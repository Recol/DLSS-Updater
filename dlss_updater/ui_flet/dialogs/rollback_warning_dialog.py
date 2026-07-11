"""
Rollback Warning Dialog
Shows before an update when the target DLL version is one the user has rolled
back from in >=2 other games recently — a signal that the target may be
problematic for this user's game library.

Three outcomes:
- 'skip' — proceed with the update, but skip flagged DLLs
- 'proceed' — proceed with the update including flagged DLLs
- 'cancel' — abort the update entirely
"""

import anyio
import logging

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import get_theme_registry


class RollbackWarningDialog:
    """
    Warning dialog shown pre-update when the user has rolled back from the
    target DLL version in other games recently.

    Attributes:
        flagged: List of dicts with keys:
            - dll_filename (str)
            - target_version (str)
            - affected_games (list[str])
            - from_versions (list[str])
    """

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        game_name: str,
        flagged: list[dict],
    ):
        self._page_ref = page
        self.logger = logger
        self.game_name = game_name
        self.flagged = flagged
        self._registry = get_theme_registry()
        self._result: str = "cancel"
        self._close_event: anyio.Event | None = None
        self._dialog: ft.AlertDialog | None = None

    def _build_flagged_list(self, is_dark: bool) -> ft.Control:
        rows = []
        warning_color = MD3Colors.get_warning(is_dark)
        text_primary = MD3Colors.get_text_primary(is_dark)
        text_secondary = MD3Colors.get_text_secondary(is_dark)
        surface = MD3Colors.get_surface(is_dark)

        for entry in self.flagged:
            dll = entry["dll_filename"]
            version = entry["target_version"]
            affected = entry.get("affected_games", [])
            event_count = entry.get("event_count", len(affected))
            game_count = len(affected)

            # Cap the games list at 3 + "and N more"
            display_games = affected[:3]
            if game_count > 3:
                games_text = ", ".join(display_games) + f" and {game_count - 3} more"
            else:
                games_text = ", ".join(display_games) if display_games else "—"

            rows.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.WARNING_AMBER, size=18, color=warning_color),
                                    ft.Text(
                                        f"{dll}",
                                        size=13,
                                        weight=ft.FontWeight.BOLD,
                                        color=text_primary,
                                    ),
                                    ft.Container(
                                        content=ft.Text(
                                            f"v{version}",
                                            size=11,
                                            color=ft.Colors.WHITE,
                                            weight=ft.FontWeight.W_500,
                                        ),
                                        bgcolor=warning_color,
                                        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                        border_radius=10,
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Text(
                                f"You've rolled back from v{version} {event_count} time{'s' if event_count != 1 else ''}"
                                f" across {game_count} game{'s' if game_count != 1 else ''}: {games_text}",
                                size=12,
                                color=text_secondary,
                            ),
                        ],
                        spacing=4,
                        tight=True,
                    ),
                    padding=ft.Padding.all(10),
                    bgcolor=surface,
                    border=ft.Border.all(1, warning_color),
                    border_radius=6,
                )
            )

        return ft.Column(controls=rows, spacing=8, tight=True)

    async def show(self) -> str:
        """Show the dialog and return 'skip', 'proceed', or 'cancel'."""
        self._result = "cancel"
        self._close_event = anyio.Event()
        is_dark = self._registry.is_dark

        async def _set_result(result: str):
            self._result = result
            self._page_ref.pop_dialog()
            self._close_event.set()

        async def on_cancel(e):
            await _set_result("cancel")

        async def on_skip(e):
            await _set_result("skip")

        async def on_proceed(e):
            await _set_result("proceed")

        warning_color = MD3Colors.get_warning(is_dark)
        text_primary = MD3Colors.get_text_primary(is_dark)
        text_secondary = MD3Colors.get_text_secondary(is_dark)

        num_flagged = len(self.flagged)
        headline = (
            f"{num_flagged} DLL{'s' if num_flagged != 1 else ''} flagged based on your history"
        )
        explanation = (
            "You have rolled back from these exact versions in multiple other games recently. "
            "That pattern suggests they may cause issues — but the decision is yours."
        )

        content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        headline,
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color=text_primary,
                    ),
                    ft.Text(
                        explanation,
                        size=12,
                        color=text_secondary,
                    ),
                    ft.Container(height=4),
                    self._build_flagged_list(is_dark),
                ],
                spacing=8,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=520,
            height=min(420, 120 + 80 * num_flagged),
            padding=4,
        )

        self._dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.REPORT_PROBLEM, color=warning_color, size=22),
                    ft.Text(f"Possible compatibility issue — {self.game_name}"),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            content=content,
            bgcolor=MD3Colors.get_surface(is_dark),
            actions=[
                ft.TextButton("Cancel", on_click=on_cancel),
                ft.TextButton("Skip flagged DLLs", on_click=on_skip),
                ft.FilledButton(
                    "Update anyway",
                    on_click=on_proceed,
                    style=ft.ButtonStyle(bgcolor=warning_color),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        self._page_ref.show_dialog(self._dialog)
        await self._close_event.wait()
        return self._result
