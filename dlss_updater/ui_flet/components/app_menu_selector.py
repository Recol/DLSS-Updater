"""
Modern App Menu Selector Component

A clean, animated menu selector based on Project Matrix's CategorySelector pattern.
Features colored icon circles, hover effects, and smooth animations.
"""

from dataclasses import dataclass, field
from typing import Callable

import flet as ft

from ..theme.colors import MD3Colors, Animations
from ..theme.theme_aware import ThemeAwareMixin, get_theme_registry


@dataclass
class MenuItem:
    """Represents a single menu item"""
    id: str
    title: str
    description: str
    icon: str
    on_click: Callable | None = None
    is_disabled: bool = False
    show_badge: bool = False
    tooltip: str | None = None


@dataclass
class MenuCategory:
    """Represents a category of menu items"""
    id: str
    title: str
    icon: str
    color: str  # Hex color for the category
    items: list[MenuItem] = field(default_factory=list)


class AppMenuSelector(ThemeAwareMixin, ft.Container):
    """
    Material Design 3 App Menu Selector with categorized items,
    colored icon circles, hover effects, and smooth animations.

    Based on Project Matrix CategorySelector pattern.
    """

    def __init__(
        self,
        page: ft.Page,
        categories: list[MenuCategory],
        on_item_selected: Callable[[str], None] | None = None,
        initially_expanded: bool = False,
        is_dark: bool = True,
    ):
        super().__init__()
        self._page_ref = page
        self.categories = categories
        self.on_item_selected = on_item_selected
        self.is_expanded = initially_expanded
        self.selected_item_id: str | None = None

        # Get theme registry and state
        self._registry = get_theme_registry()
        self._theme_priority = 25  # Cards are mid-priority
        self._is_dark = self._registry.is_dark

        # Badge references for dynamic updates
        self._badge_refs: dict[str, ft.Container] = {}

        # Item container refs for hover effects
        self._item_refs: dict[str, ft.Container] = {}

        # Store references for themed elements
        self._title_texts: list[ft.Text] = []
        self._description_texts: list[ft.Text] = []
        self._category_titles: list[ft.Text] = []
        self._category_subtitles: list[ft.Text] = []
        self._expansion_tiles: list[ft.ExpansionTile] = []
        self._trailing_icons: list[ft.Icon] = []
        self._muted_icon_circles: list[ft.Container] = []

        # Build the component
        self._build()

        # Register for theme updates
        self._register_theme_aware()

    def _get_is_dark(self) -> bool:
        """Get current theme mode from registry"""
        return self._registry.is_dark

    def _create_icon_circle(
        self,
        icon: str,
        color: str,
        size: int = 36,
        icon_size: int = 18,
    ) -> ft.Container:
        """
        Create a colored circular container with icon inside.
        Follows Project Matrix CategorySelector pattern.
        """
        return ft.Container(
            content=ft.Icon(
                icon,
                size=icon_size,
                color=ft.Colors.WHITE,
            ),
            width=size,
            height=size,
            bgcolor=color,
            border_radius=size // 2,  # Perfect circle
            alignment=ft.Alignment.CENTER,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=4,
                offset=ft.Offset(0, 2),
                color=f"{color}40",  # 25% opacity of the category color
            ),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

    def _create_icon_circle_muted(
        self,
        icon: str,
        size: int = 36,
        icon_size: int = 18,
    ) -> ft.Container:
        """Create a muted/disabled icon circle"""
        is_dark = self._get_is_dark()
        container = ft.Container(
            content=ft.Icon(
                icon,
                size=icon_size,
                color=MD3Colors.get_text_secondary(is_dark),
            ),
            width=size,
            height=size,
            bgcolor=MD3Colors.get_surface_bright(is_dark) if hasattr(MD3Colors, 'get_surface_bright') else (
                "#3A3A3A" if is_dark else "#E0E0E0"
            ),
            border_radius=size // 2,
            alignment=ft.Alignment.CENTER,
        )
        self._muted_icon_circles.append(container)
        return container

    def _on_item_hover(self, e, container: ft.Container, category_color: str):
        """Handle item hover state"""
        is_dark = self._get_is_dark()
        if e.data == "true":
            # Hover enter
            container.bgcolor = f"{category_color}15"  # 8% opacity
            container.border = ft.border.all(1, f"{category_color}30")
        else:
            # Hover exit
            container.bgcolor = "transparent"
            container.border = None

        if self._page_ref:
            container.update()

    def _on_item_click(self, item: MenuItem):
        """Handle item click"""
        import inspect

        if item.is_disabled:
            return

        # Update selected state
        old_selected = self.selected_item_id
        self.selected_item_id = item.id

        # Execute the item's callback (handle both sync and async)
        if item.on_click:
            result = item.on_click(None)
            # If callback is async (returns coroutine), use Flet's run_task
            if inspect.iscoroutine(result) and self._page_ref:
                async def run_async(coro):
                    await coro
                self._page_ref.run_task(run_async, result)

        # Notify parent
        if self.on_item_selected:
            self.on_item_selected(item.id)

    def _build_menu_item(
        self,
        item: MenuItem,
        category_color: str,
    ) -> ft.Container:
        """
        Build a styled menu item with:
        - Icon in colored circle
        - Title + description
        - Hover effect
        - Badge indicator
        """
        is_dark = self._get_is_dark()

        # Icon circle (colored or muted based on disabled state)
        if item.is_disabled:
            icon_circle = self._create_icon_circle_muted(item.icon)
        else:
            icon_circle = self._create_icon_circle(item.icon, category_color)

        # Badge indicator
        badge = ft.Container(
            width=8,
            height=8,
            bgcolor=ft.Colors.RED,
            border_radius=4,
            visible=item.show_badge,
        )
        self._badge_refs[item.id] = badge

        # Title text
        title_text = ft.Text(
            item.title,
            size=14,
            weight=ft.FontWeight.W_500,
            color=MD3Colors.get_on_surface(is_dark) if not item.is_disabled else MD3Colors.get_themed("text_tertiary", is_dark),
        )
        self._title_texts.append(title_text)

        # Description text
        description_text = ft.Text(
            item.description,
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark) if not item.is_disabled else MD3Colors.get_text_secondary(is_dark),
        )
        self._description_texts.append(description_text)

        # Trailing icon
        trailing_icon = ft.Icon(
            ft.Icons.ARROW_FORWARD_IOS,
            size=14,
            color=MD3Colors.get_on_surface_variant(is_dark) if not item.is_disabled else MD3Colors.get_themed("text_disabled", is_dark),
        )
        self._trailing_icons.append(trailing_icon)

        # Trailing container (badge or arrow)
        trailing = ft.Container(
            content=ft.Stack(
                controls=[
                    ft.Container(
                        content=trailing_icon,
                        opacity=0.6,
                    ),
                    ft.Container(
                        content=badge,
                        right=-2,
                        top=-2,
                    ),
                ],
            ),
            width=24,
            height=24,
        )

        # Main content row
        content = ft.Row(
            controls=[
                icon_circle,
                ft.Column(
                    controls=[
                        title_text,
                        description_text,
                    ],
                    spacing=2,
                    expand=True,
                ),
                trailing,
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Item container with hover effects
        item_container = ft.Container(
            content=content,
            padding=ft.padding.symmetric(horizontal=12, vertical=10),
            border_radius=8,
            bgcolor="transparent",
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            on_hover=lambda e: self._on_item_hover(e, item_container, category_color) if not item.is_disabled else None,
            on_click=lambda e: self._on_item_click(item) if not item.is_disabled else None,
            tooltip=item.tooltip or item.description,
            opacity=0.5 if item.is_disabled else 1.0,
        )

        # Store reference
        self._item_refs[item.id] = item_container

        return item_container

    def _build_category_tile(self, category: MenuCategory) -> ft.ExpansionTile:
        """
        Build an ExpansionTile for a category with:
        - Colored icon circle in header
        - Clean title styling
        - Subtitle showing item count
        """
        is_dark = self._get_is_dark()

        # Build menu items for this category
        item_controls = [
            self._build_menu_item(item, category.color)
            for item in category.items
        ]

        # Category leading icon in colored circle
        leading_icon = self._create_icon_circle(
            category.icon,
            category.color,
            size=40,
            icon_size=22,
        )

        # Category title
        title = ft.Text(
            category.title,
            size=15,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_on_surface(is_dark),
        )
        self._category_titles.append(title)

        # Subtitle with item count
        item_count = len(category.items)
        subtitle = ft.Text(
            f"{item_count} option{'s' if item_count != 1 else ''}",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )
        self._category_subtitles.append(subtitle)

        expansion_tile = ft.ExpansionTile(
            leading=leading_icon,
            title=title,
            subtitle=subtitle,
            controls=item_controls,
            expanded=False,
            maintain_state=True,
            bgcolor="transparent",
            collapsed_bgcolor="transparent",
            icon_color=MD3Colors.get_on_surface(is_dark),
            collapsed_icon_color=MD3Colors.get_on_surface_variant(is_dark),
            text_color=MD3Colors.get_on_surface(is_dark),
            collapsed_text_color=MD3Colors.get_on_surface_variant(is_dark),
            tile_padding=ft.padding.symmetric(horizontal=12, vertical=8),
            controls_padding=ft.padding.only(left=52, right=8, bottom=8),
            shape=ft.RoundedRectangleBorder(radius=8),
        )
        self._expansion_tiles.append(expansion_tile)
        return expansion_tile

    def _build(self):
        """Build the complete AppMenuSelector component"""
        is_dark = self._get_is_dark()

        # Build category tiles
        category_tiles = [
            self._build_category_tile(cat) for cat in self.categories
        ]

        # Main content column
        self.content = ft.Column(
            controls=category_tiles,
            spacing=4,
            tight=True,
        )

        # Container styling (full width, no border)
        self.bgcolor = MD3Colors.get_surface_variant(is_dark)
        self.padding = ft.padding.symmetric(vertical=8, horizontal=16)
        self.animate = ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT)

    def set_badge_visible(self, item_id: str, visible: bool):
        """Set badge visibility for a specific menu item"""
        if item_id in self._badge_refs:
            self._badge_refs[item_id].visible = visible
            if self._page_ref:
                self._badge_refs[item_id].update()

    def refresh_theme(self):
        """Refresh the component after theme change - delegates to apply_theme"""
        is_dark = self._get_is_dark()
        # Use run_task to call async method from sync context
        if self._page_ref:
            self._page_ref.run_task(self.apply_theme, is_dark, 0)

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for cascade updates"""
        return {
            "bgcolor": MD3Colors.get_themed_pair("surface_variant"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay - extended for complex updates"""
        import asyncio
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            # Update container background
            self.bgcolor = MD3Colors.get_surface_variant(is_dark)

            # Update all title texts
            for title in self._title_texts:
                # Check if it's a disabled item (has muted color)
                if title.color in ("#666666", "#888888", MD3Colors.get_themed("text_tertiary", True), MD3Colors.get_themed("text_tertiary", False)):
                    title.color = MD3Colors.get_themed("text_tertiary", is_dark)
                else:
                    title.color = MD3Colors.get_on_surface(is_dark)

            # Update all description texts
            for desc in self._description_texts:
                if desc.color in ("#888888", MD3Colors.get_text_secondary(True), MD3Colors.get_text_secondary(False)):
                    desc.color = MD3Colors.get_text_secondary(is_dark)
                else:
                    desc.color = MD3Colors.get_on_surface_variant(is_dark)

            # Update category titles
            for title in self._category_titles:
                title.color = MD3Colors.get_on_surface(is_dark)

            # Update category subtitles
            for subtitle in self._category_subtitles:
                subtitle.color = MD3Colors.get_on_surface_variant(is_dark)

            # Update trailing icons
            for icon in self._trailing_icons:
                if icon.color in ("#555555", MD3Colors.get_themed("text_disabled", True), MD3Colors.get_themed("text_disabled", False)):
                    icon.color = MD3Colors.get_themed("text_disabled", is_dark)
                else:
                    icon.color = MD3Colors.get_on_surface_variant(is_dark)

            # Update expansion tiles
            for tile in self._expansion_tiles:
                tile.icon_color = MD3Colors.get_on_surface(is_dark)
                tile.collapsed_icon_color = MD3Colors.get_on_surface_variant(is_dark)
                tile.text_color = MD3Colors.get_on_surface(is_dark)
                tile.collapsed_text_color = MD3Colors.get_on_surface_variant(is_dark)

            # Update muted icon circles
            for container in self._muted_icon_circles:
                container.bgcolor = "#3A3A3A" if is_dark else "#E0E0E0"
                if container.content and hasattr(container.content, 'color'):
                    container.content.color = MD3Colors.get_text_secondary(is_dark)

            if hasattr(self, 'update'):
                self.update()
        except Exception:
            pass  # Silent fail - component may have been garbage collected
