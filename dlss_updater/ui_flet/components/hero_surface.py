"""
Hero Surface — shared "hero design" DNA.

This module extracts the reusable visual primitives that make up the hero
card language pioneered by ``game_card.py`` (the game library's full-bleed
artwork cards): a bottom-weighted scrim gradient for text legibility over
artwork, a diagonal brand-color "wash" for cards without artwork, an
oversized decorative watermark glyph, lightweight Container-based status
pills (see CLAUDE.md's "Container-based Badge Pattern"), and a helper for
picking the correct light/dark accent out of a ``(dark, light)`` color pair
(the convention used by ``settings_view.TILE_COLORS``).

Everything here is a pure function or module constant — no classes, no
``ft.Page`` access, no mutable state. Callers own the ``ft.Control`` objects
these functions return and are free to re-theme/rebuild them as needed.
"""

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors

# ==================== HOVER ANIMATION ====================
# Matches the hover-scale convention shared by game_card.py (scale=1.015,
# 200ms EASE_OUT) and hub_card.py (scale=1.01-1.02, 150ms EASE_OUT).
HOVER_SCALE = 1.02
HOVER_ANIM_MS = 200

# ==================== BRAND WASH OPACITY ====================
# Diagonal brand-color gradient used on cards/tiles that have no artwork of
# their own (e.g. launcher shells, settings tiles). Dark theme gets a
# stronger wash since dark surfaces need more saturation to read as tinted.
WASH_OPACITY_DARK = 0.22
WASH_OPACITY_LIGHT = 0.14

# ==================== WATERMARK OPACITY ====================
# Oversized decorative glyph opacity — subtle enough to never compete with
# foreground content.
WATERMARK_OPACITY_DARK = 0.08
WATERMARK_OPACITY_LIGHT = 0.04

# ==================== PILL GEOMETRY ====================
PILL_HEIGHT = 26

# ==================== SCRIM TUNING ====================
# The bottom-weighted scrim keeps the identity caption legible over artwork.
# Dark mode fades toward the near-black surface, which reads as a natural
# vignette — extending it high up the tile is harmless. Light mode fades
# toward WHITE, so the same tall ramp visibly desaturates/washes out the
# artwork (verified on the light-mode Games mosaic). The light profile
# therefore keeps a much taller CLEAR zone (scrim confined to the caption
# band near the bottom) while preserving a strong opaque edge where the text
# actually sits. Stops/opacities are paired 1:1 (position, white-veil alpha);
# an alpha of 0 emits a fully transparent stop. This layer is shadow-LESS, so
# alpha stops are safe here (CLAUDE.md rendering pitfall #1 applies only to
# shadowed containers).
_SCRIM_STOPS_DARK = [0.0, 0.45, 0.75, 1.0]
_SCRIM_OPACITY_DARK = [0.0, 0.15, 0.60, 0.85]
_SCRIM_STOPS_LIGHT = [0.0, 0.58, 0.82, 1.0]
_SCRIM_OPACITY_LIGHT = [0.0, 0.10, 0.50, 0.90]


def build_scrim_gradient(is_dark: bool) -> ft.LinearGradient:
    """Bottom-weighted gradient scrim for legibility over artwork.

    Generalized copy of ``GameCard._build_scrim_gradient()``. Fades from
    fully transparent (top) to the theme's own surface color (bottom) —
    not a hardcoded black — so the scrim's opaque edge matches whatever
    themed surface sits below it (footer, card background, etc.) in both
    light and dark mode. Light mode uses a taller clear zone so it doesn't
    wash out the artwork it sits over (see the _SCRIM_* constants above).
    """
    base = MD3Colors.get_surface(is_dark)
    stops = _SCRIM_STOPS_DARK if is_dark else _SCRIM_STOPS_LIGHT
    opacities = _SCRIM_OPACITY_DARK if is_dark else _SCRIM_OPACITY_LIGHT
    return ft.LinearGradient(
        begin=ft.Alignment.TOP_CENTER,
        end=ft.Alignment.BOTTOM_CENTER,
        colors=[
            ft.Colors.TRANSPARENT if o <= 0 else ft.Colors.with_opacity(o, base)
            for o in opacities
        ],
        stops=stops,
    )


# ==================== ART MOSAIC OVERLAYS ====================
# Overlays for hero surfaces built out of the user's OWN artwork (the hub's
# Games mosaic). These deliberately do NOT reuse build_scrim_gradient: that one
# fades toward the themed surface because a game card's artwork meets an opaque
# themed footer directly below it. A mosaic has no themed surface below it at
# all, so in light mode a white-fading ramp only hazes the art instead of
# anchoring the caption. These fade toward BLACK in both themes, and the caller
# switches its caption to light-on-dark text while the mosaic is live.
#
# Both are intended for shadow-LESS Containers inside the hero's Stack.
# CLAUDE.md rendering pitfall #1: an alpha-carrying gradient on a Container
# that ALSO has a box-shadow renders near-black, because the shadow is painted
# directly behind the box and shows through every transparent region.
_ART_SCRIM_STOPS = [0.0, 0.55, 0.80, 1.0]
_ART_SCRIM_ALPHA_DARK = [0.0, 0.10, 0.55, 0.88]
_ART_SCRIM_ALPHA_LIGHT = [0.0, 0.08, 0.46, 0.78]

# Flat unifying tint laid over EVERY mosaic tile. Cached cover art ranges from
# near-black to near-white, so without it a single bright cover blows the
# mosaic out and the scrim's contrast becomes unpredictable. Light mode leans
# on it slightly less: the tint is also what keeps a bright tile from merging
# into the near-white light canvas, but the hairline outline carries part of
# that job there.
ART_TINT_DARK = 0.26
ART_TINT_LIGHT = 0.20


