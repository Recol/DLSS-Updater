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
        self._page_ref = page
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
    def accent(self) -> str | None:
        """
        Optional brand accent color for the panel header's brand wash.

        Override to give a panel its own identity (e.g. NVIDIA green for a
        DLSS-specific panel). Defaults to None, which SlidePanel resolves to
        the app's PRIMARY color for a generic, quiet header wash.

        Returns:
            Hex color string (e.g. "#76B900"), or None for the default.
        """
        return None

    @property
    def icon(self) -> str | None:
        """
        Optional decorative watermark glyph shown (small, low-opacity) in the
        panel header, echoing the hero card language elsewhere in the app.

        Override to give a panel a more specific glyph. Defaults to a
        generic "tune" icon; return None to omit the watermark entirely.

        Returns:
            An ft.Icons name, or None for no watermark.
        """
        return ft.Icons.TUNE

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

    # Legacy colour arguments -> the shared helper's themed tones.
    _SNACKBAR_TONES = {
        "#2D6E88": "info",
        "#4CAF50": "success",
        "#F44336": "error",
    }

    def _show_snackbar(self, message: str, bgcolor: str = "#2D6E88"):
        """
        Helper method to show a snackbar notification.

        Goes through ``page.show_dialog()`` (via the shared ``show_snackbar``
        helper), which updates only the dialog stack and drops the snackbar
        when it is dismissed. The old ``page.overlay.append`` + ``page.update()``
        leaked one SnackBar into the overlay per call and serialized the page.

        Args:
            message: Message to display
            bgcolor: Background color. The info/success/error colours map to
                the helper's themed tones; any other colour (e.g. the amber
                warning) is kept verbatim.
        """
        from dlss_updater.ui_flet.components.snackbar import show_snackbar
        from dlss_updater.ui_flet.theme.theme_aware import get_theme_registry

        if self._page_ref is None:
            return
        tone = self._SNACKBAR_TONES.get((bgcolor or "").upper())
        if tone is not None:
            show_snackbar(
                self._page_ref,
                message,
                tone=tone,
                is_dark=get_theme_registry().is_dark,
            )
            return
        # Custom colour the helper has no tone for: same show_dialog path.
        self._page_ref.show_dialog(
            ft.SnackBar(content=ft.Text(message), bgcolor=bgcolor)
        )

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
                    on_click=lambda e: self._page_ref.pop_dialog()
                ),
            ],
        )
        self._page_ref.show_dialog(error_dialog)
