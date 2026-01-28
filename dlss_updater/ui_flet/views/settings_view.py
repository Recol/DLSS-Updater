"""
Settings View
Hub for accessing all application settings: Update preferences, UI preferences, blacklist, etc.
"""

import logging

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin


class SettingsView(ThemeAwareMixin, ft.Column):
    """
    Settings hub view with cards for each settings category.
    Opens slide panels for actual settings content.
    """

    _theme_priority = 20

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        on_open_preferences=None,
        on_open_ui_preferences=None,
        on_open_blacklist=None,
        on_open_dlss_overlay=None,
        on_toggle_theme=None,
    ):
        super().__init__()
        self._page_ref = page
        self.logger = logger
        self.expand = True
        self.scroll = ft.ScrollMode.AUTO

        self._on_open_preferences = on_open_preferences
        self._on_open_ui_preferences = on_open_ui_preferences
        self._on_open_blacklist = on_open_blacklist
        self._on_open_dlss_overlay = on_open_dlss_overlay
        self._on_toggle_theme = on_toggle_theme

        is_dark = page.theme_mode == ft.ThemeMode.DARK

        settings_accent = TabColors.SETTINGS if is_dark else TabColors._TAB_COLORS_LIGHT.get("Settings", "#6A1B9A")

        # Per-tile icon colors (dark, light) for visual distinction
        # Each tile gets a unique color that works on both themes
        tile_colors = {
            "update_prefs": ("#2D6E88", "#1A5A70"),    # Teal (brand primary)
            "ui_prefs":     ("#9C27B0", "#6A1B9A"),    # Purple (settings accent)
            "blacklist":    ("#EF5350", "#C62828"),     # Red (warning/block)
            "dlss_overlay": ("#76B900", "#558B00"),     # NVIDIA green
            "theme":        ("#FF9800", "#E65100"),     # Amber (light/dark toggle)
        }

        def _tc(key: str) -> str:
            pair = tile_colors[key]
            return pair[0] if is_dark else pair[1]

        # Build settings tiles
        tiles = [
            self._create_settings_tile(
                "Update Preferences",
                "Configure DLL update behavior and scanning options",
                ft.Icons.TUNE,
                _tc("update_prefs"),
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_preferences, e),
            ),
            self._create_settings_tile(
                "UI Preferences",
                "Customize interface appearance and behavior",
                ft.Icons.PALETTE,
                _tc("ui_prefs"),
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_ui_preferences, e),
            ),
            self._create_settings_tile(
                "Blacklist",
                "Manage games excluded from updates",
                ft.Icons.BLOCK,
                _tc("blacklist"),
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_blacklist, e),
            ),
            self._create_settings_tile(
                "Theme",
                "Toggle between dark and light mode",
                ft.Icons.DARK_MODE if is_dark else ft.Icons.LIGHT_MODE,
                _tc("theme"),
                is_dark,
                on_click=lambda e: self._handle_click(self._on_toggle_theme, e),
            ),
        ]

        # Add DLSS overlay if available
        from dlss_updater.platform_utils import FEATURES
        if FEATURES.dlss_overlay:
            tiles.insert(3, self._create_settings_tile(
                "DLSS Overlay",
                "Configure NVIDIA DLSS overlay display options",
                ft.Icons.LAYERS,
                _tc("dlss_overlay"),
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_dlss_overlay, e),
            ))

        # Header
        header = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS, size=28, color=settings_accent),
                    ft.Text(
                        "Settings",
                        size=22,
                        weight=ft.FontWeight.W_600,
                        color=MD3Colors.get_on_surface(is_dark),
                    ),
                ],
                spacing=12,
            ),
            padding=ft.padding.only(bottom=16),
        )
        self._header_icon = header.content.controls[0]
        self._header_text = header.content.controls[1]

        # Settings list
        settings_column = ft.Column(
            controls=tiles,
            spacing=8,
        )

        # Wrap in responsive container
        self.controls = [
            ft.Container(
                content=ft.Column(
                    controls=[header, settings_column],
                    spacing=0,
                    expand=True,
                ),
                padding=ft.padding.all(24),
                expand=True,
            ),
        ]

        self._settings_tiles = tiles
        self._register_theme_aware()

    def _create_settings_tile(
        self,
        title: str,
        subtitle: str,
        icon: str,
        accent: str,
        is_dark: bool,
        on_click=None,
    ) -> ft.Container:
        """Create a single settings tile."""
        # Solid icon on a visible tinted background
        # Dark mode: white icon on accent-colored circle
        # Light mode: white icon on accent-colored circle (darkened accent for contrast)
        tile = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Icon(icon, size=22, color=ft.Colors.WHITE),
                        width=44,
                        height=44,
                        border_radius=12,
                        bgcolor=accent,
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                title,
                                size=15,
                                weight=ft.FontWeight.W_500,
                                color=MD3Colors.get_on_surface(is_dark),
                            ),
                            ft.Text(
                                subtitle,
                                size=12,
                                color=MD3Colors.get_on_surface_variant(is_dark),
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Icon(
                        ft.Icons.CHEVRON_RIGHT,
                        size=20,
                        color=MD3Colors.get_on_surface_variant(is_dark),
                    ),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.all(16),
            border_radius=12,
            bgcolor=MD3Colors.get_surface(is_dark),
            border=ft.border.all(1, MD3Colors.get_outline(is_dark)),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            on_click=on_click,
            ink=True,
        )
        return tile

    def _handle_click(self, callback, e):
        """Handle settings tile click with async support."""
        if callback:
            if self._page_ref:
                self._page_ref.run_task(callback, e)

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme to settings view."""
        if delay_ms > 0:
            import asyncio
            await asyncio.sleep(delay_ms / 1000)

        settings_accent = TabColors.SETTINGS if is_dark else TabColors._TAB_COLORS_LIGHT.get("Settings", "#6A1B9A")

        self._header_icon.color = settings_accent
        self._header_text.color = MD3Colors.get_on_surface(is_dark)

        # Tiles need full rebuild for theme change
        # They'll get updated via page restart (theme toggle restarts app)
        try:
            self.update()
        except Exception:
            pass
