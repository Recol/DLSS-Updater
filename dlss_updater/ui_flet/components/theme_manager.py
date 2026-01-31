"""
Theme Manager Component
Handles light/dark theme switching with cascade animations and persistence.
Designed for Python 3.14 free-threaded compatibility.
"""

from __future__ import annotations

import asyncio
import sys
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
    - User preference persistence
    - Thread-safe for Python 3.14 free-threaded compatibility

    Note: OS theme detection (darkdetect) was removed due to blocking calls
    that caused GUI freezes on Linux Flatpak/Wayland and Windows with AV software.
    """

    def __init__(self, page: ft.Page):
        self._page_ref = page
        self.is_dark = True  # Default to dark
        self._toggle_lock = asyncio.Lock()
        self._registry = get_theme_registry()

        # Load user preference or detect OS theme
        self._load_initial_theme()

        # Apply initial theme (sync, without cascade)
        self.apply_theme(save=False)

    def _load_initial_theme(self) -> None:
        """
        Load initial theme from user preference or default to dark.

        Note: OS theme detection (darkdetect) was removed due to blocking calls
        that caused GUI freezes on Linux Flatpak/Wayland and Windows with AV software.
        """
        try:
            # Load saved theme preference
            theme_pref = config_manager.get("Appearance", "theme", fallback="dark")
            self.is_dark = theme_pref == "dark"

        except Exception as e:
            print(f"Failed to load theme preference: {e}")
            self.is_dark = True

    def toggle_theme(self) -> None:
        """
        Toggle between light and dark themes (synchronous).
        For async cascade animation, use toggle_theme_async().
        """
        self.is_dark = not self.is_dark
        self.apply_theme()

    async def toggle_theme_async(self) -> None:
        """
        Toggle theme and show restart confirmation dialog.
        A full restart ensures all components render correctly with the new theme.
        """
        async with self._toggle_lock:
            self.is_dark = not self.is_dark
            self._save_preference()

            # Show restart confirmation dialog
            await self._show_theme_restart_dialog()

    async def _show_theme_restart_dialog(self) -> None:
        """
        Show a modal dialog informing the user that a restart is required
        for the theme change to take effect.
        """
        from dlss_updater.platform_utils import IS_WINDOWS

        # Use the NEW theme state for dialog colors (previews the new theme)
        is_dark = self.is_dark
        new_theme_name = "Dark" if is_dark else "Light"

        async def on_close_now(e):
            """Handle 'Close Now' button click - exits the application (Windows only)."""
            self._page_ref.pop_dialog()
            sys.exit(0)

        async def on_ok(e):
            """Handle 'OK' button click - just closes the dialog."""
            self._page_ref.pop_dialog()

        # Platform-specific message
        if IS_WINDOWS:
            message_text = "The application will close. Please reopen it to see the new theme."
        else:
            message_text = "Please close and reopen the application to see the new theme."

        # Build dialog content
        content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.INFO_OUTLINE,
                                color=MD3Colors.get_primary(is_dark),
                                size=24,
                            ),
                            ft.Text(
                                f"Theme changed to {new_theme_name} Mode",
                                size=14,
                                weight=ft.FontWeight.W_500,
                                color=MD3Colors.get_text_primary(is_dark),
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=ft.Text(
                            message_text,
                            size=13,
                            color=MD3Colors.get_text_secondary(is_dark),
                        ),
                        padding=ft.padding.only(left=36),
                    ),
                ],
                spacing=12,
                tight=True,
            ),
            width=380,
            padding=ft.padding.only(top=8, bottom=8),
        )

        # Platform-specific actions
        if IS_WINDOWS:
            actions = [
                ft.FilledButton(
                    "Close Now",
                    on_click=on_close_now,
                    style=ft.ButtonStyle(
                        bgcolor=MD3Colors.get_primary(is_dark),
                        color=MD3Colors.ON_PRIMARY,
                    ),
                ),
            ]
        else:
            actions = [
                ft.FilledButton(
                    "OK",
                    on_click=on_ok,
                    style=ft.ButtonStyle(
                        bgcolor=MD3Colors.get_primary(is_dark),
                        color=MD3Colors.ON_PRIMARY,
                    ),
                ),
            ]

        # Create the dialog
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.RESTART_ALT,
                        color=MD3Colors.get_primary(is_dark),
                        size=24,
                    ),
                    ft.Text(
                        "Restart Required",
                        color=MD3Colors.get_text_primary(is_dark),
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            content=content,
            bgcolor=MD3Colors.get_surface(is_dark),
            actions=actions,
            actions_alignment=ft.MainAxisAlignment.END,
        )

        self._page_ref.show_dialog(dialog)
        self._page_ref.update()

    def apply_theme(self, save: bool = True) -> None:
        """
        Apply the current theme (synchronous page-level update).

        Args:
            save: Whether to save preference to config
        """
        # Update registry state
        self._registry.is_dark = self.is_dark

        # Store theme state for component access
        # Note: In Flet 0.80.4+, session/client_storage APIs may differ
        try:
            # Try shared_preferences (new API) or client_storage (old API)
            if hasattr(self._page_ref, 'shared_preferences'):
                # Flet 0.80.4+ uses shared_preferences (async, but we're in sync context)
                pass  # Skip for sync method - will be set in async version
            elif hasattr(self._page_ref, 'client_storage'):
                self._page_ref.client_storage.set("is_dark_theme", self.is_dark)
        except Exception:
            pass

        # Session storage - wrap in try/except for API compatibility
        try:
            if hasattr(self._page_ref.session, 'set'):
                self._page_ref.session.set("is_dark_theme", self.is_dark)
        except Exception:
            pass

        # Apply page-level theme
        self._page_ref.theme_mode = ft.ThemeMode.DARK if self.is_dark else ft.ThemeMode.LIGHT
        self._page_ref.bgcolor = MD3Colors.get_background(self.is_dark)

        self._page_ref.theme = ft.Theme(
            color_scheme_seed="#2D6E88" if self.is_dark else "#1A5A70",
            use_material3=True,
        )

        self._page_ref.update()

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

        # Store theme state for component access
        try:
            if hasattr(self._page_ref, 'shared_preferences'):
                await self._page_ref.shared_preferences.set("is_dark_theme", self.is_dark)
            elif hasattr(self._page_ref, 'client_storage'):
                self._page_ref.client_storage.set("is_dark_theme", self.is_dark)
        except Exception:
            pass

        try:
            if hasattr(self._page_ref.session, 'set'):
                self._page_ref.session.set("is_dark_theme", self.is_dark)
        except Exception:
            pass

        # Apply page-level theme immediately
        self._page_ref.theme_mode = ft.ThemeMode.DARK if self.is_dark else ft.ThemeMode.LIGHT
        self._page_ref.bgcolor = MD3Colors.get_background(self.is_dark)

        self._page_ref.theme = ft.Theme(
            color_scheme_seed="#2D6E88" if self.is_dark else "#1A5A70",
            use_material3=True,
        )

        self._page_ref.update()

        # Cascade to registered components with progressive page updates
        await self._registry.apply_theme_to_all(
            self.is_dark,
            cascade=True,
            base_delay_ms=30,  # ~250ms total cascade duration
            page=self._page_ref,  # Enable progressive updates between batches
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
