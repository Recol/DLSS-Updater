"""
PanelContentBase - Abstract base class for slide panel content
Provides standardized interface for implementing reusable panel content
"""

import logging
from abc import ABC, abstractmethod
import flet as ft


class PanelContentBase(ABC):
    """
    Abstract base class for slide panel content.

    All panel implementations must inherit from this class and implement:
    - title property: Display title for the panel header
    - subtitle property (optional): Descriptive subtitle
    - width property: Panel width in pixels
    - build() method: Returns the content control
    - on_save() method: Called when user clicks Save/Apply
    - validate() method (optional): Validation before save

    Usage Example:
        class MyPanel(PanelContentBase):
            @property
            def title(self) -> str:
                return "My Panel"

            @property
            def width(self) -> int:
                return 400

            def build(self) -> ft.Control:
                return ft.Column([...])

            async def on_save(self) -> bool:
                # Save logic here
                return True
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        """
        Initialize panel content base.

        Args:
            page: Flet Page instance for UI updates
            logger: Logger instance for diagnostics
        """
        self.page = page
        self.logger = logger

    @property
    @abstractmethod
    def title(self) -> str:
        """
        Panel title displayed in the header.

        Returns:
            Title string (e.g., "Update Preferences")
        """
        pass

    @property
    def subtitle(self) -> str | None:
        """
        Optional subtitle displayed below the title.

        Returns:
            Subtitle string or None (default: None)
        """
        return None

    @property
    @abstractmethod
    def width(self) -> int:
        """
        Panel width in pixels.

        Returns:
            Width in pixels (e.g., 500)
        """
        pass

    @abstractmethod
    def build(self) -> ft.Control:
        """
        Build and return the panel content control.

        This method should construct all UI elements and return the root control
        (typically a Column or Container with all panel content).

        Returns:
            Root Flet control for the panel content
        """
        pass

    def validate(self) -> tuple[bool, str | None]:
        """
        Validate panel state before saving.

        Override this method to implement custom validation logic.
        Called automatically before on_save().

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if validation passed, False otherwise
            - error_message: Error description if validation failed, None if passed

        Example:
            def validate(self) -> tuple[bool, str | None]:
                if not self.name_field.value:
                    return False, "Name is required"
                return True, None
        """
        return True, None

    @abstractmethod
    async def on_save(self) -> bool:
        """
        Save panel changes and perform any necessary actions.

        This method is called when the user clicks Save/Apply. It should:
        1. Call validate() to ensure data is valid
        2. Persist changes (config, database, etc.)
        3. Show success/error feedback to user
        4. Return True if save succeeded, False otherwise

        The panel will automatically close if this method returns True.

        Returns:
            True if save succeeded and panel should close, False otherwise

        Example:
            async def on_save(self) -> bool:
                is_valid, error = self.validate()
                if not is_valid:
                    self._show_error(error)
                    return False

                # Save logic
                config_manager.set_value("key", self.value)
                self._show_success("Saved successfully")
                return True
        """
        pass

    async def on_open(self):
        """
        Called when the panel is opened.

        Override this method to implement initialization logic
        (e.g., load data, reset state, etc.).
        Default implementation does nothing.
        """
        pass

    async def on_close(self):
        """
        Called when the panel is closed.

        Override this method to implement cleanup logic.
        Default implementation does nothing.
        """
        pass

    def on_cancel(self):
        """
        Called when the user cancels/closes the panel.

        Override this method to implement cleanup or reset logic.
        Default implementation does nothing.
        """
        pass

    def _show_snackbar(self, message: str, bgcolor: str = "#2D6E88"):
        """
        Helper method to show a snackbar notification.

        Args:
            message: Message to display
            bgcolor: Background color (default: primary theme color)
        """
        snackbar = ft.SnackBar(
            content=ft.Text(message),
            bgcolor=bgcolor,
        )
        self.page.overlay.append(snackbar)
        snackbar.open = True
        self.page.update()

    def _show_error_dialog(self, title: str, message: str):
        """
        Helper method to show an error dialog.

        Args:
            title: Dialog title
            message: Error message
        """
        error_dialog = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Text(message),
            actions=[
                ft.FilledButton(
                    "OK",
                    on_click=lambda e: self.page.close(error_dialog)
                ),
            ],
        )
        self.page.open(error_dialog)
