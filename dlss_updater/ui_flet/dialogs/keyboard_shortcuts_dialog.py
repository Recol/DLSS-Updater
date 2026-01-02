"""
Keyboard Shortcuts Help Dialog
Display all available keyboard shortcuts in a formatted, categorized table

Shortcuts include:
- Navigation (Ctrl+1/2/3 for views, Tab to cycle focus)
- Actions (Ctrl+R refresh, Ctrl+S scan, Ctrl+U update, Ctrl+F search)
- Game Management (F favorite, T tag, Space preview, Enter open details)
- Application (Ctrl+Q quit, F11 fullscreen, Ctrl+, settings)
"""

import logging
from typing import List, Dict
import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows
from dlss_updater.ui_flet.theme.md3_system import MD3Spacing


# Keyboard shortcuts configuration
KEYBOARD_SHORTCUTS = [
    {
        "category": "Navigation",
        "icon": ft.Icons.NAVIGATION,
        "color": MD3Colors.PRIMARY,
        "shortcuts": [
            {"keys": "Ctrl + 1", "action": "Switch to Launchers view"},
            {"keys": "Ctrl + 2", "action": "Switch to Games view"},
            {"keys": "Ctrl + 3", "action": "Switch to Backups view"},
            {"keys": "Ctrl + 4", "action": "Switch to Dashboard view"},
            {"keys": "Tab", "action": "Cycle focus between elements"},
            {"keys": "Shift + Tab", "action": "Cycle focus backwards"},
        ],
    },
    {
        "category": "Actions",
        "icon": ft.Icons.FLASH_ON,
        "color": MD3Colors.INFO,
        "shortcuts": [
            {"keys": "Ctrl + R", "action": "Refresh current view"},
            {"keys": "Ctrl + S", "action": "Scan for games"},
            {"keys": "Ctrl + U", "action": "Start update"},
            {"keys": "Ctrl + F", "action": "Focus search bar"},
            {"keys": "Esc", "action": "Clear search / Close dialog"},
        ],
    },
    {
        "category": "Game Management",
        "icon": ft.Icons.VIDEOGAME_ASSET,
        "color": MD3Colors.SUCCESS,
        "shortcuts": [
            {"keys": "F", "action": "Toggle favorite (when game selected)"},
            {"keys": "T", "action": "Assign tags (when game selected)"},
            {"keys": "Space", "action": "Quick preview game details"},
            {"keys": "Enter", "action": "Open game details dialog"},
            {"keys": "Delete", "action": "Remove game from library"},
        ],
    },
    {
        "category": "Application",
        "icon": ft.Icons.SETTINGS,
        "color": MD3Colors.SECONDARY,
        "shortcuts": [
            {"keys": "Ctrl + Q", "action": "Quit application"},
            {"keys": "F11", "action": "Toggle fullscreen"},
            {"keys": "Ctrl + ,", "action": "Open settings"},
            {"keys": "Ctrl + Shift + L", "action": "Toggle logger panel"},
            {"keys": "F1", "action": "Show this help dialog"},
        ],
    },
]


class KeyboardShortcutBadge(ft.Container):
    """Small badge showing keyboard shortcut for menu items"""

    def __init__(self, shortcut_text: str):
        super().__init__()

        # Parse shortcut text (e.g., "Ctrl + R" -> ["Ctrl", "R"])
        keys = [k.strip() for k in shortcut_text.split('+')]

        # Create key badges
        key_badges = []
        for i, key in enumerate(keys):
            key_badges.append(
                ft.Container(
                    content=ft.Text(
                        key,
                        size=10,
                        weight=ft.FontWeight.W_600,
                        color=MD3Colors.ON_SURFACE_VARIANT,
                    ),
                    bgcolor=MD3Colors.SURFACE_DIM,
                    border_radius=4,
                    padding=ft.padding.symmetric(horizontal=6, vertical=2),
                    border=ft.border.all(1, MD3Colors.OUTLINE_VARIANT),
                )
            )

            # Add "+" between keys
            if i < len(keys) - 1:
                key_badges.append(
                    ft.Text("+", size=10, color=MD3Colors.ON_SURFACE_VARIANT)
                )

        self.content = ft.Row(
            controls=key_badges,
            spacing=4,
            tight=True,
        )


