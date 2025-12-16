"""
Update Summary Dialog
Shows results after update completion
"""

import logging
import flet as ft

from dlss_updater.ui_flet.async_updater import UpdateResult


class UpdateSummaryDialog:
    """
    Dialog showing update results with tabs for different categories
    """

    def __init__(self, page: ft.Page, logger: logging.Logger, result: UpdateResult):
        self.page = page
        self.logger = logger
        self.result = result

    async def show(self):
        """Show the update summary dialog"""

        # Create tabs for different result categories
        tabs = []

        # Tab 1: Updated Games
        if self.result.updated_games:
            updated_content = ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=24),
                                ft.Text(
                                    f"{len(self.result.updated_games)} games updated successfully",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                ),
                            ],
                            spacing=8,
                        ),
                        padding=ft.padding.only(bottom=12),
                    ),
                    ft.Divider(),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text(game, size=14)
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
                                ft.Icon(ft.Icons.SKIP_NEXT, color=ft.Colors.ORANGE, size=24),
                                ft.Text(
                                    f"{len(self.result.skipped_games)} games skipped",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                ),
                            ],
                            spacing=8,
                        ),
                        padding=ft.padding.only(bottom=12),
                    ),
                    ft.Divider(),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text(game, size=14)
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
                                ft.Icon(ft.Icons.ERROR, color=ft.Colors.RED, size=24),
                                ft.Text(
                                    f"{len(self.result.errors)} errors occurred",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                ),
                            ],
                            spacing=8,
                        ),
                        padding=ft.padding.only(bottom=12),
                    ),
                    ft.Divider(),
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
                                            ),
                                            ft.Text(
                                                error.get("error", "Unknown error"),
                                                size=12,
                                                color=ft.Colors.GREY,
                                            ),
                                        ],
                                        spacing=4,
                                    ),
                                    padding=ft.padding.all(8),
                                    bgcolor="#3C3C3C",
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
                                ft.Icon(ft.Icons.INFO_OUTLINE, size=48, color=ft.Colors.GREY),
                                ft.Text("No games were processed", color=ft.Colors.GREY),
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
            summary_text.append(f"✓ {len(self.result.updated_games)} updated")
        if self.result.skipped_games:
            summary_text.append(f"⊘ {len(self.result.skipped_games)} skipped")
        if self.result.errors:
            summary_text.append(f"✗ {len(self.result.errors)} errors")

        summary_str = " • ".join(summary_text) if summary_text else "No changes made"

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Update Complete"),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(summary_str, size=14, color=ft.Colors.GREY),
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
            actions=[
                ft.TextButton("Close", on_click=lambda e: self.page.close(dialog)),
            ],
        )

        self.page.open(dialog)
