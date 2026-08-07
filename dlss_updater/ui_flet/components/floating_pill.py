"""
Floating Pill Navigation Component
A floating capsule navigation bar shown at the bottom of views (hidden on hub).
"""

import anyio
import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors, Shadows
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin

# Vertical space (px) views must reserve at the bottom of scrollable content so
# the last item can scroll clear of the floating pill (pill ~44px + 16px offset
# + breathing room).
PILL_CLEARANCE = 88


# ---- Pill surface (per-theme) ----
# The capsule was originally styled for dark only: SURFACE_VARIANT fill +
# a 1px OUTLINE stroke + the elevation-3 shadow. In DARK that reads correctly —
# #333333 on the #141414 canvas is a lift, and the #5A5A5A hairline is a *lighter*
# edge on top of it. In LIGHT the same recipe inverts: the #F5F5F5 fill is
# ~indistinguishable from the #FAFBFC canvas, so the only thing separating pill
# from page is the #79747E stroke — a mid-grey ring around a near-white capsule,
# which reads as a hard "stamped" outline rather than a floating element. The
# elevation-3 shadow (a hover-state, accent-tinted ramp) piles onto that.
#
# Light mode therefore gets: a pure white fill that *is* lighter than the canvas,
# a barely-there OUTLINE_VARIANT hairline for edge definition, and a normal
# two-layer ambient+key drop shadow. Dark mode values are byte-identical to
# before. NOTE: opaque fill only — never a translucent gradient on a shadowed
# Container (the shadow paints straight through it; see CLAUDE.md).


def _pill_bgcolor(is_dark: bool) -> str:
    """Pill fill — elevated above the page canvas in BOTH themes."""
    if is_dark:
        return MD3Colors.get_surface_variant(True)  # #333333 (unchanged)
    return MD3Colors.SURFACE_LIGHT  # #FFFFFF — lifts off the #FAFBFC canvas


def _pill_border(is_dark: bool) -> ft.Border:
    """Hairline edge. Light mode uses the low-contrast outline *variant* so the
    capsule is defined by its shadow, not by a dark ring."""
    color = (
        MD3Colors.get_outline(True) if is_dark else MD3Colors.OUTLINE_VARIANT_LIGHT
    )
    return ft.Border.all(1, color)


def _pill_shadow(is_dark: bool):
    """Drop shadow. Dark keeps the existing elevation-3 ramp; light gets a softer
    ambient+key pair (no accent glow) so the pill floats instead of being stamped."""
    if is_dark:
        return Shadows.LEVEL_3
    return [
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=14,
            offset=ft.Offset(0, 5),
            color="rgba(0, 0, 0, 0.10)",  # Ambient — the "float"
        ),
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=4,
            offset=ft.Offset(0, 1),
            color="rgba(0, 0, 0, 0.06)",  # Key — grounds the capsule
        ),
    ]


def _pill_divider_color(is_dark: bool) -> str:
    """Home/views separator inside the pill — the same outline-variant softening
    in light mode, so it reads as a hairline rather than a dark tick."""
    return MD3Colors.get_outline(True) if is_dark else MD3Colors.OUTLINE_VARIANT_LIGHT