def build_art_scrim_gradient(is_dark: bool) -> ft.LinearGradient:
    """Bottom-weighted BLACK scrim for captions over arbitrary user artwork.

    Clear through the top ~55% so the art reads unobstructed, then ramps to a
    near-opaque black ground under the caption band. See the _ART_SCRIM_*
    constants for why this is black rather than the themed surface.
    """
    alphas = _ART_SCRIM_ALPHA_DARK if is_dark else _ART_SCRIM_ALPHA_LIGHT
    return ft.LinearGradient(
        begin=ft.Alignment.TOP_CENTER,
        end=ft.Alignment.BOTTOM_CENTER,
        colors=[
            ft.Colors.TRANSPARENT if a <= 0 else ft.Colors.with_opacity(a, ft.Colors.BLACK)
            for a in alphas
        ],
        stops=_ART_SCRIM_STOPS,
    )


def art_tint_color(is_dark: bool) -> str:
    """Flat low-alpha black that unifies mosaic tiles of wildly varying brightness."""
    return ft.Colors.with_opacity(
        ART_TINT_DARK if is_dark else ART_TINT_LIGHT, ft.Colors.BLACK
    )


def _blend_hex(base: str, tint: str, alpha: float) -> str:
    """Alpha-blend opaque ``tint`` over opaque ``base`` (both ``#RRGGBB``),
    returning an opaque ``#RRGGBB``. Used to pre-compose wash gradients so
    they carry NO alpha channel — see build_brand_wash for why.
    """
    b, t = base.lstrip("#"), tint.lstrip("#")
    br, bg_, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    tr, tg, tb = int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    r = round(tr * alpha + br * (1 - alpha))
    g = round(tg * alpha + bg_ * (1 - alpha))
    bl = round(tb * alpha + bb * (1 - alpha))
    return f"#{r:02X}{g:02X}{bl:02X}"


def build_brand_wash(
    accent: str, is_dark: bool, opacity: float | None = None
) -> ft.LinearGradient:
    """Diagonal brand-color wash (top-left -> bottom-right) for artwork-less surfaces.

    IMPORTANT: the gradient is fully OPAQUE — the accent is pre-blended over
    the themed surface color rather than using alpha stops. Two reasons
    (both verified live, see CLAUDE.md's Flet client notes):
    1. Flutter's BoxDecoration ignores ``color`` when ``gradient`` is set, so
       a translucent wash painted directly on a Container never composites
       over that Container's own bgcolor.
    2. The Container's box-shadow is painted directly behind the box, so any
       transparent gradient region shows the shadow's BLACK through it —
       invisible over dark themes, but it rendered every washed surface
       near-black in light mode.

    ``opacity`` (the accent blend strength at the top-left corner) defaults
    to ``WASH_OPACITY_DARK``/``WASH_OPACITY_LIGHT`` based on ``is_dark``.
    Accents may be given as ``#RRGGBB``; anything unparseable falls back to
    a flat surface gradient.
    """
    if opacity is None:
        opacity = WASH_OPACITY_DARK if is_dark else WASH_OPACITY_LIGHT
    base = MD3Colors.get_surface(is_dark)
    try:
        start = _blend_hex(base, accent, opacity)
    except (ValueError, IndexError):
        start = base
    return ft.LinearGradient(
        begin=ft.Alignment.TOP_LEFT,
        end=ft.Alignment.BOTTOM_RIGHT,
        colors=[start, base],
    )


def build_watermark_icon(
    icon: str,
    is_dark: bool,
    size: int = 110,
    alignment: ft.Alignment | None = None,
) -> ft.Container:
    """Oversized decorative glyph, purely visual (never intercepts input).

    A plain ``ft.Icon`` with no event handlers, hosted in a Container so it
    can be positioned/sized independently of surrounding layout. Defaults
    to bottom-right placement to read as an "overflowing" background motif.
    """
    opacity = WATERMARK_OPACITY_DARK if is_dark else WATERMARK_OPACITY_LIGHT
    return ft.Container(
        content=ft.Icon(icon, size=size, color=ft.Colors.WHITE),
        alignment=alignment or ft.Alignment.BOTTOM_RIGHT,
        opacity=opacity,
    )


def build_pill(
    text: str,
    *,
    icon: str | None = None,
    bgcolor: str,
    text_color: str = ft.Colors.WHITE,
    icon_color: str | None = None,
    text_size: int = 11,
    icon_size: int = 14,
) -> ft.Container:
    """Lightweight Container-based status/badge pill.

    Matches CLAUDE.md's "Container-based Badge Pattern" — a Row of an
    optional Icon + Text inside a rounded Container, used in place of the
    heavier ``ft.Chip`` for read-only badges.
    """
    row_controls: list[ft.Control] = []
    if icon is not None:
        row_controls.append(ft.Icon(icon, size=icon_size, color=icon_color or text_color))
    row_controls.append(
        ft.Text(text, size=text_size, color=text_color, weight=ft.FontWeight.W_500)
    )

    return ft.Container(
        content=ft.Row(row_controls, spacing=4, tight=True),
        bgcolor=bgcolor,
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        border_radius=16,
        height=PILL_HEIGHT,
    )


def themed_accent(pair: tuple[str, str], is_dark: bool) -> str:
    """Pick the themed variant from a ``(dark_value, light_value)`` accent pair.

    Matches the tuple ordering used by ``settings_view.TILE_COLORS`` and
    ``MD3Colors.THEMED``: index 0 is the dark-mode value, index 1 the
    light-mode value.
    """
    return pair[0] if is_dark else pair[1]
