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
- ``HubActionCard`` — the hub's primary call-to-action band, sitting under
  the Games hero. Same wash + watermark language, but it is an ACTION
  surface rather than a navigation target: the card itself has no
  ``on_click`` and hosts its own primary/secondary buttons.

All three share the "hero surface" primitives (gradients/watermark/pills)
from ``hero_surface.py`` rather than duplicating them.
"""

import itertools

import anyio
import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows, TabColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.components.hero_surface import (
    WATERMARK_OPACITY_DARK,
    WATERMARK_OPACITY_LIGHT,
    art_tint_color,
    build_art_scrim_gradient,
    build_brand_wash,
    build_pill,
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

# Height of the hub's call-to-action band (see HubActionCard). Deliberately
# short: it sits under the Games hero and must not compete with it.
ACTION_CARD_HEIGHT = 96


def _on_accent(is_dark: bool) -> str:
    """Legible foreground for text/icons on a FILLED warning/success accent.

    Those two roles invert across themes (warning is a light amber #FFB74D in
    dark mode and a dark amber #7A5800 in light mode, success likewise), so a
    fixed white foreground is unreadable on the dark-mode fill. Only used for
    solid accent fills — outlined/tinted surfaces keep the normal on-surface
    colors.
    """
    return ft.Colors.BLACK87 if is_dark else ft.Colors.WHITE


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
        wash_opacity_dark: float | None = None,
        wash_opacity_light: float | None = None,
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
        # Per-tile wash strength override (None -> hero_surface defaults). Used
        # to lift low-chroma accents (e.g. the Launchers teal) so they read as
        # tinted as the higher-chroma tiles at the shared default alpha.
        self._wash_opacity_dark = wash_opacity_dark
        self._wash_opacity_light = wash_opacity_light

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
            gradient=build_brand_wash(accent, is_dark, opacity=self._wash_opacity(is_dark)),
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

    def _wash_opacity(self, is_dark: bool) -> float | None:
        """Themed per-tile wash alpha override (None -> hero_surface default)."""
        return self._wash_opacity_dark if is_dark else self._wash_opacity_light

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
        if e.data is True or e.data == "true":
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
        self.gradient = build_brand_wash(accent, is_dark, opacity=self._wash_opacity(is_dark))

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

    Layered ft.Stack: art mosaic (up to 6 cached game covers) -> flat unifying
    tint -> bottom-weighted scrim -> bottom-left identity block (icon + title +
    subtitle + stat pills). When fewer than 2 cached art images are available
    (e.g. a fresh install), falls back to the same brand-wash + watermark
    treatment as ``HubCard`` so the card still looks intentional with zero art.

    Every alpha-carrying overlay lives on a shadow-LESS Container inside the
    Stack; the shadow belongs to the outer card Container, which paints only an
    opaque bgcolor (CLAUDE.md rendering pitfall #1).

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
        # Colors are (re)derived by _apply_identity_colors() rather than fixed
        # here: they depend on which backdrop is live, not only on the theme.
        self._icon_widget = ft.Icon(icon, size=28)

        self._title_text = ft.Text(title, size=22, weight=ft.FontWeight.BOLD)

        self._subtitle_text = ft.Text(subtitle, size=13)

        self._apply_identity_colors(is_dark)

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

        # ---- Art layer (populated by set_mosaic) + its overlays ----
        self._art_layer = ft.Container(
            expand=True,
            content=None,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )
        # Both overlays are stretched by explicit edge positioning rather than
        # expand=True: ft.Stack's fit defaults to LOOSE, and a childless
        # Container has no flex parent to expand into. ignore_interactions
        # keeps them out of the hit test so the whole hero stays one tap target.
        self._art_tint_layer = ft.Container(
            left=0,
            top=0,
            right=0,
            bottom=0,
            bgcolor=art_tint_color(is_dark),
            ignore_interactions=True,
            visible=False,
        )
        self._scrim_layer = ft.Container(
            left=0,
            top=0,
            right=0,
            bottom=0,
            gradient=build_art_scrim_gradient(is_dark),
            ignore_interactions=True,
            visible=False,
        )

        self._stack = ft.Stack(
            controls=[
                self._art_layer,
                self._art_tint_layer,
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
            border=self._hero_border(is_dark),
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

    @staticmethod
    def _hero_border(is_dark: bool) -> ft.Border:
        """Hairline outline around the whole hero.

        Load-bearing in light mode: a bright cover in the mosaic is close
        enough to the page canvas (#FAFBFC) that the card's edge disappears
        entirely without it. Kept in dark mode too so the treatment reads as
        deliberate rather than as a light-mode patch.
        """
        return ft.Border.all(1, MD3Colors.get_themed("outline_variant", is_dark))

    def _identity_colors(self, is_dark: bool) -> tuple[str, str]:
        """``(title/icon color, subtitle color)`` for whichever backdrop is live.

        Over the mosaic the caption sits on the BLACK art scrim in BOTH themes,
        so light mode's dark on-surface text would be unreadable there. The
        artwork-less fallback keeps the normal themed text colors, since it
        renders over a themed brand wash.
        """
        if self._mosaic_active:
            return ft.Colors.WHITE, ft.Colors.WHITE70
        return MD3Colors.get_text_primary(is_dark), MD3Colors.get_on_surface_variant(is_dark)

    def _apply_identity_colors(self, is_dark: bool) -> None:
        """Repaint the identity block for the current backdrop + theme."""
        title_color, subtitle_color = self._identity_colors(is_dark)
        self._icon_widget.color = title_color
        self._title_text.color = title_color
        self._subtitle_text.color = subtitle_color

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
        if e.data is True or e.data == "true":
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
        self._art_tint_layer.visible = True
        self._scrim_layer.visible = True
        self._mosaic_active = True

        # The caption flips to light-on-dark now that it sits on the art scrim
        # rather than on the themed brand wash.
        self._apply_identity_colors(get_theme_registry().is_dark)

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
        """Apply theme: wash/scrim gradients, art tint, outline and watermark
        opacity all rebuild per-theme. Pills are intentionally theme-invariant
        (dark chip + white text reads over arbitrary art in both themes) so
        they're left alone."""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        accent = themed_accent((self._accent_dark, self._accent_light), is_dark)

        self.bgcolor = MD3Colors.get_surface(is_dark)
        self.border = self._hero_border(is_dark)
        self._wash_layer.gradient = build_brand_wash(accent, is_dark)
        self._art_tint_layer.bgcolor = art_tint_color(is_dark)
        self._scrim_layer.gradient = build_art_scrim_gradient(is_dark)
        self._watermark.opacity = WATERMARK_OPACITY_DARK if is_dark else WATERMARK_OPACITY_LIGHT

        self._apply_identity_colors(is_dark)

        try:
            self.update()
        except Exception:
            pass


class HubActionCard(ThemeAwareMixin, ft.Container):
    """
    The hub's primary call-to-action band (sits directly under the Games hero).

    Until 4.6.0 the two primary actions of the whole application ("Scan for
    Games" / "Start Update") existed ONLY in the Launchers action bar, so the
    landing screen reported state ("16 games found · 8 backups") and offered
    nothing to do about it. This card is that missing action surface.

    Three states, driven entirely by ``set_state()``:

    * **nothing scanned yet** — accent = GAMES blue, primary = "Scan for games"
    * **N outdated** — accent = WARNING amber, primary = "Update all (N)",
      plus a "Rescan" affordance when the scan cache is stale
    * **nothing outdated** — accent = SUCCESS green, a calm "Everything is up
      to date" with NO primary button. A greyed-out button would read as
      something broken; the absence of one reads as "nothing to do".

    Visually it reuses HubCard's language (opaque brand wash + oversized
    watermark glyph + bottom-anchored identity), but unlike the navigation
    cards it is NOT clickable as a whole — its buttons own every tap, so
    there is no ambiguity between "navigate" and "start a 5-minute update".
    """

    _theme_priority = 15

    def __init__(
        self,
        page: ft.Page | None = None,
        on_update_all=None,
        on_scan=None,
        get_scope=None,
        on_scope_changed=None,
    ):
        self._page_ref = page
        self._on_update_all = on_update_all
        self._on_scan = on_scan

        # State (populated by set_state(); the defaults describe a fresh install)
        self._needs_update = 0
        self._game_count = 0
        self._has_scan = False
        self._scan_stale = False
        self._scan_age: str | None = None

        # Read from the ThemeRegistry singleton (see HubCard.__init__ for why
        # this rather than page.theme_mode).
        is_dark = get_theme_registry().is_dark

        # ---- Update scope ----
        # Built further down, once the run button it uses as its trigger
        # exists. Only wired when the host supplies both callbacks.
        self.scope_menu = None
        self._scope_deps = (get_scope, on_scope_changed)

        # ---- Leading badge (state glyph in a tinted circle) ----
        self._badge_icon = ft.Icon(ft.Icons.SEARCH, size=22)
        self._badge = ft.Container(
            content=self._badge_icon,
            width=44,
            height=44,
            border_radius=22,
            alignment=ft.Alignment.CENTER,
        )

        # ---- Identity block (headline + supporting line) ----
        # Not built here: self._last_text starts None below, so the
        # _apply_state(is_dark) call at the end of __init__ unconditionally
        # takes its rebuild branch and constructs the real _title_text /
        # _subtitle_text / _text_column there. Building throwaway ones here
        # too would just be discarded a few lines later on every card.
        self._title_text: ft.Text | None = None
        self._subtitle_text: ft.Text | None = None

        # ---- Primary action (filled accent) ----
        # Two buttons, not one, because this CTA is dual-purpose. "Scan for
        # games" runs immediately and is deliberately unscoped, while
        # "Update all (N)" opens the technology checklist first. A Flet control
        # can only have one parent, so the scoped variant cannot be the same
        # object moved in and out of the menu - _apply_state() shows exactly
        # one of these and hides the other.
        self._primary_icon = ft.Icon(ft.Icons.DOWNLOAD, size=18)
        self._primary_label = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self._primary_button = self._build_action_button(
            self._primary_icon, self._primary_label, on_click=self._on_primary_click
        )

        # The scoped twin. No on_click: it is the menu's trigger, and a
        # Container on_click would swallow the tap before the menu opened.
        self._run_icon = ft.Icon(ft.Icons.DOWNLOAD, size=18)
        self._run_label_text = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self._run_button = self._build_action_button(
            self._run_icon, self._run_label_text, on_click=None
        )

        get_scope, on_scope_changed = self._scope_deps
        if get_scope is not None and on_scope_changed is not None:
            from dlss_updater.ui_flet.components.update_scope_menu import UpdateScopeMenu

            self.scope_menu = UpdateScopeMenu(
                page=page,
                get_scope=get_scope,
                on_scope_changed=on_scope_changed,
                accent=MD3Colors.get_warning(is_dark),
                trigger_content=self._run_button,
                # run_bulk_update() takes no arguments, so the event is dropped
                # here rather than widening its signature for one caller.
                on_run=lambda e: self._on_update_all() if self._on_update_all else None,
                run_label=self._run_menu_label,
                radius=20,
            )

        # ---- Secondary action (outlined "Rescan") ----
        self._secondary_icon = ft.Icon(ft.Icons.REFRESH, size=16)
        self._secondary_label = ft.Text("Rescan", size=12, weight=ft.FontWeight.W_500)
        self._secondary_button = ft.Container(
            content=ft.Row(
                controls=[self._secondary_icon, self._secondary_label],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=36,
            padding=ft.Padding.symmetric(horizontal=14),
            border_radius=18,
            alignment=ft.Alignment.CENTER,
            ink=True,
            on_click=self._on_secondary_click,
            visible=False,
            tooltip="Rescan your launchers for games",
        )

        # ---- Decorative watermark (never intercepts input) ----
        self._watermark = build_watermark_icon(ft.Icons.BOLT, is_dark, size=84)
        self._watermark.right = -16
        self._watermark.bottom = -22

        # Empty placeholder — content=None/[] can't be passed to
        # AnimatedSwitcher (mirrors why the switcher below needs SOME
        # control), and it is replaced immediately by the _apply_state()
        # rebuild branch a few lines down. expand=True is not set here: the
        # switcher (the actual Flex child) carries expand=True, and setting
        # it again on this inner Column is inert since AnimatedSwitcher, not
        # a Flex, is its parent.
        self._text_column = ft.Column(controls=[], tight=True, alignment=ft.MainAxisAlignment.CENTER)
        self._state_switcher = ft.AnimatedSwitcher(
            content=self._text_column,
            duration=ft.Duration(milliseconds=220),
            reverse_duration=ft.Duration(milliseconds=160),
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=ft.AnimationCurve.EASE_OUT,
            switch_out_curve=ft.AnimationCurve.EASE_IN,
            expand=True,
        )
        self._last_text: tuple[str, str] | None = None
        # Set when a switcher-content rebuild (below, in _apply_state) is
        # issued while this card is detached from the page — see
        # _apply_state's "Detached-patch guard" comment. Forces the next
        # _apply_state() call to rebuild fresh instances even if
        # (title, subtitle) happens to match _last_text, since the client
        # never actually received the dropped patch.
        self._stats_stale: bool = False

        # Keep a reference to the wrapper itself (not just self.scope_menu):
        # _apply_state() hides the scoped button entirely in the "Scan for
        # games" / "Everything is up to date" states, where scanning is
        # deliberately unscoped. Toggling only the child leaves the Semantics
        # node in the tree advertising an invisible, unreachable button —
        # belt and braces, both are set in _apply_state().
        self._scope_menu_semantics: ft.Semantics | None = None
        if self.scope_menu is not None:
            self._scope_menu_semantics = ft.Semantics(
                content=self.scope_menu,
                label="Update all",
                button=True,
                focusable=True,
            )

        content_row = ft.Row(
            controls=[
                self._badge,
                self._state_switcher,
                self._secondary_button,
                ft.Row(
                    controls=[self._primary_button]
                    + ([self._scope_menu_semantics] if self._scope_menu_semantics else []),
                    spacing=0,
                    tight=True,
                ),
            ],
            spacing=14,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        super().__init__(
            content=ft.Stack(
                controls=[
                    self._watermark,
                    ft.Container(
                        content=content_row,
                        padding=ft.Padding.symmetric(horizontal=20, vertical=14),
                        expand=True,
                    ),
                ],
                expand=True,
            ),
            height=ACTION_CARD_HEIGHT,
            padding=ft.Padding.all(0),
            border_radius=20,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=Shadows.LEVEL_2,
        )

        # Paints bgcolor/gradient/labels for the initial (empty) state.
        self._apply_state(is_dark)

        self._register_theme_aware()

    # ===== State =====

    def set_state(
        self,
        *,
        needs_update: int,
        game_count: int,
        has_scan: bool,
        scan_stale: bool,
        scan_age: str | None,
    ) -> None:
        """Set the card's state from the hub's freshly loaded stats.

        Does NOT call update() — the caller (hub_view.load_stats) owns the
        single page.update() for the whole stats batch.
        """
        self._needs_update = max(0, int(needs_update or 0))
        self._game_count = max(0, int(game_count or 0))
        self._has_scan = bool(has_scan)
        self._scan_stale = bool(scan_stale)
        self._scan_age = scan_age
        self._apply_state(get_theme_registry().is_dark)

    def _apply_state(self, is_dark: bool) -> None:
        """Repaint every state-dependent property (also the theme entry point).

        The accent drives the card wash, the badge tint and the primary
        button fill together, so state and theme changes both funnel here
        rather than duplicating the mapping in apply_theme().
        """
        n = self._needs_update

        if n > 0:
            accent = MD3Colors.get_warning(is_dark)
            badge_icon = ft.Icons.SYSTEM_UPDATE_ALT
            title = "1 game needs an update" if n == 1 else f"{n} games need updates"
            subtitle = "Update every outdated DLSS, FSR and XeSS DLL in one pass"
            primary = (f"Update all ({n})", ft.Icons.DOWNLOAD)
            primary_tooltip = "Update every outdated DLL across your library"
        elif not self._has_scan or self._game_count == 0:
            accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)
            badge_icon = ft.Icons.SEARCH
            title = "No games scanned yet"
            subtitle = "Scan your launchers to find upgradeable DLLs"
            primary = ("Scan for games", ft.Icons.SEARCH)
            primary_tooltip = "Scan every configured launcher for games"
        else:
            accent = MD3Colors.get_success(is_dark)
            badge_icon = ft.Icons.TASK_ALT
            title = "Everything is up to date"
            subtitle = self._scan_age or "Every detected DLL is on the latest version"
            primary = None
            primary_tooltip = None

        # Card surface: opaque pre-blended wash (CLAUDE.md pitfall #1 — this
        # Container has a shadow, so the gradient must carry no alpha).
        self.bgcolor = MD3Colors.get_surface(is_dark)
        self.gradient = build_brand_wash(accent, is_dark)

        self._badge.bgcolor = ft.Colors.with_opacity(0.16, accent)
        self._badge_icon.name = badge_icon
        self._badge_icon.color = accent

        # Detached-patch guard (CLAUDE.md "Flet desktop client rendering
        # pitfalls" #2 — the client silently drops property patches aimed at
        # controls inside detached subtrees while the server marks them
        # delivered). load_stats() calls set_state() -> _apply_state() from
        # MainView._refresh_after_bulk_run() even when this card is behind
        # the nav controller's content-detachment (main_view.py's comment
        # there: "Best-effort when the hub is detached; returning to it
        # re-runs load_stats anyway"). That recovery only works if the
        # switcher-content rebuild below actually reruns with different
        # inputs; if the rebuild was issued while detached and is later
        # retried with the SAME (title, subtitle), the cheap recolor branch
        # would fire instead and never repaint a subtree the client never
        # received. Mirrors games_view's _chips_theme_stale /
        # _selection_theme_stale. `self.page` can't be read unguarded here —
        # _apply_state also runs from __init__, before attachment, where
        # Flet 0.86 raises RuntimeError("Control must be added to the page
        # first") — so this reuses the try/except-around-`.page` idiom
        # HubView._on_page_resize already uses for the same purpose.
        try:
            attached = self.page is not None
        except Exception:
            attached = False

        # AnimatedSwitcher animates on content REPLACEMENT, so a state change
        # must build a fresh column. A THEME change must not: _apply_state is
        # also the theme entry point, and rebuilding there would cross-fade
        # identical text on every light/dark toggle. Recolour in place instead.
        if (title, subtitle) != self._last_text or self._stats_stale:
            self._title_text = ft.Text(
                title,
                size=16,
                weight=ft.FontWeight.W_600,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                color=MD3Colors.get_on_surface(is_dark),
            )
            self._subtitle_text = ft.Text(
                subtitle,
                size=12,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                color=MD3Colors.get_on_surface_variant(is_dark),
            )
            self._text_column = ft.Column(
                controls=[self._title_text, self._subtitle_text],
                spacing=2,
                tight=True,
                alignment=ft.MainAxisAlignment.CENTER,
            )
            self._state_switcher.content = self._text_column
            self._last_text = (title, subtitle)
            self._stats_stale = not attached
        else:
            self._title_text.color = MD3Colors.get_on_surface(is_dark)
            self._subtitle_text.color = MD3Colors.get_on_surface_variant(is_dark)

        # Scanning is deliberately unscoped, so only the "Update all (N)" state
        # routes through the menu. Exactly one of the two buttons is shown.
        scoped = primary is not None and n > 0 and self.scope_menu is not None

        if primary is not None:
            label, icon = primary
            fg = _on_accent(is_dark)
            target, icon_ctl, label_ctl = (
                (self._run_button, self._run_icon, self._run_label_text)
                if scoped
                else (self._primary_button, self._primary_icon, self._primary_label)
            )
            target.bgcolor = accent
            target.tooltip = primary_tooltip
            icon_ctl.name = icon
            icon_ctl.color = fg
            label_ctl.value = label
            label_ctl.color = fg

        self._primary_button.visible = primary is not None and not scoped

        if self.scope_menu is not None:
            # Both the child AND the Semantics wrapper get toggled: the wrapper
            # is what a screen reader actually sees, so leaving it visible while
            # the child collapses would announce a button that isn't on screen
            # and can't be activated.
            self.scope_menu.visible = scoped
            if self._scope_menu_semantics is not None:
                self._scope_menu_semantics.visible = scoped
            if scoped:
                self.scope_menu.set_accent(accent)
                # Re-label the menu's run row for the new count.
                self.scope_menu.refresh_ticks()

        # Rescan stays available whenever a scan exists AND it either went
        # stale or there is nothing to update (the calm state's only action).
        self._secondary_button.visible = self._has_scan and (
            self._scan_stale or n == 0
        )
        self._secondary_button.border = ft.Border.all(1, MD3Colors.get_outline(is_dark))
        self._secondary_icon.color = MD3Colors.get_on_surface_variant(is_dark)
        self._secondary_label.color = MD3Colors.get_on_surface_variant(is_dark)

        self._watermark.opacity = WATERMARK_OPACITY_DARK if is_dark else WATERMARK_OPACITY_LIGHT

    def _build_action_button(self, icon_ctl, label_ctl, on_click) -> ft.Container:
        """One pill shape for both the plain and the scoped primary button, so
        swapping between them is invisible to the user."""
        return ft.Container(
            content=ft.Row(
                controls=[icon_ctl, label_ctl],
                spacing=8,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=40,
            padding=ft.Padding.symmetric(horizontal=18),
            border_radius=20,
            alignment=ft.Alignment.CENTER,
            ink=True,
            on_click=on_click,
            on_hover=self._on_button_hover,
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            scale=1.0,
        )

    def _run_menu_label(self) -> str:
        n = self._needs_update
        return f"Update {n} game{'' if n == 1 else 's'}"

    # ===== Interaction =====

    def _on_primary_click(self, e):
        """Run the state's primary action (update when outdated, else scan)."""
        handler = self._on_update_all if self._needs_update > 0 else self._on_scan
        if handler and self._page_ref:
            self._page_ref.run_task(handler)

    def _on_secondary_click(self, e):
        if self._on_scan and self._page_ref:
            self._page_ref.run_task(self._on_scan)

    def _on_button_hover(self, e):
        """Subtle lift on the hovered button (e.data is a bool in Flet 0.86).

        Reads the target off the event rather than assuming _primary_button:
        the scoped twin shares this handler, and only one of the two is on
        screen at a time."""
        target = getattr(e, "control", None) or self._primary_button
        target.scale = 1.03 if (e.data is True or e.data == "true") else 1.0
        if self._page_ref:
            try:
                target.update()
            except Exception:
                pass

    # ===== Lifecycle =====

    def _unregister_theme_aware(self) -> None:
        """Also drop the caret: this card is REPLACED on every theme change
        (HubView.rebuild_for_theme / MainView's hub swap) and its menu registers
        itself, so without this the registry accumulates detached menus."""
        if self.scope_menu is not None:
            self.scope_menu._unregister_theme_aware()
        super()._unregister_theme_aware()

    def did_mount(self):
        """Defensive theme re-sync on every (re)mount - see HubCard.did_mount()."""
        page = self._page_ref
        if page is not None and hasattr(page, "run_task"):
            try:
                page.run_task(self.apply_theme, get_theme_registry().is_dark)
            except Exception:
                pass

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Re-derive every themed property (state mapping owns them all)."""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        self._apply_state(is_dark)

        try:
            self.update()
        except Exception:
            pass
