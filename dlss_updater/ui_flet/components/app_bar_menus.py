"""
High-Performance App Bar Menu Components

Replaces the slow AppMenuSelector (100+ nested controls, 585ms expand, 1183ms collapse)
with 3 separate PopupMenuButton icons for maximum performance (~20-30ms per popup).

Each popup uses native Flutter GPU-accelerated rendering with minimal control trees.
"""

from dataclasses import dataclass
from typing import Callable
import inspect

import flet as ft

from ..theme.colors import MD3Colors
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


class BasePopupMenu(ThemeAwareMixin):
    """
    Base class for popup menu buttons.

    Each menu is a colored icon circle that opens a PopupMenu when clicked.
    Uses native Flutter PopupMenuButton for GPU-accelerated performance.
    """

    def __init__(
        self,
        page: ft.Page,
        icon: str,
        color: str,
        tooltip: str,
        items: list[MenuItem],
        is_dark: bool = True,
    ):
        self._page_ref = page
        self._icon = icon
        self._color = color
        self._tooltip = tooltip
        self._items = items
        self._is_dark = is_dark

        # Theme registration
        self._registry = get_theme_registry()
        self._theme_priority = 25  # Same as cards

        # Badge references for dynamic updates
        self._badge_refs: dict[str, ft.Container] = {}

        # Build the popup button
        self.button = self._build_popup_button()

        # Register for theme updates
        self._register_theme_aware()

    def _build_popup_button(self) -> ft.PopupMenuButton:
        """Build the PopupMenuButton with colored icon circle"""
        # Create menu items
        menu_items = [self._build_menu_item(item) for item in self._items]

        # Create the colored icon circle as button content
        icon_circle = ft.Container(
            content=ft.Icon(self._icon, size=18, color=ft.Colors.WHITE),
            width=36,
            height=36,
            bgcolor=self._color,
            border_radius=18,
            alignment=ft.Alignment.CENTER,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=4,
                offset=ft.Offset(0, 2),
                color=f"{self._color}40",
            ),
        )

        return ft.PopupMenuButton(
            content=icon_circle,
            items=menu_items,
            tooltip=self._tooltip,
            menu_position=ft.PopupMenuPosition.UNDER,
            shape=ft.RoundedRectangleBorder(radius=12),
            bgcolor=MD3Colors.get_surface_container(self._is_dark),
            icon_color=MD3Colors.get_on_surface(self._is_dark),
        )

    def _build_menu_item(self, item: MenuItem) -> ft.PopupMenuItem:
        """Build a styled menu item with icon circle, title, and description

        PERF: Only creates Stack+badge Container when show_badge=True.
        Most items don't need badges, saving 2 controls per item (~32 controls total).
        """
        is_dark = self._is_dark

        # Icon circle (colored or muted based on disabled state)
        if item.is_disabled:
            icon_circle = ft.Container(
                content=ft.Icon(
                    item.icon,
                    size=16,
                    color=MD3Colors.get_text_secondary(is_dark),
                ),
                width=32,
                height=32,
                bgcolor="#3A3A3A" if is_dark else "#E0E0E0",
                border_radius=16,
                alignment=ft.Alignment.CENTER,
            )
        else:
            icon_circle = ft.Container(
                content=ft.Icon(item.icon, size=16, color=ft.Colors.WHITE),
                width=32,
                height=32,
                bgcolor=self._color,
                border_radius=16,
                alignment=ft.Alignment.CENTER,
            )

        # Title text
        title_color = (
            MD3Colors.get_themed("text_tertiary", is_dark)
            if item.is_disabled
            else MD3Colors.get_on_surface(is_dark)
        )
        title_text = ft.Text(
            item.title,
            size=14,
            weight=ft.FontWeight.W_500,
            color=title_color,
        )

        # Description text
        desc_color = (
            MD3Colors.get_text_secondary(is_dark)
            if item.is_disabled
            else MD3Colors.get_on_surface_variant(is_dark)
        )
        description_text = ft.Text(
            item.description,
            size=11,
            color=desc_color,
        )

        # PERF: Only wrap icon in Stack if badge is needed
        if item.show_badge:
            # Badge indicator - only created when needed
            badge = ft.Container(
                width=8,
                height=8,
                bgcolor=ft.Colors.RED,
                border_radius=4,
                visible=True,
            )
            self._badge_refs[item.id] = badge

            icon_with_badge = ft.Stack(
                controls=[
                    icon_circle,
                    ft.Container(
                        content=badge,
                        right=-2,
                        top=-2,
                    ),
                ],
                width=32,
                height=32,
            )
            left_control = icon_with_badge
        else:
            # No badge needed - use icon directly (saves Stack + Container)
            left_control = icon_circle

        # Content layout
        content = ft.Row(
            controls=[
                left_control,
                ft.Column(
                    controls=[title_text, description_text],
                    spacing=2,
                ),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Create click handler that properly handles async callbacks
        def create_click_handler(menu_item: MenuItem):
            def handler(e):
                if menu_item.is_disabled:
                    return
                if menu_item.on_click:
                    result = menu_item.on_click(e)
                    # Handle async callbacks
                    if inspect.iscoroutine(result) and self._page_ref:
                        async def run_async(coro):
                            await coro
                        self._page_ref.run_task(run_async, result)
            return handler

        return ft.PopupMenuItem(
            content=content,
            on_click=create_click_handler(item) if not item.is_disabled else None,
        )

    def set_badge_visible(self, item_id: str, visible: bool):
        """Set badge visibility for a specific menu item"""
        if item_id in self._badge_refs:
            self._badge_refs[item_id].visible = visible
            if self._page_ref:
                self._badge_refs[item_id].update()

    def rebuild(self, is_dark: bool):
        """Rebuild the menu with new theme colors"""
        self._is_dark = is_dark
        self.button = self._build_popup_button()

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for cascade updates"""
        return {
            "button.bgcolor": MD3Colors.get_themed_pair("surface_container"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay"""
        import asyncio
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            self._is_dark = is_dark
            # Rebuild popup button with new colors
            self.button.bgcolor = MD3Colors.get_surface_container(is_dark)
            self.button.icon_color = MD3Colors.get_on_surface(is_dark)

            if hasattr(self.button, 'update'):
                self.button.update()
        except Exception:
            pass  # Silent fail - component may have been garbage collected


class CommunityMenu(BasePopupMenu):
    """
    Pink heart icon - Community & Support items

    Items:
    - Support Development (Buy me a coffee)
    - Report Bug
    - Twitter
    - Discord
    - Show Discord Invite
    - Release Notes
    """

    def __init__(
        self,
        page: ft.Page,
        is_dark: bool,
        callbacks: dict[str, Callable],
    ):
        items = [
            MenuItem(
                id="support",
                title="Support Development",
                description="Buy me a coffee",
                icon=ft.Icons.COFFEE,
                on_click=callbacks.get("support"),
                tooltip="Support the developer",
            ),
            MenuItem(
                id="bug_report",
                title="Report Bug",
                description="Submit an issue on GitHub",
                icon=ft.Icons.BUG_REPORT,
                on_click=callbacks.get("bug_report"),
            ),
            MenuItem(
                id="twitter",
                title="Twitter",
                description="Follow on Twitter",
                icon=ft.Icons.TAG,
                on_click=callbacks.get("twitter"),
            ),
            MenuItem(
                id="discord",
                title="Discord",
                description="Message on Discord",
                icon=ft.Icons.CHAT,
                on_click=callbacks.get("discord"),
            ),
            MenuItem(
                id="discord_invite",
                title="Show Discord Invite",
                description="Re-display the Discord community banner",
                icon=ft.Icons.CAMPAIGN,
                on_click=callbacks.get("discord_invite"),
            ),
            MenuItem(
                id="release_notes",
                title="Release Notes",
                description="View changelog",
                icon=ft.Icons.ARTICLE,
                on_click=callbacks.get("release_notes"),
            ),
        ]

        super().__init__(
            page=page,
            icon=ft.Icons.FAVORITE,
            color="#E91E63",  # Pink
            tooltip="Community & Support",
            items=items,
            is_dark=is_dark,
        )


class PreferencesMenu(BasePopupMenu):
    """
    Orange gear icon - Preferences items

    Items:
    - Update Preferences
    - UI Preferences
    - Manage Blacklist
    - DLSS Debug Overlay
    - Theme Toggle
    """

    def __init__(
        self,
        page: ft.Page,
        is_dark: bool,
        callbacks: dict[str, Callable],
        features_dlss_overlay: bool = True,
    ):
        items = [
            MenuItem(
                id="update_prefs",
                title="Update Preferences",
                description="Configure update settings",
                icon=ft.Icons.SETTINGS,
                on_click=callbacks.get("update_prefs"),
            ),
            MenuItem(
                id="ui_prefs",
                title="UI Preferences",
                description="Interface and performance",
                icon=ft.Icons.DISPLAY_SETTINGS,
                on_click=callbacks.get("ui_prefs"),
            ),
            MenuItem(
                id="blacklist",
                title="Manage Blacklist",
                description="Exclude specific games",
                icon=ft.Icons.BLOCK,
                on_click=callbacks.get("blacklist"),
            ),
            MenuItem(
                id="dlss_overlay",
                title="DLSS Debug Overlay",
                description="Toggle in-game DLSS indicator" if features_dlss_overlay else "Requires NVIDIA GPU",
                icon=ft.Icons.BUG_REPORT,
                on_click=callbacks.get("dlss_overlay"),
                is_disabled=not features_dlss_overlay,
                tooltip="Requires NVIDIA GPU" if not features_dlss_overlay else None,
            ),
        ]

        # Theme toggle
        items.append(
            MenuItem(
                id="theme",
                title=f"Theme: {'Dark' if is_dark else 'Light'} Mode",
                description="Switch to light mode" if is_dark else "Switch to dark mode",
                icon=ft.Icons.DARK_MODE if is_dark else ft.Icons.LIGHT_MODE,
                on_click=callbacks.get("theme"),
            )
        )

        # Store for rebuild
        self._callbacks = callbacks
        self._features_dlss_overlay = features_dlss_overlay

        super().__init__(
            page=page,
            icon=ft.Icons.SETTINGS,
            color="#FF9800",  # Orange
            tooltip="Preferences",
            items=items,
            is_dark=is_dark,
        )

    def rebuild(self, is_dark: bool):
        """Rebuild with updated theme state (needed for theme toggle item)"""
        self._is_dark = is_dark
        self._items = self._build_items(is_dark)
        self.button = self._build_popup_button()

    def _build_items(self, is_dark: bool) -> list[MenuItem]:
        """Build items list with current theme state"""
        items = [
            MenuItem(
                id="update_prefs",
                title="Update Preferences",
                description="Configure update settings",
                icon=ft.Icons.SETTINGS,
                on_click=self._callbacks.get("update_prefs"),
            ),
            MenuItem(
                id="ui_prefs",
                title="UI Preferences",
                description="Interface and performance",
                icon=ft.Icons.DISPLAY_SETTINGS,
                on_click=self._callbacks.get("ui_prefs"),
            ),
            MenuItem(
                id="blacklist",
                title="Manage Blacklist",
                description="Exclude specific games",
                icon=ft.Icons.BLOCK,
                on_click=self._callbacks.get("blacklist"),
            ),
            MenuItem(
                id="dlss_overlay",
                title="DLSS Debug Overlay",
                description="Toggle in-game DLSS indicator" if self._features_dlss_overlay else "Requires NVIDIA GPU",
                icon=ft.Icons.BUG_REPORT,
                on_click=self._callbacks.get("dlss_overlay"),
                is_disabled=not self._features_dlss_overlay,
                tooltip="Requires NVIDIA GPU" if not self._features_dlss_overlay else None,
            ),
        ]

        items.append(
            MenuItem(
                id="theme",
                title=f"Theme: {'Dark' if is_dark else 'Light'} Mode",
                description="Switch to light mode" if is_dark else "Switch to dark mode",
                icon=ft.Icons.DARK_MODE if is_dark else ft.Icons.LIGHT_MODE,
                on_click=self._callbacks.get("theme"),
            )
        )

        return items


class ApplicationMenu(BasePopupMenu):
    """
    Blue grid icon - Application items

    Items:
    - Check for Updates
    """

    def __init__(
        self,
        page: ft.Page,
        is_dark: bool,
        callbacks: dict[str, Callable],
    ):
        items = [
            MenuItem(
                id="check_updates",
                title="Check for Updates",
                description="Check for app updates",
                icon=ft.Icons.SYSTEM_UPDATE,
                on_click=callbacks.get("check_updates"),
                show_badge=False,
            ),
        ]

        super().__init__(
            page=page,
            icon=ft.Icons.APPS,
            color="#2196F3",  # Blue
            tooltip="Application",
            items=items,
            is_dark=is_dark,
        )


def create_app_bar_menus(
    page: ft.Page,
    is_dark: bool,
    callbacks: dict[str, Callable],
    features_dlss_overlay: bool = True,
) -> tuple[CommunityMenu, PreferencesMenu, ApplicationMenu]:
    """
    Factory function to create all 3 app bar menu components.

    Args:
        page: Flet page reference
        is_dark: Whether dark mode is active
        callbacks: Dict of callback functions for menu items
        features_dlss_overlay: Whether DLSS overlay is available

    Returns:
        Tuple of (CommunityMenu, PreferencesMenu, ApplicationMenu)
    """
    community = CommunityMenu(page, is_dark, callbacks)
    preferences = PreferencesMenu(
        page, is_dark, callbacks,
        features_dlss_overlay=features_dlss_overlay,
    )
    application = ApplicationMenu(page, is_dark, callbacks)

    return community, preferences, application
