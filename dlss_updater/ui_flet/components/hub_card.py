"""
Hub Card Components
Hero-styled cards for the hub home screen.

Two cards live here:

- ``HubCard`` — the compact 280px-column side cards (Launchers, DLSS
  Settings, Settings). A "brand wash" gradient + an oversized watermark
  glyph replace the old flat surface + left accent border; identity
  (icon/title/subtitle) anchors bottom-left, matching the game library's
  hero card idiom (see ``game_card.py``).
- ``GamesHeroCard`` — the large right-hand Games card. A true photographic
  hero: a 3x2 mosaic of the user's own cached game artwork sits behind a
  bottom-weighted scrim, with the identity block + stat pills overlaid.
  Falls back to the same brand-wash + watermark treatment as ``HubCard``
  when too little cached art exists (e.g. a fresh install).

Both share the "hero surface" primitives (gradients/watermark/pills) from
``hero_surface.py`` rather than duplicating them.
"""

import itertools

import anyio
import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.components.hero_surface import (
    WATERMARK_OPACITY_DARK,
    WATERMARK_OPACITY_LIGHT,
    build_brand_wash,
    build_pill,
    build_scrim_gradient,
    build_watermark_icon,
    themed_accent,
)

# Small inline icon that sits next to the title in the bottom-left identity
# block (distinct from the oversized decorative watermark glyph, which reuses
# the same icon name at a much larger size).
IDENTITY_ICON_SIZE = 22

# Watermark glyph size range for the compact side cards (hero_surface's own
# default of 110 is tuned for the larger Games hero card).
_SIDE_WATERMARK_MIN = 96
_SIDE_WATERMARK_MAX = 110


