"""
Launcher Card Component
Expandable card showing launcher configuration and detected games using Material Design 3 ExpansionTile
"""

import logging
from pathlib import Path
from typing import Optional, Callable, List, Dict

import flet as ft

from dlss_updater.config import LauncherPathName
from dlss_updater.models import GameCardData, DLLInfo
from dlss_updater.ui_flet.theme.colors import MD3Colors, LauncherColors


class LauncherCard(ft.ExpansionTile):
    """
    Expandable card for launcher configuration with game list using Material Design 3 ExpansionTile
    """

    def __init__(
        self,
        name: str,
        launcher_enum: LauncherPathName,
        icon: str,
        is_custom: bool,
        on_browse: Callable,
        on_reset: Callable,
        page: ft.Page,
        logger: logging.Logger,
    ):
        # Store instance variables
        self.name_str = name
        self.launcher_enum = launcher_enum
        self.icon_name = icon
        self.is_custom = is_custom
        self.on_browse_callback = on_browse
        self.on_reset_callback = on_reset
        self.page = page
        self.logger = logger

        # State
        self.current_path: Optional[str] = None
        self.games_count: int = 0
        self.games_data: List[Dict] = []  # List of detected games

        # Get theme state
        is_dark = page.session.get("is_dark_theme") if page and page.session.contains_key("is_dark_theme") else True

        # Build UI components
        self._build_components(is_dark)

        # Initialize ExpansionTile with Material Design 3 styling
        super().__init__(
            leading=self.leading_row,
            title=self.title_text,
            subtitle=self.subtitle_column,
            trailing=self.trailing_row,
            controls=[],  # Initially empty, populated by set_games()
            initially_expanded=False,
            bgcolor=MD3Colors.get_surface_variant(is_dark) if not is_custom else None,
            shape=ft.RoundedRectangleBorder(radius=8),
            maintain_state=True,
            collapsed_bgcolor=MD3Colors.get_surface_variant(is_dark) if not is_custom else None,
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
            self.border = ft.border.all(1, MD3Colors.PRIMARY)

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
            alignment=ft.alignment.center,
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

        # Path text
        self.path_text = ft.Text(
            "No path configured",
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

        # Subtitle: Column with game count and path
        self.subtitle_column = ft.Column(
            controls=[
                self.game_count_text,
                self.path_text,
            ],
            spacing=2,
            tight=True,
        )

        # Browse button
        self.browse_btn = ft.OutlinedButton(
            "Browse",
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self.on_browse_callback,
            style=ft.ButtonStyle(
                color=MD3Colors.PRIMARY,
            ),
            height=36,
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

        # Leading: Row with status icon + launcher icon for visual symmetry
        self.leading_row = ft.Row(
            controls=[
                self.status_icon,
                self.launcher_icon,
            ],
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Trailing: Row with action buttons only (status moved to leading)
        self.trailing_row = ft.Row(
            controls=[
                self.browse_btn,
                self.reset_btn,
            ],
            spacing=8,
            tight=True,
        )

    async def set_path(self, path: str):
        """Update the launcher path"""
        self.current_path = path

        # Get theme state
        is_dark = self.page.session.get("is_dark_theme") if self.page and self.page.session.contains_key("is_dark_theme") else True

        if path:
            # Show truncated path
            path_obj = Path(path)
            if len(path) > 50:
                display_path = f"{path[:20]}...{path[-27:]}"
            else:
                display_path = path

            self.path_text.value = display_path
            self.path_text.color = MD3Colors.get_on_surface(is_dark)

            # Update status icon
            self.status_icon.name = ft.Icons.CHECK_CIRCLE
            self.status_icon.color = MD3Colors.SUCCESS

            # Show path configured
            self.game_count_text.value = "Path configured"
            self.game_count_text.color = MD3Colors.SUCCESS
        else:
            self.path_text.value = "No path configured"
            self.path_text.color = MD3Colors.get_on_surface_variant(is_dark)
            self.status_icon.name = ft.Icons.INFO_OUTLINE
            self.status_icon.color = ft.Colors.GREY
            self.game_count_text.value = ""

            # Clear controls when no path
            self.controls = []

        if self.page:
            self.page.update()

    async def set_games(self, games_data: List[Dict]):
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
        is_dark = self.page.session.get("is_dark_theme") if self.page and self.page.session.contains_key("is_dark_theme") else True

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

        if self.page:
            self.page.update()

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
