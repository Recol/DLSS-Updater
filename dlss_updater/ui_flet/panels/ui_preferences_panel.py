"""
UIPreferencesPanel - UI preferences configuration panel
Allows users to configure UI behavior and performance settings
"""

import logging
import flet as ft
from dlss_updater.ui_flet.components.slide_panel import PanelContentBase
from dlss_updater.config import config_manager
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors


class UIPreferencesPanel(ThemeAwareMixin, PanelContentBase):
    """
    Panel for managing UI preferences.

    Features:
    - Smooth scrolling toggle
    - Keep games in memory toggle (with restart warning)
    - Material Design 3 styling with consistent theme colors
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        """
        Initialize UI preferences panel.

        Args:
            page: Flet Page instance
            logger: Logger instance for diagnostics
        """
        super().__init__(page, logger)

        # Theme support
        self._registry = get_theme_registry()
        self._theme_priority = 60  # Panels animate later in cascade

        # Store themed element references
        self._interface_label: ft.Text | None = None
        self._performance_label: ft.Text | None = None
        self._divider: ft.Divider | None = None
        self._warning_icon: ft.Icon | None = None
        self._warning_text: ft.Text | None = None

        self._load_preferences()
        self._build_switches()

        # Register for theme updates
        self._register_theme_aware()

    @property
    def title(self) -> str:
        """Panel title."""
        return "UI Preferences"

    @property
    def subtitle(self) -> str | None:
        """Panel subtitle."""
        return "Configure interface settings"

    @property
    def width(self) -> int:
        """Panel width in pixels."""
        return 500

    def _load_preferences(self):
        """Load current preferences from config."""
        self.smooth_scroll_pref = config_manager.get_smooth_scrolling_enabled()
        self.keep_games_in_memory_pref = config_manager.get_keep_games_in_memory()

    def _build_switches(self):
        """Build all switch controls with ListTile layout."""
        is_dark = self._registry.is_dark

        # Smooth Scrolling Switch
        self.smooth_scroll_switch = ft.Switch(
            value=self.smooth_scroll_pref,
            active_color=MD3Colors.get_primary(is_dark),
        )
        self.smooth_scroll_tile = ft.ListTile(
            title=ft.Text("Smooth Scrolling", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Enable smooth scroll animations"),
            trailing=self.smooth_scroll_switch,
        )

        # Keep Games in Memory Switch
        self.keep_games_in_memory_switch = ft.Switch(
            value=self.keep_games_in_memory_pref,
            active_color=MD3Colors.get_primary(is_dark),
        )
        self.keep_games_in_memory_tile = ft.ListTile(
            title=ft.Text("Keep Games in Memory", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Faster tab switching, uses more RAM (this will scale with number of games)"),
            trailing=self.keep_games_in_memory_switch,
        )

        # Restart warning for memory option
        self._warning_icon = ft.Icon(
            ft.Icons.INFO_OUTLINE,
            size=16,
            color=MD3Colors.get_warning(is_dark),
        )
        self._warning_text = ft.Text(
            "Requires app restart to take effect",
            size=12,
            color=MD3Colors.get_warning(is_dark),
            italic=True,
        )
        self.keep_games_warning = ft.Container(
            content=ft.Row(
                controls=[self._warning_icon, self._warning_text],
                spacing=8,
            ),
            padding=ft.padding.only(left=16),
        )

    def build(self) -> ft.Control:
        """
        Build the UI preferences panel content.

        Returns:
            Column containing all preference controls
        """
        is_dark = self._registry.is_dark

        self._interface_label = ft.Text(
            "Interface Settings:",
            weight=ft.FontWeight.BOLD,
            size=16,
            color=MD3Colors.get_text_primary(is_dark),
        )
        self._divider = ft.Divider(height=20, color=MD3Colors.get_divider(is_dark))
        self._performance_label = ft.Text(
            "Performance:",
            weight=ft.FontWeight.BOLD,
            size=16,
            color=MD3Colors.get_text_primary(is_dark),
        )

        return ft.Column(
            controls=[
                self._interface_label,
                self.smooth_scroll_tile,
                self._divider,
                self._performance_label,
                self.keep_games_in_memory_tile,
                self.keep_games_warning,
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
        )

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """
        Return themed property mappings for cascade animation.

        Returns:
            Dict mapping property paths to (dark_value, light_value) tuples.
        """
        props = {}

        # Labels
        if self._interface_label:
            props["_interface_label.color"] = MD3Colors.get_themed_pair("text_primary")
        if self._performance_label:
            props["_performance_label.color"] = MD3Colors.get_themed_pair("text_primary")

        # Divider
        if self._divider:
            props["_divider.color"] = MD3Colors.get_themed_pair("divider")

        # Warning elements
        if self._warning_icon:
            props["_warning_icon.color"] = MD3Colors.get_themed_pair("warning")
        if self._warning_text:
            props["_warning_text.color"] = MD3Colors.get_themed_pair("warning")

        # Switches - active color
        props["smooth_scroll_switch.active_color"] = MD3Colors.get_themed_pair("primary")
        props["keep_games_in_memory_switch.active_color"] = MD3Colors.get_themed_pair("primary")

        return props

    def validate(self) -> tuple[bool, str | None]:
        """
        No validation needed for UI preferences.

        Returns:
            Tuple of (True, None) - always valid
        """
        return True, None

    async def on_save(self) -> bool:
        """
        Save UI preferences to config.

        Saves to config_manager and shows appropriate feedback.

        Returns:
            True if save succeeded (always True for UI prefs)
        """
        # Track if restart-requiring preference changed
        old_keep_in_memory = config_manager.get_keep_games_in_memory()
        new_keep_in_memory = self.keep_games_in_memory_switch.value

        # Save all preferences
        config_manager.set_smooth_scrolling_enabled(self.smooth_scroll_switch.value)
        config_manager.set_keep_games_in_memory(new_keep_in_memory)

        self.logger.info("UI preferences saved")

        # Show appropriate feedback
        if old_keep_in_memory != new_keep_in_memory:
            self._show_snackbar(
                "Preferences saved. Restart app for memory setting to take effect.",
                "#FFB74D"
            )
        else:
            self._show_snackbar("Preferences saved successfully")

        return True

    def on_cancel(self):
        """
        Called when panel is cancelled.

        Reloads preferences from config to discard unsaved changes.
        """
        self.logger.debug("UI preferences panel cancelled, discarding changes")
        self._load_preferences()
