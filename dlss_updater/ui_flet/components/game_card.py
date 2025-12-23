"""
Game Card Component
Individual game card with Steam image, DLL badges, and action buttons
"""

import asyncio
from pathlib import Path
from typing import Optional, List
import flet as ft

from dlss_updater.database import GameDLL
from dlss_updater.steam_integration import fetch_steam_image
from dlss_updater.ui_flet.theme.colors import Shadows, TechnologyColors
from dlss_updater.constants import DLL_GROUPS


class GameCard(ft.Card):
    """Individual game card with image, DLL info, and actions"""

    def __init__(self, game, dlls: List[GameDLL], page: ft.Page, logger, on_update=None, on_view_backups=None):
        super().__init__()
        self.game = game
        self.dlls = dlls
        self.page = page
        self.logger = logger
        self.on_update_callback = on_update
        self.on_view_backups_callback = on_view_backups

        # Update button reference for loading state
        self.update_button: Optional[ft.PopupMenuButton] = None
        self.is_updating = False

        # Reference to dll_badges for refresh
        self.dll_badges_container: Optional[ft.Row] = None
        self.right_content: Optional[ft.Column] = None

        # Card styling optimized for grid layout
        self.elevation = 2
        self.surface_tint_color = "#2D6E88"
        self.margin = ft.margin.all(0)  # ResponsiveRow handles spacing
        self.shadow = Shadows.LEVEL_2
        self.width = None  # Let ResponsiveRow control width
        self.expand = True  # Fill available space in grid cell

        # Animation
        self.animate = ft.Animation(200, ft.AnimationCurve.EASE_OUT)
        self.animate_scale = ft.Animation(200, ft.AnimationCurve.EASE_OUT)

        # Hover callback
        self.on_hover = self._on_hover

        # Build content
        self._build_card_content()

    def _create_skeleton_loader(self):
        """Create animated skeleton loader for image placeholder"""
        return ft.Container(
            width=None,  # Full width
            height=140,  # Match image height
            gradient=ft.LinearGradient(
                begin=ft.alignment.center_left,
                end=ft.alignment.center_right,
                colors=["#1E1E1E", "#2E2E2E", "#1E1E1E"],
            ),
            border_radius=8,
            content=ft.Icon(
                ft.Icons.VIDEOGAME_ASSET,
                size=48,
                color=ft.Colors.with_opacity(0.3, ft.Colors.GREY),
            ),
            alignment=ft.alignment.center,
        )

    def _build_card_content(self):
        """Build card content layout"""
        # Image container with skeleton loader (responsive for grid)
        self.image_widget = ft.Image(
            src="/assets/placeholder_game.png",
            width=None,  # Full card width
            height=140,  # Slightly taller for better aspect ratio in grid
            fit=ft.ImageFit.COVER,
            border_radius=ft.border_radius.all(8),
            error_content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=48, color=ft.Colors.GREY),
        )

        self.image_container = ft.Container(
            content=self._create_skeleton_loader(),  # Start with skeleton
            width=140,  # Constrain image width for proper layout
            height=140,  # Match image height
            border_radius=8,
            bgcolor="#1E1E1E",
            alignment=ft.alignment.center,
        )

        # Game name, launcher, and path
        game_info = ft.Column(
            controls=[
                ft.Text(
                    self.game.name,
                    size=16,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.WHITE,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    no_wrap=False,  # Allow wrapping for long names
                ),
                ft.Text(
                    self.game.launcher,
                    size=12,
                    color="#888888",
                    no_wrap=True,
                ),
                # Path row with tooltip and copy button
                ft.Row(
                    controls=[
                        ft.Container(
                            content=ft.Text(
                                self._truncate_path(self.game.path, 40),
                                size=10,
                                color="#666666",
                                no_wrap=True,
                                italic=True,
                            ),
                            tooltip=self.game.path,
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CONTENT_COPY,
                            icon_size=12,
                            icon_color="#666666",
                            tooltip="Copy path",
                            on_click=self._on_copy_path_clicked,
                            width=20,
                            height=20,
                        ),
                    ],
                    spacing=4,
                    tight=True,
                ),
            ],
            spacing=4,
            tight=True,
        )

        # DLL badges
        self.dll_badges_container = self._create_dll_badges()

        # Action buttons - store reference to update button for loading state
        # Build popup menu items for selective DLL updates
        self.update_button = self._create_update_popup_menu()

        action_buttons = ft.Row(
            controls=[
                self.update_button,
                ft.TextButton(
                    "View Backups",
                    icon=ft.Icons.RESTORE,
                    on_click=self._on_view_backups_clicked,
                    style=ft.ButtonStyle(
                        color="#888888",
                    ),
                ),
            ],
            spacing=8,
            wrap=True,
        )

        # Right side content
        self.right_content = ft.Column(
            controls=[
                game_info,
                self.dll_badges_container,
                action_buttons,
            ],
            spacing=8,
            expand=True,
            tight=True,
        )

        # Card content layout
        self.content = ft.Container(
            content=ft.Row(
                controls=[
                    self.image_container,
                    self.right_content,
                ],
                spacing=16,
            ),
            padding=12,
        )

    def _create_dll_badges(self) -> ft.Row:
        """Create DLL type badges with version info using Material Design 3 Chips"""
        badges = []

        dll_colors = {
            "DLSS": "#76B900",  # NVIDIA green
            "XeSS": "#0071C5",  # Intel blue
            "FSR": "#ED1C24",   # AMD red
            "DLSS-G": "#76B900",  # NVIDIA green
            "DLSS-D": "#76B900",  # NVIDIA green
            "Streamline": "#76B900",
            "DirectStorage": "#FFB900",  # Windows yellow
        }

        for dll in self.dlls[:4]:  # Show max 4 badges
            color = dll_colors.get(dll.dll_type, "#888888")

            chip = ft.Chip(
                label=ft.Column(
                    controls=[
                        ft.Text(
                            dll.dll_type,
                            size=10,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.WHITE,
                            no_wrap=True,
                        ),
                        ft.Text(
                            dll.current_version[:8] if dll.current_version else "Unknown",
                            size=8,
                            color=ft.Colors.WHITE,
                            no_wrap=True,
                        ),
                    ],
                    spacing=2,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                bgcolor=color,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                leading=ft.Icon(
                    ft.Icons.CHECK_CIRCLE if dll.current_version else ft.Icons.HELP_OUTLINE,
                    size=14,
                    color=ft.Colors.WHITE,
                ),
            )
            badges.append(chip)

        if len(self.dlls) > 4:
            badges.append(
                ft.Chip(
                    label=ft.Text(
                        f"+{len(self.dlls) - 4}",
                        size=10,
                        color=ft.Colors.WHITE,
                        no_wrap=True,
                    ),
                    bgcolor="#888888",
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                )
            )

        return ft.Row(
            controls=badges,
            spacing=6,
            wrap=True,
            tight=True,
        )

    def _get_dll_groups_for_game(self) -> List[str]:
        """Get unique DLL technology groups present in this game"""
        groups_present = set()
        for dll in self.dlls:
            dll_filename = dll.dll_filename.lower() if dll.dll_filename else ""
            for group_name, group_dlls in DLL_GROUPS.items():
                if dll_filename in [d.lower() for d in group_dlls]:
                    groups_present.add(group_name)
                    break
        return sorted(list(groups_present))

    def _create_update_popup_menu(self) -> ft.PopupMenuButton:
        """Create popup menu button for selective DLL updates"""
        groups = self._get_dll_groups_for_game()

        # Build menu items
        menu_items = [
            ft.PopupMenuItem(
                text="Update All",
                icon=ft.Icons.UPDATE,
                on_click=lambda e: self._on_update_group_selected("all"),
            ),
        ]

        # Add divider and group-specific options if we have groups
        if groups:
            menu_items.append(ft.PopupMenuItem())  # Divider

            for group in groups:
                color = TechnologyColors.get_color(group)
                # Create a colored container as leading element
                menu_items.append(
                    ft.PopupMenuItem(
                        content=ft.Row(
                            controls=[
                                ft.Container(
                                    width=8,
                                    height=8,
                                    bgcolor=color,
                                    border_radius=4,
                                ),
                                ft.Text(f"Update {group}", size=14),
                            ],
                            spacing=8,
                        ),
                        on_click=lambda e, g=group: self._on_update_group_selected(g),
                    )
                )

        # Use content property to show "Update ▼" text instead of just an icon
        return ft.PopupMenuButton(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.UPDATE, size=18, color="#2D6E88"),
                    ft.Text("Update", size=14, color="#2D6E88"),
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color="#2D6E88"),
                ],
                spacing=4,
                tight=True,
            ),
            tooltip="Select DLLs to update",
            items=menu_items,
        )

    def _on_update_group_selected(self, group: str):
        """Handle DLL group selection from popup menu"""
        if self.is_updating:
            return
        if self.on_update_callback:
            # Pass both game and selected group
            self.on_update_callback(self.game, group)

    async def load_image(self):
        """Async load Steam image with fade-in animation"""
        if not self.game.steam_app_id:
            self.logger.debug(f"No Steam app ID for {self.game.name}, skipping image")
            return

        try:
            from dlss_updater.database import db_manager

            # Check cache first
            cached_path = await db_manager.get_cached_image_path(self.game.steam_app_id)

            if cached_path and Path(cached_path).exists():
                await self._fade_in_image(cached_path)
                self.logger.debug(f"Loaded cached image for {self.game.name}")
                return

            # Fetch from Steam CDN
            self.logger.info(f"Fetching Steam image for {self.game.name} (app_id: {self.game.steam_app_id})")
            image_path = await fetch_steam_image(self.game.steam_app_id)

            if image_path and image_path.exists():
                await self._fade_in_image(str(image_path))
                self.logger.info(f"Successfully loaded image for {self.game.name}")
            else:
                self.logger.debug(f"No image available for {self.game.name}")

        except Exception as e:
            self.logger.error(f"Error loading image for {self.game.name}: {e}", exc_info=True)

    async def _fade_in_image(self, image_path: str):
        """Fade in image smoothly"""
        # Update image source
        self.image_widget.src = image_path

        # Prepare for fade-in
        self.image_container.opacity = 0
        self.image_container.animate_opacity = ft.Animation(300, ft.AnimationCurve.EASE_IN)
        self.image_container.content = self.image_widget
        self.page.update()

        # Small delay then fade in
        await asyncio.sleep(0.05)
        self.image_container.opacity = 1
        self.page.update()

    def _on_hover(self, e):
        """Handle hover effect with multi-layer shadow"""
        if e.data == "true":
            self.elevation = 8
            self.shadow = Shadows.LEVEL_3  # Multi-layer with glow
            self.scale = 1.02
        else:
            self.elevation = 2
            self.shadow = Shadows.LEVEL_2
            self.scale = 1.0
        self.update()

    def _on_view_backups_clicked(self, e):
        """Handle view backups button click"""
        if self.on_view_backups_callback:
            self.on_view_backups_callback(self.game)

    def set_updating(self, is_updating: bool):
        """Set updating state - shows spinner and disables button"""
        self.is_updating = is_updating
        if self.update_button and self.update_button.content:
            row = self.update_button.content
            color = "#888888" if is_updating else "#2D6E88"
            # Update icon and colors in the content row
            if row.controls and len(row.controls) >= 3:
                # First control is the icon
                row.controls[0].name = ft.Icons.HOURGLASS_TOP if is_updating else ft.Icons.UPDATE
                row.controls[0].color = color
                # Second control is the text
                row.controls[1].color = color
                # Third control is the dropdown arrow
                row.controls[2].color = color
            self.update_button.disabled = is_updating
            self.update_button.update()

    def refresh_dlls(self, new_dlls: List[GameDLL]):
        """Refresh DLL badges with new data after update"""
        self.dlls = new_dlls

        # Rebuild the DLL badges
        new_badges = self._create_dll_badges()

        # Replace the old badges in right_content
        if self.right_content and len(self.right_content.controls) >= 2:
            self.right_content.controls[1] = new_badges
            self.dll_badges_container = new_badges
            self.right_content.update()

    def _truncate_path(self, path: str, max_length: int = 40) -> str:
        """Truncate long paths for display, keeping end visible"""
        if len(path) <= max_length:
            return path
        return "..." + path[-(max_length - 3):]

    async def _on_copy_path_clicked(self, e):
        """Copy game path to clipboard with snackbar confirmation"""
        await self.page.set_clipboard_async(self.game.path)
        self.page.open(ft.SnackBar(content=ft.Text("Path copied to clipboard"), bgcolor="#2D6E88"))
