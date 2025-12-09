"""
Navigation Drawer Component
Material Design 3 compliant drawer with animations and hover effects
"""

import flet as ft
import inspect
import logging
from typing import Callable, Optional, List
from dlss_updater.ui_flet.theme.colors import MD3Colors, Animations, Shadows
from dlss_updater.version import __version__


class NavigationDrawerItem:
    """Represents a navigation drawer destination"""

    def __init__(
        self,
        label: str,
        icon: str,
        selected_icon: str,
        index: int
    ):
        self.label = label
        self.icon = icon
        self.selected_icon = selected_icon
        self.index = index


class CustomNavigationDrawer:
    """
    Material Design 3 Navigation Drawer with enhanced animations and hover effects
    """

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        on_destination_changed: Callable[[int], None],
        initial_index: int = 0
    ):
        self.page = page
        self.logger = logger
        self.on_destination_changed = on_destination_changed
        self.selected_index = initial_index

        # Define navigation items
        self.items = [
            NavigationDrawerItem(
                label="Launchers",
                icon=ft.Icons.FOLDER_SPECIAL_OUTLINED,
                selected_icon=ft.Icons.FOLDER_SPECIAL,
                index=0
            ),
            NavigationDrawerItem(
                label="Games",
                icon=ft.Icons.VIDEOGAME_ASSET_OUTLINED,
                selected_icon=ft.Icons.VIDEOGAME_ASSET,
                index=1
            ),
            NavigationDrawerItem(
                label="Backups",
                icon=ft.Icons.RESTORE_OUTLINED,
                selected_icon=ft.Icons.RESTORE,
                index=2
            ),
        ]

        # Build the drawer
        self.drawer = self._build_drawer()

    def _build_drawer(self) -> ft.NavigationDrawer:
        """Build the complete navigation drawer with all sections"""

        # Get theme state
        is_dark = self.page.session.get("is_dark_theme") if self.page and self.page.session.contains_key("is_dark_theme") else True

        # Header section
        header = self._build_header(is_dark)

        # Destination items
        destinations = self._build_destinations()

        # Footer section (optional)
        footer = self._build_footer(is_dark)

        # Assemble drawer
        drawer = ft.NavigationDrawer(
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            elevation=16,
            shadow_color=MD3Colors.SHADOW,
            selected_index=self.selected_index,
            on_change=self._on_destination_change,
            on_dismiss=self._on_drawer_dismissed,
            controls=[
                header,
                ft.Divider(height=1, color=MD3Colors.get_outline(is_dark)),
                *destinations,
                ft.Container(expand=True),  # Spacer to push footer down
                ft.Divider(height=1, color=MD3Colors.get_outline(is_dark)),
                footer,
            ]
        )

        return drawer

    def _build_header(self, is_dark: bool) -> ft.Container:
        """Build the drawer header with branding"""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(height=20),  # Top padding
                    ft.Container(
                        content=ft.Icon(
                            ft.Icons.MEMORY,
                            color=MD3Colors.PRIMARY,
                            size=48,
                        ),
                        alignment=ft.alignment.center,
                    ),
                    ft.Container(height=12),
                    ft.Text(
                        "DLSS Updater",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=MD3Colors.get_on_surface(is_dark),
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        f"Version {__version__}",
                        size=12,
                        color=MD3Colors.get_on_surface_variant(is_dark),
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Container(height=16),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            padding=ft.padding.symmetric(horizontal=16, vertical=8),
        )

    def _build_destinations(self) -> List[ft.NavigationDrawerDestination]:
        """Build navigation destination items"""
        destination_controls = []

        for item in self.items:
            # Create the destination - NavigationDrawer handles selection visuals
            destination = ft.NavigationDrawerDestination(
                label=item.label,
                icon=item.icon,
                selected_icon=item.selected_icon,
            )
            destination_controls.append(destination)

        return destination_controls

    def _build_footer(self, is_dark: bool) -> ft.Container:
        """Build the drawer footer with additional info or actions"""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Keep your games up to date",
                        size=11,
                        color=MD3Colors.get_on_surface_variant(is_dark),
                        text_align=ft.TextAlign.CENTER,
                        italic=True,
                    ),
                    ft.Container(height=8),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.all(16),
        )

    def _on_destination_change(self, e):
        """Handle destination selection change"""
        new_index = e.control.selected_index

        self.logger.info(f"Navigation drawer destination changed: {new_index}")

        # Update selected index
        self.selected_index = new_index

        # Close drawer
        self.page.close(self.drawer)

        # Notify parent component - use run_task for async callbacks
        if self.on_destination_changed:
            # Check if the callback is a coroutine function (async)
            if inspect.iscoroutinefunction(self.on_destination_changed):
                self.page.run_task(self.on_destination_changed, new_index)
            else:
                self.on_destination_changed(new_index)

    def _on_drawer_dismissed(self, e):
        """Handle drawer dismissal (close via overlay click or back button)"""
        self.logger.info("Navigation drawer dismissed")

    def get_drawer(self) -> ft.NavigationDrawer:
        """Get the drawer control for adding to page"""
        return self.drawer

    def set_selected_index(self, index: int):
        """Programmatically update the selected destination"""
        if 0 <= index < len(self.items):
            self.selected_index = index
            self.drawer.selected_index = index
            if self.page:
                self.page.update()
