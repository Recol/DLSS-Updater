"""
Theme Manager Component
Handles light/dark theme switching with persistence
"""

import flet as ft
from dlss_updater.config import config_manager
from dlss_updater.ui_flet.theme.colors import MD3Colors


class ThemeManager:
    """
    Manages application theme (light/dark mode) with config persistence
    """

    def __init__(self, page: ft.Page):
        self.page = page
        self.is_dark = True  # Default to dark

        # Load from config if exists
        try:
            theme_pref = config_manager.get("Appearance", "theme", fallback="dark")
            self.is_dark = theme_pref == "dark"
        except Exception as e:
            print(f"Failed to load theme preference: {e}")
            self.is_dark = True

        # Apply initial theme
        self.apply_theme(save=False)

    def toggle_theme(self):
        """Toggle between light and dark themes"""
        self.is_dark = not self.is_dark
        self.apply_theme()

    def apply_theme(self, save=True):
        """Apply the current theme"""
        # Store theme state globally on page (skip if page not ready)
        try:
            self.page.client_storage.set("is_dark_theme", self.is_dark)
        except Exception:
            # Page not ready yet, store as attribute instead
            pass

        # Always store as page attribute for immediate access
        self.page.session.set("is_dark_theme", self.is_dark)

        if self.is_dark:
            self.page.theme_mode = ft.ThemeMode.DARK
            # Use dynamic background color
            self.page.bgcolor = MD3Colors.get_background(self.is_dark)

            # Dark theme
            self.page.theme = ft.Theme(
                color_scheme_seed="#2D6E88",
                use_material3=True,
            )
        else:
            self.page.theme_mode = ft.ThemeMode.LIGHT
            # Use dynamic background color
            self.page.bgcolor = MD3Colors.get_background(self.is_dark)

            # Light theme
            self.page.theme = ft.Theme(
                color_scheme_seed="#2D6E88",
                use_material3=True,
            )

        self.page.update()

        # Save to config
        if save:
            self._save_preference()

    def _save_preference(self):
        """Save theme preference to config"""
        try:
            # ConfigManager inherits from ConfigParser, so call methods directly
            if not config_manager.has_section("Appearance"):
                config_manager.add_section("Appearance")

            config_manager.set("Appearance", "theme", "dark" if self.is_dark else "light")
            config_manager.save()  # ConfigManager has save() not save_config()
        except Exception as e:
            print(f"Failed to save theme preference: {e}")

    def is_dark_mode(self) -> bool:
        """Check if currently in dark mode"""
        return self.is_dark

    def get_icon(self) -> str:
        """Get the appropriate theme toggle icon"""
        return ft.Icons.DARK_MODE if self.is_dark else ft.Icons.LIGHT_MODE

    def get_tooltip(self) -> str:
        """Get the tooltip for theme toggle button"""
        return "Switch to light mode" if self.is_dark else "Switch to dark mode"
