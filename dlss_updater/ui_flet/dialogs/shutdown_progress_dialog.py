"""
Shutdown Progress Dialog
Shows step-by-step progress during application shutdown.

This dialog provides visual feedback during the cleanup process when
the user closes the app, preventing the appearance of the app "freezing".
"""

import logging

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors


class ShutdownProgressDialog:
    """
    Modal dialog showing shutdown progress with step-by-step status updates.

    Features:
    - Progress bar showing overall shutdown progress
    - Spinner for current step
    - Status text showing current operation
    - Step counter (e.g., "Step 4/9")
    - Modal (cannot be dismissed by user)
    - No action buttons (closes automatically when complete)

    Usage:
        dialog = ShutdownProgressDialog(page)
        dialog.show()

        # In shutdown method:
        dialog.update_step(1)
        await cancel_tasks()
        dialog.update_step(2)
        await close_view()
        # ... etc

        dialog.show_complete()
    """

    TOTAL_STEPS = 9
    STEP_DESCRIPTIONS = {
        1: "Cancelling background tasks...",
        2: "Closing Games view...",
        3: "Stopping cache manager...",
        4: "Shutting down search service...",
        5: "Closing network connections...",
        6: "Closing database...",
        7: "Stopping worker threads...",
        8: "Cleaning up UI...",
        9: "Finalizing shutdown...",
    }

    # Color scheme
    PRIMARY_BLUE = "#2D6E88"
    PROGRESS_COLOR = "#4CAF50"  # Green for progress

    def __init__(self, page: ft.Page, logger: logging.Logger | None = None):
        """
        Initialize the shutdown progress dialog.

        Args:
            page: The Flet page to show the dialog on
            logger: Optional logger for debug output
        """
        self._page_ref = page
        self.logger = logger or logging.getLogger(__name__)
        self._dialog: ft.AlertDialog | None = None
        self._current_step = 0

        # UI element references for updates
        self._progress_bar: ft.ProgressBar | None = None
        self._progress_ring: ft.ProgressRing | None = None
        self._status_text: ft.Text | None = None
        self._step_counter: ft.Text | None = None
        self._complete_icon: ft.Icon | None = None

        # Get theme state
        self._is_dark = True  # Default to dark, will check page if available
        try:
            if hasattr(page, 'theme_mode'):
                self._is_dark = page.theme_mode == ft.ThemeMode.DARK
        except Exception:
            pass

    def _build_content(self) -> ft.Container:
        """Build the dialog content with progress indicators."""
        is_dark = self._is_dark

        # Progress bar (determinate)
        self._progress_bar = ft.ProgressBar(
            value=0,
            width=400,
            height=8,
            color=self.PROGRESS_COLOR,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            border_radius=4,
        )

        # Spinner for current step (visible during shutdown)
        self._progress_ring = ft.ProgressRing(
            width=32,
            height=32,
            stroke_width=3,
            color=self.PRIMARY_BLUE,
            visible=True,
        )

        # Complete checkmark (hidden initially)
        self._complete_icon = ft.Icon(
            ft.Icons.CHECK_CIRCLE,
            size=32,
            color=self.PROGRESS_COLOR,
            visible=False,
        )

        # Status text showing current operation
        self._status_text = ft.Text(
            "Preparing to close...",
            size=14,
            color=MD3Colors.get_text_primary(is_dark),
            text_align=ft.TextAlign.CENTER,
        )

        # Step counter
        self._step_counter = ft.Text(
            "Step 0/9",
            size=12,
            color=MD3Colors.get_text_secondary(is_dark),
            text_align=ft.TextAlign.CENTER,
        )

        return ft.Container(
            content=ft.Column(
                controls=[
                    # Header with icon
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.POWER_SETTINGS_NEW,
                                size=28,
                                color=self.PRIMARY_BLUE,
                            ),
                            ft.Text(
                                "Closing Application",
                                size=18,
                                weight=ft.FontWeight.BOLD,
                                color=MD3Colors.get_text_primary(is_dark),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=12,
                    ),
                    ft.Container(height=16),  # Spacer
                    # Progress bar
                    self._progress_bar,
                    ft.Container(height=16),  # Spacer
                    # Status row with spinner/checkmark
                    ft.Row(
                        controls=[
                            ft.Stack(
                                controls=[
                                    self._progress_ring,
                                    self._complete_icon,
                                ],
                                width=32,
                                height=32,
                            ),
                            ft.Container(width=12),  # Spacer
                            ft.Column(
                                controls=[
                                    self._status_text,
                                    self._step_counter,
                                ],
                                spacing=4,
                                horizontal_alignment=ft.CrossAxisAlignment.START,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(height=8),  # Spacer
                    # Subtle message
                    ft.Text(
                        "Please wait while cleanup completes...",
                        size=11,
                        color=MD3Colors.get_text_secondary(is_dark),
                        italic=True,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=0,
            ),
            width=450,
            padding=ft.padding.all(24),
        )

    def show(self) -> None:
        """Show the shutdown progress dialog."""
        self.logger.debug("Showing shutdown progress dialog")

        content = self._build_content()

        self._dialog = ft.AlertDialog(
            modal=True,
            title=None,  # Title is built into content
            content=content,
            bgcolor=MD3Colors.get_surface(self._is_dark),
            actions=[],  # No actions - cannot dismiss
            actions_alignment=ft.MainAxisAlignment.CENTER,
        )

        try:
            self._page_ref.show_dialog(self._dialog)
        except Exception as e:
            self.logger.warning(f"Could not show shutdown dialog: {e}")

    def update_step(self, step: int) -> None:
        """
        Update the progress to the specified step.

        Args:
            step: The current step number (1-9)
        """
        if not self._dialog:
            return

        self._current_step = step

        try:
            # Update progress bar
            progress = step / self.TOTAL_STEPS
            if self._progress_bar:
                self._progress_bar.value = progress

            # Update status text
            description = self.STEP_DESCRIPTIONS.get(step, "Processing...")
            if self._status_text:
                self._status_text.value = description

            # Update step counter
            if self._step_counter:
                self._step_counter.value = f"Step {step}/{self.TOTAL_STEPS}"

            # Try to update the page
            self._page_ref.update()
        except Exception as e:
            self.logger.debug(f"Error updating shutdown progress: {e}")

    def show_complete(self) -> None:
        """Show completion state with checkmark."""
        if not self._dialog:
            return

        try:
            # Hide spinner, show checkmark
            if self._progress_ring:
                self._progress_ring.visible = False
            if self._complete_icon:
                self._complete_icon.visible = True

            # Update to 100%
            if self._progress_bar:
                self._progress_bar.value = 1.0

            # Update text
            if self._status_text:
                self._status_text.value = "Shutdown complete"
            if self._step_counter:
                self._step_counter.value = f"Step {self.TOTAL_STEPS}/{self.TOTAL_STEPS}"

            # Try to update the page
            self._page_ref.update()
        except Exception as e:
            self.logger.debug(f"Error showing completion state: {e}")

    def close(self) -> None:
        """Close the dialog (called before window destroy)."""
        if self._dialog:
            try:
                self._page_ref.pop_dialog()
            except Exception as e:
                self.logger.debug(f"Error closing shutdown dialog: {e}")
