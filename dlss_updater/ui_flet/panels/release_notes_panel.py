"""
ReleaseNotesPanel - Display application release notes
Read-only panel showing release notes from release_notes.txt
"""

import logging
from pathlib import Path
import flet as ft
from dlss_updater.ui_flet.components.slide_panel import PanelContentBase


class ReleaseNotesPanel(PanelContentBase):
    """
    Panel for displaying application release notes.

    Features:
    - Loads release notes from release_notes.txt
    - Selectable text for easy copying
    - Scrollable content for long release notes
    - Read-only (no save action needed)
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        """
        Initialize release notes panel.

        Args:
            page: Flet Page instance
            logger: Logger instance for diagnostics
        """
        super().__init__(page, logger)
        self._notes_text: str = ""

    @property
    def title(self) -> str:
        """Panel title."""
        return "Release Notes"

    @property
    def subtitle(self) -> str | None:
        """Panel subtitle."""
        return None

    @property
    def width(self) -> int:
        """Panel width in pixels."""
        return 600

    def _load_notes(self) -> str:
        """
        Load release notes from file.

        Returns:
            Release notes text or error message
        """
        try:
            # Path relative to this file: panels -> ui_flet -> dlss_updater -> project root
            release_notes_path = Path(__file__).parent.parent.parent.parent / "release_notes.txt"
            if release_notes_path.exists():
                self._notes_text = release_notes_path.read_text(encoding="utf-8")
                self.logger.debug(f"Loaded release notes from {release_notes_path}")
            else:
                self._notes_text = "Release notes not found."
                self.logger.warning(f"Release notes file not found: {release_notes_path}")
        except Exception as e:
            self._notes_text = f"Error loading release notes: {e}"
            self.logger.error(f"Failed to load release notes: {e}", exc_info=True)

        return self._notes_text

    async def on_open(self):
        """Load release notes when panel opens."""
        self._load_notes()

    def build(self) -> ft.Control:
        """
        Build the release notes panel content.

        Returns:
            Container with scrollable release notes text
        """
        # Load notes if not already loaded
        if not self._notes_text:
            self._load_notes()

        return ft.Container(
            content=ft.Text(
                self._notes_text,
                selectable=True,
                size=13,
            ),
            bgcolor="#3C3C3C",
            padding=ft.padding.all(16),
            border_radius=4,
        )

    async def on_save(self) -> bool:
        """
        Save action for release notes panel.

        This is a read-only panel, so save just closes the panel.

        Returns:
            True (always succeeds since no save is needed)
        """
        # Read-only panel, just close
        return True
