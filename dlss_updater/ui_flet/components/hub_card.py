"""
Hub Card Component
Staggered card for the hub home screen with left accent bar, hover animation, and optional stats badge.
"""

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows, Animations
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin


class HubCard(ThemeAwareMixin, ft.Container):
    """
    A hub navigation card with left accent bar, icon, title, subtitle, and hover effects.

    Props:
        title: Card title text
        subtitle: Card subtitle/description text
        icon: ft.Icons icon name
        accent_color_dark: Accent color for dark mode
        accent_color_light: Accent color for light mode
        icon_size: Size of the icon (default 40)
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

        is_dark = page.theme_mode == ft.ThemeMode.DARK if page else True
        accent = self._accent_dark if is_dark else self._accent_light

        # Build inner content
        self._icon_widget = ft.Icon(
            icon,
            size=icon_size,
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

        # Stats badge (optional, hidden by default)
        self._stats_text = ft.Text(
            "",
            size=12,
            weight=ft.FontWeight.W_500,
            color=accent,
        )
        self._stats_badge = ft.Container(
            content=self._stats_text,
            visible=False,
            padding=ft.padding.symmetric(horizontal=8, vertical=2),
            border_radius=10,
            bgcolor=f"{accent}15",
        )

        card_content = ft.Column(
            controls=[
                self._icon_widget,
                ft.Container(height=8),
                self._title_text,
                ft.Container(height=4),
                self._subtitle_text,
                ft.Container(height=8),
                self._stats_badge,
            ],
            spacing=0,
            expand=True,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        super().__init__(
            content=card_content,
            padding=ft.padding.all(24),
            border_radius=border_radius_val,
            bgcolor=MD3Colors.get_surface(is_dark),
            border=ft.border.only(
                left=ft.BorderSide(3, accent),
                top=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
                right=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
                bottom=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
            ),
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

    def _on_hover(self, e):
        """Handle hover effect - scale + shadow + border glow"""
        is_dark = self._page_ref.theme_mode == ft.ThemeMode.DARK if self._page_ref else True
        accent = self._accent_dark if is_dark else self._accent_light

        if e.data == "true":
            # Scale slightly (smaller for large cards)
            max_scale = 1.01 if self._icon_size >= 64 else 1.02
            self.scale = max_scale
            self.shadow = Shadows.LEVEL_3
            self.border = ft.border.only(
                left=ft.BorderSide(3, accent),
                top=ft.BorderSide(1, accent),
                right=ft.BorderSide(1, accent),
                bottom=ft.BorderSide(1, accent),
            )
        else:
            self.scale = 1.0
            self.shadow = Shadows.LEVEL_2
            self.border = ft.border.only(
                left=ft.BorderSide(3, accent),
                top=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
                right=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
                bottom=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
            )

        if self._page_ref:
            self.update()

    def set_stats(self, text: str):
        """Update the stats badge text and show it."""
        if text:
            self._stats_text.value = text
            self._stats_badge.visible = True
        else:
            self._stats_badge.visible = False

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme colors to all sub-elements."""
        if delay_ms > 0:
            import asyncio
            await asyncio.sleep(delay_ms / 1000)

        accent = self._accent_dark if is_dark else self._accent_light

        self.bgcolor = MD3Colors.get_surface(is_dark)
        self.border = ft.border.only(
            left=ft.BorderSide(3, accent),
            top=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
            right=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
            bottom=ft.BorderSide(1, MD3Colors.get_outline(is_dark)),
        )

        self._icon_widget.color = accent
        self._title_text.color = MD3Colors.get_on_surface(is_dark)
        self._subtitle_text.color = MD3Colors.get_on_surface_variant(is_dark)
        self._stats_text.color = accent
        self._stats_badge.bgcolor = f"{accent}15"

        try:
            self.update()
        except Exception:
            pass
