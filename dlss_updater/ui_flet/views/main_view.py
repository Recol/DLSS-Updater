"""
Main View for DLSS Updater Flet UI
Async-based Material Design interface with expandable launcher cards
"""

import asyncio
import logging
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

import flet as ft

from dlss_updater.config import config_manager, LauncherPathName, get_config_path
from dlss_updater.models import ScanCacheData, GameCardData, DLLInfo, encode_json, decode_json, format_json
from dlss_updater.ui_flet.components.launcher_card import LauncherCard
from dlss_updater.ui_flet.components.loading_overlay import LoadingOverlay
from dlss_updater.ui_flet.components.logger_panel import LoggerPanel
from dlss_updater.ui_flet.components.theme_manager import ThemeManager
from dlss_updater.ui_flet.theme.colors import Shadows
from dlss_updater.ui_flet.dialogs.update_preferences_dialog import UpdatePreferencesDialog
from dlss_updater.ui_flet.dialogs.blacklist_dialog import BlacklistDialog
from dlss_updater.ui_flet.dialogs.update_summary_dialog import UpdateSummaryDialog
from dlss_updater.ui_flet.dialogs.release_notes_dialog import ReleaseNotesDialog
from dlss_updater.ui_flet.dialogs.app_update_dialog import AppUpdateDialog
from dlss_updater.ui_flet.dialogs.dlss_overlay_dialog import DLSSOverlayDialog
from dlss_updater.ui_flet.async_updater import AsyncUpdateCoordinator, UpdateProgress
from dlss_updater.ui_flet.views.games_view import GamesView
from dlss_updater.ui_flet.views.backups_view import BackupsView
from dlss_updater.ui_flet.components.navigation_drawer import CustomNavigationDrawer
from dlss_updater.ui_flet.components.dll_cache_snackbar import DLLCacheProgressSnackbar
from dlss_updater.ui_flet.components.app_menu_selector import AppMenuSelector, MenuCategory, MenuItem
from dlss_updater.version import __version__
from dlss_updater.utils import find_game_root