class FloatingPill(ThemeAwareMixin, ft.Container):
    """
    Floating pill navigation bar with Home + 3 view icons.

    Positioned at bottom center, floating 16px above bottom edge.
    Shows active state with colored circle behind icon.
    """

    _theme_priority = 15

    # View name -> config mapping
    VIEW_CONFIGS = {
        "launchers": {
            "icon": ft.Icons.ROCKET_LAUNCH,
            "label": "Launchers",
            "accent_dark": TabColors.LAUNCHERS,
            "accent_light": TabColors.LAUNCHERS_LIGHT,
        },
        "games": {
            "icon": ft.Icons.SPORTS_ESPORTS,
            "label": "Games",
            "accent_dark": TabColors.GAMES,
            "accent_light": TabColors.GAMES_LIGHT,
        },
        "backups": {
            "icon": ft.Icons.SETTINGS_BACKUP_RESTORE,
            "label": "Backups",
            "accent_dark": TabColors.BACKUPS,
            "accent_light": TabColors.BACKUPS_LIGHT,
        },
        "settings": {
            "icon": ft.Icons.SETTINGS,
            "label": "Settings",
            "accent_dark": TabColors.SETTINGS,
            "accent_light": TabColors._TAB_COLORS_LIGHT.get("Settings", "#6A1B9A"),
        },
    }

    def __init__(
        self,
        on_navigate,
        on_home,
        page: ft.Page | None = None,
    ):
        self._page_ref = page
        self._on_navigate = on_navigate
        self._on_home = on_home
        self._active_view: str | None = None

        is_dark = page.theme_mode == ft.ThemeMode.DARK if page else True

        # Build icon buttons
        self._icon_containers: dict[str, ft.Container] = {}
        self._icon_widgets: dict[str, ft.Icon] = {}

        # Home button
        self._home_icon = ft.Icon(
            ft.Icons.HOME_ROUNDED,
            size=22,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )
        self._home_container = ft.Container(
            content=self._home_icon,
            width=36,
            height=36,
            border_radius=18,
            alignment=ft.Alignment.CENTER,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            on_click=lambda e: self._handle_home(),
            on_hover=self._on_home_hover,
            tooltip="Home",
        )

        # Divider
        divider = ft.Container(
            width=1,
            height=24,
            bgcolor=_pill_divider_color(is_dark),
        )
        self._pill_divider = divider

        # View icons
        view_icons = []
        for view_name, config in self.VIEW_CONFIGS.items():
            icon_widget = ft.Icon(
                config["icon"],
                size=22,
                color=MD3Colors.get_on_surface_variant(is_dark),
            )
            self._icon_widgets[view_name] = icon_widget

            icon_container = ft.Container(
                content=icon_widget,
                width=36,
                height=36,
                border_radius=18,
                alignment=ft.Alignment.CENTER,
                animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
                on_click=lambda e, vn=view_name: self._handle_navigate(vn),
                on_hover=lambda e, vn=view_name: self._on_icon_hover(e, vn),
                tooltip=config["label"],
            )
            self._icon_containers[view_name] = icon_container
            view_icons.append(icon_container)

        # Pill layout
        pill_row = ft.Row(
            controls=[
                self._home_container,
                divider,
                *view_icons,
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        super().__init__(
            content=pill_row,
            padding=ft.Padding.symmetric(horizontal=16, vertical=4),
            border_radius=24,
            bgcolor=_pill_bgcolor(is_dark),
            border=_pill_border(is_dark),
            shadow=_pill_shadow(is_dark),
            animate_opacity=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

        self._register_theme_aware()

    def _handle_navigate(self, view_name: str):
        """Handle view icon click."""
        if self._on_navigate:
            if self._page_ref:
                self._page_ref.run_task(self._on_navigate, view_name)

    def _handle_home(self):
        """Handle home icon click."""
        if self._on_home:
            if self._page_ref:
                self._page_ref.run_task(self._on_home)

    def _on_icon_hover(self, e, view_name: str):
        """Handle icon hover effect."""
        if view_name == self._active_view:
            return

        is_dark = self._page_ref.theme_mode == ft.ThemeMode.DARK if self._page_ref else True
        config = self.VIEW_CONFIGS[view_name]
        accent = config["accent_dark"] if is_dark else config["accent_light"]
        container = self._icon_containers[view_name]

        if e.data is True or e.data == "true":
            container.bgcolor = f"{accent}14"  # 8% tint
        else:
            container.bgcolor = None

        if self._page_ref:
            container.update()

    def _on_home_hover(self, e):
        """Handle home icon hover effect."""
        is_dark = self._page_ref.theme_mode == ft.ThemeMode.DARK if self._page_ref else True
        primary = MD3Colors.get_primary(is_dark)

        if e.data is True or e.data == "true":
            self._home_container.bgcolor = f"{primary}14"
        else:
            self._home_container.bgcolor = None

        if self._page_ref:
            self._home_container.update()

    def set_active(self, view_name: str | None):
        """Set the active view icon with colored circle behind it."""
        is_dark = self._page_ref.theme_mode == ft.ThemeMode.DARK if self._page_ref else True
        self._active_view = view_name

        for vn, container in self._icon_containers.items():
            icon = self._icon_widgets[vn]
            config = self.VIEW_CONFIGS[vn]
            accent = config["accent_dark"] if is_dark else config["accent_light"]

            if vn == view_name:
                # Active: solid accent circle, white icon
                container.bgcolor = accent
                icon.color = ft.Colors.WHITE
            else:
                # Inactive: no background, variant color
                container.bgcolor = None
                icon.color = MD3Colors.get_on_surface_variant(is_dark)

    def show(self):
        """Show the pill with fade-in."""
        self.visible = True
        self.opacity = 1.0

    def hide(self):
        """Hide the pill with fade-out."""
        self.opacity = 0.0
        self.visible = False

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme to pill and all icons."""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        self.bgcolor = _pill_bgcolor(is_dark)
        self.border = _pill_border(is_dark)
        # The shadow is theme-dependent now (light gets a softer ambient+key pair),
        # so it has to flip here too — it used to be a constant.
        self.shadow = _pill_shadow(is_dark)
        self._pill_divider.bgcolor = _pill_divider_color(is_dark)
        self._home_icon.color = MD3Colors.get_on_surface_variant(is_dark)

        # Re-apply active state with new theme colors
        self.set_active(self._active_view)

        try:
            self.update()
        except Exception:
            pass
