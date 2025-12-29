"""
Game Card Component
Individual game card with Steam image, DLL badges, and action buttons
"""

import asyncio
from pathlib import Path
from typing import Optional, List, Dict
import flet as ft

from dlss_updater.database import GameDLL
from dlss_updater.steam_integration import fetch_steam_image
from dlss_updater.ui_flet.theme.colors import Shadows, TechnologyColors
from dlss_updater.ui_flet.theme.md3_system import MD3Spacing
from dlss_updater.constants import DLL_GROUPS


class GameCard(ft.Card):
    """Individual game card with image, DLL info, and actions"""

    def __init__(self, game, dlls: List[GameDLL], page: ft.Page, logger, on_update=None, on_view_backups=None, on_restore=None, backup_groups: Optional[Dict[str, List]] = None):
        super().__init__()
        self.game = game
        self.dlls = dlls
        self.page = page
        self.logger = logger
        self.on_update_callback = on_update
        self.on_view_backups_callback = on_view_backups
        self.on_restore_callback = on_restore
        self.backup_groups = backup_groups or {}
        self.has_backups = bool(backup_groups)

        # Button references for loading state
        self.update_button: Optional[ft.PopupMenuButton] = None
        self.restore_button: Optional[ft.PopupMenuButton] = None
        self.is_updating = False

        # Reference to dll_badges for refresh
        self.dll_badges_container: Optional[ft.Container] = None
        self.right_content: Optional[ft.Column] = None

        # Async lock for UI updates to prevent race conditions
        self._ui_lock = asyncio.Lock()
        self._image_loaded = False  # Prevent duplicate image loads

        # Responsive badge state
        self._current_breakpoint = self._get_breakpoint()
        self._max_visible_badges = self._get_max_badges_for_breakpoint(self._current_breakpoint)

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

    def _get_breakpoint(self) -> str:
        """Determine current breakpoint from page width"""
        if not self.page or not self.page.width:
            return "lg"
        width = self.page.width
        if width < 576:
            return "xs"
        elif width < 768:
            return "sm"
        elif width < 992:
            return "md"
        return "lg"

    def _get_max_badges_for_breakpoint(self, breakpoint: str) -> int:
        """Get maximum visible badges for current breakpoint"""
        return {"xs": 2, "sm": 2, "md": 3, "lg": 4}.get(breakpoint, 4)

    async def update_badge_count(self, breakpoint: str):
        """Update badge count based on breakpoint (called from GamesView)"""
        new_max = self._get_max_badges_for_breakpoint(breakpoint)
        if new_max != self._max_visible_badges:
            self._max_visible_badges = new_max
            self._current_breakpoint = breakpoint
            async with self._ui_lock:
                new_badges = self._create_dll_badges()
                if self.right_content and len(self.right_content.controls) >= 2:
                    self.right_content.controls[1] = new_badges
                    self.dll_badges_container = new_badges
                    if self.page:
                        self.page.update()

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
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    no_wrap=True,  # Prevent wrapping to maintain consistent card height
                    tooltip=self.game.name,  # Show full name on hover
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
                                self.game.path,
                                size=10,
                                color="#666666",
                                no_wrap=True,
                                italic=True,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                max_lines=1,
                            ),
                            tooltip=self.game.path,
                            expand=True,
                            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
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
        self.restore_button = self._create_restore_popup_menu()

        action_buttons = ft.Container(
            content=ft.Row(
                controls=[
                    self.update_button,
                    self.restore_button,
                ],
                spacing=8,
                wrap=True,  # Allow buttons to wrap when narrow
                run_spacing=4,
            ),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,  # Clip overflow
        )

        # Right side content - START with spacer pushes buttons to bottom consistently
        self.right_content = ft.Column(
            controls=[
                game_info,
                self.dll_badges_container,
                ft.Container(expand=True),  # Spacer pushes buttons to bottom
                action_buttons,
            ],
            spacing=4,
            expand=True,
            alignment=ft.MainAxisAlignment.START,
        )

        # Card content layout
        self.content = ft.Container(
            content=ft.Row(
                controls=[
                    self.image_container,
                    self.right_content,
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=12,
            height=180,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

    def _create_dll_badges(self) -> ft.Container:
        """Create DLL type badges with version info - responsive count based on breakpoint"""
        badges = []

        dll_colors = {
            "DLSS": "#76B900",
            "XeSS": "#0071C5",
            "FSR": "#ED1C24",
            "DLSS-G": "#76B900",
            "DLSS-D": "#76B900",
            "Streamline": "#76B900",
            "DirectStorage": "#FFB900",
        }

        # Use responsive max badges count based on current breakpoint
        max_visible = getattr(self, '_max_visible_badges', 4)
        visible_dlls = self.dlls[:max_visible]
        overflow_dlls = self.dlls[max_visible:]

        for dll in visible_dlls:
            color = dll_colors.get(dll.dll_type, "#888888")
            chip = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.CHECK_CIRCLE if dll.current_version else ft.Icons.HELP_OUTLINE,
                            size=12,
                            color=ft.Colors.WHITE,
                        ),
                        ft.Column(
                            controls=[
                                ft.Text(dll.dll_type, size=9, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE, no_wrap=True),
                                ft.Text(dll.current_version[:8] if dll.current_version else "N/A", size=7, color=ft.Colors.with_opacity(0.9, ft.Colors.WHITE), no_wrap=True),
                            ],
                            spacing=0,
                            tight=True,
                        ),
                    ],
                    spacing=4,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=color,
                padding=ft.padding.symmetric(horizontal=6, vertical=3),
                border_radius=8,
                border=ft.border.all(1, ft.Colors.with_opacity(0.2, ft.Colors.WHITE)),
            )
            badges.append(chip)

        # Add "+N more" popover if there are overflow DLLs
        if overflow_dlls:
            # Create popover menu items for overflow DLLs
            popover_items = []
            for dll in overflow_dlls:
                color = dll_colors.get(dll.dll_type, "#888888")
                popover_items.append(
                    ft.PopupMenuItem(
                        content=ft.Row(
                            controls=[
                                ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
                                ft.Text(dll.dll_type, size=12, weight=ft.FontWeight.BOLD),
                                ft.Text(dll.current_version[:8] if dll.current_version else "N/A", size=10, color="#888888"),
                            ],
                            spacing=8,
                            tight=True,
                        ),
                    )
                )

            more_button = ft.PopupMenuButton(
                content=ft.Container(
                    content=ft.Text(f"+{len(overflow_dlls)}", size=10, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    bgcolor="#555555",
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                    border_radius=8,
                ),
                items=popover_items,
                tooltip=f"{len(overflow_dlls)} more DLLs",
            )
            badges.append(more_button)

        return ft.Container(
            content=ft.Row(
                controls=badges,
                spacing=6,
                tight=True,
            ),
            height=36,
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

    def _create_restore_popup_menu(self) -> ft.PopupMenuButton:
        """Create popup menu button for selective DLL restore from backups"""
        if not self.backup_groups:
            # Return disabled button if no backups
            return ft.PopupMenuButton(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.RESTORE, size=18, color="#666666"),
                        ft.Text("Restore", size=14, color="#666666"),
                        ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color="#666666"),
                    ],
                    spacing=4,
                    tight=True,
                ),
                tooltip="No backups available",
                items=[],
                disabled=True,
            )

        groups = sorted(self.backup_groups.keys())

        # Build menu items
        menu_items = [
            ft.PopupMenuItem(
                text="Restore All",
                icon=ft.Icons.RESTORE,
                on_click=lambda e: self._on_restore_group_selected("all"),
            ),
        ]

        if groups:
            menu_items.append(ft.PopupMenuItem())  # Divider

            for group in groups:
                color = TechnologyColors.get_color(group)
                backup_count = len(self.backup_groups[group])
                menu_items.append(
                    ft.PopupMenuItem(
                        content=ft.Row(
                            controls=[
                                ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
                                ft.Text(f"Restore {group} ({backup_count})", size=14),
                            ],
                            spacing=8,
                        ),
                        on_click=lambda e, g=group: self._on_restore_group_selected(g),
                    )
                )

        return ft.PopupMenuButton(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.RESTORE, size=18, color="#4CAF50"),
                    ft.Text("Restore", size=14, color="#4CAF50"),
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color="#4CAF50"),
                ],
                spacing=4,
                tight=True,
            ),
            tooltip="Restore DLLs from backup",
            items=menu_items,
        )

    def _on_restore_group_selected(self, group: str):
        """Handle DLL group selection from restore popup menu"""
        if self.on_restore_callback:
            self.on_restore_callback(self.game, group)

    async def load_image(self):
        """Async load Steam image with fade-in animation"""
        # Prevent duplicate image loads
        if self._image_loaded:
            return

        if not self.game.steam_app_id:
            self.logger.debug(f"No Steam app ID for {self.game.name}, skipping image")
            return

        try:
            from dlss_updater.database import db_manager

            # Check cache first
            cached_path = await db_manager.get_cached_image_path(self.game.steam_app_id)

            if cached_path:
                # Use try/except instead of exists() check to avoid race condition
                try:
                    await self._fade_in_image(cached_path)
                    self._image_loaded = True
                    self.logger.debug(f"Loaded cached image for {self.game.name}")
                    return
                except (FileNotFoundError, OSError):
                    # File was deleted between cache lookup and load - fetch fresh
                    self.logger.debug(f"Cached image missing for {self.game.name}, fetching fresh")

            # Fetch from Steam CDN
            self.logger.info(f"Fetching Steam image for {self.game.name} (app_id: {self.game.steam_app_id})")
            image_path = await fetch_steam_image(self.game.steam_app_id)

            if image_path:
                try:
                    await self._fade_in_image(str(image_path))
                    self._image_loaded = True
                    self.logger.info(f"Successfully loaded image for {self.game.name}")
                except (FileNotFoundError, OSError) as e:
                    self.logger.debug(f"Failed to load fetched image for {self.game.name}: {e}")
            else:
                self.logger.debug(f"No image available for {self.game.name}")

        except Exception as e:
            self.logger.error(f"Error loading image for {self.game.name}: {e}", exc_info=True)

    async def _fade_in_image(self, image_path: str):
        """Fade in image smoothly with UI lock to prevent race conditions"""
        async with self._ui_lock:
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
        """Handle hover effect with multi-layer shadow and border glow"""
        if e.data == "true":
            self.elevation = 8
            self.shadow = Shadows.LEVEL_3
            self.scale = 1.015
            self.border = ft.border.all(1, ft.Colors.with_opacity(0.3, "#2D6E88"))
        else:
            self.elevation = 2
            self.shadow = Shadows.LEVEL_2
            self.scale = 1.0
            self.border = None
        self.update()

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

    async def refresh_dlls(self, new_dlls: List[GameDLL]):
        """Refresh DLL badges with new data after update (async for UI lock)"""
        async with self._ui_lock:
            self.dlls = new_dlls

            # Rebuild the DLL badges
            new_badges = self._create_dll_badges()

            # Replace the old badges in right_content
            if self.right_content and len(self.right_content.controls) >= 2:
                self.right_content.controls[1] = new_badges
                self.dll_badges_container = new_badges
                self.right_content.update()

    async def refresh_restore_button(self, new_backup_groups: Dict[str, List]):
        """Refresh restore button with new backup data after restore"""
        async with self._ui_lock:
            self.backup_groups = new_backup_groups
            self.has_backups = bool(new_backup_groups)

            # Rebuild restore button
            new_restore_button = self._create_restore_popup_menu()

            # Find and replace in action buttons container (index 3 due to spacer at index 2)
            if self.right_content and len(self.right_content.controls) >= 4:
                action_buttons_container = self.right_content.controls[3]
                if action_buttons_container.content and action_buttons_container.content.controls:
                    if len(action_buttons_container.content.controls) >= 2:
                        action_buttons_container.content.controls[1] = new_restore_button
                        self.restore_button = new_restore_button
                        action_buttons_container.content.update()

    async def _on_copy_path_clicked(self, e):
        """Copy game path to clipboard with snackbar confirmation"""
        await self.page.set_clipboard_async(self.game.path)
        self.page.open(ft.SnackBar(content=ft.Text("Path copied to clipboard"), bgcolor="#2D6E88"))
