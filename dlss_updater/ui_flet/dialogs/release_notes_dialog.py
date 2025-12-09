"""
Release Notes Dialog
Displays application release notes
"""

import logging
from pathlib import Path
import flet as ft


class ReleaseNotesDialog:
    """
    Dialog for displaying release notes from release_notes.txt
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger

    async def show(self):
        """Show the release notes dialog"""

        # Load release notes from file
        try:
            release_notes_path = Path(__file__).parent.parent.parent.parent / "release_notes.txt"
            if release_notes_path.exists():
                with open(release_notes_path, "r", encoding="utf-8") as f:
                    notes_text = f.read()
            else:
                notes_text = "Release notes not found."
                self.logger.warning(f"Release notes file not found: {release_notes_path}")

        except Exception as e:
            notes_text = f"Error loading release notes: {e}"
            self.logger.error(f"Failed to load release notes: {e}", exc_info=True)

        # Create dialog
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Release Notes"),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Container(
                            content=ft.Text(
                                notes_text,
                                selectable=True,
                                size=13,
                            ),
                            bgcolor="#3C3C3C",
                            padding=ft.padding.all(16),
                            border_radius=4,
                        ),
                    ],
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=700,
                height=500,
            ),
            actions=[
                ft.TextButton("Close", on_click=lambda e: self.page.close(dialog)),
            ],
        )

        self.page.open(dialog)
