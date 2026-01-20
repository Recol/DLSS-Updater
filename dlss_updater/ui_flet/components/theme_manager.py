"""
Theme Manager Component
Handles light/dark theme switching with cascade animations and persistence.
Designed for Python 3.14 free-threaded compatibility.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import flet as ft

from dlss_updater.config import config_manager
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import get_theme_registry

if TYPE_CHECKING:
    pass


class ThemeManager:
    """
    Manages application theme (light/dark mode) with cascade animations
    and config persistence.

    Features:
    - Progressive theme updates with cascade animation (~250ms total)
    - OS theme detection on startup (via darkdetect)
    - User preference persistence
    - Thread-safe for Python 3.14 free-threaded compatibility
    """

    def __init__(self, page: ft.Page):
        self.page = page
        self.is_dark = True  # Default to dark
        self._toggle_lock = asyncio.Lock()
        self._registry = get_theme_registry()

        # Load user preference or detect OS theme
        self._load_initial_theme()

        # Apply initial theme (sync, without cascade)
        self.apply_theme(save=False)

    def _load_initial_theme(self) -> None:
        """
        Load initial theme from:
        1. User preference (if explicitly set)
        2. OS theme detection (if available)
        3. Default to dark
        """
        try:
            # Check for user override
            user_override = config_manager.get(
                "Appearance", "user_override", fallback="false"
            )

            if user_override.lower() == "true":
                # User has explicitly set a preference, use it
                theme_pref = config_manager.get("Appearance", "theme", fallback="dark")
                self.is_dark = theme_pref == "dark"
                return

            # No user override - try OS theme detection
            os_theme = self._detect_os_theme()
            if os_theme is not None:
                self.is_dark = os_theme == "dark"
                return

            # Fall back to saved preference or default
            theme_pref = config_manager.get("Appearance", "theme", fallback="dark")
            self.is_dark = theme_pref == "dark"

        except Exception as e:
            print(f"Failed to load theme preference: {e}")
            self.is_dark = True

    @staticmethod
    def _detect_os_theme() -> str | None:
        """
        Detect OS theme using darkdetect library.

        Returns:
            "dark" or "light" if detection succeeds, None otherwise
        """
        try:
            import darkdetect
            os_theme = darkdetect.theme()  # Returns "Dark" or "Light"
            if os_theme:
                return os_theme.lower()
        except ImportError:
            # darkdetect not installed
            pass
        except Exception:
            # Detection failed
            pass
        return None

    def toggle_theme(self) -> None:
        """
        Toggle between light and dark themes (synchronous).
        For async cascade animation, use toggle_theme_async().
        """
        self.is_dark = not self.is_dark
        self.apply_theme()

    async def toggle_theme_async(self) -> None:
        """
        Toggle theme and restart the application.
        A full restart ensures all components render correctly with the new theme.
        """
        async with self._toggle_lock:
            self.is_dark = not self.is_dark
            self._save_preference()

            # Restart the application for clean theme application
            self._restart_application()

    def apply_theme(self, save: bool = True) -> None:
        """
        Apply the current theme (synchronous page-level update).

        Args:
            save: Whether to save preference to config
        """
        # Update registry state
        self._registry.is_dark = self.is_dark

        # Store theme state for component access
        try:
            self.page.client_storage.set("is_dark_theme", self.is_dark)
        except Exception:
            pass

        self.page.session.set("is_dark_theme", self.is_dark)

        # Apply page-level theme
        self.page.theme_mode = ft.ThemeMode.DARK if self.is_dark else ft.ThemeMode.LIGHT
        self.page.bgcolor = MD3Colors.get_background(self.is_dark)

        self.page.theme = ft.Theme(
            color_scheme_seed="#2D6E88" if self.is_dark else "#1A5A70",
            use_material3=True,
        )

        self.page.update()

        if save:
            self._save_preference()

    async def apply_theme_async(self, save: bool = True) -> None:
        """
        Apply theme with cascade animation to all registered components.

        Args:
            save: Whether to save preference to config
        """
        # Update registry state first
        self._registry.is_dark = self.is_dark

        # Store theme state
        try:
            self.page.client_storage.set("is_dark_theme", self.is_dark)
        except Exception:
            pass

        self.page.session.set("is_dark_theme", self.is_dark)

        # Apply page-level theme immediately
        self.page.theme_mode = ft.ThemeMode.DARK if self.is_dark else ft.ThemeMode.LIGHT
        self.page.bgcolor = MD3Colors.get_background(self.is_dark)

        self.page.theme = ft.Theme(
            color_scheme_seed="#2D6E88" if self.is_dark else "#1A5A70",
            use_material3=True,
        )

        self.page.update()

        # Cascade to registered components with progressive page updates
        await self._registry.apply_theme_to_all(
            self.is_dark,
            cascade=True,
            base_delay_ms=30,  # ~250ms total cascade duration
            page=self.page,  # Enable progressive updates between batches
        )

        if save:
            self._save_preference()

    def _save_preference(self) -> None:
        """Save theme preference and user override flag to config"""
        try:
            if not config_manager.has_section("Appearance"):
                config_manager.add_section("Appearance")

            config_manager.set("Appearance", "theme", "dark" if self.is_dark else "light")
            config_manager.set("Appearance", "user_override", "true")
            config_manager.save()
        except Exception as e:
            print(f"Failed to save theme preference: {e}")

    def _restart_application(self) -> None:
        """Restart the application to apply theme changes cleanly"""
        import sys
        import os

        # Get the executable path
        if getattr(sys, 'frozen', False):
            # Running as compiled executable (PyInstaller)
            executable = sys.executable
            os.execv(executable, [executable] + sys.argv[1:])
        else:
            # Running as Python script
            python = sys.executable
            os.execv(python, [python] + sys.argv)

    def is_dark_mode(self) -> bool:
        """Check if currently in dark mode"""
        return self.is_dark

    def get_icon(self) -> str:
        """Get the appropriate theme toggle icon"""
        return ft.Icons.DARK_MODE if self.is_dark else ft.Icons.LIGHT_MODE

    def get_tooltip(self) -> str:
        """Get the tooltip for theme toggle button"""
        return "Switch to light mode" if self.is_dark else "Switch to dark mode"

    def get_registry(self):
        """Get the theme registry for direct access"""
        return self._registry
