"""
PreferencesPanel - Update preferences configuration panel
Allows users to configure which DLL technologies to update and backup settings
"""

import logging
import flet as ft
from dlss_updater.ui_flet.components.slide_panel import PanelContentBase
from dlss_updater.config import config_manager


class PreferencesPanel(PanelContentBase):
    """
    Panel for managing update preferences.

    Features:
    - Technology toggles (DLSS, Streamline, DirectStorage, XeSS, FSR)
    - Backup preference toggle
    - Smooth scrolling preference toggle
    - Validation (at least one technology must be selected)
    - Material Design 3 styling with consistent theme colors
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        """
        Initialize preferences panel.

        Args:
            page: Flet Page instance
            logger: Logger instance for diagnostics
        """
        super().__init__(page, logger)
        self._load_preferences()
        self._build_switches()

    @property
    def title(self) -> str:
        """Panel title."""
        return "Update Preferences"

    @property
    def subtitle(self) -> str | None:
        """Panel subtitle."""
        return "Configure DLL update settings"

    @property
    def width(self) -> int:
        """Panel width in pixels."""
        return 500

    def _load_preferences(self):
        """Load current preferences from config."""
        self.prefs = {
            "dlss": config_manager.get_update_preference("DLSS"),
            "streamline": config_manager.get_update_preference("Streamline"),
            "directstorage": config_manager.get_update_preference("DirectStorage"),
            "xess": config_manager.get_update_preference("XeSS"),
            "fsr": config_manager.get_update_preference("FSR"),
        }
        self.backup_pref = config_manager.get_backup_preference()

    def _build_switches(self):
        """Build all switch controls with ListTile layout."""
        # DLSS Switch
        self.dlss_switch = ft.Switch(
            value=self.prefs["dlss"],
            active_color="#2D6E88",
        )
        self.dlss_tile = ft.ListTile(
            title=ft.Text("DLSS", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Deep Learning Super Sampling"),
            trailing=self.dlss_switch,
        )

        # Streamline Switch
        self.streamline_switch = ft.Switch(
            value=self.prefs["streamline"],
            active_color="#2D6E88",
        )
        self.streamline_tile = ft.ListTile(
            title=ft.Text("Streamline", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Reflex, DLSS Frame Gen, etc."),
            trailing=self.streamline_switch,
        )

        # DirectStorage Switch
        self.directstorage_switch = ft.Switch(
            value=self.prefs["directstorage"],
            active_color="#2D6E88",
        )
        self.directstorage_tile = ft.ListTile(
            title=ft.Text("DirectStorage", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Fast loading"),
            trailing=self.directstorage_switch,
        )

        # XeSS Switch
        self.xess_switch = ft.Switch(
            value=self.prefs["xess"],
            active_color="#2D6E88",
        )
        self.xess_tile = ft.ListTile(
            title=ft.Text("XeSS", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Intel Xe Super Sampling"),
            trailing=self.xess_switch,
        )

        # FSR Switch
        self.fsr_switch = ft.Switch(
            value=self.prefs["fsr"],
            active_color="#2D6E88",
        )
        self.fsr_tile = ft.ListTile(
            title=ft.Text("FSR", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("AMD FidelityFX"),
            trailing=self.fsr_switch,
        )

        # Backup Switch
        self.backup_switch = ft.Switch(
            value=self.backup_pref,
            active_color="#2D6E88",
        )
        self.backup_tile = ft.ListTile(
            title=ft.Text("Create backups before updating", weight=ft.FontWeight.BOLD),
            trailing=self.backup_switch,
        )

    def build(self) -> ft.Control:
        """
        Build the preferences panel content.

        Returns:
            Column containing all preference controls
        """
        return ft.Column(
            controls=[
                ft.Text("Technologies to Update:", weight=ft.FontWeight.BOLD, size=16),
                self.dlss_tile,
                self.streamline_tile,
                self.directstorage_tile,
                self.xess_tile,
                self.fsr_tile,
                ft.Divider(height=20),
                ft.Text("Backup Options:", weight=ft.FontWeight.BOLD, size=14),
                self.backup_tile,
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
        )

    def validate(self) -> tuple[bool, str | None]:
        """
        Validate preferences before saving.

        Ensures at least one technology is selected.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # At least one technology must be selected
        if not any([
            self.dlss_switch.value,
            self.streamline_switch.value,
            self.directstorage_switch.value,
            self.xess_switch.value,
            self.fsr_switch.value,
        ]):
            return False, "At least one technology must be selected."
        return True, None

    async def on_save(self) -> bool:
        """
        Save preferences to config.

        Validates input, saves to config_manager, and shows feedback.

        Returns:
            True if save succeeded, False otherwise
        """
        # Validate first
        is_valid, error = self.validate()
        if not is_valid:
            # Show error dialog
            self._show_error_dialog("Invalid Configuration", error)
            return False

        # Save all preferences
        config_manager.set_update_preference("DLSS", self.dlss_switch.value)
        config_manager.set_update_preference("Streamline", self.streamline_switch.value)
        config_manager.set_update_preference("DirectStorage", self.directstorage_switch.value)
        config_manager.set_update_preference("XeSS", self.xess_switch.value)
        config_manager.set_update_preference("FSR", self.fsr_switch.value)
        config_manager.set_backup_preference(self.backup_switch.value)

        self.logger.info("Update preferences saved")

        # Show success feedback
        self._show_snackbar("Preferences saved successfully")

        return True

    def on_cancel(self):
        """
        Called when panel is cancelled.

        Reloads preferences from config to discard unsaved changes.
        """
        self.logger.debug("Preferences panel cancelled, discarding changes")
        self._load_preferences()
