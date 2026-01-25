"""
High Performance Mode Warning Dialog
Shows warning and system info when user enables high-performance mode
"""

import asyncio
import logging
from typing import Callable, Any

import flet as ft
import psutil


class HighPerfWarningDialog:
    """
    Warning dialog shown when enabling High Performance Mode.

    Displays current RAM usage, feature explanations, and warnings
    about temporary memory increase during updates.
    """

    # Color scheme constants
    PRIMARY_BLUE = "#2D6E88"
    WARNING_ORANGE = ft.Colors.ORANGE
    INFO_BOX_BG = "#3C3C3C"
    WARNING_BOX_BG = "#4A3415"
    SUCCESS_GREEN = "#4CAF50"

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self._page_ref = page
        self.logger = logger
        self._confirmed = False
        self._dialog: ft.AlertDialog | None = None
        self._close_event: asyncio.Event | None = None

    def _get_memory_stats(self) -> tuple[float, float, float]:
        """
        Get current memory statistics.

        Returns:
            Tuple of (available_gb, total_gb, percent_used)
        """
        mem = psutil.virtual_memory()
        available_gb = mem.available / (1024 ** 3)
        total_gb = mem.total / (1024 ** 3)
        percent_used = mem.percent
        return available_gb, total_gb, percent_used

    def _build_memory_status_row(
        self, available_gb: float, total_gb: float, percent_used: float
    ) -> ft.Container:
        """Build the memory status display row."""
        # Determine status based on memory usage
        if percent_used < 80:
            status_icon = ft.Icons.CHECK_CIRCLE
            status_color = self.SUCCESS_GREEN
            status_text = "Sufficient memory for high-performance mode"
        else:
            status_icon = ft.Icons.WARNING
            status_color = self.WARNING_ORANGE
            status_text = "Memory usage is high - may fall back to standard mode"

        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(status_icon, color=status_color, size=20),
                            ft.Text(
                                f"Available: {available_gb:.1f} GB of {total_gb:.1f} GB ({percent_used:.0f}% used)",
                                size=14,
                                weight=ft.FontWeight.W_500,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        status_text,
                        size=12,
                        color=status_color,
                        italic=True,
                    ),
                ],
                spacing=4,
            ),
            bgcolor=self.INFO_BOX_BG,
            padding=ft.padding.all(12),
            border_radius=6,
        )

    def _build_feature_list(self) -> ft.Container:
        """Build the feature explanation list."""
        features = [
            (ft.Icons.MEMORY, "Loading source DLLs into RAM for faster access"),
            (ft.Icons.BACKUP, "Creating all backups before any updates begin"),
            (ft.Icons.SYNC, "Updating multiple files simultaneously"),
        ]

        feature_tiles = []
        for icon, description in features:
            feature_tiles.append(
                ft.ListTile(
                    leading=ft.Icon(icon, color=self.PRIMARY_BLUE, size=20),
                    title=ft.Text(description, size=13),
                    dense=True,
                    content_padding=ft.padding.symmetric(horizontal=0, vertical=0),
                )
            )

        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "This mode enables:",
                        size=13,
                        weight=ft.FontWeight.W_500,
                        color=ft.Colors.GREY_400,
                    ),
                    *feature_tiles,
                ],
                spacing=0,
                tight=True,
            ),
            padding=ft.padding.only(top=8, bottom=8),
        )

    def _build_warning_box(self) -> ft.Container:
        """Build the warning information box."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, color=self.WARNING_ORANGE, size=18),
                            ft.Text(
                                "Important",
                                size=13,
                                weight=ft.FontWeight.BOLD,
                                color=self.WARNING_ORANGE,
                            ),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        "During updates, RAM usage may temporarily increase by 50-150 MB",
                        size=12,
                    ),
                    ft.Text(
                        "If memory becomes low, the system will automatically fall back to standard mode",
                        size=12,
                        color=ft.Colors.GREY_400,
                    ),
                ],
                spacing=6,
            ),
            bgcolor=self.WARNING_BOX_BG,
            padding=ft.padding.all(12),
            border_radius=6,
            border=ft.border.all(1, self.WARNING_ORANGE),
        )

    def _build_recommendation(self) -> ft.Container:
        """Build the recommendation text."""
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LIGHTBULB_OUTLINE, color=ft.Colors.GREY_500, size=16),
                    ft.Text(
                        "Recommended for: Systems with 8+ GB RAM",
                        size=12,
                        color=ft.Colors.GREY_500,
                        italic=True,
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.only(top=4),
        )

    async def show(self) -> bool:
        """
        Show the dialog and return True if user confirmed.

        Returns:
            True if user clicked "Enable", False for "Cancel" or close
        """
        self._confirmed = False
        self._close_event = asyncio.Event()

        # Get current memory stats
        available_gb, total_gb, percent_used = self._get_memory_stats()
        self.logger.debug(
            f"High perf dialog: RAM {available_gb:.1f}GB available of {total_gb:.1f}GB ({percent_used:.0f}% used)"
        )

        async def on_cancel(e):
            """Handle cancel button click."""
            self._confirmed = False
            self._page_ref.pop_dialog()
            self._close_event.set()

        async def on_enable(e):
            """Handle enable button click."""
            self._confirmed = True
            self.logger.info("User enabled High Performance Mode")
            self._page_ref.pop_dialog()
            self._close_event.set()

        # Build dialog content
        content = ft.Column(
            controls=[
                # Memory status
                self._build_memory_status_row(available_gb, total_gb, percent_used),
                # Feature list
                self._build_feature_list(),
                # Warning box
                self._build_warning_box(),
                # Recommendation
                self._build_recommendation(),
            ],
            spacing=12,
            tight=True,
        )

        # Create dialog with title including warning icon
        self._dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SPEED, color=self.WARNING_ORANGE, size=24),
                    ft.Text("Enable High Performance Updates?"),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            content=ft.Container(
                content=content,
                width=500,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=on_cancel),
                ft.FilledButton(
                    "Enable",
                    on_click=on_enable,
                    style=ft.ButtonStyle(
                        bgcolor=self.PRIMARY_BLUE,
                    ),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        # Show dialog
        self._page_ref.show_dialog(self._dialog)

        # Wait for user response
        await self._close_event.wait()

        return self._confirmed