class HubCard(ThemeAwareMixin, ft.Container):
    """
    A hub navigation card styled as a compact "brand wash" hero: a diagonal
    accent-tinted gradient with an oversized watermark glyph bottom-right,
    and a bottom-left identity block (icon + title + subtitle + stat pill).

    Props:
        title: Card title text
        subtitle: Card subtitle/description text
        icon: ft.Icons icon name (used for both the inline identity icon and
            the oversized watermark glyph)
        accent_color_dark: Accent color for dark mode
        accent_color_light: Accent color for light mode
        icon_size: Drives the watermark glyph size (default 40 -> ~100px watermark)
        title_size: Size of the title text (default 18)
        on_click: Click callback for navigation
        border_radius_val: Border radius (default 16)
    """

    _theme_priority = 15

    def __init__(
        self,
        title: str,
        subtitle: str,
        icon: str,
        accent_color_dark: str,
        accent_color_light: str,
        icon_size: int = 40,
        title_size: int = 18,
        on_click=None,
        border_radius_val: int = 16,
        page: ft.Page | None = None,
    ):
        self._page_ref = page
        self._title = title
        self._subtitle = subtitle
        self._icon = icon
        self._accent_dark = accent_color_dark
        self._accent_light = accent_color_light
        self._icon_size = icon_size
        self._title_size = title_size
        self._border_radius_val = border_radius_val
        self._on_click_callback = on_click

        # Read from the ThemeRegistry singleton (single source of truth kept
        # in lockstep with page.theme_mode by ThemeManager), matching the
        # established convention used by game_card.py's GameCard. This
        # avoids depending on `page.theme_mode` having already been set by
        # the time this control constructs.
        is_dark = get_theme_registry().is_dark
        accent = themed_accent((accent_color_dark, accent_color_light), is_dark)

        # ---- Bottom-left identity block: small icon + title + subtitle ----
        self._icon_widget = ft.Icon(
            icon,
            size=IDENTITY_ICON_SIZE,
            color=accent,
        )

        self._title_text = ft.Text(
            title,
            size=title_size,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_on_surface(is_dark),
        )

        self._subtitle_text = ft.Text(
            subtitle,
            size=13,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )

        # Stats pill (optional, hidden by default) — Container-based badge,
        # not ft.Chip, per CLAUDE.md's "Container-based Badge Pattern".
        self._stats_pill = build_pill(
            "",
            bgcolor=ft.Colors.with_opacity(0.14, accent),
            text_color=accent,
        )
        self._stats_pill_text: ft.Text = self._stats_pill.content.controls[-1]
        self._stats_pill.visible = False

        # Optional secondary stats line (e.g. backups / last scan detail)
        self._stats_detail_text = ft.Text(
            "",
            size=11,
            color=MD3Colors.get_on_surface_variant(is_dark),
            visible=False,
        )

        identity_column = ft.Column(
            controls=[
                ft.Row(
                    controls=[self._icon_widget, self._title_text],
                    spacing=8,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._subtitle_text,
                ft.Container(height=6),
                self._stats_pill,
                ft.Container(height=4),
                self._stats_detail_text,
            ],
            spacing=4,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

        identity_overlay = ft.Container(
            content=identity_column,
            left=0,
            bottom=0,
            right=0,
            padding=ft.Padding.only(left=18, right=18, bottom=16, top=16),
        )

        # ---- Oversized watermark glyph, bottom-right, deliberately clipped ----
        watermark_size = min(_SIDE_WATERMARK_MAX, max(_SIDE_WATERMARK_MIN, icon_size + 60))
        self._watermark = build_watermark_icon(icon, is_dark, size=watermark_size)
        self._watermark.right = -14
        self._watermark.bottom = -14

        card_content = ft.Stack(
            controls=[self._watermark, identity_overlay],
            expand=True,
        )

        super().__init__(
            content=card_content,
            padding=ft.Padding.all(0),
            border_radius=border_radius_val,
            bgcolor=MD3Colors.get_surface(is_dark),
            gradient=build_brand_wash(accent, is_dark),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=Shadows.LEVEL_2,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            scale=1.0,
            expand=True,
            on_click=on_click,
            on_hover=self._on_hover,
            ink=True,
        )

        self._register_theme_aware()

    def did_mount(self):
        """Defensive theme re-sync on every (re)mount.

        HubView is toggled in/out of the page tree via the nav controller's
        content-detachment pattern (see CLAUDE.md). If a theme toggle fires
        while this card is detached, ThemeAwareMixin.apply_theme() still
        mutates every property correctly, but its own self.update() call
        raises (BaseControl.update() requires an attached page) and is
        silently swallowed - so the corrected state may not always have
        reached the client by the time this card is detached.

        did_mount() is guaranteed to fire only once the card is genuinely
        (re)attached, so scheduling a fresh apply_theme() here - against
        whatever the registry's CURRENT is_dark is - is a cheap, idempotent
        safety net that self-heals regardless of the exact cause. Scheduled
        via run_task rather than called inline to avoid re-entering the
        session's in-flight patch/mount processing.
        """
        page = self._page_ref
        if page is not None and hasattr(page, "run_task"):
            try:
                page.run_task(self.apply_theme, get_theme_registry().is_dark)
            except Exception:
                pass

    def _on_hover(self, e):
        """Handle hover effect - scale + shadow (wash replaces the old left accent bar)."""
        if e.data == "true":
            max_scale = 1.01 if self._icon_size >= 64 else 1.02
            self.scale = max_scale
            self.shadow = Shadows.LEVEL_3
        else:
            self.scale = 1.0
            self.shadow = Shadows.LEVEL_2

        if self._page_ref:
            self.update()

    def set_stats(self, text: str):
        """Update the stats pill text and show it."""
        if text:
            self._stats_pill_text.value = text
            self._stats_pill.visible = True
        else:
            self._stats_pill.visible = False

    def set_stats_detail(self, text: str):
        """Update the secondary stats line below the pill."""
        if text:
            self._stats_detail_text.value = text
            self._stats_detail_text.visible = True
        else:
            self._stats_detail_text.visible = False

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme colors to all sub-elements, including the wash gradient
        and watermark opacity (both must rebuild per-theme, not just recolor)."""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        accent = themed_accent((self._accent_dark, self._accent_light), is_dark)

        self.bgcolor = MD3Colors.get_surface(is_dark)
        self.gradient = build_brand_wash(accent, is_dark)

        self._icon_widget.color = accent
        self._title_text.color = MD3Colors.get_on_surface(is_dark)
        self._subtitle_text.color = MD3Colors.get_on_surface_variant(is_dark)
        self._stats_pill.bgcolor = ft.Colors.with_opacity(0.14, accent)
        self._stats_pill_text.color = accent
        self._stats_detail_text.color = MD3Colors.get_on_surface_variant(is_dark)
        self._watermark.opacity = WATERMARK_OPACITY_DARK if is_dark else WATERMARK_OPACITY_LIGHT

        try:
            self.update()
        except Exception:
            pass


class GamesHeroCard(ThemeAwareMixin, ft.Container):
    """
    The large Games hub card, restyled as a true artwork-backed hero.

    Layered ft.Stack: art mosaic (up to 6 cached game covers) -> bottom-weighted
    scrim -> bottom-left identity block (icon + title + subtitle + stat pills).
    When fewer than 2 cached art images are available (e.g. a fresh install),
    falls back to the same brand-wash + watermark treatment as ``HubCard`` so
    the card still looks intentional with zero art.

    The mosaic is populated once via ``set_mosaic()`` and is static afterward
    (no per-image updates) — callers own the single ``page.update()`` call.
    """

    _theme_priority = 15

    def __init__(
        self,
        title: str,
        subtitle: str,
        icon: str,
        accent_color_dark: str,
        accent_color_light: str,
        on_click=None,
        border_radius_val: int = 20,
        page: ft.Page | None = None,
    ):
        self._page_ref = page
        self._title = title
        self._icon = icon
        self._accent_dark = accent_color_dark
        self._accent_light = accent_color_light
        self._border_radius_val = border_radius_val
        self._mosaic_active = False

        # Read from the ThemeRegistry singleton (single source of truth kept
        # in lockstep with page.theme_mode by ThemeManager), matching the
        # established convention used by game_card.py's GameCard. This
        # avoids depending on `page.theme_mode` having already been set by
        # the time this control constructs.
        is_dark = get_theme_registry().is_dark
        accent = themed_accent((accent_color_dark, accent_color_light), is_dark)

        # ---- Bottom-left identity block ----
        self._icon_widget = ft.Icon(
            icon,
            size=28,
            color=MD3Colors.get_text_primary(is_dark),
        )

        self._title_text = ft.Text(
            title,
            size=22,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
        )

        self._subtitle_text = ft.Text(
            subtitle,
            size=13,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )

        # Stat pills row (game count / backups / last scan age) — populated by
        # set_pills(). Pills use a fixed dark translucent bg + white text so
        # they stay legible over arbitrary user artwork in BOTH themes (same
        # convention as game_card.py's overlay_cluster icon buttons).
        self._pills_row = ft.Row(controls=[], spacing=8, wrap=True)

        identity_column = ft.Column(
            controls=[
                ft.Row(
                    controls=[self._icon_widget, self._title_text],
                    spacing=10,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._subtitle_text,
                ft.Container(height=8),
                self._pills_row,
            ],
            spacing=4,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

        identity_overlay = ft.Container(
            content=identity_column,
            left=0,
            bottom=0,
            right=0,
            padding=ft.Padding.only(left=24, right=24, bottom=22, top=16),
        )

        # ---- Fallback layer: brand wash + watermark (default/no-art state) ----
        self._wash_layer = ft.Container(
            expand=True,
            gradient=build_brand_wash(accent, is_dark),
        )
        self._watermark = build_watermark_icon(icon, is_dark, size=110)
        self._watermark.right = -18
        self._watermark.bottom = -18

        # ---- Art layer (populated by set_mosaic) + its scrim ----
        self._art_layer = ft.Container(
            expand=True,
            content=None,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )
        self._scrim_layer = ft.Container(
            expand=True,
            gradient=build_scrim_gradient(is_dark),
            visible=False,
        )

        self._stack = ft.Stack(
            controls=[
                self._art_layer,
                self._wash_layer,
                self._watermark,
                self._scrim_layer,
                identity_overlay,
            ],
            expand=True,
        )

        super().__init__(
            content=self._stack,
            padding=ft.Padding.all(0),
            border_radius=border_radius_val,
            bgcolor=MD3Colors.get_surface(is_dark),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=Shadows.LEVEL_2,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            scale=1.0,
            expand=True,
            on_click=on_click,
            on_hover=self._on_hover,
            ink=True,
        )

        self._register_theme_aware()

    def did_mount(self):
        """Defensive theme re-sync on every (re)mount - see HubCard.did_mount()
        for the full rationale (nav controller's content-detachment pattern
        can leave a swallowed apply_theme() self.update() unflushed)."""
        page = self._page_ref
        if page is not None and hasattr(page, "run_task"):
            try:
                page.run_task(self.apply_theme, get_theme_registry().is_dark)
            except Exception:
                pass

    def _on_hover(self, e):
        """Handle hover effect - scale + shadow (unchanged from the pre-hero HubCard)."""
        if e.data == "true":
            self.scale = 1.01  # smaller scale for the large card, matches prior behavior
            self.shadow = Shadows.LEVEL_3
        else:
            self.scale = 1.0
            self.shadow = Shadows.LEVEL_2

        if self._page_ref:
            self.update()

    def set_mosaic(self, paths: list[str]) -> None:
        """Populate the 3x2 art mosaic from up to 6 local cached image paths.

        No-op (keeps the brand-wash + watermark fallback) when fewer than 2
        paths are supplied — a fresh install with 0-1 cached covers should
        still look intentional. Static after this call: no per-image updates,
        no internal page.update() — the caller (hub_view.load_stats) owns the
        single page.update() for the whole batch.
        """
        if len(paths) < 2 or self._mosaic_active:
            return

        cells = list(paths[:6])
        if len(cells) < 6:
            cells = list(itertools.islice(itertools.cycle(cells), 6))

        def _cell(path: str) -> ft.Image:
            return ft.Image(
                src=path,
                expand=True,
                fit=ft.BoxFit.COVER,
                error_content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=24, color=ft.Colors.GREY),
            )

        columns = [
            ft.Column([_cell(cells[col]), _cell(cells[col + 3])], spacing=0, expand=True)
            for col in range(3)
        ]

        self._art_layer.content = ft.Row(columns, spacing=0, expand=True)
        self._wash_layer.visible = False
        self._watermark.visible = False
        self._scrim_layer.visible = True
        self._mosaic_active = True

    def set_pills(self, pills: list[tuple[str, str | None]]) -> None:
        """Rebuild the stat pill row. Each item is (text, optional ft.Icons icon)."""
        self._pills_row.controls = [
            build_pill(
                text,
                icon=icon,
                bgcolor=ft.Colors.with_opacity(0.55, ft.Colors.BLACK),
                text_color=ft.Colors.WHITE,
            )
            for text, icon in pills
            if text
        ]

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme: wash + scrim gradients and watermark opacity all rebuild
        per-theme. Pills are intentionally theme-invariant (dark chip + white
        text reads over arbitrary art in both themes) so they're left alone."""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        accent = themed_accent((self._accent_dark, self._accent_light), is_dark)

        self.bgcolor = MD3Colors.get_surface(is_dark)
        self._wash_layer.gradient = build_brand_wash(accent, is_dark)
        self._scrim_layer.gradient = build_scrim_gradient(is_dark)
        self._watermark.opacity = WATERMARK_OPACITY_DARK if is_dark else WATERMARK_OPACITY_LIGHT

        self._icon_widget.color = MD3Colors.get_text_primary(is_dark)
        self._title_text.color = MD3Colors.get_text_primary(is_dark)
        self._subtitle_text.color = MD3Colors.get_on_surface_variant(is_dark)

        try:
            self.update()
        except Exception:
            pass
