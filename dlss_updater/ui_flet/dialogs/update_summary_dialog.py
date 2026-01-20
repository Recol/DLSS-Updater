"""
Update Summary Dialog
Shows results after update completion
Theme-aware: responds to light/dark mode changes
"""

import logging
import flet as ft

from dlss_updater.ui_flet.async_updater import UpdateResult
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors


class UpdateSummaryDialog(ThemeAwareMixin):
    """
    Dialog showing update results with tabs for different categories.
    Theme-aware: responds to light/dark mode changes.
    """

    def __init__(self, page: ft.Page, logger: logging.Logger, result: UpdateResult):
        self.page = page
        self.logger = logger
        self.result = result

        # Theme registry setup
        self._registry = get_theme_registry()
        self._theme_priority = 70  # Dialogs are low priority (animate last)

        # Themed element references
        self._themed_elements: dict[str, ft.Control] = {}

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware updates."""
        return {}  # Dialog rebuilds on show, individual elements handle themes

    def _close_dialog(self, dialog, e=None):
        """Close dialog and unregister from theme system."""
        self._unregister_theme_aware()
        self.page.close(dialog)

    async def show(self):
        """Show the update summary dialog"""
        # Register for theme updates
        self._register_theme_aware()
        is_dark = self._registry.is_dark

        # Create tabs for different result categories
        tabs = []

        # Tab 1: Updated Games
        if self.result.updated_games:
            updated_content = ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.CHECK_CIRCLE, color=MD3Colors.get_success(is_dark), size=24),
                                ft.Text(
                                    f"{len(self.result.updated_games)} games updated successfully",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                    color=MD3Colors.get_text_primary(is_dark),
                                ),
                            ],
                            spacing=8,
                        ),
                        padding=ft.padding.only(bottom=12),
                    ),
                    ft.Divider(color=MD3Colors.get_divider(is_dark)),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text(game, size=14, color=MD3Colors.get_text_primary(is_dark))
                                for game in self.result.updated_games
                            ],
                            spacing=8,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                        height=300,
                    ),
                ],
                spacing=8,
            )
            tabs.append(
                ft.Tab(
                    text=f"Updated ({len(self.result.updated_games)})",
                    icon=ft.Icons.CHECK_CIRCLE,
                    content=ft.Container(content=updated_content, padding=16),
                )
            )

        # Tab 2: Skipped Games
        if self.result.skipped_games:
            skipped_content = ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.SKIP_NEXT, color=MD3Colors.get_warning(is_dark), size=24),
                                ft.Text(
                                    f"{len(self.result.skipped_games)} games skipped",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                    color=MD3Colors.get_text_primary(is_dark),
                                ),
                            ],
                            spacing=8,
                        ),
                        padding=ft.padding.only(bottom=12),
                    ),
                    ft.Divider(color=MD3Colors.get_divider(is_dark)),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text(game, size=14, color=MD3Colors.get_text_primary(is_dark))
                                for game in self.result.skipped_games
                            ],
                            spacing=8,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                        height=300,
                    ),
                ],
                spacing=8,
            )
            tabs.append(
                ft.Tab(
                    text=f"Skipped ({len(self.result.skipped_games)})",
                    icon=ft.Icons.SKIP_NEXT,
                    content=ft.Container(content=skipped_content, padding=16),
                )
            )

        # Tab 3: Errors
        if self.result.errors:
            error_content = ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.ERROR, color=MD3Colors.get_error(is_dark), size=24),
                                ft.Text(
                                    f"{len(self.result.errors)} errors occurred",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                    color=MD3Colors.get_text_primary(is_dark),
                                ),
                            ],
                            spacing=8,
                        ),
                        padding=ft.padding.only(bottom=12),
                    ),
                    ft.Divider(color=MD3Colors.get_divider(is_dark)),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Container(
                                    content=ft.Column(
                                        controls=[
                                            ft.Text(
                                                error.get("game", "Unknown"),
                                                size=14,
                                                weight=ft.FontWeight.BOLD,
                                                color=MD3Colors.get_text_primary(is_dark),
                                            ),
                                            ft.Text(
                                                error.get("error", "Unknown error"),
                                                size=12,
                                                color=MD3Colors.get_text_secondary(is_dark),
                                            ),
                                        ],
                                        spacing=4,
                                    ),
                                    padding=ft.padding.all(8),
                                    bgcolor=MD3Colors.get_surface_container(is_dark),
                                    border_radius=4,
                                )
                                for error in self.result.errors
                            ],
                            spacing=8,
                            scroll=ft.ScrollMode.AUTO,
                        ),
                        height=300,
                    ),
                ],
                spacing=8,
            )
            tabs.append(
                ft.Tab(
                    text=f"Errors ({len(self.result.errors)})",
                    icon=ft.Icons.ERROR,
                    content=ft.Container(content=error_content, padding=16),
                )
            )

        # If no results at all, show empty state
        if not tabs:
            tabs.append(
                ft.Tab(
                    text="Results",
                    icon=ft.Icons.INFO,
                    content=ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.INFO_OUTLINE, size=48, color=MD3Colors.get_text_secondary(is_dark)),
                                ft.Text("No games were processed", color=MD3Colors.get_text_secondary(is_dark)),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        padding=ft.padding.all(32),
                        height=300,
                    ),
                )
            )

        # Summary header
        summary_text = []
        if self.result.updated_games:
            summary_text.append(f"{len(self.result.updated_games)} updated")
        if self.result.skipped_games:
            summary_text.append(f"{len(self.result.skipped_games)} skipped")
        if self.result.errors:
            summary_text.append(f"{len(self.result.errors)} errors")

        summary_str = " | ".join(summary_text) if summary_text else "No changes made"

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Update Complete", color=MD3Colors.get_text_primary(is_dark)),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(summary_str, size=14, color=MD3Colors.get_text_secondary(is_dark)),
                        ft.Container(height=8),
                        ft.Tabs(
                            tabs=tabs,
                            selected_index=0,
                            animation_duration=200,
                        ),
                    ],
                    spacing=0,
                ),
                width=700,
                height=450,
            ),
            bgcolor=MD3Colors.get_surface(is_dark),
            actions=[
                ft.TextButton("Close", on_click=lambda e: self._close_dialog(dialog, e)),
            ],
        )

        self.page.open(dialog)
