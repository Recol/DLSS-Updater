"""
Settings View
Hub for accessing all application settings: Update preferences, UI preferences, blacklist, etc.
"""

import logging

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin
from dlss_updater.ui_flet.components.floating_pill import PILL_CLEARANCE

# Per-tile icon colors (dark, light) for visual distinction
TILE_COLORS = {
    "update_prefs": ("#2D6E88", "#1A5A70"),    # Teal (brand primary)
    "ui_prefs":     ("#9C27B0", "#6A1B9A"),    # Purple (settings accent)
    "blacklist":    ("#EF5350", "#C62828"),     # Red (warning/block)
    "ignore_list":  ("#FF9800", "#E65100"),     # Orange (personal ignore)
    "dlss_overlay": ("#76B900", "#558B00"),     # NVIDIA green
    "dlss_linux_presets": ("#4FC3F7", "#0288D1"),  # Light blue (Linux DLSS)
    "theme":        ("#FF9800", "#E65100"),     # Amber (light/dark toggle)
    "check_updates": ("#2196F3", "#0D47A1"),   # Blue (app updates)
}


class SettingsView(ThemeAwareMixin, ft.Column):
    """
    Settings hub view with tiles for each settings category.
    Opens slide panels for actual settings content.

    Tiles are laid out 2-up on medium+ windows via ResponsiveRow, with the
    theme toggle inlined as a Switch (no navigation hop for the most-used
    setting).
    """

    _theme_priority = 20

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        on_open_preferences=None,
        on_open_ui_preferences=None,
        on_open_blacklist=None,
        on_open_ignore_list=None,
        on_open_dlss_overlay=None,
        on_open_dlss_sr_presets=None,
        on_toggle_theme=None,
        on_check_updates=None,
    ):
        super().__init__()
        self._page_ref = page
        self.logger = logger
        self.expand = True
        self.scroll = ft.ScrollMode.AUTO

        self._on_open_preferences = on_open_preferences
        self._on_open_ui_preferences = on_open_ui_preferences
        self._on_open_blacklist = on_open_blacklist
        self._on_open_ignore_list = on_open_ignore_list
        self._on_open_dlss_overlay = on_open_dlss_overlay
        self._on_open_dlss_sr_presets = on_open_dlss_sr_presets
        self._on_toggle_theme = on_toggle_theme
        self._on_check_updates = on_check_updates

        is_dark = page.theme_mode == ft.ThemeMode.DARK

        settings_accent = TabColors.SETTINGS if is_dark else TabColors._TAB_COLORS_LIGHT.get("Settings", "#6A1B9A")

        # Tile metadata for theme recoloring: list of dicts with control refs
        self._tile_meta: list[dict] = []

        # Inline theme switch (replaces navigation hop for theme toggle)
        self._theme_switch = ft.Switch(
            value=is_dark,
            on_change=lambda e: self._handle_click(self._on_toggle_theme, e),
            tooltip="Toggle dark mode",
        )

        # Build settings tiles
        tiles = [
            self._create_settings_tile(
                "Update Preferences",
                "Configure DLL update behavior and scanning options",
                ft.Icons.TUNE,
                "update_prefs",
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_preferences, e),
            ),
            self._create_settings_tile(
                "UI Preferences",
                "Customize interface appearance and behavior",
                ft.Icons.PALETTE,
                "ui_prefs",
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_ui_preferences, e),
            ),
            self._create_settings_tile(
                "Blacklist",
                "Manage games excluded from updates",
                ft.Icons.BLOCK,
                "blacklist",
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_blacklist, e),
            ),
            self._create_settings_tile(
                "Ignored Games",
                "Manage your personal game ignore list",
                ft.Icons.VISIBILITY_OFF,
                "ignore_list",
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_ignore_list, e),
            ),
            self._create_settings_tile(
                "Theme",
                "Toggle between dark and light mode",
                ft.Icons.DARK_MODE if is_dark else ft.Icons.LIGHT_MODE,
                "theme",
                is_dark,
                trailing=self._theme_switch,
            ),
            self._create_settings_tile(
                "Check for Updates",
                "Check if a newer version of DLSS Updater is available",
                ft.Icons.SYSTEM_UPDATE,
                "check_updates",
                is_dark,
                on_click=lambda e: self._handle_click(self._on_check_updates, e),
            ),
        ]

        # Add DLSS overlay if available
        from dlss_updater.platform_utils import FEATURES
        if FEATURES.dlss_overlay:
            tiles.insert(3, self._create_settings_tile(
                "DLSS Overlay",
                "Configure NVIDIA DLSS overlay display options",
                ft.Icons.LAYERS,
                "dlss_overlay",
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_dlss_overlay, e),
            ))

        # Add Linux DLSS SR Presets if available (Linux only with NVIDIA GPU)
        if FEATURES.dlss_linux_presets:
            tiles.insert(4, self._create_settings_tile(
                "Linux DLSS SR Presets",
                "Configure Super Resolution presets for Proton/Wine",
                ft.Icons.TUNE,
                "dlss_linux_presets",
                is_dark,
                on_click=lambda e: self._handle_click(self._on_open_dlss_sr_presets, e),
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
            padding=ft.Padding.only(bottom=16),
        )
        self._header_icon = header.content.controls[0]
        self._header_text = header.content.controls[1]

        # Settings grid: 2-up on medium+ windows, single column on narrow
        settings_grid = ft.ResponsiveRow(
            controls=tiles,
            spacing=8,
            run_spacing=8,
        )

        # Wrap in responsive container; bottom padding keeps the last tile
        # clear of the floating pill
        self.controls = [
            ft.Container(
                content=ft.Column(
                    controls=[header, settings_grid],
                    spacing=0,
                    expand=True,
                ),
                padding=ft.Padding.only(
                    left=24, right=24, top=24, bottom=PILL_CLEARANCE
                ),
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
        color_key: str,
        is_dark: bool,
        on_click=None,
        trailing: ft.Control | None = None,
    ) -> ft.Container:
        """Create a single settings tile (2-up responsive, dense padding)."""
        accent = TILE_COLORS[color_key][0] if is_dark else TILE_COLORS[color_key][1]

        icon_widget = ft.Icon(icon, size=22, color=ft.Colors.WHITE)
        icon_circle = ft.Container(
            content=icon_widget,
            width=44,
            height=44,
            border_radius=12,
            bgcolor=accent,
            alignment=ft.Alignment.CENTER,
        )
        title_text = ft.Text(
            title,
            size=15,
            weight=ft.FontWeight.W_500,
            color=MD3Colors.get_on_surface(is_dark),
        )
        subtitle_text = ft.Text(
            subtitle,
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        trailing_control = trailing or ft.Icon(
            ft.Icons.CHEVRON_RIGHT,
            size=20,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )

        tile = ft.Container(
            content=ft.Row(
                controls=[
                    icon_circle,
                    ft.Column(
                        controls=[title_text, subtitle_text],
                        spacing=2,
                        expand=True,
                    ),
                    trailing_control,
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border_radius=12,
            bgcolor=MD3Colors.get_surface(is_dark),
            border=ft.Border.all(1, MD3Colors.get_outline(is_dark)),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            on_click=on_click,
            ink=on_click is not None,
            col={"xs": 12, "md": 6},
        )

        self._tile_meta.append({
            "tile": tile,
            "color_key": color_key,
            "icon_circle": icon_circle,
            "icon_widget": icon_widget,
            "title_text": title_text,
            "subtitle_text": subtitle_text,
            # Chevron needs recoloring; an inline trailing control themes itself
            "chevron": trailing_control if trailing is None else None,
        })
        return tile

    def _handle_click(self, callback, e):
        """Handle settings tile click with async support."""
        if callback:
            if self._page_ref:
                self._page_ref.run_task(callback, e)

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme to settings view, recoloring all tiles in place."""
        if delay_ms > 0:
            import asyncio
            await asyncio.sleep(delay_ms / 1000)

        settings_accent = TabColors.SETTINGS if is_dark else TabColors._TAB_COLORS_LIGHT.get("Settings", "#6A1B9A")

        self._header_icon.color = settings_accent
        self._header_text.color = MD3Colors.get_on_surface(is_dark)

        # Keep the inline theme switch in sync with the active theme
        self._theme_switch.value = is_dark

        for meta in self._tile_meta:
            pair = TILE_COLORS[meta["color_key"]]
            accent = pair[0] if is_dark else pair[1]
            meta["icon_circle"].bgcolor = accent
            meta["title_text"].color = MD3Colors.get_on_surface(is_dark)
            meta["subtitle_text"].color = MD3Colors.get_on_surface_variant(is_dark)
            if meta["chevron"] is not None:
                meta["chevron"].color = MD3Colors.get_on_surface_variant(is_dark)
            tile = meta["tile"]
            tile.bgcolor = MD3Colors.get_surface(is_dark)
            tile.border = ft.Border.all(1, MD3Colors.get_outline(is_dark))
            # Theme tile icon reflects the active mode
            if meta["color_key"] == "theme":
                meta["icon_widget"].name = ft.Icons.DARK_MODE if is_dark else ft.Icons.LIGHT_MODE

        try:
            self.update()
        except Exception:
            pass