class KeyboardShortcutsDialog:
    """Dialog showing all keyboard shortcuts organized by category"""

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger

    async def show(self):
        """Show keyboard shortcuts help dialog"""

        # Build category sections
        category_sections = []

        for category_data in KEYBOARD_SHORTCUTS:
            # Category header
            category_header = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            category_data['icon'],
                            color=category_data['color'],
                            size=20,
                        ),
                        ft.Text(
                            category_data['category'],
                            size=16,
                            weight=ft.FontWeight.BOLD,
                            color=MD3Colors.ON_SURFACE,
                        ),
                    ],
                    spacing=12,
                ),
                padding=ft.padding.only(bottom=8),
            )

            # Shortcut rows
            shortcut_rows = []
            for shortcut in category_data['shortcuts']:
                row = ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Container(
                                content=KeyboardShortcutBadge(shortcut['keys']),
                                width=150,
                            ),
                            ft.Text(
                                shortcut['action'],
                                size=13,
                                color=MD3Colors.ON_SURFACE,
                                expand=True,
                            ),
                        ],
                        spacing=16,
                    ),
                    padding=ft.padding.symmetric(vertical=6, horizontal=12),
                    bgcolor=MD3Colors.SURFACE,
                    border_radius=6,
                )
                shortcut_rows.append(row)

            # Category container
            category_container = ft.Container(
                content=ft.Column(
                    controls=[
                        category_header,
                        *shortcut_rows,
                    ],
                    spacing=4,
                ),
                padding=16,
                bgcolor=MD3Colors.SURFACE_VARIANT,
                border_radius=12,
                shadow=Shadows.LEVEL_1,
            )

            category_sections.append(category_container)

        # Tip section
        tip_section = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LIGHTBULB_OUTLINE, color=MD3Colors.WARNING, size=20),
                    ft.Column(
                        controls=[
                            ft.Text(
                                "Tip: Press F1 anytime to view shortcuts",
                                size=12,
                                weight=ft.FontWeight.BOLD,
                                color=MD3Colors.ON_SURFACE,
                            ),
                            ft.Text(
                                "Keyboard shortcuts make navigation faster and more efficient",
                                size=11,
                                color=MD3Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=12,
            ),
            padding=12,
            bgcolor=f"{MD3Colors.WARNING}15",
            border_radius=8,
            border=ft.border.all(1, f"{MD3Colors.WARNING}40"),
        )

        # Dialog
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.KEYBOARD, color=MD3Colors.PRIMARY, size=28),
                    ft.Text("Keyboard Shortcuts", size=20),
                ],
                spacing=12,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        tip_section,
                        ft.Container(height=8),
                        *category_sections,
                    ],
                    spacing=12,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=600,
                height=500,
            ),
            actions=[
                ft.FilledButton(
                    "Got it!",
                    icon=ft.Icons.CHECK,
                    on_click=lambda e: self.page.close(dialog),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.CENTER,
        )

        self.page.open(dialog)
        self.logger.info("Keyboard shortcuts dialog opened")


class KeyboardShortcutHandler:
    """
    Global keyboard shortcut handler for the application
    Should be initialized in MainView and registered with page.on_keyboard_event
    """

    def __init__(self, page: ft.Page, logger: logging.Logger, main_view):
        self.page = page
        self.logger = logger
        self.main_view = main_view

    async def handle_keyboard_event(self, e: ft.KeyboardEvent):
        """Handle keyboard events and execute shortcuts"""
        # Get pressed keys
        key = e.key.lower()
        ctrl = e.ctrl
        shift = e.shift

        self.logger.debug(f"Keyboard event: key={key}, ctrl={ctrl}, shift={shift}")

        # Navigation shortcuts (Ctrl + 1/2/3/4)
        if ctrl and key in ['1', '2', '3', '4']:
            view_index = int(key) - 1
            await self._navigate_to_view(view_index)
            return

        # Action shortcuts
        if ctrl and key == 'r':
            await self._refresh_current_view()
            return

        if ctrl and key == 's':
            await self._trigger_scan()
            return

        if ctrl and key == 'u':
            await self._trigger_update()
            return

        if ctrl and key == 'f':
            await self._focus_search()
            return

        # Application shortcuts
        if ctrl and key == 'q':
            self.page.window.close()
            return

        if key == 'f11':
            self.page.window.fullscreen = not self.page.window.fullscreen
            self.page.update()
            return

        if ctrl and key == ',':
            await self._open_settings()
            return

        if ctrl and shift and key == 'l':
            await self._toggle_logger()
            return

        if key == 'f1':
            await self._show_shortcuts_help()
            return

        # Esc key - clear search or close dialogs
        if key == 'escape':
            await self._handle_escape()
            return

    async def _navigate_to_view(self, index: int):
        """Navigate to view by index"""
        if hasattr(self.main_view, 'navigation_drawer_component'):
            await self.main_view._on_drawer_navigation_changed(index)

    async def _refresh_current_view(self):
        """Refresh current view"""
        current_index = self.main_view.current_view_index

        if current_index == 1 and hasattr(self.main_view, 'games_view'):
            await self.main_view.games_view.load_games()
        elif current_index == 2 and hasattr(self.main_view, 'backups_view'):
            await self.main_view.backups_view.load_backups()

    async def _trigger_scan(self):
        """Trigger game scan"""
        if hasattr(self.main_view, '_on_scan_clicked'):
            await self.main_view._on_scan_clicked(None)

    async def _trigger_update(self):
        """Trigger update"""
        if hasattr(self.main_view, '_on_update_clicked'):
            await self.main_view._on_update_clicked(None)

    async def _focus_search(self):
        """Focus search bar if in Games view"""
        if self.main_view.current_view_index == 1 and hasattr(self.main_view, 'games_view'):
            if hasattr(self.main_view.games_view, 'search_bar'):
                self.main_view.games_view.search_bar.focus()

    async def _open_settings(self):
        """Open settings dialog"""
        if hasattr(self.main_view, '_on_settings_clicked'):
            await self.main_view._on_settings_clicked(None)

    async def _toggle_logger(self):
        """Toggle logger panel"""
        if hasattr(self.main_view, 'logger_panel'):
            # Toggle expansion
            self.main_view.logger_panel.toggle_panel()

    async def _show_shortcuts_help(self):
        """Show keyboard shortcuts help dialog"""
        dialog = KeyboardShortcutsDialog(self.page, self.logger)
        await dialog.show()

    async def _handle_escape(self):
        """Handle Escape key - clear search or close dialogs"""
        # Try to clear search if in Games view
        if self.main_view.current_view_index == 1 and hasattr(self.main_view, 'games_view'):
            if hasattr(self.main_view.games_view, 'search_bar'):
                search_bar = self.main_view.games_view.search_bar
                if search_bar.get_value():
                    search_bar.set_value("")
                    await self.main_view.games_view._on_search_cleared()
