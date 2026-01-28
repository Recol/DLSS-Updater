"""
Floating Pill Navigation Component
A floating capsule navigation bar shown at the bottom of views (hidden on hub).
"""

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors, Shadows
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin


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
            bgcolor=MD3Colors.get_outline(is_dark),
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
            padding=ft.padding.symmetric(horizontal=16, vertical=4),
            border_radius=24,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            border=ft.border.all(1, MD3Colors.get_outline(is_dark)),
            shadow=Shadows.LEVEL_3,
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

        if e.data == "true":
            container.bgcolor = f"{accent}14"  # 8% tint
        else:
            container.bgcolor = None

        if self._page_ref:
            container.update()

    def _on_home_hover(self, e):
        """Handle home icon hover effect."""
        is_dark = self._page_ref.theme_mode == ft.ThemeMode.DARK if self._page_ref else True
        primary = MD3Colors.get_primary(is_dark)

        if e.data == "true":
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
            import asyncio
            await asyncio.sleep(delay_ms / 1000)

        self.bgcolor = MD3Colors.get_surface_variant(is_dark)
        self.border = ft.border.all(1, MD3Colors.get_outline(is_dark))
        self._pill_divider.bgcolor = MD3Colors.get_outline(is_dark)
        self._home_icon.color = MD3Colors.get_on_surface_variant(is_dark)

        # Re-apply active state with new theme colors
        self.set_active(self._active_view)

        try:
            self.update()
        except Exception:
            pass
