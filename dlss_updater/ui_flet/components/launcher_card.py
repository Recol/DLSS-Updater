"""
Launcher Card Component
Expandable card showing launcher configuration and detected games using Material Design 3 ExpansionTile
"""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Callable, Any

import flet as ft

from dlss_updater.config import LauncherPathName, config_manager
from dlss_updater.platform_utils import IS_WINDOWS, IS_LINUX
from dlss_updater.models import GameCardData, DLLInfo, MAX_PATHS_PER_LAUNCHER
from dlss_updater.ui_flet.theme.colors import MD3Colors, LauncherColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


class LauncherCard(ThemeAwareMixin, ft.ExpansionTile):
    """
    Expandable card for launcher configuration with game list using Material Design 3 ExpansionTile

    Performance: Uses is_isolated=True to prevent parent update() from including
    this control's changes. Must call self.update() manually for changes.
    """

    def is_isolated(self):
        """Isolated controls are excluded from parent's update digest."""
        return True

    def __init__(
        self,
        name: str,
        launcher_enum: LauncherPathName,
        icon: str,
        is_custom: bool,
        on_reset: Callable,
        on_add_subfolder: Callable,  # New: callback for adding sub-folder
        page: ft.Page,
        logger: logging.Logger,
    ):
        # Store instance variables
        self.name_str = name
        self.launcher_enum = launcher_enum
        self.icon_name = icon
        self.is_custom = is_custom
        self.on_reset_callback = on_reset
        self.on_add_subfolder_callback = on_add_subfolder
        self._page_ref = page
        self.logger = logger

        # State - multi-path support
        self.current_paths: list[str] = []  # List of configured paths
        self.games_count: int = 0
        self.games_data: list[dict] = []  # List of detected games

        # Get theme registry and state
        self._registry = get_theme_registry()
        self._theme_priority = 25  # Cards are mid-priority
        is_dark = self._registry.is_dark

        # Build UI components
        self._build_components(is_dark)

        # Initialize ExpansionTile with Material Design 3 styling
        # Use transparent backgrounds - cards should blend with page background
        super().__init__(
            leading=self.leading_row,
            title=self.title_text,
            subtitle=self.subtitle_column,
            trailing=self.trailing_row,
            controls=[],  # Initially empty, populated by set_games()
            expanded=False,
            bgcolor=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=8),
            maintain_state=True,
            collapsed_bgcolor=ft.Colors.TRANSPARENT,
            text_color=MD3Colors.get_on_surface(is_dark),
            icon_color=MD3Colors.PRIMARY,
            collapsed_text_color=MD3Colors.get_on_surface(is_dark),
            collapsed_icon_color=MD3Colors.PRIMARY,
            tile_padding=ft.padding.symmetric(horizontal=16, vertical=8),
            controls_padding=ft.padding.only(left=56, right=16, bottom=12, top=4),
        )

        # Apply custom styling for custom launchers
        if self.is_custom:
            # Custom cards get border
            self.border = ft.border.all(1, MD3Colors.get_primary(is_dark))

        # Register for theme updates
        self._register_theme_aware()

    @property
    def name(self) -> str:
        """Property for backward compatibility - returns the launcher name"""
        return self.name_str

    def _create_launcher_icon_circle(
        self,
        icon: str,
        color: str,
        size: int = 44,
        icon_size: int = 24,
    ) -> ft.Container:
        """
        Create a colored circular container with launcher icon inside.
        Follows AppMenuSelector pattern for visual consistency.
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
                color=f"{color}40",  # 25% opacity of the brand color
            ),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

    def _build_components(self, is_dark: bool):
        """Build the UI components for the ExpansionTile"""

        # Get launcher brand color
        self.brand_color = LauncherColors.get_color(self.launcher_enum.name)

        # Leading: Launcher icon in colored circle
        self.launcher_icon = self._create_launcher_icon_circle(
            self.icon_name,
            self.brand_color,
            size=44,
            icon_size=24,
        )

        # Title: Launcher name with no wrapping
        self.title_text = ft.Text(
            self.name_str,
            size=16,
            weight=ft.FontWeight.BOLD,
            no_wrap=True,
            expand=True,
        )

        # Status indicator
        self.status_icon = ft.Icon(
            ft.Icons.INFO_OUTLINE,
            color=ft.Colors.GREY,
            size=20,
        )

        # Path health indicator
        self.path_health_icon = ft.Icon(
            ft.Icons.VERIFIED,
            color=MD3Colors.SUCCESS,
            size=18,
            visible=False,
            tooltip="All paths valid",
        )

        # Path status text (shows count of configured paths)
        self.path_text = ft.Text(
            "No paths configured",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        # Game count text
        self.game_count_text = ft.Text(
            "",
            size=14,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )

        # Add Sub-Folder button
        self.add_subfolder_btn = ft.TextButton(
            "Add Sub-Folder",
            icon=ft.Icons.CREATE_NEW_FOLDER,
            on_click=self.on_add_subfolder_callback,
            style=ft.ButtonStyle(
                color=MD3Colors.PRIMARY,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
            ),
            height=28,
        )

        # Paths row - holds path chips and add button
        self.paths_row = ft.Row(
            controls=[self.add_subfolder_btn],  # Initially just the add button
            spacing=6,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Subtitle: Column with game count, path status, and paths row
        self.subtitle_column = ft.Column(
            controls=[
                self.game_count_text,
                self.path_text,
                self.paths_row,
            ],
            spacing=4,
            tight=True,
        )

        # Config menu (replaces Browse button)
        # Platform-appropriate text for file manager
        open_folder_text = "Open in Explorer" if IS_WINDOWS else "Open in File Manager"
        self.config_menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_color=MD3Colors.get_on_surface_variant(is_dark),
            tooltip="More options",
            items=[
                ft.PopupMenuItem(content="Copy Path(s)", icon=ft.Icons.CONTENT_COPY, on_click=self._on_copy_paths),
                ft.PopupMenuItem(content=open_folder_text, icon=ft.Icons.FOLDER_OPEN, on_click=self._on_open_explorer),
                ft.PopupMenuItem(content="Auto-Detect", icon=ft.Icons.AUTO_FIX_HIGH, on_click=self._on_auto_detect),
                ft.PopupMenuItem(),  # Divider
                ft.PopupMenuItem(content="Clear All", icon=ft.Icons.DELETE_OUTLINE, on_click=self.on_reset_callback),
            ],
        )

        # Reset button
        self.reset_btn = ft.TextButton(
            "Reset",
            icon=ft.Icons.REFRESH,
            on_click=self.on_reset_callback,
            style=ft.ButtonStyle(
                color=ft.Colors.GREY,
            ),
            height=36,
        )

        # Leading: Row with status icon + path health icon + launcher icon for visual symmetry
        self.leading_row = ft.Row(
            controls=[
                self.status_icon,
                self.path_health_icon,
                self.launcher_icon,
            ],
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Trailing: Row with config menu and reset button (Browse button removed)
        self.trailing_row = ft.Row(
            controls=[
                self.config_menu,
                self.reset_btn,
            ],
            spacing=8,
            tight=True,
        )

    def _create_path_chip(self, path: str, is_dark: bool) -> ft.Container:
        """
        Create a removable chip for a path.

        Args:
            path: The path to display
            is_dark: Whether dark theme is active

        Returns:
            Container with path chip and remove button
        """
        # Truncate long paths for display
        display_path = path
        if len(path) > 35:
            display_path = f"{path[:12]}...{path[-18:]}"

        # Create handler that properly schedules the async coroutine
        def on_remove_click(e, p=path):
            self._page_ref.run_task(self._on_remove_path, p)

        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(
                        display_path,
                        size=11,
                        color=MD3Colors.get_on_surface(is_dark),
                        tooltip=path,  # Show full path on hover
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=14,
                        icon_color=MD3Colors.get_on_surface_variant(is_dark),
                        on_click=on_remove_click,
                        tooltip="Remove path",
                        style=ft.ButtonStyle(
                            padding=ft.padding.all(2),
                        ),
                        width=20,
                        height=20,
                    ),
                ],
                spacing=2,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            border_radius=16,
            padding=ft.padding.only(left=10, right=4, top=4, bottom=4),
        )

    async def _on_remove_path(self, path: str):
        """
        Handle removing a path from this launcher.

        Args:
            path: The path to remove
        """
        self.logger.info(f"Removing path from {self.name_str}: {path}")

        # Remove from config
        config_manager.remove_launcher_path(self.launcher_enum, path)

        # Update local state
        if path in self.current_paths:
            self.current_paths.remove(path)

        # Update UI
        await self._update_paths_display()

        # Note: We don't trigger a re-scan here - user can manually scan
        # to update the game list after removing paths

    async def _update_paths_display(self):
        """Update the paths display in subtitle area."""
        is_dark = self._registry.is_dark

        # Create path chips
        path_chips = [self._create_path_chip(p, is_dark) for p in self.current_paths]

        # Enable/disable add button based on path limit
        at_limit = len(self.current_paths) >= MAX_PATHS_PER_LAUNCHER
        self.add_subfolder_btn.disabled = at_limit
        if at_limit:
            self.add_subfolder_btn.tooltip = f"Maximum {MAX_PATHS_PER_LAUNCHER} paths reached"
        else:
            self.add_subfolder_btn.tooltip = None

        # Update paths row: chips + add button
        self.paths_row.controls = path_chips + [self.add_subfolder_btn]

        # Update path status text
        path_count = len(self.current_paths)
        if path_count == 0:
            self.path_text.value = "No paths configured"
            self.path_text.color = MD3Colors.get_on_surface_variant(is_dark)
            self.status_icon.name = ft.Icons.INFO_OUTLINE
            self.status_icon.color = ft.Colors.GREY
        elif path_count == 1:
            self.path_text.value = "1 path configured"
            self.path_text.color = MD3Colors.get_on_surface(is_dark)
            self.status_icon.name = ft.Icons.CHECK_CIRCLE
            self.status_icon.color = MD3Colors.SUCCESS
        else:
            self.path_text.value = f"{path_count} paths configured"
            self.path_text.color = MD3Colors.get_on_surface(is_dark)
            self.status_icon.name = ft.Icons.CHECK_CIRCLE
            self.status_icon.color = MD3Colors.SUCCESS

        # Update path health indicator (async to avoid blocking on filesystem I/O)
        all_valid = await self._validate_paths_async()
        if self.current_paths:
            self.path_health_icon.visible = True
            if all_valid:
                self.path_health_icon.name = ft.Icons.VERIFIED
                self.path_health_icon.color = MD3Colors.SUCCESS
                self.path_health_icon.tooltip = "All paths valid"
            else:
                self.path_health_icon.name = ft.Icons.WARNING_AMBER
                self.path_health_icon.color = MD3Colors.WARNING
                self.path_health_icon.tooltip = "Some paths inaccessible"
        else:
            self.path_health_icon.visible = False

        if self._page_ref:
            self._page_ref.update()

    async def set_paths(self, paths: list[str]):
        """
        Update the launcher paths (multi-path support).

        Args:
            paths: List of paths configured for this launcher
        """
        self.current_paths = paths if paths else []

        # Get theme state
        is_dark = self._registry.is_dark

        # Update paths display
        await self._update_paths_display()

        # Update game count text if no paths
        if not self.current_paths:
            self.game_count_text.value = ""
            # Clear controls when no path
            self.controls = []
        else:
            # Will be updated by set_games()
            if self.games_count == 0:
                self.game_count_text.value = "Paths configured"
                self.game_count_text.color = MD3Colors.SUCCESS

        if self._page_ref:
            self._page_ref.update()

    # Keep set_path for backward compatibility
    async def set_path(self, path: str):
        """
        Update the launcher path (legacy single-path method).

        For backward compatibility. Converts single path to list.

        Args:
            path: The path to set
        """
        if path:
            await self.set_paths([path])
        else:
            await self.set_paths([])

    async def set_games(self, games_data: list[dict]):
        """
        Update the game list for this launcher

        Args:
            games_data: List of dicts with game info: {
                'name': str,
                'path': str,
                'dlls': List[Dict] with {'type': str, 'version': str, 'update_available': bool}
            }
        """
        self.games_data = games_data
        self.games_count = len(games_data)

        # Get theme state
        is_dark = self._registry.is_dark

        # Update count text
        if self.games_count > 0:
            self.game_count_text.value = f"{self.games_count} game{'s' if self.games_count != 1 else ''} detected"
            self.game_count_text.color = MD3Colors.SUCCESS

            # Build game list tiles
            game_tiles = []
            for game in games_data:
                game_tile = self._create_game_tile(game)
                game_tiles.append(game_tile)

            # Update ExpansionTile controls
            self.controls = game_tiles

        else:
            self.game_count_text.value = "No games found"
            self.game_count_text.color = MD3Colors.get_on_surface_variant(is_dark)

            # Clear controls when no games
            self.controls = []

        if self._page_ref:
            self._page_ref.update()

    def _validate_paths_sync(self) -> bool:
        """Check if all paths exist (sync version for filesystem I/O)"""
        if not self.current_paths:
            return True
        return all(Path(p).exists() for p in self.current_paths)

    async def _validate_paths_async(self) -> bool:
        """Check if all paths exist (async version - offloads blocking I/O to thread pool)"""
        if not self.current_paths:
            return True
        return await asyncio.to_thread(self._validate_paths_sync)

    async def _on_copy_paths(self, e):
        """Copy all configured paths to clipboard"""
        if self.current_paths:
            paths_text = "\n".join(self.current_paths)
            self._page_ref.set_clipboard(paths_text)
            self._page_ref.snack_bar = ft.SnackBar(ft.Text("Paths copied to clipboard"))
            self._page_ref.snack_bar.open = True
            self._page_ref.update()

    async def _on_open_explorer(self, e):
        """Open first path in file manager (cross-platform, non-blocking)"""
        if self.current_paths:
            path = self.current_paths[0]
            try:
                if IS_WINDOWS:
                    import os
                    # Wrap blocking os.startfile in thread pool
                    await asyncio.to_thread(os.startfile, path)
                elif IS_LINUX:
                    # Use async subprocess for non-blocking execution
                    await asyncio.create_subprocess_exec(
                        'xdg-open', path,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
            except Exception as ex:
                self.logger.error(f"Failed to open path in file manager: {ex}")
                self._page_ref.snack_bar = ft.SnackBar(ft.Text(f"Could not open: {path}"))
                self._page_ref.snack_bar.open = True
                self._page_ref.update()

    async def _on_auto_detect(self, e):
        """Attempt to auto-detect launcher path (offloads blocking scan to thread pool)"""
        from dlss_updater.scanner import auto_detect_launcher_path
        # Wrap blocking filesystem scan in thread pool to avoid freezing UI
        detected = await asyncio.to_thread(auto_detect_launcher_path, self.launcher_enum)
        if detected:
            added = config_manager.add_launcher_path(self.launcher_enum, detected)
            if added:
                self.current_paths.append(detected)
                await self._update_paths_display()
                self._page_ref.snack_bar = ft.SnackBar(ft.Text(f"Detected: {detected}"))
            else:
                self._page_ref.snack_bar = ft.SnackBar(ft.Text("Path already configured or at limit"))
        else:
            self._page_ref.snack_bar = ft.SnackBar(ft.Text("Could not auto-detect path"))
        self._page_ref.snack_bar.open = True
        self._page_ref.update()

    def _create_game_tile(self, game: GameCardData) -> ft.ListTile:
        """
        Create a ListTile for a game showing DLL info as Chips

        Args:
            game: GameCardData object containing game information

        Returns:
            ft.ListTile with game name and DLL chips
        """
        # Create DLL Chips
        dll_chips = []
        has_updates = False

        for dll in game.dlls:
            dll_type = dll.dll_type
            current_ver = dll.current_version
            latest_ver = dll.latest_version
            update_available = dll.update_available

            if update_available:
                has_updates = True

            # Determine chip color based on DLL type
            if dll_type.upper() == "DLSS":
                chip_bgcolor = "#76B900"  # NVIDIA green
            elif dll_type.upper() == "XESS":
                chip_bgcolor = "#0071C5"  # Intel blue
            elif dll_type.upper() == "FSR":
                chip_bgcolor = "#ED1C24"  # AMD red
            else:
                chip_bgcolor = MD3Colors.PRIMARY  # Default

            # Add update indicator if available
            if update_available:
                label_text = f"{dll_type}: {current_ver} → {latest_ver}"
                chip_icon = ft.Icon(ft.Icons.ARROW_UPWARD, size=14, color=ft.Colors.WHITE)
            else:
                label_text = f"{dll_type}: {current_ver}"
                chip_icon = ft.Icon(ft.Icons.CHECK, size=14, color=ft.Colors.WHITE)

            dll_chip = ft.Chip(
                label=ft.Row(
                    controls=[
                        chip_icon,
                        ft.Text(
                            label_text,
                            size=11,
                            color=ft.Colors.WHITE,
                            weight=ft.FontWeight.W_500,
                        ),
                    ],
                    spacing=4,
                    tight=True,
                ),
                bgcolor=chip_bgcolor,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                disabled=True,  # Makes it non-interactive
            )
            dll_chips.append(dll_chip)

        # Subtitle: Row of DLL chips
        if dll_chips:
            subtitle_content = ft.Row(
                controls=dll_chips,
                spacing=6,
                wrap=True,
            )
        else:
            subtitle_content = ft.Text(
                "No DLLs detected",
                size=11,
                color=ft.Colors.GREY,
            )

        # Game icon color based on update availability
        game_icon_color = ft.Colors.ORANGE if has_updates else MD3Colors.PRIMARY

        # Create ListTile
        return ft.ListTile(
            leading=ft.Icon(
                ft.Icons.VIDEOGAME_ASSET,
                size=20,
                color=game_icon_color,
            ),
            title=ft.Text(
                game.name,
                size=13,
                weight=ft.FontWeight.W_500,
            ),
            subtitle=subtitle_content,
            dense=True,
            content_padding=ft.padding.symmetric(horizontal=0, vertical=4),
        )

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for cascade updates"""
        return {
            "text_color": MD3Colors.get_themed_pair("on_surface"),
            "collapsed_text_color": MD3Colors.get_themed_pair("on_surface"),
            "path_text.color": MD3Colors.get_themed_pair("on_surface_variant"),
            "game_count_text.color": MD3Colors.get_themed_pair("on_surface_variant"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay - extended for complex updates"""
        import asyncio
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            # Update ExpansionTile colors
            self.text_color = MD3Colors.get_on_surface(is_dark)
            self.collapsed_text_color = MD3Colors.get_on_surface(is_dark)
            self.icon_color = MD3Colors.get_primary(is_dark)
            self.collapsed_icon_color = MD3Colors.get_primary(is_dark)

            # Keep transparent backgrounds for all launchers
            # Background colors should remain transparent to match page background
            self.bgcolor = ft.Colors.TRANSPARENT
            self.collapsed_bgcolor = ft.Colors.TRANSPARENT

            # Custom launchers get themed border
            if self.is_custom:
                self.border = ft.border.all(1, MD3Colors.get_primary(is_dark))

            # Update path text
            if hasattr(self, 'path_text'):
                self.path_text.color = MD3Colors.get_on_surface_variant(is_dark)

            # Update game count text
            if hasattr(self, 'game_count_text'):
                # Keep success color if it has games, otherwise use on_surface_variant
                if self.games_count > 0 or (self.current_paths and self.games_count == 0):
                    pass  # Keep existing SUCCESS color
                else:
                    self.game_count_text.color = MD3Colors.get_on_surface_variant(is_dark)

            # Update config menu icon color
            if hasattr(self, 'config_menu'):
                self.config_menu.icon_color = MD3Colors.get_on_surface_variant(is_dark)

            # Update path chips by rebuilding paths display
            if self.current_paths:
                await self._update_paths_display()

            if hasattr(self, 'update'):
                self.update()
        except Exception:
            pass  # Silent fail - component may have been garbage collected
