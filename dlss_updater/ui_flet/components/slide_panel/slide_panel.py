"""
Slide panel component for DLSS Updater.
Provides a sliding panel from the right side with scrim overlay, animations, and content management.
"""

import asyncio
import anyio
import flet as ft
import logging
from typing import Optional

from .panel_content_base import PanelContentBase
from ...theme.colors import MD3Colors, Shadows, Animations
from ...theme.theme_aware import ThemeAwareMixin, get_theme_registry
from ..hero_surface import (
    build_brand_wash,
    build_watermark_icon,
    WATERMARK_OPACITY_DARK,
    WATERMARK_OPACITY_LIGHT,
)

# Chrome brand-wash opacity for the panel header — matches the app bar / logger
# panel's restrained wash (see main_view.py's _CHROME_WASH_OPACITY_DARK/LIGHT)
# so all of the app's "quiet chrome" surfaces read as one consistent system.
_HEADER_WASH_OPACITY_DARK = 0.10
_HEADER_WASH_OPACITY_LIGHT = 0.06


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
        self._page_ref = page
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
        self._header_watermark: Optional[ft.Container] = None
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
        max_viewport_width = int(self._page_ref.width * self.MAX_WIDTH_PERCENT)

        # Clamp between min/max, and respect viewport constraint
        width = max(self.MIN_WIDTH, min(content_width, self.MAX_WIDTH))
        width = min(width, max_viewport_width)

        return width

    def _build_header(self) -> ft.Container:
        """
        Build panel header with title, subtitle, and close button.

        The header carries a subtle brand-wash gradient (PRIMARY by default,
        or the content's own `accent` — e.g. NVIDIA green for the DLSS
        settings panel) plus a small, low-opacity watermark glyph from
        `content.icon`, echoing the hero card language used elsewhere.

        Returns:
            Container with header content
        """
        is_dark = self._registry.is_dark
        accent = self.content.accent or MD3Colors.PRIMARY

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

        # Small decorative watermark glyph, bottom-right, behind the row
        # content (mirrors hub_card.py's negative-offset "bleed" technique,
        # scaled down for the header band). Close button sits top-right, so
        # anchoring the glyph toward the bottom keeps the two from clashing.
        self._header_watermark: ft.Container | None = None
        header_content: ft.Control = header_row
        if self.content.icon:
            self._header_watermark = build_watermark_icon(
                self.content.icon, is_dark, size=48
            )
            self._header_watermark.right = -4
            self._header_watermark.bottom = -8
            header_content = ft.Stack(controls=[self._header_watermark, header_row])

        self._header_container = ft.Container(
            content=header_content,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            gradient=build_brand_wash(
                accent,
                is_dark,
                opacity=_HEADER_WASH_OPACITY_DARK if is_dark else _HEADER_WASH_OPACITY_LIGHT,
            ),
            padding=ft.Padding.only(left=24, right=16, top=20, bottom=16),
            border=ft.Border.only(
                bottom=ft.BorderSide(1, MD3Colors.get_divider(is_dark))
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
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
            content="Cancel",
            on_click=self._on_cancel_click,
            style=ft.ButtonStyle(
                color=MD3Colors.get_on_surface(is_dark),
            ),
        )

        # Save button
        self._save_btn = ft.FilledButton(
            content="Save",
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
            padding=ft.Padding.only(left=24, right=24, top=16, bottom=16),
            border=ft.Border.only(top=ft.BorderSide(1, MD3Colors.get_divider(is_dark))),
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

        # Header container: surface base + brand wash gradient (precomputed
        # for both themes here so the existing dict-driven apply_theme()
        # contract can set it directly — no custom apply_theme() override
        # needed even though a gradient isn't a simple color pair).
        if self._header_container:
            props["_header_container.bgcolor"] = MD3Colors.get_themed_pair("surface_variant")
            accent = self.content.accent or MD3Colors.PRIMARY
            props["_header_container.gradient"] = (
                build_brand_wash(accent, True, opacity=_HEADER_WASH_OPACITY_DARK),
                build_brand_wash(accent, False, opacity=_HEADER_WASH_OPACITY_LIGHT),
            )

        # Header watermark glyph opacity
        if self._header_watermark:
            props["_header_watermark.opacity"] = (
                WATERMARK_OPACITY_DARK,
                WATERMARK_OPACITY_LIGHT,
            )

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

        Optimized for Flet 0.80.4 performance:
        - Single page.update() to add overlay AND start animation
        - content.on_open() runs concurrently with animation (non-blocking)
        """
        if self._is_open:
            return

        self.logger.info(f"Opening slide panel: {self.content.title}")

        try:
            # Add to page overlay
            self._page_ref.overlay.append(self._stack_overlay)

            # Setup keyboard handler for ESC key
            self._on_keyboard_handler = self._handle_keyboard_event
            self._page_ref.on_keyboard_event = self._on_keyboard_handler

            # Set animation targets BEFORE the update
            # Animate scrim opacity 0 -> 1 (full opacity with rgba alpha)
            self._scrim_container.opacity = 1
            # Animate panel offset (1,0) -> (0,0) to slide in from right
            self._panel_container.offset = ft.Offset(0, 0)

            # Single update: adds overlay + triggers animations
            self._page_ref.update()

            # Run content.on_open() concurrently with animation (non-blocking)
            # This allows the panel to start animating while content initializes
            async def initialize_content():
                try:
                    await self.content.on_open()
                except Exception as e:
                    self.logger.warning(f"Content on_open error: {e}")

            # Start content initialization without waiting
            # Register for proper shutdown cancellation
            from dlss_updater.task_registry import register_task
            register_task(asyncio.create_task(initialize_content()), "panel_content_init")

            # Wait for animation to complete
            await anyio.sleep(self.OPEN_DURATION / 1000)

            self._is_open = True
            self.logger.info("Slide panel opened")
        except Exception as e:
            # Cleanup on failure to prevent overlay accumulation
            if self._stack_overlay in self._page_ref.overlay:
                self._page_ref.overlay.remove(self._stack_overlay)
            if self._page_ref.on_keyboard_event == self._on_keyboard_handler:
                self._page_ref.on_keyboard_event = None
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

            self._page_ref.update()

            # Wait for animation to complete
            await anyio.sleep(self.CLOSE_DURATION / 1000)

            # Call content on_close
            await self.content.on_close()

        finally:
            # Always cleanup overlay and handlers even if animation/close fails
            self._is_open = False

            # Remove from overlay
            if self._stack_overlay in self._page_ref.overlay:
                self._page_ref.overlay.remove(self._stack_overlay)

            # Remove keyboard handler
            if self._page_ref.on_keyboard_event == self._on_keyboard_handler:
                self._page_ref.on_keyboard_event = None

            # Unregister from theme system to allow garbage collection
            self._unregister_theme_aware()

            self._page_ref.update()
            self.logger.info("Slide panel closed")

    async def _handle_save(self) -> None:
        """Handle save button click."""
        self.logger.info("Save button clicked")

        # Validate content
        is_valid, error_message = self.content.validate()
        if not is_valid:
            self.logger.warning(f"Validation failed: {error_message}")
            # Show error snackbar
            self._page_ref.show_snack_bar(
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
        self._page_ref.run_task(self._handle_cancel)

    def _on_close_click(self, e) -> None:
        """Handle close button click."""
        self._page_ref.run_task(self._handle_cancel)

    def _on_cancel_click(self, e) -> None:
        """Handle cancel button click."""
        self._page_ref.run_task(self._handle_cancel)

    def _on_save_click(self, e) -> None:
        """Handle save button click."""
        self._page_ref.run_task(self._handle_save)

    def _handle_keyboard_event(self, e: ft.KeyboardEvent) -> None:
        """
        Handle keyboard events (ESC to close).

        Args:
            e: Keyboard event
        """
        if e.key == "Escape" and self._is_open:
            self.logger.info("ESC key pressed, closing panel")
            self._page_ref.run_task(self.hide)
