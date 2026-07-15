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


def build_scrim_gradient(is_dark: bool) -> ft.LinearGradient:
    """Bottom-weighted gradient scrim for legibility over artwork.

    Generalized copy of ``GameCard._build_scrim_gradient()``. Fades from
    fully transparent (top) to the theme's own surface color (bottom) —
    not a hardcoded black — so the scrim's opaque edge matches whatever
    themed surface sits below it (footer, card background, etc.) in both
    light and dark mode.
    """
    base = MD3Colors.get_surface(is_dark)
    return ft.LinearGradient(
        begin=ft.Alignment.TOP_CENTER,
        end=ft.Alignment.BOTTOM_CENTER,
        colors=[
            ft.Colors.TRANSPARENT,
            ft.Colors.with_opacity(0.15, base),
            ft.Colors.with_opacity(0.60, base),
            ft.Colors.with_opacity(0.85, base),
        ],
        stops=[0.0, 0.45, 0.75, 1.0],
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
