"""
Loading Overlay Component
Semi-transparent overlay with progress indicator
"""

import asyncio
import flet as ft

from dlss_updater.ui_flet.theme.colors import Shadows, MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


class LoadingOverlay(ThemeAwareMixin, ft.Container):
    """
    Full-screen loading overlay with progress indicator
    Similar to PyQt6's LoadingOverlay but using Flet components
    Supports light/dark theme
    """

    def __init__(self, page: ft.Page = None):
        super().__init__()

        # State
        self._is_showing = False  # Track visibility state (don't shadow ft.Container.visible)
        self._progress_value = 0
        self._page_ref = page
        self._registry = get_theme_registry()
        self._theme_priority = 40  # Utility components are mid-low priority

        # Get theme preference from registry
        is_dark = self._registry.is_dark

        # Progress ring with breathing animation (500ms for responsiveness per MD3 guidelines)
        self.progress_ring = ft.ProgressRing(
            width=60,
            height=60,
            stroke_width=4,
            color=MD3Colors.get_primary(is_dark),
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            animate_scale=ft.Animation(500, ft.AnimationCurve.EASE_IN_OUT),
        )

        # Status text
        self.status_text = ft.Text(
            "Processing...",
            size=16,
            color=MD3Colors.get_text_primary(is_dark),
            text_align=ft.TextAlign.CENTER,
            animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
        )

        # Progress percentage text
        self.progress_text = ft.Text(
            "0%",
            size=24,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
            text_align=ft.TextAlign.CENTER,
        )

        # Progress bar with gradient (for determinate progress)
        self.progress_bar = ft.ProgressBar(
            width=300,
            height=4,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            color=MD3Colors.get_primary(is_dark),
            value=0,
        )

        # Content container with glassmorphism effect
        # In dark mode use dark bg, in light mode use light bg with subtle transparency
        content_bg = "rgba(46, 46, 46, 0.95)" if is_dark else "rgba(255, 255, 255, 0.95)"
        border_color = "rgba(255, 255, 255, 0.1)" if is_dark else "rgba(0, 0, 0, 0.1)"

        self.content_container = ft.Container(
            content=ft.Column(
                controls=[
                    self.progress_ring,
                    ft.Container(height=16),
                    self.progress_text,
                    ft.Container(height=8),
                    self.progress_bar,
                    ft.Container(height=16),
                    self.status_text,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=content_bg,
            border_radius=16,
            padding=ft.padding.all(40),
            border=ft.border.all(1, border_color),
            shadow=Shadows.LEVEL_5,
        )

        # Overlay styling
        self.content = ft.Container(
            content=self.content_container,
            alignment=ft.Alignment.CENTER,
            expand=True,
        )
        overlay_bg = ft.Colors.with_opacity(0.7, ft.Colors.BLACK) if is_dark else ft.Colors.with_opacity(0.5, ft.Colors.BLACK)
        self.bgcolor = overlay_bg
        self.expand = True
        # Note: Don't add to page.overlay here - add dynamically in show()

        # Register for theme updates
        self._register_theme_aware()

    def show(self, page: ft.Page, message: str = "Processing..."):
        """Show the loading overlay by adding to page.overlay"""
        self.status_text.value = message
        self._progress_value = 0
        self.progress_bar.value = 0
        self.progress_text.value = "0%"
        # Add to overlay if not already present (ensures it intercepts input)
        if self not in page.overlay:
            page.overlay.append(self)
        self._is_showing = True
        page.update()

    def hide(self, page: ft.Page):
        """Hide the loading overlay by removing from page.overlay"""
        # Remove from overlay to stop intercepting input events
        if self in page.overlay:
            page.overlay.remove(self)
        self._is_showing = False
        page.update()

    def set_progress(self, percentage: int, page: ft.Page, message: str = None):
        """
        Update progress (0-100)

        Args:
            percentage: Progress percentage (0-100)
            page: Flet page instance
            message: Optional status message
        """
        self._progress_value = max(0, min(100, percentage))
        self.progress_bar.value = self._progress_value / 100
        self.progress_text.value = f"{self._progress_value}%"

        if message:
            self.status_text.value = message

        page.update()

    async def set_progress_async(self, percentage: int, page: ft.Page, message: str = None):
        """Async version with direct update (no animation loop).

        Optimized for performance:
        - Single page.update() call
        - No count-up animation (reduces 4 updates to 1)
        - Progress bar has CSS animation for smooth visual feedback
        """
        end = max(0, min(100, percentage))

        # Direct update - progress bar's built-in animation handles visual smoothing
        self.progress_text.value = f"{end}%"
        self.progress_bar.value = end / 100
        self._progress_value = end

        # Update message if changed
        if message and message != self.status_text.value:
            self.status_text.value = message

        # Single page update for all changes
        page.update()

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for loading overlay"""
        return {
            "progress_ring.color": MD3Colors.get_themed_pair("primary"),
            "progress_ring.bgcolor": MD3Colors.get_themed_pair("surface_variant"),
            "progress_bar.color": MD3Colors.get_themed_pair("primary"),
            "progress_bar.bgcolor": MD3Colors.get_themed_pair("surface_variant"),
            "status_text.color": MD3Colors.get_themed_pair("text_primary"),
            "progress_text.color": MD3Colors.get_themed_pair("text_primary"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with cascade animation support"""
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            # Apply basic properties via parent method
            properties = self.get_themed_properties()
            for prop_path, (dark_val, light_val) in properties.items():
                value = dark_val if is_dark else light_val
                self._set_nested_property(prop_path, value)

            # Update content container glassmorphism effect
            content_bg = "rgba(46, 46, 46, 0.95)" if is_dark else "rgba(255, 255, 255, 0.95)"
            border_color = "rgba(255, 255, 255, 0.1)" if is_dark else "rgba(0, 0, 0, 0.1)"
            self.content_container.bgcolor = content_bg
            self.content_container.border = ft.border.all(1, border_color)

            # Update overlay background opacity
            overlay_bg = ft.Colors.with_opacity(0.7, ft.Colors.BLACK) if is_dark else ft.Colors.with_opacity(0.5, ft.Colors.BLACK)
            self.bgcolor = overlay_bg

            if hasattr(self, 'update'):
                self.update()

        except Exception:
            pass  # Silent fail - component may have been garbage collected
