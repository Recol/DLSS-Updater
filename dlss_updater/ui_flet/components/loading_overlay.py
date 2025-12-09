"""
Loading Overlay Component
Semi-transparent overlay with progress indicator
"""

import asyncio
import flet as ft

from dlss_updater.ui_flet.theme.colors import Shadows, MD3Colors


class LoadingOverlay(ft.Container):
    """
    Full-screen loading overlay with progress indicator
    Similar to PyQt6's LoadingOverlay but using Flet components
    """

    def __init__(self, page: ft.Page = None):
        super().__init__()

        # State
        self.visible = False
        self._progress_value = 0
        self.page = page

        # Get theme preference
        is_dark = page.session.get("is_dark_theme") if page and page.session.contains_key("is_dark_theme") else True

        # Progress ring with breathing animation
        self.progress_ring = ft.ProgressRing(
            width=60,
            height=60,
            stroke_width=4,
            color="#2D6E88",
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            animate_scale=ft.Animation(1500, ft.AnimationCurve.EASE_IN_OUT),
        )

        # Status text
        self.status_text = ft.Text(
            "Processing...",
            size=16,
            color=ft.Colors.WHITE,
            text_align=ft.TextAlign.CENTER,
            animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
        )

        # Progress percentage text
        self.progress_text = ft.Text(
            "0%",
            size=24,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.WHITE,
            text_align=ft.TextAlign.CENTER,
        )

        # Progress bar with gradient (for determinate progress)
        self.progress_bar = ft.ProgressBar(
            width=300,
            height=4,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            color="#2D6E88",
            value=0,
        )

        # Content container with glassmorphism effect
        content_container = ft.Container(
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
            bgcolor="rgba(46, 46, 46, 0.95)",
            border_radius=16,
            padding=ft.padding.all(40),
            border=ft.border.all(1, "rgba(255, 255, 255, 0.1)"),
            shadow=Shadows.LEVEL_5,
        )

        # Overlay styling
        self.content = ft.Container(
            content=content_container,
            alignment=ft.alignment.center,
            expand=True,
        )
        self.bgcolor = ft.Colors.with_opacity(0.7, ft.Colors.BLACK)
        self.expand = True
        self.visible = False

    def show(self, page: ft.Page, message: str = "Processing..."):
        """Show the loading overlay"""
        self.status_text.value = message
        self._progress_value = 0
        self.progress_bar.value = 0
        self.progress_text.value = "0%"
        self.visible = True
        page.update()

    def hide(self, page: ft.Page):
        """Hide the loading overlay"""
        self.visible = False
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
        """Async version with smooth count-up animation"""
        # Animate percentage change
        start = self.progress_bar.value * 100 if self.progress_bar.value else 0
        end = max(0, min(100, percentage))

        # Count-up animation (10 steps)
        steps = 10
        for i in range(steps + 1):
            current = start + (end - start) * (i / steps)
            self.progress_text.value = f"{int(current)}%"
            self.progress_bar.value = current / 100
            page.update()
            await asyncio.sleep(0.03)  # 30ms per step = 300ms total

        self._progress_value = end

        # Fade message if changed
        if message and message != self.status_text.value:
            # Fade out
            self.status_text.opacity = 0
            page.update()
            await asyncio.sleep(0.2)

            # Change and fade in
            self.status_text.value = message
            self.status_text.opacity = 1
            page.update()
