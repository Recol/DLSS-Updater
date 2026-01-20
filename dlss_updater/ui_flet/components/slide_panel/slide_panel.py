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
from ...theme.theme_aware import ThemeAwareMixin, get_theme_registry


class SlidePanel(ThemeAwareMixin):
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

        # Theme support
        self._registry = get_theme_registry()
        self._theme_priority = 60  # Panels animate later in cascade

        # Store themed element references
        self._header_container: Optional[ft.Container] = None
        self._content_container: Optional[ft.Container] = None
        self._footer_container: Optional[ft.Container] = None
        self._title_text: Optional[ft.Text] = None
        self._subtitle_text: Optional[ft.Text] = None
        self._close_btn: Optional[ft.IconButton] = None
        self._cancel_btn: Optional[ft.OutlinedButton] = None
        self._save_btn: Optional[ft.FilledButton] = None

        # Build UI components
        self._build()

        # Register for theme updates
        self._register_theme_aware()

    def _build(self) -> None:
        """Build panel UI structure."""
        is_dark = self._registry.is_dark

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
            bgcolor=MD3Colors.get_themed("surface_bright", is_dark),
            shadow=Shadows.LEVEL_5,
            offset=ft.Offset(1, 0),  # Start off-screen (right)
            animate_offset=ft.Animation(
                self.OPEN_DURATION, ft.AnimationCurve.EASE_OUT_CUBIC
            ),
        )

        # Scrim overlay (semi-transparent background, click to dismiss)
        self._scrim_container = ft.Container(
            expand=True,
            bgcolor="rgba(0, 0, 0, 0.5)",
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
        is_dark = self._registry.is_dark

        # Title text
        self._title_text = ft.Text(
            self.content.title,
            size=20,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_on_surface(is_dark),
        )

        # Optional subtitle
        self._subtitle_text = None
        if self.content.subtitle:
            self._subtitle_text = ft.Text(
                self.content.subtitle,
                size=14,
                color=MD3Colors.get_on_surface_variant(is_dark),
            )

        # Title column (title + optional subtitle)
        title_column = ft.Column(
            controls=[self._title_text]
            + ([self._subtitle_text] if self._subtitle_text else []),
            spacing=4,
            expand=True,
        )

        # Close button
        self._close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_color=MD3Colors.get_on_surface(is_dark),
            tooltip="Close (ESC)",
            on_click=self._on_close_click,
        )

        # Header row
        header_row = ft.Row(
            controls=[title_column, self._close_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        self._header_container = ft.Container(
            content=header_row,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            padding=ft.padding.only(left=24, right=16, top=20, bottom=16),
            border=ft.border.only(
                bottom=ft.BorderSide(1, MD3Colors.get_divider(is_dark))
            ),
        )
        return self._header_container

    def _build_content_area(self) -> ft.Container:
        """
        Build scrollable content area.

        Returns:
            Container with scrollable content
        """
        is_dark = self._registry.is_dark

        # Get content from implementation
        content_control = self.content.build()

        # Wrap in column for consistent padding
        content_column = ft.Column(
            controls=[content_control],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        self._content_container = ft.Container(
            content=content_column,
            bgcolor=MD3Colors.get_surface(is_dark),
            padding=24,
            expand=True,
        )
        return self._content_container

    def _build_footer(self) -> ft.Container:
        """
        Build footer with Cancel and Save buttons.

        Returns:
            Container with footer buttons
        """
        is_dark = self._registry.is_dark

        # Cancel button
        self._cancel_btn = ft.OutlinedButton(
            text="Cancel",
            on_click=self._on_cancel_click,
            style=ft.ButtonStyle(
                color=MD3Colors.get_on_surface(is_dark),
            ),
        )

        # Save button
        self._save_btn = ft.FilledButton(
            text="Save",
            on_click=self._on_save_click,
            style=ft.ButtonStyle(
                bgcolor=MD3Colors.get_primary(is_dark),
                color=MD3Colors.ON_PRIMARY,
            ),
        )

        # Button row (right-aligned)
        button_row = ft.Row(
            controls=[self._cancel_btn, self._save_btn],
            alignment=ft.MainAxisAlignment.END,
            spacing=12,
        )

        self._footer_container = ft.Container(
            content=button_row,
            height=self.FOOTER_HEIGHT,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            padding=ft.padding.only(left=24, right=24, top=16, bottom=16),
            border=ft.border.only(top=ft.BorderSide(1, MD3Colors.get_divider(is_dark))),
        )
        return self._footer_container

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """
        Return themed property mappings for cascade animation.

        Returns:
            Dict mapping property paths to (dark_value, light_value) tuples.
        """
        props = {}

        # Panel container surface
        if self._panel_container:
            props["_panel_container.bgcolor"] = MD3Colors.get_themed_pair("surface_bright")

        # Header container
        if self._header_container:
            props["_header_container.bgcolor"] = MD3Colors.get_themed_pair("surface_variant")

        # Content container
        if self._content_container:
            props["_content_container.bgcolor"] = MD3Colors.get_themed_pair("surface")

        # Footer container
        if self._footer_container:
            props["_footer_container.bgcolor"] = MD3Colors.get_themed_pair("surface_variant")

        # Title text
        if self._title_text:
            props["_title_text.color"] = MD3Colors.get_themed_pair("on_surface")

        # Subtitle text
        if self._subtitle_text:
            props["_subtitle_text.color"] = MD3Colors.get_themed_pair("on_surface_variant")

        # Close button
        if self._close_btn:
            props["_close_btn.icon_color"] = MD3Colors.get_themed_pair("on_surface")

        return props

    async def show(self) -> None:
        """
        Show the panel with animation.

        Adds panel to page overlay and animates in from right.
        """
        if self._is_open:
            return

        self.logger.info(f"Opening slide panel: {self.content.title}")

        try:
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
        except Exception as e:
            # Cleanup on failure to prevent overlay accumulation
            if self._stack_overlay in self.page.overlay:
                self.page.overlay.remove(self._stack_overlay)
            if self.page.on_keyboard_event == self._on_keyboard_handler:
                self.page.on_keyboard_event = None
            self.logger.error(f"Failed to show slide panel: {e}")
            raise

    async def hide(self) -> None:
        """
        Hide the panel with animation.

        Animates panel out to right and removes from overlay.
        Uses try-finally to ensure cleanup even if animation/close fails.
        """
        if not self._is_open:
            return

        self.logger.info("Closing slide panel")

        try:
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

        finally:
            # Always cleanup overlay and handlers even if animation/close fails
            self._is_open = False

            # Remove from overlay
            if self._stack_overlay in self.page.overlay:
                self.page.overlay.remove(self._stack_overlay)

            # Remove keyboard handler
            if self.page.on_keyboard_event == self._on_keyboard_handler:
                self.page.on_keyboard_event = None

            # Unregister from theme system to allow garbage collection
            self._unregister_theme_aware()

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
