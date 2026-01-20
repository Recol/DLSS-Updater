"""
DLL Cache Progress Notification Component
Custom Container-based notification with fade animations for reliable show/hide
"""

import asyncio
from enum import Enum
import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


class NotificationState(Enum):
    """State machine states for the notification"""
    HIDDEN = "hidden"
    INITIALIZING = "initializing"
    UPDATING = "updating"
    COMPLETING = "completing"
    ERROR = "error"


class DLLCacheProgressSnackbar(ThemeAwareMixin):
    """
    Custom Container-based progress notification for DLL cache initialization.
    Uses visibility + opacity animation for reliable show/hide behavior.
    Supports light/dark theme.
    """

    def __init__(self, page: ft.Page):
        self.page = page
        self._state = NotificationState.HIDDEN
        self._registry = get_theme_registry()
        self._theme_priority = 40  # Utility components are mid-low priority
        self._build_components()
        self._register_theme_aware()

    def _build_components(self):
        """Build the notification UI components"""
        is_dark = self._registry.is_dark

        # Spinner (ProgressRing) - shown during initialization
        self.spinner = ft.ProgressRing(
            width=20,
            height=20,
            stroke_width=2,
            color=ft.Colors.WHITE,
        )

        # Success icon (hidden initially)
        self.success_icon = ft.Icon(
            ft.Icons.CHECK_CIRCLE,
            color=ft.Colors.WHITE,
            size=20,
            visible=False,
        )

        # Error icon (hidden initially)
        self.error_icon = ft.Icon(
            ft.Icons.ERROR,
            color=ft.Colors.WHITE,
            size=20,
            visible=False,
        )

        # Status icon container (switches between spinner/success/error)
        self.status_icon_container = ft.Container(
            content=ft.Stack(
                controls=[
                    self.spinner,
                    self.success_icon,
                    self.error_icon,
                ],
            ),
            width=24,
            height=24,
        )

        # Message text
        self.message_text = ft.Text(
            "Initialising DLL cache...",
            color=ft.Colors.WHITE,
            size=14,
            weight=ft.FontWeight.W_500,
        )

        # Progress percentage text
        self.progress_text = ft.Text(
            "",
            color=ft.Colors.WHITE70,
            size=12,
        )

        # Progress bar
        self.progress_bar = ft.ProgressBar(
            width=180,
            height=4,
            color=ft.Colors.WHITE,
            bgcolor="rgba(255, 255, 255, 0.2)",
            value=None,  # Indeterminate initially
        )

        # Progress container (bar + percentage)
        self.progress_container = ft.Row(
            controls=[
                self.progress_bar,
                self.progress_text,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Content column (message + progress)
        content_column = ft.Column(
            controls=[
                self.message_text,
                self.progress_container,
            ],
            spacing=4,
            tight=True,
        )

        # Main content row
        content_row = ft.Row(
            controls=[
                self.status_icon_container,
                content_column,
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Main notification container with animations
        self.container = ft.Container(
            content=content_row,
            bgcolor=MD3Colors.get_themed("snackbar_bg", is_dark),
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            border_radius=8,
            opacity=0,  # Start hidden (transparent)
            visible=False,  # Start hidden
            animate_opacity=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                color="rgba(0, 0, 0, 0.3)",
                offset=ft.Offset(0, 2),
            ),
        )

        # Wrapper to position at bottom center of screen
        self.wrapper = ft.Container(
            content=self.container,
            alignment=ft.alignment.bottom_center,
            padding=ft.padding.only(bottom=20),
            expand=True,
        )

    async def show_initializing(self):
        """Show the notification in initializing state"""
        self._state = NotificationState.INITIALIZING
        is_dark = self._registry.is_dark

        # Reset to initial state
        self.spinner.visible = True
        self.success_icon.visible = False
        self.error_icon.visible = False
        self.message_text.value = "Initialising DLL cache..."
        self.progress_bar.value = None  # Indeterminate
        self.progress_text.value = ""
        self.progress_container.visible = True
        self.container.bgcolor = MD3Colors.get_themed("snackbar_bg", is_dark)

        # Show with fade in
        self.container.visible = True
        self.wrapper.visible = True
        self.page.update()

        # Small delay then fade in
        await asyncio.sleep(0.05)
        self.container.opacity = 1
        self.page.update()

    async def update_progress(self, current: int, total: int, message: str):
        """Update progress display"""
        if self._state == NotificationState.HIDDEN:
            return

        self._state = NotificationState.UPDATING
        percentage = int((current / total * 100)) if total > 0 else 0

        # Update UI
        self.progress_bar.value = percentage / 100
        self.progress_text.value = f"{percentage}%"
        self.message_text.value = message if message else "Updating DLL cache..."

        self.page.update()

    async def show_complete(self, auto_dismiss_delay: float = 2.5):
        """Show completion state with green checkmark, then auto-dismiss"""
        self._state = NotificationState.COMPLETING
        is_dark = self._registry.is_dark

        # Switch to success icon
        self.spinner.visible = False
        self.success_icon.visible = True
        self.error_icon.visible = False

        # Update message
        self.message_text.value = "DLL cache ready"
        self.progress_bar.value = 1.0
        self.progress_text.value = "100%"

        # Change background to success color (themed)
        self.container.bgcolor = MD3Colors.get_success(is_dark)

        self.page.update()

        # Auto-dismiss after delay
        await asyncio.sleep(auto_dismiss_delay)
        await self.hide()

    async def show_error(self, error_message: str = "Failed to initialise DLL cache"):
        """Show error state"""
        self._state = NotificationState.ERROR
        is_dark = self._registry.is_dark

        # Switch to error icon
        self.spinner.visible = False
        self.success_icon.visible = False
        self.error_icon.visible = True

        # Update message
        self.message_text.value = error_message
        self.progress_container.visible = False

        # Change background to error color (themed)
        self.container.bgcolor = MD3Colors.get_error(is_dark)

        self.page.update()

        # Auto-dismiss after longer delay for errors
        await asyncio.sleep(5)
        await self.hide()

    async def hide(self):
        """Hide the notification with fade out animation"""
        self._state = NotificationState.HIDDEN
        is_dark = self._registry.is_dark

        # Fade out
        self.container.opacity = 0
        self.page.update()

        # Wait for animation to complete
        await asyncio.sleep(0.35)

        # Hide completely
        self.container.visible = False
        self.wrapper.visible = False

        # Reset styling for next use (themed)
        self.container.bgcolor = MD3Colors.get_themed("snackbar_bg", is_dark)
        self.progress_container.visible = True

        self.page.update()

    def get_wrapper(self) -> ft.Container:
        """Get the wrapper container to add to page overlay"""
        return self.wrapper

    @property
    def is_visible(self) -> bool:
        """Check if notification is currently visible"""
        return self._state != NotificationState.HIDDEN

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for the snackbar"""
        # The snackbar uses white text on colored backgrounds for all themes
        # The background colors change based on state (primary, success, error)
        # which are already themed in the state methods
        return {
            "container.bgcolor": MD3Colors.get_themed_pair("snackbar_bg"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with cascade animation support"""
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            # Update container bgcolor based on current state
            if self._state == NotificationState.INITIALIZING or self._state == NotificationState.UPDATING:
                self.container.bgcolor = MD3Colors.get_themed("snackbar_bg", is_dark)
            elif self._state == NotificationState.COMPLETING:
                self.container.bgcolor = MD3Colors.get_success(is_dark)
            elif self._state == NotificationState.ERROR:
                self.container.bgcolor = MD3Colors.get_error(is_dark)
            # HIDDEN state doesn't need update

            if self.page:
                self.page.update()

        except Exception:
            pass  # Silent fail - component may have been garbage collected