class MainView(ft.Column):
    """
    Main application view with navigation and launcher management
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        super().__init__()
        self.page = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        # File picker for launcher paths
        self.file_picker = ft.FilePicker(on_result=self._on_folder_selected)
        self.current_launcher_selecting: Optional[LauncherPathName] = None

        # Loading overlay
        self.loading_overlay = LoadingOverlay()

        # Launcher cards dictionary
        self.launcher_cards: Dict[LauncherPathName, LauncherCard] = {}

        # Async update coordinator
        self.update_coordinator = AsyncUpdateCoordinator(logger)

        # Logger panel
        self.logger_panel = None  # Will be created in _build_ui

        # Theme manager
        self.theme_manager = ThemeManager(page)
        self.theme_toggle_btn = None  # Will be created in _create_app_bar

        # DLL cache progress snackbar
        self.dll_cache_snackbar: Optional[DLLCacheProgressSnackbar] = None

        # Menu state
        self.menu_expanded = False
        self.app_menu_selector = None  # Will be created in _create_app_bar

        # Navigation state
        self.current_view_index = 0  # 0=Launchers, 1=Games, 2=Backups

        # Scan state management - store last scan results for update operations
        self.last_scan_results: Optional[Dict] = None
        self.last_scan_timestamp: Optional[str] = None
        self.scan_cache_path = Path(get_config_path()).parent / "scan_cache.json"

        # View instances (will be created in _build_ui)
        self.launchers_view = None
        self.games_view = None
        self.backups_view = None
        self.content_switcher = None

        # Initialize launcher configurations
        self.launcher_configs = [
            {"name": "Steam Games", "enum": LauncherPathName.STEAM, "icon": ft.Icons.VIDEOGAME_ASSET},
            {"name": "EA Games", "enum": LauncherPathName.EA, "icon": ft.Icons.SPORTS_ESPORTS},
            {"name": "Epic Games", "enum": LauncherPathName.EPIC, "icon": ft.Icons.GAMES},
            {"name": "Ubisoft Games", "enum": LauncherPathName.UBISOFT, "icon": ft.Icons.GAMEPAD},
            {"name": "GOG Games", "enum": LauncherPathName.GOG, "icon": ft.Icons.VIDEOGAME_ASSET_OUTLINED},
            {"name": "Battle.net Games", "enum": LauncherPathName.BATTLENET, "icon": ft.Icons.MILITARY_TECH},
            {"name": "Xbox Games", "enum": LauncherPathName.XBOX, "icon": ft.Icons.SPORTS_ESPORTS_OUTLINED},
            {"name": "Custom Folder 1", "enum": LauncherPathName.CUSTOM1, "icon": ft.Icons.FOLDER_SPECIAL, "custom": True},
            {"name": "Custom Folder 2", "enum": LauncherPathName.CUSTOM2, "icon": ft.Icons.FOLDER_SPECIAL, "custom": True},
            {"name": "Custom Folder 3", "enum": LauncherPathName.CUSTOM3, "icon": ft.Icons.FOLDER_SPECIAL, "custom": True},
            {"name": "Custom Folder 4", "enum": LauncherPathName.CUSTOM4, "icon": ft.Icons.FOLDER_SPECIAL, "custom": True},
        ]

    async def initialize(self):
        """Initialize the view asynchronously"""
        self.logger.info("Initializing main view...")

        # Add file picker to page overlay
        self.page.overlay.append(self.file_picker)
        self.page.overlay.append(self.loading_overlay)

        # Create DLL cache progress notification and add wrapper to overlay
        self.dll_cache_snackbar = DLLCacheProgressSnackbar(self.page)
        self.page.overlay.append(self.dll_cache_snackbar.get_wrapper())

        # Load cached scan results if available
        await self._load_scan_cache()

        # Build UI components
        await self._build_ui()
        self.logger.info("Main view initialized")

    async def _build_ui(self):
        """Build the main UI structure with NavigationDrawer"""
        # Create app bar (with drawer toggle button)
        app_bar = await self._create_app_bar()

        # Create launcher view (with existing cards and action buttons)
        self.launchers_view = await self._create_launchers_view()

        # Create other views
        self.games_view = GamesView(self.page, self.logger)
        self.backups_view = BackupsView(self.page, self.logger)

        # Create AnimatedSwitcher for smooth view transitions
        self.content_switcher = ft.AnimatedSwitcher(
            content=self.launchers_view,
            transition=ft.AnimatedSwitcherTransition.SCALE,
            duration=350,
            switch_in_curve=ft.AnimationCurve.EASE_OUT_CUBIC,
            switch_out_curve=ft.AnimationCurve.EASE_IN_CUBIC,
            expand=True,
        )

        # Create NavigationDrawer
        self.navigation_drawer_component = CustomNavigationDrawer(
            page=self.page,
            logger=self.logger,
            on_destination_changed=self._on_drawer_navigation_changed,
            initial_index=self.current_view_index
        )

        # Set page drawer
        self.page.drawer = self.navigation_drawer_component.get_drawer()

        # Create logger panel
        self.logger_panel = LoggerPanel(self.page, self.logger)

        # Assemble main layout: AppBar + Content + Logger
        # NOTE: NavigationRail has been removed - drawer opens via overlay
        self.controls = [
            app_bar,
            ft.Container(
                height=1,
                gradient=ft.LinearGradient(
                    begin=ft.alignment.center_left,
                    end=ft.alignment.center_right,
                    colors=["transparent", "#5A5A5A", "transparent"],
                ),
            ),
            ft.Row(
                controls=[
                    self.content_switcher,  # Full-width content
                ],
                expand=True,
                spacing=0,
            ),
            self.logger_panel,
        ]

    def _create_action_button(
        self,
        label: str,
        icon: str,
        color: str,
        on_click,
    ) -> ft.Container:
        """
        Create an action button with icon in colored circle.
        Follows AppMenuSelector menu item pattern.
        """
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark

        # Icon circle
        icon_circle = ft.Container(
            content=ft.Icon(icon, size=20, color=ft.Colors.WHITE),
            width=40,
            height=40,
            bgcolor=color,
            border_radius=20,
            alignment=ft.alignment.center,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=4,
                offset=ft.Offset(0, 2),
                color=f"{color}40",
            ),
        )

        # Label text
        label_text = ft.Text(
            label,
            size=14,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_on_surface(is_dark),
        )

        # Button container - store reference for hover effect
        button_container = ft.Container(
            content=ft.Row(
                controls=[icon_circle, label_text],
                spacing=12,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=20, vertical=12),
            border_radius=12,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            border=ft.border.all(1, MD3Colors.get_outline(is_dark)),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            on_click=on_click,
        )

        # Add hover effect
        def on_hover(e):
            if e.data == "true":
                button_container.bgcolor = f"{color}15"
                button_container.border = ft.border.all(1, f"{color}30")
            else:
                button_container.bgcolor = MD3Colors.get_surface_variant(is_dark)
                button_container.border = ft.border.all(1, MD3Colors.get_outline(is_dark))
            if self.page:
                button_container.update()

        button_container.on_hover = on_hover

        return button_container

    async def _create_launchers_view(self):
        """Create the launchers view with cards and action buttons"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark

        # Create launcher cards
        launcher_list = await self._create_launcher_list()

        # Create last scan info text
        self.last_scan_info_text = ft.Text(
            "",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            ref=ft.Ref[ft.Text]()
        )
        self._update_scan_info_text()

        # Create styled action buttons with icon circles
        scan_button = self._create_action_button(
            label="Scan for Games",
            icon=ft.Icons.SEARCH,
            color="#2D6E88",  # Teal
            on_click=self._on_scan_clicked,
        )

        update_button = self._create_action_button(
            label="Start Update",
            icon=ft.Icons.DOWNLOAD,
            color="#2D5A88",  # Blue
            on_click=self._on_update_clicked,
        )

        # Create action buttons container with dark surface style
        action_buttons = ft.Container(
            content=ft.Column(
                controls=[
                    # Last scan info
                    ft.Container(
                        content=self.last_scan_info_text,
                        alignment=ft.alignment.center,
                        padding=ft.padding.only(bottom=12),
                    ),
                    # Buttons row
                    ft.Row(
                        controls=[
                            scan_button,
                            ft.Container(width=16),  # Spacer
                            update_button,
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                spacing=0,
            ),
            padding=ft.padding.all(16),
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            border=ft.border.only(top=ft.BorderSide(1, MD3Colors.get_outline(is_dark))),
        )

        # Return launchers view as a Column
        return ft.Column(
            controls=[
                launcher_list,
                action_buttons,
            ],
            expand=True,
            spacing=0,
        )

    def _create_header_icon_button(
        self,
        icon: str,
        color: str,
        tooltip: str,
        on_click,
        size: int = 40,
        icon_size: int = 20,
    ) -> ft.Container:
        """
        Create a header button with colored icon circle.
        Follows AppMenuSelector pattern for visual consistency.
        """
        return ft.Container(
            content=ft.Icon(icon, size=icon_size, color=ft.Colors.WHITE),
            width=size,
            height=size,
            bgcolor=color,
            border_radius=size // 2,
            alignment=ft.alignment.center,
            on_click=on_click,
            tooltip=tooltip,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            animate_rotation=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=4,
                offset=ft.Offset(0, 2),
                color=f"{color}40",  # 25% opacity glow
            ),
        )

    async def _create_app_bar(self) -> ft.Container:
        """Create the application bar with hamburger menu and ExpansionTiles"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark

        # Create hamburger button with colored circle
        hamburger_btn = self._create_header_icon_button(
            icon=ft.Icons.MENU,
            color=MD3Colors.PRIMARY,  # Teal
            tooltip="Navigation Menu",
            on_click=self._open_navigation_drawer,
        )

        # Badge for hamburger button (shown when updates available)
        self.hamburger_badge_dot = ft.Container(
            width=10,
            height=10,
            bgcolor=ft.Colors.RED,
            border_radius=5,
            visible=False,
        )

        self.hamburger_badge = ft.Stack(
            controls=[
                hamburger_btn,
                ft.Container(
                    content=self.hamburger_badge_dot,
                    right=0,
                    top=0,
                ),
            ],
            width=48,
            height=48,
        )

        # Create Support Development button with colored circle
        support_btn = self._create_header_icon_button(
            icon=ft.Icons.FAVORITE,
            color="#E91E63",  # Pink
            tooltip="Support Development - Buy me a coffee",
            on_click=lambda _: webbrowser.open("https://buymeacoffee.com/decouk"),
        )

        # Create More menu button with colored circle
        self.more_menu_btn = self._create_header_icon_button(
            icon=ft.Icons.MORE_VERT,
            color=MD3Colors.PRIMARY,  # Teal
            tooltip="More Options",
            on_click=self._toggle_app_menu,
        )

        # Compact top bar
        top_bar = ft.Row(
            controls=[
                self.hamburger_badge,
                ft.Column(
                    controls=[
                        ft.Text(
                            "DLSS Updater",
                            size=24,
                            weight=ft.FontWeight.BOLD,
                            color=MD3Colors.get_on_surface(is_dark),
                        ),
                        ft.Text(
                            f"Version {__version__}",
                            size=12,
                            color=MD3Colors.get_on_surface_variant(is_dark),
                            weight=ft.FontWeight.W_400,
                            opacity=0.9,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                # Right side buttons: Support, More menu
                support_btn,
                self.more_menu_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # Create the new AppMenuSelector component
        self.app_menu_selector = self._create_app_menu()

        # Create expandable menu container with slide animation
        self.app_menu_container = ft.Container(
            content=self.app_menu_selector,
            height=0,
            opacity=0,
            offset=ft.Offset(0, -0.1),  # Start slightly above
            animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            animate_offset=ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        # Return app bar with dark surface style (full width, no border)
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=top_bar,
                        padding=ft.padding.symmetric(horizontal=16),
                    ),
                    self.app_menu_container,
                ],
                spacing=0,
            ),
            padding=ft.padding.symmetric(vertical=16, horizontal=0),
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            shadow=Shadows.LEVEL_2,
        )

    def _toggle_app_menu(self, e):
        """Toggle menu visibility with smooth animations"""
        import math
        self.menu_expanded = not self.menu_expanded

        if self.menu_expanded:
            self.app_menu_container.height = None
            self.app_menu_container.opacity = 1
            self.app_menu_container.offset = ft.Offset(0, 0)  # Slide to position
            # Rotate button 180° and change icon
            self.more_menu_btn.rotate = ft.Rotate(angle=math.pi)
            self.more_menu_btn.content.name = ft.Icons.EXPAND_LESS
        else:
            self.app_menu_container.height = 0
            self.app_menu_container.opacity = 0
            self.app_menu_container.offset = ft.Offset(0, -0.1)  # Slide up
            # Reset rotation and change icon back
            self.more_menu_btn.rotate = ft.Rotate(angle=0)
            self.more_menu_btn.content.name = ft.Icons.MORE_VERT

        self.page.update()
        self.logger.info(f"Menu {'expanded' if self.menu_expanded else 'collapsed'}")

    def _create_app_menu(self) -> AppMenuSelector:
        """Create the AppMenuSelector with all categories"""
        categories = [
            MenuCategory(
                id="community",
                title="Community & Support",
                icon=ft.Icons.FAVORITE,
                color="#E91E63",  # Pink
                items=[
                    MenuItem(
                        id="support",
                        title="Support Development",
                        description="Buy me a coffee",
                        icon=ft.Icons.COFFEE,
                        on_click=lambda _: webbrowser.open("https://buymeacoffee.com/decouk"),
                        tooltip="Support the developer",
                    ),
                    MenuItem(
                        id="bug_report",
                        title="Report Bug",
                        description="Submit an issue on GitHub",
                        icon=ft.Icons.BUG_REPORT,
                        on_click=lambda _: webbrowser.open("https://github.com/Recol/DLSS-Updater/issues"),
                    ),
                    MenuItem(
                        id="twitter",
                        title="Twitter",
                        description="Follow on Twitter",
                        icon=ft.Icons.TAG,
                        on_click=lambda _: webbrowser.open("https://x.com/iDeco_UK"),
                    ),
                    MenuItem(
                        id="discord",
                        title="Discord",
                        description="Message on Discord",
                        icon=ft.Icons.CHAT,
                        on_click=lambda _: webbrowser.open("https://discord.com/users/162568099839606784"),
                    ),
                    MenuItem(
                        id="release_notes",
                        title="Release Notes",
                        description="View changelog",
                        icon=ft.Icons.ARTICLE,
                        on_click=self._on_release_notes_clicked,
                    ),
                ],
            ),
            MenuCategory(
                id="preferences",
                title="Preferences",
                icon=ft.Icons.TUNE,
                color="#FF9800",  # Orange
                items=[
                    MenuItem(
                        id="update_prefs",
                        title="Update Preferences",
                        description="Configure update settings",
                        icon=ft.Icons.SETTINGS,
                        on_click=self._on_settings_clicked,
                    ),
                    MenuItem(
                        id="blacklist",
                        title="Manage Blacklist",
                        description="Exclude specific games",
                        icon=ft.Icons.BLOCK,
                        on_click=self._on_blacklist_clicked,
                    ),
                    MenuItem(
                        id="dlss_overlay",
                        title="DLSS Debug Overlay",
                        description="Toggle in-game DLSS indicator",
                        icon=ft.Icons.BUG_REPORT,
                        on_click=self._on_dlss_overlay_clicked,
                    ),
                    MenuItem(
                        id="theme",
                        title="Theme: Dark Mode",
                        description="Disabled (light theme has issues)",
                        icon=ft.Icons.BRIGHTNESS_6,
                        is_disabled=True,
                        tooltip="Theme switching is temporarily disabled",
                    ),
                ],
            ),
            MenuCategory(
                id="application",
                title="Application",
                icon=ft.Icons.APPS,
                color="#2196F3",  # Blue
                items=[
                    MenuItem(
                        id="check_updates",
                        title="Check for Updates",
                        description="Check for app updates",
                        icon=ft.Icons.SYSTEM_UPDATE,
                        on_click=self._on_check_updates_clicked,
                        show_badge=False,
                    ),
                ],
            ),
        ]

        return AppMenuSelector(
            page=self.page,
            categories=categories,
            on_item_selected=self._on_menu_item_selected,
            initially_expanded=False,
            is_dark=self.theme_manager.is_dark,
        )

    def _on_menu_item_selected(self, item_id: str):
        """Handle menu item selection callback"""
        self.logger.debug(f"Menu item selected: {item_id}")

    async def _toggle_theme_from_menu(self, e):
        """Handle theme toggle from menu"""
        self.theme_manager.toggle_theme()

        # Rebuild expansion menu with updated colors
        await self._rebuild_expansion_menu()

        self.logger.info(f"Theme toggled to {'Dark' if self.theme_manager.is_dark else 'Light'} Mode")

    async def _rebuild_expansion_menu(self):
        """Rebuild expansion menu with updated colors after theme change"""
        # Rebuild the AppMenuSelector with new theme colors
        self.app_menu_selector = self._create_app_menu()
        self.app_menu_container.content = self.app_menu_selector
        self.page.update()

    async def _create_launcher_list(self) -> ft.Container:
        """Create the scrollable list of launcher cards"""
        launcher_cards = []

        # Create launcher cards
        for config in self.launcher_configs:
            is_custom = config.get("custom", False)
            card = LauncherCard(
                name=config["name"],
                launcher_enum=config["enum"],
                icon=config["icon"],
                is_custom=is_custom,
                on_browse=self._create_browse_handler(config["enum"]),
                on_reset=self._create_reset_handler(config["enum"]),
                page=self.page,
                logger=self.logger,
            )

            # Load existing path from config
            current_path = config_manager.check_path_value(config["enum"])
            if current_path:
                await card.set_path(current_path)

            self.launcher_cards[config["enum"]] = card
            launcher_cards.append(card)

        # Create scrollable column
        launcher_column = ft.Column(
            controls=launcher_cards,
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # Create responsive container that centers on large screens
        # Using ResponsiveRow to handle different window sizes
        responsive_content = ft.Column(
            controls=[launcher_column],
            col={"xs": 12, "sm": 12, "md": 10, "lg": 8, "xl": 7},
            expand=True,
        )

        return ft.Container(
            content=ft.ResponsiveRow(
                controls=[responsive_content],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=ft.padding.all(16),
            expand=True,
        )

    def _create_browse_handler(self, launcher: LauncherPathName):
        """Create browse click handler for specific launcher"""
        async def handler(e):
            self.current_launcher_selecting = launcher
            self.file_picker.get_directory_path(dialog_title="Select Game Folder")
        return handler

    def _open_navigation_drawer(self, e):
        """Open the navigation drawer"""
        self.page.open(self.page.drawer)

    async def _on_drawer_navigation_changed(self, index: int):
        """Handle navigation drawer selection change"""
        self.current_view_index = index

        # Switch to appropriate view
        if self.current_view_index == 0:
            self.content_switcher.content = self.launchers_view
        elif self.current_view_index == 1:
            self.content_switcher.content = self.games_view
            # Load games when navigating to games view
            await self.games_view.load_games()
        elif self.current_view_index == 2:
            self.content_switcher.content = self.backups_view
            # Load backups when navigating to backups view
            await self.backups_view.load_backups()

        self.page.update()
        self.logger.info(f"Navigated to view index: {self.current_view_index}")

    async def _on_browse_clicked(self, launcher: LauncherPathName):
        """Handle browse button click"""
        self.current_launcher_selecting = launcher
        self.file_picker.get_directory_path(dialog_title="Select Game Folder")

    async def _on_folder_selected(self, e: ft.FilePickerResultEvent):
        """Handle folder selection from file picker"""
        if e.path and self.current_launcher_selecting:
            path = e.path
            self.logger.info(f"Selected path for {self.current_launcher_selecting.name}: {path}")

            # Update config
            config_manager.update_launcher_path(self.current_launcher_selecting, path)

            # Update card UI
            card = self.launcher_cards.get(self.current_launcher_selecting)
            if card:
                await card.set_path(path)

            # Show notification
            await self._show_snackbar(f"Path updated for {card.name}")

            self.current_launcher_selecting = None

    def _create_reset_handler(self, launcher: LauncherPathName):
        """Create reset click handler for specific launcher"""
        async def handler(e):
            await self._on_reset_clicked(launcher)
        return handler

    async def _on_reset_clicked(self, launcher: LauncherPathName):
        """Handle reset button click"""
        # Show confirmation dialog
        async def confirm_reset(e):
            self.page.close(dialog)
            config_manager.update_launcher_path(launcher, "")

            card = self.launcher_cards.get(launcher)
            if card:
                await card.set_path("")
                await self._show_snackbar(f"Path reset for {card.name}")

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Reset Path?"),
            content=ft.Text(
                f"This will reset the path for {self.launcher_cards[launcher].name}. Continue?"
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("Reset", on_click=confirm_reset),
            ],
        )

        self.page.open(dialog)

    def _toggle_theme(self, e):
        """Handle theme toggle button click"""
        self.theme_manager.toggle_theme()
        # Update button icon and tooltip
        e.control.icon = self.theme_manager.get_icon()
        e.control.tooltip = self.theme_manager.get_tooltip()
        self.page.update()

    def show_update_badge(self, show: bool = True):
        """
        Show or hide update available badge on hamburger menu

        Args:
            show: True to display red badge indicator, False to hide it
        """
        if hasattr(self, 'hamburger_badge_dot'):
            self.hamburger_badge_dot.visible = show
        # Use new AppMenuSelector API for menu badge
        if hasattr(self, 'app_menu_selector'):
            self.app_menu_selector.set_badge_visible("check_updates", show)
        if self.page:
            self.page.update()
            self.logger.info(f"Update badge {'shown' if show else 'hidden'}")

    async def _on_release_notes_clicked(self, e):
        """Handle release notes button click"""
        dialog = ReleaseNotesDialog(self.page, self.logger)
        await dialog.show()

    async def _on_check_updates_clicked(self, e):
        """Handle check for updates button click"""
        dialog = AppUpdateDialog(self.page, self.logger)
        await dialog.check_and_show()

    async def _on_blacklist_clicked(self, e):
        """Handle blacklist button click"""
        dialog = BlacklistDialog(self.page, self.logger)
        await dialog.show()

    async def _on_dlss_overlay_clicked(self, e):
        """Handle DLSS overlay settings button click"""
        dialog = DLSSOverlayDialog(self.page, self.logger)
        await dialog.show()

    async def _on_settings_clicked(self, e):
        """Handle settings button click"""
        # Show preferences dialog
        dialog = UpdatePreferencesDialog(self.page, self.logger)
        await dialog.show()

    async def _on_scan_clicked(self, e):
        """Handle scan button click - scans for games and populates cards"""
        self.logger.info("Scan button clicked")

        # Check if DLL cache is ready (warn but don't block - scanning works without it)
        from dlss_updater.config import is_dll_cache_ready
        if not is_dll_cache_ready():
            self.logger.warning("Scanning before DLL cache initialized - version info may be incomplete")

        try:
            # Show loading overlay
            self.loading_overlay.show(self.page, "Scanning for games...")

            # Progress callback
            async def on_progress(progress: UpdateProgress):
                await self.loading_overlay.set_progress_async(
                    progress.percentage,
                    self.page,
                    progress.message
                )

            # Run scan only
            dll_dict = await self.update_coordinator.scan_for_games(on_progress)

            # SAVE SCAN RESULTS FOR LATER UPDATE (both in memory and to disk)
            self.last_scan_results = dll_dict
            await self._save_scan_cache()

            # Update the scan info display
            self._update_scan_info_text()
            self.page.update()

            # Hide loading overlay
            self.loading_overlay.hide(self.page)

            # Parse DLL dict and update launcher cards
            await self._populate_launcher_cards(dll_dict)

            # Count unique games (group DLLs by game root)
            unique_games = set()
            for launcher, dll_paths in dll_dict.items():
                for dll_path in dll_paths:
                    game_root = find_game_root(Path(dll_path), launcher)
                    unique_games.add(str(game_root))

            # Show success notification with accurate counts
            total_games = len(unique_games)
            total_dlls = sum(len(dlls) for dlls in dll_dict.values())
            await self._show_snackbar(f"Scan complete: {total_games} games, {total_dlls} DLLs found, you can now find them in the Games tab",)

        except Exception as ex:
            self.logger.error(f"Scan failed: {ex}", exc_info=True)
            self.loading_overlay.hide(self.page)

            # Auto-expand logger on error
            if self.logger_panel:
                self.logger_panel.expand_on_error()

            # Show error dialog
            error_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Scan Failed", color=ft.Colors.RED),
                content=ft.Text(str(ex)),
                actions=[
                    ft.FilledButton(
                        "OK",
                        on_click=lambda e: self.page.close(error_dialog)
                    ),
                ],
            )
            self.page.open(error_dialog)

    async def _populate_launcher_cards(self, dll_dict: Dict):
        """
        Populate launcher cards with game data from scan results

        Args:
            dll_dict: Dictionary from scanner with launcher -> list of DLL paths
        """
        # Map enum values to scanner dict keys
        # Scanner uses keys like "Steam", "EA Launcher", etc. (see scanner.py line 183-194)
        enum_to_scanner_key = {
            LauncherPathName.STEAM: "Steam",
            LauncherPathName.EA: "EA Launcher",
            LauncherPathName.UBISOFT: "Ubisoft Launcher",
            LauncherPathName.EPIC: "Epic Games Launcher",
            LauncherPathName.GOG: "GOG Launcher",
            LauncherPathName.BATTLENET: "Battle.net Launcher",
            LauncherPathName.XBOX: "Xbox Launcher",
            LauncherPathName.CUSTOM1: "Custom Folder 1",
            LauncherPathName.CUSTOM2: "Custom Folder 2",
            LauncherPathName.CUSTOM3: "Custom Folder 3",
            LauncherPathName.CUSTOM4: "Custom Folder 4",
        }

        # Group games by launcher
        # dll_dict structure: {launcher_key: [dll_paths...]}
        # We need to convert this to: {launcher_enum: [{name, path, dlls: [...]}]}

        for launcher_config in self.launcher_configs:
            launcher_enum = launcher_config["enum"]
            launcher_key = enum_to_scanner_key.get(launcher_enum)

            # Get DLLs for this launcher
            dlls_for_launcher = dll_dict.get(launcher_key, []) if launcher_key else []

            # Group by game (extract game name from path)
            games_by_name = {}
            for dll_path in dlls_for_launcher:
                # Extract game name from path (simplified)
                # The scanner provides full paths like: C:\Games\Steam\Game\nvngx_dlss.dll
                path_obj = Path(dll_path)
                # Use find_game_root to detect the true game root directory
                game_dir = find_game_root(path_obj, launcher_key)

                # Try to get game name (parent directory name)
                game_name = game_dir.name

                if game_name not in games_by_name:
                    games_by_name[game_name] = GameCardData(
                        name=game_name,
                        path=str(game_dir),
                        dlls=[]
                    )

                # Add DLL info - query database for actual version data
                dll_filename = path_obj.name
                dll_type = self._get_dll_type(dll_filename)

                # Query database for version (scanner already stored it during scan)
                current_version = 'Unknown'
                try:
                    from dlss_updater.database import db_manager
                    game_dll = await db_manager.get_game_dll_by_path(str(dll_path))

                    if game_dll and game_dll.current_version:
                        current_version = game_dll.current_version
                    else:
                        # Fallback: read directly from file
                        from dlss_updater.updater import get_dll_version
                        file_version = get_dll_version(Path(dll_path))
                        if file_version:
                            current_version = file_version
                except Exception as e:
                    self.logger.warning(f"Could not get version for {dll_path}: {e}")

                # Get latest version from config
                from dlss_updater.config import LATEST_DLL_VERSIONS
                latest_version = LATEST_DLL_VERSIONS.get(dll_type, 'Unknown')

                # Compare versions to determine if update available
                update_available = False
                if current_version != 'Unknown' and latest_version != 'Unknown':
                    try:
                        from dlss_updater.updater import parse_version
                        current_parsed = parse_version(current_version)
                        latest_parsed = parse_version(latest_version)
                        update_available = current_parsed < latest_parsed
                    except Exception as e:
                        self.logger.warning(f"Version comparison failed for {dll_type}: {e}")

                dll_info = DLLInfo(
                    dll_type=dll_type,
                    current_version=current_version,
                    latest_version=latest_version,
                    update_available=update_available
                )
                games_by_name[game_name].dlls.append(dll_info)

            # Convert to list
            games_list = list(games_by_name.values())

            # Update the launcher card
            card = self.launcher_cards.get(launcher_enum)
            if card:
                await card.set_games(games_list)

    def _get_dll_type(self, filename: str) -> str:
        """Get friendly DLL type name from filename"""
        filename_lower = filename.lower()

        if 'dlss' in filename_lower:
            if 'dlssg' in filename_lower:
                return 'DLSS-G'
            elif 'dlssd' in filename_lower:
                return 'DLSS-D'
            else:
                return 'DLSS'
        elif 'xess' in filename_lower:
            return 'XeSS'
        elif 'fsr' in filename_lower or 'fidelityfx' in filename_lower:
            return 'FSR'
        elif 'sl.' in filename_lower or 'streamline' in filename_lower:
            return 'Streamline'
        elif 'dstorage' in filename_lower:
            return 'DirectStorage'
        else:
            return filename

    async def _on_update_clicked(self, e):
        """Handle update button click - updates already-scanned games"""
        self.logger.info("Update button clicked")

        # Check if DLL cache is ready
        from dlss_updater.config import is_dll_cache_ready
        if not is_dll_cache_ready():
            self.logger.warning("Update attempted before DLL cache initialized")
            error_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Please Wait", color=ft.Colors.ORANGE),
                content=ft.Text("DLL cache is still initializing. Please wait a moment and try again."),
                actions=[
                    ft.FilledButton(
                        "OK",
                        on_click=lambda e: self.page.close(error_dialog)
                    ),
                ],
            )
            self.page.open(error_dialog)
            return

        # Check if scan has been run
        if not self.last_scan_results:
            self.logger.warning("Update attempted without prior scan")
            error_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Scan Required", color=ft.Colors.ORANGE),
                content=ft.Text("Please run 'Scan for Games' first before updating."),
                actions=[
                    ft.FilledButton(
                        "OK",
                        on_click=lambda e: self.page.close(error_dialog)
                    ),
                ],
            )
            self.page.open(error_dialog)
            return

        # Calculate scan age for logging
        if self.last_scan_timestamp:
            scan_time = datetime.fromisoformat(self.last_scan_timestamp)
            age = datetime.now() - scan_time
            hours_ago = age.total_seconds() / 3600
            if hours_ago < 1:
                age_str = f"{int(age.total_seconds() / 60)} minutes ago"
            elif hours_ago < 24:
                age_str = f"{int(hours_ago)} hours ago"
            else:
                age_str = f"{int(hours_ago / 24)} days ago"
            self.logger.info(f"Using scan results from {age_str}")

        try:
            # Show loading overlay
            self.loading_overlay.show(self.page, "Updating games...")

            # Progress callback
            async def on_progress(progress: UpdateProgress):
                await self.loading_overlay.set_progress_async(
                    progress.percentage,
                    self.page,
                    progress.message
                )

            # Run update ONLY (use cached scan results)
            result = await self.update_coordinator.update_games(
                self.last_scan_results,
                on_progress
            )

            # Hide loading overlay
            self.loading_overlay.hide(self.page)

            # Show results dialog
            summary_dialog = UpdateSummaryDialog(self.page, self.logger, result)
            await summary_dialog.show()

        except Exception as ex:
            self.logger.error(f"Update failed: {ex}", exc_info=True)
            self.loading_overlay.hide(self.page)

            # Auto-expand logger on error
            if self.logger_panel:
                self.logger_panel.expand_on_error()

            # Show error dialog
            error_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Update Failed", color=ft.Colors.RED),
                content=ft.Text(str(ex)),
                actions=[
                    ft.FilledButton(
                        "OK",
                        on_click=lambda e: self.page.close(error_dialog)
                    ),
                ],
            )
            self.page.open(error_dialog)

    def _update_scan_info_text(self):
        """Update the last scan info text displayed in UI"""
        if not self.last_scan_results or not self.last_scan_timestamp:
            if hasattr(self, 'last_scan_info_text'):
                self.last_scan_info_text.value = "No scan performed yet"
            return

        try:
            scan_time = datetime.fromisoformat(self.last_scan_timestamp)
            age = datetime.now() - scan_time
            hours_ago = age.total_seconds() / 3600

            if hours_ago < 1:
                time_str = f"{int(age.total_seconds() / 60)} minutes ago"
            elif hours_ago < 24:
                time_str = f"{int(hours_ago)} hours ago"
            else:
                time_str = f"{int(hours_ago / 24)} days ago"

            total_games = sum(len(dlls) for dlls in self.last_scan_results.values())

            if hasattr(self, 'last_scan_info_text'):
                self.last_scan_info_text.value = f"Last scan: {total_games} games found ({time_str})"
        except Exception as e:
            self.logger.warning(f"Failed to format scan info: {e}")
            if hasattr(self, 'last_scan_info_text'):
                self.last_scan_info_text.value = "Scan data available"

    async def _load_scan_cache(self):
        """Load cached scan results from disk if available"""
        def _read_cache_file():
            """Blocking file read - run in thread pool"""
            if self.scan_cache_path.exists():
                with open(self.scan_cache_path, 'rb') as f:
                    return f.read()
            return None

        try:
            # Use thread pool for blocking file I/O
            cache_data = await asyncio.to_thread(_read_cache_file)
            if cache_data:
                cache = decode_json(cache_data, type=ScanCacheData)
                self.last_scan_results = cache.scan_results
                self.last_scan_timestamp = cache.timestamp

                if self.last_scan_results:
                    total_games = sum(len(dlls) for dlls in self.last_scan_results.values())
                    scan_time = datetime.fromisoformat(self.last_scan_timestamp)
                    age = datetime.now() - scan_time
                    hours_ago = age.total_seconds() / 3600

                    if hours_ago < 1:
                        time_str = f"{int(age.total_seconds() / 60)} minutes ago"
                    elif hours_ago < 24:
                        time_str = f"{int(hours_ago)} hours ago"
                    else:
                        time_str = f"{int(hours_ago / 24)} days ago"

                    self.logger.info(f"Loaded cached scan results: {total_games} games (scanned {time_str})")
        except Exception as e:
            self.logger.warning(f"Failed to load scan cache: {e}")
            self.last_scan_results = None
            self.last_scan_timestamp = None

    async def _save_scan_cache(self):
        """Save scan results to disk for persistence across app restarts"""
        def _write_cache_file(path, data):
            """Blocking file write - run in thread pool"""
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)

        try:
            timestamp = datetime.now().isoformat()
            cache = ScanCacheData(
                scan_results=self.last_scan_results,
                timestamp=timestamp
            )
            cache_data = format_json(encode_json(cache))
            # Use thread pool for blocking file I/O
            await asyncio.to_thread(_write_cache_file, self.scan_cache_path, cache_data)
            self.last_scan_timestamp = timestamp
            self.logger.info(f"Saved scan cache to {self.scan_cache_path}")
        except Exception as e:
            self.logger.error(f"Failed to save scan cache: {e}")

    async def _show_snackbar(self, message: str, duration: int = 2000):
        """Show a snackbar notification"""
        snackbar = ft.SnackBar(
            content=ft.Text(message, color=ft.Colors.WHITE),
            bgcolor="#2D6E88",
            duration=duration,
        )
        self.page.overlay.append(snackbar)
        snackbar.open = True
        self.page.update()

    def get_dll_cache_snackbar(self) -> DLLCacheProgressSnackbar:
        """Get the DLL cache progress snackbar for external use"""
        return self.dll_cache_snackbar
