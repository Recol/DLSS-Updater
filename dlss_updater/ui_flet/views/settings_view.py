"""
Settings View
Hub for accessing all application settings: Update preferences, UI preferences, blacklist, etc.
"""

import anyio
import logging

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors, Shadows
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin
from dlss_updater.ui_flet.components.floating_pill import PILL_CLEARANCE
from dlss_updater.ui_flet.components.hero_surface import (
    build_brand_wash,
    build_watermark_icon,
    themed_accent,
    HOVER_ANIM_MS,
)

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

# Settings tiles are the RESTRAINT area of the hero design: the brand wash and
# icon watermark are dialed down well below hero_surface's card-level defaults
# (WASH_OPACITY_*/WATERMARK_OPACITY_*) so tiles stay quiet and instantly
# scannable — a tint, not a poster.
TILE_WASH_OPACITY_DARK = 0.12
TILE_WASH_OPACITY_LIGHT = 0.08
TILE_WATERMARK_OPACITY_DARK = 0.06
TILE_WATERMARK_OPACITY_LIGHT = 0.03

# Watermark glyph geometry: sized to bleed off the tile's bottom-right corner
# (clipped by the tile's clip_behavior) rather than sit fully inside it.
TILE_WATERMARK_SIZE = 76
TILE_WATERMARK_OFFSET = -16


def _tile_wash_opacity(is_dark: bool) -> float:
    return TILE_WASH_OPACITY_DARK if is_dark else TILE_WASH_OPACITY_LIGHT


def _tile_watermark_opacity(is_dark: bool) -> float:
    return TILE_WATERMARK_OPACITY_DARK if is_dark else TILE_WATERMARK_OPACITY_LIGHT


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

        # Add Proton upscaler options if available (Linux only)
        if FEATURES.dlss_linux_presets:
            tiles.insert(4, self._create_settings_tile(
                "Proton Upscalers",
                "DLSS, FSR 4 & XeSS launch options for Proton/Wine",
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
        """Create a single settings tile (2-up responsive, dense padding).

        Structure: an outer Container (surface bgcolor, rounded, clipped) hosts
        a Stack of [brand wash, icon watermark, foreground content] so the wash
        and watermark can bleed across the full tile without disturbing the
        original padding/layout of the foreground row.
        """
        accent = themed_accent(TILE_COLORS[color_key], is_dark)

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

        # Brand wash: diagonal accent -> transparent, quieter than hub cards.
        # Positioned to fill the Stack (left/top/right/bottom=0) so it sits as
        # a layer directly above the tile's own surface bgcolor.
        wash_container = ft.Container(
            gradient=build_brand_wash(accent, is_dark, opacity=_tile_wash_opacity(is_dark)),
            left=0,
            top=0,
            right=0,
            bottom=0,
        )

        # Decorative watermark of the tile's own icon, bled off the
        # bottom-right corner and clipped by the tile's clip_behavior.
        watermark_container = build_watermark_icon(icon, is_dark, size=TILE_WATERMARK_SIZE)
        watermark_container.opacity = _tile_watermark_opacity(is_dark)
        watermark_container.right = TILE_WATERMARK_OFFSET
        watermark_container.bottom = TILE_WATERMARK_OFFSET
        watermark_widget = watermark_container.content  # ft.Icon, recolored/reshaped in apply_theme

        # Foreground content keeps the tile's original padding/layout. It is
        # NOT stack-positioned, so it (not the fill layers) determines the
        # Stack's natural size. Trailing control stays above the watermark.
        foreground = ft.Container(
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
        )

        tile = ft.Container(
            content=ft.Stack(controls=[wash_container, watermark_container, foreground]),
            border_radius=12,
            bgcolor=MD3Colors.get_surface(is_dark),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(HOVER_ANIM_MS, ft.AnimationCurve.EASE_OUT),
            scale=1.0,
            shadow=None,
            on_click=on_click,
            ink=on_click is not None,
            col={"xs": 12, "md": 6},
        )
        tile.on_hover = lambda e, t=tile: self._on_tile_hover(e, t)

        self._tile_meta.append({
            "tile": tile,
            "color_key": color_key,
            "icon_circle": icon_circle,
            "icon_widget": icon_widget,
            "title_text": title_text,
            "subtitle_text": subtitle_text,
            "wash_container": wash_container,
            "watermark_container": watermark_container,
            "watermark_widget": watermark_widget,
            # Chevron needs recoloring; an inline trailing control themes itself
            "chevron": trailing_control if trailing is None else None,
        })
        return tile

    def _on_tile_hover(self, e, tile: ft.Container) -> None:
        """Subtle hover motion: small scale-up + a modest shadow.

        Dialed back from hub_card.py's hover (scale 1.01-1.02 / Shadows.LEVEL_3
        multi-layer glow) to match the settings-tile restraint mandate — a
        single-layer LEVEL_1 shadow reads as "lifted" without competing with
        the wash/watermark tint already on the tile.
        """
        if e.data is True or e.data == "true":
            tile.scale = 1.01
            tile.shadow = Shadows.LEVEL_1
        else:
            tile.scale = 1.0
            tile.shadow = None
        if self._page_ref:
            tile.update()

    def _handle_click(self, callback, e):
        """Handle settings tile click with async support."""
        if callback:
            if self._page_ref:
                self._page_ref.run_task(callback, e)

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme to settings view, recoloring all tiles in place."""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

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
            # Rebuild the brand wash + watermark for the new theme's accent/opacity
            meta["wash_container"].gradient = build_brand_wash(
                accent, is_dark, opacity=_tile_wash_opacity(is_dark)
            )
            meta["watermark_container"].opacity = _tile_watermark_opacity(is_dark)
            # Theme tile icon (+ its watermark twin) reflects the active mode
            if meta["color_key"] == "theme":
                new_icon = ft.Icons.DARK_MODE if is_dark else ft.Icons.LIGHT_MODE
                meta["icon_widget"].name = new_icon
                meta["watermark_widget"].name = new_icon

        try:
            self.update()
        except Exception:
            pass
