"""
Slide panel component for DLSS Updater.
Provides a sliding panel from the right side with scrim overlay, animations, and content management.
"""

import asyncio
import flet as ft
import logging
from typing import Optional

from .panel_content_base import PanelContentBase
from ...theme.colors import MD3Colors, Shadows, Animations


class SlidePanel:
    """
    Slide panel that animates from the right side of the screen.

    Features:
    - Slides from right with smooth animations
    - Semi-transparent scrim overlay (click to dismiss)
    - Header with title, subtitle, and close button
    - Scrollable content area
    - Footer with Cancel/Save buttons
    - ESC key to dismiss
    - Responsive width clamping (400-600px, max 90% viewport)
    """

    # Animation timing (milliseconds)
    OPEN_DURATION = 300
    CLOSE_DURATION = 250

    # Color constants
    COLOR_PANEL_SURFACE = "#3A3A3A"
    COLOR_HEADER_FOOTER = "#2E2E2E"
    COLOR_CONTENT_BG = "#1E1E1E"
    COLOR_SCRIM = "rgba(0, 0, 0, 0.5)"

    # Layout constants
    HEADER_HEIGHT = 80  # Enough for title + subtitle + padding
    FOOTER_HEIGHT = 72
    MIN_WIDTH = 400
    MAX_WIDTH = 600
    MAX_WIDTH_PERCENT = 0.9

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        content: PanelContentBase,
    ):
        """
        Initialize slide panel.

        Args:
            page: Flet page instance
            logger: Logger for panel operations
            content: Panel content implementation
        """
        self.page = page
        self.logger = logger
        self.content = content
        self._is_open = False
        self._stack_overlay: Optional[ft.Stack] = None
        self._panel_container: Optional[ft.Container] = None
        self._scrim_container: Optional[ft.Container] = None
        self._on_keyboard_handler = None

        # Build UI components
        self._build()

    def _build(self) -> None:
        """Build panel UI structure."""
        # Calculate responsive panel width
        panel_width = self._calculate_panel_width()

        # Build header
        header = self._build_header()

        # Build content area (scrollable)
        content_area = self._build_content_area()

        # Build footer
        footer = self._build_footer()

        # Panel container with all sections
        panel_column = ft.Column(
            controls=[header, content_area, footer],
            spacing=0,
            expand=True,
        )

        # Panel container with offset for animation (starts off-screen to the right)
        self._panel_container = ft.Container(
            content=panel_column,
            width=panel_width,
            bgcolor=self.COLOR_PANEL_SURFACE,
            shadow=Shadows.LEVEL_5,
            offset=ft.Offset(1, 0),  # Start off-screen (right)
            animate_offset=ft.Animation(
                self.OPEN_DURATION, ft.AnimationCurve.EASE_OUT_CUBIC
            ),
        )

        # Scrim overlay (semi-transparent background, click to dismiss)
        self._scrim_container = ft.Container(
            expand=True,
            bgcolor=self.COLOR_SCRIM,
            opacity=0,  # Start invisible
            animate_opacity=ft.Animation(
                self.OPEN_DURATION, ft.AnimationCurve.EASE_OUT
            ),
            on_click=self._on_scrim_click,
        )

        # Stack with scrim + panel
        self._stack_overlay = ft.Stack(
            controls=[
                self._scrim_container,
                ft.Row(
                    controls=[self._panel_container],
                    alignment=ft.MainAxisAlignment.END,
                    expand=True,
                ),
            ],
            expand=True,
        )

    def _calculate_panel_width(self) -> int:
        """
        Calculate responsive panel width.

        Returns:
            Panel width clamped between MIN_WIDTH and MAX_WIDTH,
            but not exceeding MAX_WIDTH_PERCENT of viewport
        """
        content_width = self.content.width
        max_viewport_width = int(self.page.width * self.MAX_WIDTH_PERCENT)

        # Clamp between min/max, and respect viewport constraint
        width = max(self.MIN_WIDTH, min(content_width, self.MAX_WIDTH))
        width = min(width, max_viewport_width)

        return width

    def _build_header(self) -> ft.Container:
        """
        Build panel header with title, subtitle, and close button.

        Returns:
            Container with header content
        """
        # Title text
        title_text = ft.Text(
            self.content.title,
            size=20,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.ON_SURFACE,
        )

        # Optional subtitle
        subtitle_control = None
        if self.content.subtitle:
            subtitle_control = ft.Text(
                self.content.subtitle,
                size=14,
                color=MD3Colors.ON_SURFACE_VARIANT,
            )

        # Title column (title + optional subtitle)
        title_column = ft.Column(
            controls=[title_text]
            + ([subtitle_control] if subtitle_control else []),
            spacing=4,
            expand=True,
        )

        # Close button
        close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_color=MD3Colors.ON_SURFACE,
            tooltip="Close (ESC)",
            on_click=self._on_close_click,
        )

        # Header row
        header_row = ft.Row(
            controls=[title_column, close_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        return ft.Container(
            content=header_row,
            bgcolor=self.COLOR_HEADER_FOOTER,
            padding=ft.padding.only(left=24, right=16, top=20, bottom=16),
            border=ft.border.only(
                bottom=ft.BorderSide(1, MD3Colors.OUTLINE_VARIANT)
            ),
        )

    def _build_content_area(self) -> ft.Container:
        """
        Build scrollable content area.

        Returns:
            Container with scrollable content
        """
        # Get content from implementation
        content_control = self.content.build()

        # Wrap in column for consistent padding
        content_column = ft.Column(
            controls=[content_control],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        return ft.Container(
            content=content_column,
            bgcolor=self.COLOR_CONTENT_BG,
            padding=24,
            expand=True,
        )

    def _build_footer(self) -> ft.Container:
        """
        Build footer with Cancel and Save buttons.

        Returns:
            Container with footer buttons
        """
        # Cancel button
        cancel_btn = ft.OutlinedButton(
            text="Cancel",
            on_click=self._on_cancel_click,
            style=ft.ButtonStyle(
                color=MD3Colors.ON_SURFACE,
            ),
        )

        # Save button
        save_btn = ft.FilledButton(
            text="Save",
            on_click=self._on_save_click,
            style=ft.ButtonStyle(
                bgcolor=MD3Colors.PRIMARY,
                color=MD3Colors.ON_PRIMARY,
            ),
        )

        # Button row (right-aligned)
        button_row = ft.Row(
            controls=[cancel_btn, save_btn],
            alignment=ft.MainAxisAlignment.END,
            spacing=12,
        )

        return ft.Container(
            content=button_row,
            height=self.FOOTER_HEIGHT,
            bgcolor=self.COLOR_HEADER_FOOTER,
            padding=ft.padding.only(left=24, right=24, top=16, bottom=16),
            border=ft.border.only(top=ft.BorderSide(1, MD3Colors.OUTLINE_VARIANT)),
        )

    async def show(self) -> None:
        """
        Show the panel with animation.

        Adds panel to page overlay and animates in from right.
        """
        if self._is_open:
            return

        self.logger.info(f"Opening slide panel: {self.content.title}")

        # Add to page overlay
        self.page.overlay.append(self._stack_overlay)

        # Setup keyboard handler for ESC key
        self._on_keyboard_handler = self._handle_keyboard_event
        self.page.on_keyboard_event = self._on_keyboard_handler

        self.page.update()

        # Call content on_open
        await self.content.on_open()

        # Animate scrim opacity 0 -> 0.5 (full opacity with rgba alpha)
        self._scrim_container.opacity = 1
        # Animate panel offset (1,0) -> (0,0) to slide in from right
        self._panel_container.offset = ft.Offset(0, 0)

        self.page.update()

        # Wait for animation to complete
        await asyncio.sleep(self.OPEN_DURATION / 1000)

        self._is_open = True
        self.logger.info("Slide panel opened")

    async def hide(self) -> None:
        """
        Hide the panel with animation.

        Animates panel out to right and removes from overlay.
        """
        if not self._is_open:
            return

        self.logger.info("Closing slide panel")

        self._is_open = False

        # Update animation timing for close
        self._scrim_container.animate_opacity = ft.Animation(
            self.CLOSE_DURATION, ft.AnimationCurve.EASE_IN_CUBIC
        )
        self._panel_container.animate_offset = ft.Animation(
            self.CLOSE_DURATION, ft.AnimationCurve.EASE_IN_CUBIC
        )

        # Animate out
        self._scrim_container.opacity = 0
        self._panel_container.offset = ft.Offset(1, 0)

        self.page.update()

        # Wait for animation to complete
        await asyncio.sleep(self.CLOSE_DURATION / 1000)

        # Call content on_close
        await self.content.on_close()

        # Remove from overlay
        if self._stack_overlay in self.page.overlay:
            self.page.overlay.remove(self._stack_overlay)

        # Remove keyboard handler
        if self.page.on_keyboard_event == self._on_keyboard_handler:
            self.page.on_keyboard_event = None

        self.page.update()
        self.logger.info("Slide panel closed")

    async def _handle_save(self) -> None:
        """Handle save button click."""
        self.logger.info("Save button clicked")

        # Validate content
        is_valid, error_message = self.content.validate()
        if not is_valid:
            self.logger.warning(f"Validation failed: {error_message}")
            # Show error snackbar
            self.page.show_snack_bar(
                ft.SnackBar(
                    content=ft.Text(error_message or "Validation failed"),
                    bgcolor=MD3Colors.ERROR,
                )
            )
            return

        # Call content save handler
        success = await self.content.on_save()

        if success:
            self.logger.info("Save successful")
            # Close panel
            await self.hide()
        else:
            self.logger.warning("Save failed")
            # Error handling is expected to be done by content.on_save()

    async def _handle_cancel(self) -> None:
        """Handle cancel button click or scrim click."""
        self.logger.info("Cancel/close requested")
        await self.hide()

    def _on_scrim_click(self, e) -> None:
        """Handle scrim click - close panel."""
        self.page.run_task(self._handle_cancel)

    def _on_close_click(self, e) -> None:
        """Handle close button click."""
        self.page.run_task(self._handle_cancel)

    def _on_cancel_click(self, e) -> None:
        """Handle cancel button click."""
        self.page.run_task(self._handle_cancel)

    def _on_save_click(self, e) -> None:
        """Handle save button click."""
        self.page.run_task(self._handle_save)

    def _handle_keyboard_event(self, e: ft.KeyboardEvent) -> None:
        """
        Handle keyboard events (ESC to close).

        Args:
            e: Keyboard event
        """
        if e.key == "Escape" and self._is_open:
            self.logger.info("ESC key pressed, closing panel")
            self.page.run_task(self.hide)
