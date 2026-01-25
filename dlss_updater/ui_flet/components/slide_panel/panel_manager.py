"""
Panel manager for slide panels.
Singleton that manages panel lifecycle and ensures only one panel is open at a time.
"""

import asyncio
import logging
from typing import Optional

import flet as ft

from .panel_content_base import PanelContentBase
from .slide_panel import SlidePanel


class PanelManager:
    """
    Singleton manager for slide panels.

    Ensures only one panel is open at a time and provides
    centralized panel lifecycle management.

    Usage:
        # Get manager instance
        manager = PanelManager.get_instance(page)

        # Show a panel
        content = MyPanelContent(page, logger)
        await manager.show_content(content, logger)

        # Close current panel
        await manager.close_panel()

        # Check if panel is open
        if manager.is_panel_open:
            print("Panel is open")
    """

    _instance: Optional["PanelManager"] = None
    _logger: Optional[logging.Logger] = None

    @classmethod
    def get_instance(
        cls,
        page: ft.Page,
        logger: Optional[logging.Logger] = None,
    ) -> "PanelManager":
        """
        Get or create singleton instance.

        Args:
            page: Flet page instance
            logger: Optional logger (only used on first initialization)

        Returns:
            Singleton PanelManager instance
        """
        if cls._instance is None:
            # Create logger if not provided
            if logger is None:
                logger = logging.getLogger(__name__)

            cls._instance = cls(page, logger)
            cls._logger = logger

        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """
        Reset singleton instance (useful for testing).
        """
        cls._instance = None
        cls._logger = None

    def __init__(self, page: ft.Page, logger: logging.Logger):
        """
        Initialize panel manager.

        Note: Use get_instance() instead of direct instantiation.

        Args:
            page: Flet page instance
            logger: Logger for panel operations
        """
        if PanelManager._instance is not None:
            raise RuntimeError(
                "PanelManager is a singleton. Use get_instance() instead."
            )

        self._page_ref = page
        self.logger = logger
        self._current_panel: Optional[SlidePanel] = None
        self._lock = asyncio.Lock()

        self.logger.info("PanelManager initialized")

    async def show_content(
        self,
        content: PanelContentBase,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Show panel with given content.

        If a panel is already open, it will be closed first.

        Args:
            content: Panel content implementation
            logger: Optional logger for the panel (defaults to manager logger)
        """
        async with self._lock:
            # Use provided logger or fall back to manager logger
            panel_logger = logger or self.logger

            # Close existing panel if open
            if self._current_panel is not None:
                self.logger.info(
                    f"Closing existing panel before opening: {content.title}"
                )
                await self._current_panel.hide()
                self._current_panel = None

            # Create and show new panel
            self.logger.info(f"Showing panel: {content.title}")
            self._current_panel = SlidePanel(
                self._page_ref,
                panel_logger,
                content,
            )
            await self._current_panel.show()

    async def close_panel(self) -> None:
        """
        Close the currently open panel.

        Does nothing if no panel is open.
        """
        async with self._lock:
            if self._current_panel is not None:
                self.logger.info("Closing current panel")
                await self._current_panel.hide()
                self._current_panel = None
            else:
                self.logger.debug("No panel to close")

    @property
    def is_panel_open(self) -> bool:
        """
        Check if a panel is currently open.

        Returns:
            True if panel is open, False otherwise
        """
        return (
            self._current_panel is not None
            and self._current_panel._is_open
        )

    @property
    def current_panel_title(self) -> Optional[str]:
        """
        Get title of currently open panel.

        Returns:
            Panel title if open, None otherwise
        """
        if self._current_panel is not None:
            return self._current_panel.content.title
        return None

    async def toggle_panel(
        self,
        content: PanelContentBase,
        logger: Optional[logging.Logger] = None,
    ) -> bool:
        """
        Toggle panel: close if same content is open, otherwise show new content.

        Args:
            content: Panel content to show
            logger: Optional logger for the panel

        Returns:
            True if panel was opened, False if it was closed
        """
        async with self._lock:
            # If same panel is open, close it
            if (
                self._current_panel is not None
                and self._current_panel.content.title == content.title
            ):
                self.logger.info(
                    f"Toggling off panel: {content.title}"
                )
                await self._current_panel.hide()
                self._current_panel = None
                return False

            # Otherwise, show the panel (closes existing if needed)
            # Release lock temporarily for show_content
            pass

        # Use show_content (it handles its own locking)
        await self.show_content(content, logger)
        return True
