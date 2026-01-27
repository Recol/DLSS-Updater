"""
Main View for DLSS Updater Flet UI
Async-based Material Design interface with expandable launcher cards
"""

import asyncio
import logging
import os
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable, Any

import flet as ft


def open_url(url: str) -> bool:
    """
    Open a URL in the default browser (cross-platform).

    Args:
        url: The URL to open

    Returns:
        True if successful, False otherwise
    """
    import sys
    import os

    # On Linux (including WSL2), try multiple methods
    if sys.platform == 'linux':
        # Check if running in WSL by looking for Windows interop
        is_wsl = 'microsoft' in os.uname().release.lower() or Path('/mnt/c/Windows').exists()

        if is_wsl:
            # In WSL2, use cmd.exe to open URL in Windows browser
            try:
                subprocess.Popen(
                    ['cmd.exe', '/c', 'start', '', url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return True
            except Exception:
                pass

        # Try xdg-open for native Linux
        try:
            subprocess.Popen(
                ['xdg-open', url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            pass

    # On Windows/other platforms, use webbrowser
    try:
        webbrowser.open(url)
        return True
    except Exception:
        pass

    return False

from dlss_updater.config import config_manager, LauncherPathName, get_config_path
from dlss_updater.models import ScanCacheData, GameCardData, DLLInfo, encode_json, decode_json, format_json
from dlss_updater.ui_flet.components.launcher_card import LauncherCard
from dlss_updater.ui_flet.components.loading_overlay import LoadingOverlay
from dlss_updater.ui_flet.components.logger_panel import LoggerPanel
from dlss_updater.ui_flet.components.theme_manager import ThemeManager
from dlss_updater.ui_flet.theme.colors import Shadows
from dlss_updater.ui_flet.dialogs.update_summary_dialog import UpdateSummaryDialog
from dlss_updater.ui_flet.components.slide_panel import PanelManager
from dlss_updater.ui_flet.panels import PreferencesPanel, ReleaseNotesPanel, BlacklistPanel, UIPreferencesPanel
from dlss_updater.ui_flet.dialogs.app_update_dialog import AppUpdateDialog
from dlss_updater.ui_flet.dialogs.dlss_overlay_dialog import DLSSOverlayDialog
from dlss_updater.ui_flet.async_updater import AsyncUpdateCoordinator, UpdateProgress
from dlss_updater.platform_utils import FEATURES, IS_LINUX, IS_WINDOWS
from dlss_updater.linux_paths import is_flatpak, get_flatpak_override_command
from dlss_updater.ui_flet.views.games_view import GamesView
from dlss_updater.ui_flet.views.backups_view import BackupsView
from dlss_updater.ui_flet.components.dll_cache_snackbar import DLLCacheProgressSnackbar
from dlss_updater.ui_flet.components.app_bar_menus import (
    CommunityMenu, PreferencesMenu, ApplicationMenu, create_app_bar_menus
)
from dlss_updater.version import __version__
from dlss_updater.utils import find_game_root


class MainView(ft.Column):
    """
    Main application view with navigation and launcher management
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        super().__init__()
        # Note: In Flet 0.80.4+, self._page_ref is a read-only property set when added to page
        # Store reference as _page_ref for use during initialization
        self._page_ref = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        # File picker for launcher paths (Flet 0.80.4+ async API)
        self.file_picker = ft.FilePicker()
        self.current_launcher_selecting: LauncherPathName | None = None
        self.is_adding_subfolder: bool = False  # Track if adding subfolder vs setting primary path

        # Loading overlay
        self.loading_overlay = LoadingOverlay()

        # Launcher cards dictionary
        self.launcher_cards: dict[LauncherPathName, LauncherCard] = {}

        # Async update coordinator
        self.update_coordinator = AsyncUpdateCoordinator(logger)

        # Logger panel
        self.logger_panel = None  # Will be created in _build_ui

        # Theme manager
        self.theme_manager = ThemeManager(page)
        self.theme_toggle_btn = None  # Will be created in _create_app_bar

        # DLL cache progress snackbar
        self.dll_cache_snackbar: DLLCacheProgressSnackbar | None = None

        # Popup menu components (created in _create_app_bar)
        self.community_menu: CommunityMenu | None = None
        self.preferences_menu: PreferencesMenu | None = None
        self.application_menu: ApplicationMenu | None = None

        # Discord banner
        self.discord_banner: ft.Banner | None = None

        # Navigation state
        self.current_view_index = 0  # 0=Launchers, 1=Games, 2=Backups
        self.last_view_index = 0  # Track previous view for cleanup

        # Scan state management - store last scan results for update operations
        self.last_scan_results: dict | None = None
        self.last_scan_timestamp: str | None = None
        self.scan_cache_path = Path(get_config_path()).parent / "scan_cache.json"

        # View instances (will be created in _build_ui)
        self.launchers_view = None
        self.games_view = None
        self.backups_view = None
        self.navigation_bar = None
        self.tab_buttons = []
        self.tab_contents = []
        self.content_area = None

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

        # Migrate image cache to WebP thumbnails if needed (one-time on upgrade)
        from dlss_updater.steam_integration import migrate_image_cache_if_needed
        if await migrate_image_cache_if_needed():
            self.logger.info("Image cache migrated to WebP thumbnail format")

        # Note: In Flet 0.80.4+, FilePicker uses async methods - no overlay needed
        # Note: loading_overlay is added/removed dynamically in show()/hide()

        # Create DLL cache progress notification and add wrapper to overlay
        self.dll_cache_snackbar = DLLCacheProgressSnackbar(self._page_ref)
        self._page_ref.overlay.append(self.dll_cache_snackbar.get_wrapper())

        # Load cached scan results if available
        await self._load_scan_cache()

        # Build UI components
        await self._build_ui()

        # Show Discord invite banner if not dismissed
        if not config_manager.get_discord_banner_dismissed():
            self.discord_banner = self._create_discord_banner()
            self._page_ref.show_dialog(self.discord_banner)
            self.logger.info("Discord invite banner displayed")

        self.logger.info("Main view initialized")

    async def _build_ui(self):
        """Build the main UI structure with top navigation tabs"""
        # Create app bar
        app_bar = await self._create_app_bar()

        # Create launcher view (with existing cards and action buttons)
        self.launchers_view = await self._create_launchers_view()

        # Create other views
        self.games_view = GamesView(self._page_ref, self.logger)
        self.backups_view = BackupsView(self._page_ref, self.logger)

        # Create custom navigation tabs
        self.navigation_bar = self._create_navigation_tabs()

        # Create logger panel
        self.logger_panel = LoggerPanel(self._page_ref, self.logger)

        # Assemble main layout: AppBar + Content + Logger
        self.controls = [
            app_bar,
            ft.Container(
                height=1,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment.CENTER_LEFT,
                    end=ft.Alignment.CENTER_RIGHT,
                    colors=["transparent", "#5A5A5A", "transparent"],
                ),
            ),
            self.navigation_bar,
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
            alignment=ft.Alignment.CENTER,
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
            bgcolor=MD3Colors.get_surface_container(is_dark),
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
                button_container.bgcolor = MD3Colors.get_surface_container(is_dark)
                button_container.border = ft.border.all(1, MD3Colors.get_outline(is_dark))
            if self._page_ref:
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

        # Create action buttons container with themed surface style
        # Store reference for theme updates
        # PERF: Removed wrapper Container + spacer Container (-2 controls)
        self.launchers_action_buttons = ft.Container(
            content=ft.Column(
                controls=[
                    # Last scan info - centered via Column alignment
                    self.last_scan_info_text,
                    # Buttons row - spacing via Row property
                    ft.Row(
                        controls=[scan_button, update_button],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=16,
                    ),
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.all(16),
            bgcolor=MD3Colors.get_background(is_dark),
            border=ft.border.only(top=ft.BorderSide(1, MD3Colors.get_outline(is_dark))),
        )

        # Create launchers content area container with themed background
        # Store reference for theme updates
        self.launchers_content_container = ft.Container(
            content=ft.Column(
                controls=[
                    launcher_list,
                    self.launchers_action_buttons,
                ],
                expand=True,
                spacing=0,
            ),
            bgcolor=MD3Colors.get_background(is_dark),
            expand=True,
        )

        # Return launchers view wrapped in themed container
        return self.launchers_content_container

    def _create_navigation_tabs(self) -> ft.Column:
        """Create custom colored navigation tab bar"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors

        is_dark = self._page_ref.theme_mode == ft.ThemeMode.DARK

        tab_configs = [
            {"name": "Launchers", "icon": ft.Icons.FOLDER_SPECIAL, "color": TabColors.LAUNCHERS, "content": self.launchers_view},
            {"name": "Games", "icon": ft.Icons.VIDEOGAME_ASSET, "color": TabColors.GAMES, "content": self.games_view},
            {"name": "Backups", "icon": ft.Icons.RESTORE, "color": TabColors.BACKUPS, "content": self.backups_view},
        ]

        self.tab_buttons = []
        self.tab_contents = []

        for i, config in enumerate(tab_configs):
            is_active = i == self.current_view_index
            btn = self._create_tab_button(config, is_active, i)
            self.tab_buttons.append(btn)
            self.tab_contents.append(config["content"])

        # Tab bar row - use background color to match page
        # Store reference for theme updates
        self.tab_bar_container = ft.Container(
            content=ft.Row(
                controls=self.tab_buttons,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=0,
            ),
            bgcolor=MD3Colors.get_background(is_dark),
            padding=ft.padding.symmetric(vertical=4),
        )

        # PERFORMANCE: Use Stack with opacity for fast tab switching
        # AnimatedSwitcher creates two copies during transition, doubling tree size
        # Stack with visibility keeps all views but only one visible at a time
        # This is faster for tab switching with large views like GamesView
        self.content_area = ft.Stack(
            controls=[
                ft.Container(
                    content=view,
                    visible=(i == self.current_view_index),
                    expand=True,
                    animate_opacity=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
                    opacity=1.0 if i == self.current_view_index else 0.0,
                )
                for i, view in enumerate(self.tab_contents)
            ],
            expand=True,
        )

        return ft.Column(
            controls=[self.tab_bar_container, self.content_area],
            spacing=0,
            expand=True,
        )

    def _create_tab_button(self, config: dict, is_active: bool, index: int) -> ft.Container:
        """Create individual tab button with color theming"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self._page_ref.theme_mode == ft.ThemeMode.DARK
        color = config["color"]

        # Use white text on colored background for better contrast
        icon_color = ft.Colors.WHITE if is_active else MD3Colors.get_on_surface_variant(is_dark)
        text_color = ft.Colors.WHITE if is_active else MD3Colors.get_on_surface_variant(is_dark)
        bg_color = color if is_active else "transparent"  # Full color background when active
        indicator_visible = is_active

        # Store references for later updates
        tab_icon = ft.Icon(config["icon"], size=20, color=icon_color)
        tab_label = ft.Text(
            config["name"],
            size=14,
            weight=ft.FontWeight.W_600 if is_active else ft.FontWeight.W_500,
            color=text_color,
        )
        indicator = ft.Container(
            height=3,
            bgcolor=color if indicator_visible else "transparent",
            border_radius=ft.border_radius.only(top_left=3, top_right=3),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )

        tab_btn = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            controls=[tab_icon, tab_label],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        padding=ft.padding.symmetric(horizontal=24, vertical=12),
                    ),
                    indicator,
                ],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=bg_color,
            border_radius=ft.border_radius.only(top_left=8, top_right=8),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            on_click=lambda e, idx=index: self._page_ref.run_task(self._on_tab_clicked, idx),
            on_hover=lambda e, idx=index, c=color: self._on_tab_hover(e, idx, c),
            expand=True,
        )

        return tab_btn

    def _on_tab_hover(self, e, index: int, color: str):
        """Handle tab hover effect"""
        if index == self.current_view_index:
            return
        btn = self.tab_buttons[index]
        if e.data == "true":
            btn.bgcolor = f"{color}10"
        else:
            btn.bgcolor = "transparent"
        btn.update()

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
            alignment=ft.Alignment.CENTER,
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
        """Create the application bar with title and 3 popup menu buttons"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark

        # Create callbacks dict for menu items
        menu_callbacks = {
            # Community menu callbacks
            "support": lambda _: open_url("https://buymeacoffee.com/decouk"),
            "bug_report": lambda _: open_url("https://github.com/Recol/DLSS-Updater/issues"),
            "twitter": lambda _: open_url("https://x.com/iDeco_UK"),
            "discord": lambda _: open_url("https://discord.com/users/162568099839606784"),
            "discord_invite": self._on_show_discord_invite_clicked,
            "release_notes": self._on_release_notes_clicked,
            # Preferences menu callbacks
            "update_prefs": self._on_settings_clicked,
            "ui_prefs": self._on_ui_preferences_clicked,
            "blacklist": self._on_blacklist_clicked,
            "dlss_overlay": self._on_dlss_overlay_clicked,
            "theme": self._toggle_theme_from_menu,
            # Application menu callbacks
            "check_updates": self._on_check_updates_clicked,
        }

        # Create the 3 popup menus using the factory function
        self.community_menu, self.preferences_menu, self.application_menu = create_app_bar_menus(
            page=self._page_ref,
            is_dark=is_dark,
            callbacks=menu_callbacks,
            features_dlss_overlay=FEATURES.dlss_overlay,
        )

        # Compact top bar with 3 popup menu buttons on the right
        top_bar = ft.Row(
            controls=[
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
                # Right side buttons: Community (Heart), Preferences (Gear), Application (Grid)
                self.community_menu.button,
                self.preferences_menu.button,
                self.application_menu.button,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # Return app bar with dark surface style (full width, no border)
        # Store reference for theme updates
        # PERF: Merged double Container into single (-1 control)
        self.app_bar_container = ft.Container(
            content=top_bar,
            padding=ft.padding.symmetric(vertical=16, horizontal=16),
            bgcolor=MD3Colors.get_background(is_dark),
            shadow=Shadows.LEVEL_2,
        )
        return self.app_bar_container

    async def _toggle_theme_from_menu(self, e):
        """Handle theme toggle from menu with cascade animation

        PERFORMANCE: Batches all theme-related UI updates into a single page.update() call.
        Individual update methods no longer call page.update() to avoid 3x serialization.
        """
        # Use async toggle for cascade animations to registered components
        await self.theme_manager.toggle_theme_async()

        # Rebuild popup menus with updated colors (no page.update inside)
        await self._rebuild_popup_menus()

        # Update tab bar colors for new theme (no page.update inside)
        await self._update_tab_colors_for_theme()

        # Update launchers view colors for new theme (no page.update inside)
        await self._update_launchers_view_for_theme()

        # SINGLE batched page.update() for all theme changes
        if self._page_ref:
            self._page_ref.update()

        self.logger.info(f"Theme toggled to {'Dark' if self.theme_manager.is_dark else 'Light'} Mode")

    async def _update_tab_colors_for_theme(self):
        """Update navigation tab colors after theme change"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors

        is_dark = self.theme_manager.is_dark

        # Update tab bar container background
        if hasattr(self, 'tab_bar_container') and self.tab_bar_container:
            self.tab_bar_container.bgcolor = MD3Colors.get_background(is_dark)

        # Update each tab button's colors for the new theme
        tab_configs = [
            {"name": "Launchers", "color": TabColors.get_themed_color("Launchers", is_dark)},
            {"name": "Games", "color": TabColors.get_themed_color("Games", is_dark)},
            {"name": "Backups", "color": TabColors.get_themed_color("Backups", is_dark)},
        ]

        # Update active/inactive state colors
        for idx, btn in enumerate(self.tab_buttons):
            is_selected = idx == self.current_view_index
            color = tab_configs[idx]["color"]

            if is_selected:
                btn.bgcolor = color
                btn.content.controls[0].color = ft.Colors.WHITE
                btn.content.controls[1].color = ft.Colors.WHITE
            else:
                btn.bgcolor = ft.Colors.TRANSPARENT
                btn.content.controls[0].color = MD3Colors.get_on_surface_variant(is_dark)
                btn.content.controls[1].color = MD3Colors.get_on_surface_variant(is_dark)

        # NOTE: No page.update() here - batched in _toggle_theme_from_menu()

    async def _rebuild_popup_menus(self):
        """Rebuild popup menus with updated colors after theme change"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark

        # Update app bar container background for new theme
        if hasattr(self, 'app_bar_container') and self.app_bar_container:
            self.app_bar_container.bgcolor = MD3Colors.get_background(is_dark)

        # Rebuild all popup menus with new theme colors
        if self.community_menu:
            self.community_menu.rebuild(is_dark)
        if self.preferences_menu:
            self.preferences_menu.rebuild(is_dark)
        if self.application_menu:
            self.application_menu.rebuild(is_dark)

        # Update the buttons in the app bar
        if hasattr(self, 'app_bar_container') and self.app_bar_container:
            top_bar = self.app_bar_container.content.content  # Container -> Row
            if hasattr(top_bar, 'controls') and len(top_bar.controls) >= 4:
                # Update button references (controls: title_col, community, prefs, app)
                top_bar.controls[1] = self.community_menu.button
                top_bar.controls[2] = self.preferences_menu.button
                top_bar.controls[3] = self.application_menu.button

        # NOTE: No page.update() here - batched in _toggle_theme_from_menu()

    async def _update_launchers_view_for_theme(self):
        """Update launchers view colors after theme change"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark

        # Update launchers content container background
        if hasattr(self, 'launchers_content_container') and self.launchers_content_container:
            self.launchers_content_container.bgcolor = MD3Colors.get_background(is_dark)

        # Update action buttons container background and border
        if hasattr(self, 'launchers_action_buttons') and self.launchers_action_buttons:
            self.launchers_action_buttons.bgcolor = MD3Colors.get_background(is_dark)
            self.launchers_action_buttons.border = ft.border.only(
                top=ft.BorderSide(1, MD3Colors.get_outline(is_dark))
            )

        # Update last scan info text color
        if hasattr(self, 'last_scan_info_text') and self.last_scan_info_text:
            self.last_scan_info_text.color = MD3Colors.get_on_surface_variant(is_dark)

        # NOTE: No page.update() here - batched in _toggle_theme_from_menu()

    def _create_discord_banner(self) -> ft.Banner:
        """Create the Discord invite banner using ft.Banner widget."""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark

        banner = ft.Banner(
            bgcolor=MD3Colors.ACCENT_SUBTLE,  # 12% opacity teal
            leading=ft.Icon(
                ft.Icons.CAMPAIGN,  # Announcement icon
                color=MD3Colors.PRIMARY,
                size=40,
            ),
            content=ft.Text(
                "Join our Discord community! Get early announcements, support, and connect with other users.",
                color=MD3Colors.get_on_surface(is_dark),
                size=14,
            ),
            actions=[
                ft.TextButton(
                    "Join Discord",
                    style=ft.ButtonStyle(color=ft.Colors.WHITE, bgcolor=MD3Colors.PRIMARY),
                    on_click=self._on_join_discord_clicked,
                ),
                ft.TextButton(
                    "Dismiss",
                    style=ft.ButtonStyle(color=MD3Colors.get_on_surface_variant(is_dark)),
                    on_click=self._on_dismiss_discord_banner,
                ),
            ],
        )
        return banner

    async def _on_join_discord_clicked(self, e):
        """Open Discord invite link and dismiss banner"""
        open_url("https://discord.gg/xTah8XCauN")
        await self._on_dismiss_discord_banner(e)

    async def _on_dismiss_discord_banner(self, e):
        """Dismiss banner and persist preference"""
        config_manager.set_discord_banner_dismissed(True)
        if self.discord_banner:
            self._page_ref.pop_dialog()
            self.discord_banner = None

    async def show_discord_banner(self):
        """Show Discord invite banner (for re-showing from menu)"""
        if not self.discord_banner:
            self.discord_banner = self._create_discord_banner()
        self._page_ref.show_dialog(self.discord_banner)
        self.logger.info("Discord banner shown")

    async def _on_show_discord_invite_clicked(self, e):
        """Handle Show Discord Invite menu click"""
        config_manager.set_discord_banner_dismissed(False)
        await self.show_discord_banner()

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
                on_reset=self._create_reset_handler(config["enum"]),
                on_add_subfolder=self._create_add_subfolder_handler(config["enum"]),
                page=self._page_ref,
                logger=self.logger,
            )

            # Load existing paths from config (multi-path support)
            current_paths = config_manager.get_launcher_paths(config["enum"])
            if current_paths:
                await card.set_paths(current_paths)

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

    def _create_add_subfolder_handler(self, launcher: LauncherPathName):
        """Create add subfolder click handler for specific launcher"""
        async def handler(e):
            self.current_launcher_selecting = launcher
            self.is_adding_subfolder = True  # Add subfolder adds to existing paths

            if IS_LINUX:
                # On Linux, show a text input dialog since native file pickers
                # may not work properly with WSLg or some desktop environments
                await self._show_path_input_dialog(launcher)
            else:
                # On Windows, use the native file picker (Flet 0.80.4+ async API)
                path = await self.file_picker.get_directory_path(dialog_title="Add Sub-Folder")
                if path:
                    await self._handle_folder_selected(path, launcher, is_adding=True)
                self.current_launcher_selecting = None
                self.is_adding_subfolder = False
        return handler

    async def _show_path_input_dialog(self, launcher: LauncherPathName):
        """Show a text input dialog for entering a path (Linux fallback)"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark
        path_input = ft.TextField(
            label="Enter folder path",
            hint_text="/path/to/games",
            expand=True,
            autofocus=True,
        )

        async def on_submit(e):
            path = path_input.value.strip()
            if path:
                # Validate the path exists
                if Path(path).is_dir():
                    self._page_ref.pop_dialog()
                    # Add the path to the launcher
                    added = config_manager.add_launcher_path(launcher, path)
                    if added:
                        all_paths = config_manager.get_launcher_paths(launcher)
                        card = self.launcher_cards.get(launcher)
                        if card:
                            await card.set_paths(all_paths)
                        await self._show_snackbar(f"Path added: {path}")
                    else:
                        await self._show_snackbar("Path already exists or limit reached")
                else:
                    path_input.error_text = "Directory does not exist"
                    self._page_ref.update()
            else:
                path_input.error_text = "Please enter a path"
                self._page_ref.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Add Folder Path"),
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Enter the full path to your games folder:",
                        size=14,
                        color=MD3Colors.get_on_surface_variant(is_dark),
                    ),
                    path_input,
                    ft.Text(
                        "Example: /home/user/.steam/steam/steamapps/common",
                        size=12,
                        color=MD3Colors.get_on_surface_variant(is_dark),
                        italic=True,
                    ),
                ],
                spacing=12,
                tight=True,
                width=400,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._page_ref.pop_dialog()),
                ft.FilledButton("Add", on_click=on_submit),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        self._page_ref.show_dialog(dialog)

    async def _on_tab_clicked(self, index: int):
        """Handle tab click - switch to selected tab.

        PERFORMANCE: Optimized for instant tab switching:
        1. Switch visuals + content visibility immediately (no delay)
        2. Data loading happens in background (non-blocking for cached views)
        3. Single batched page.update() call
        4. Progressive animation on tab switch for visual feedback
        """
        if index == self.current_view_index:
            return

        previous_index = self.current_view_index
        self.current_view_index = index

        # PERFORMANCE: Update everything in one batch - no artificial delays
        # 1. Update tab button visuals
        self._update_tab_visuals(previous_index, index)

        # 2. Switch content visibility immediately (Stack-based toggle)
        for i, container in enumerate(self.content_area.controls):
            if i == previous_index:
                container.opacity = 0.0
                container.visible = False
            elif i == index:
                container.visible = True
                container.opacity = 1.0

        # 3. Single batched update - user sees tab switch instantly
        self._page_ref.update()

        # 4. Resource cleanup in background (non-blocking)
        if previous_index == 1 and index != 1:
            # Don't await - let it run in background
            # Register with task registry for proper shutdown cancellation
            from dlss_updater.task_registry import register_task
            register_task(asyncio.create_task(self._cleanup_games_view()), "cleanup_games_view")

        # 5. Load data - NON-BLOCKING for instant tab switching
        # PERFORMANCE: Only register background tasks when views aren't loaded
        # Already-loaded views don't need background tasks (fast tab switching)
        from dlss_updater.task_registry import register_task
        needs_loading_update = False

        if index == 0:
            # Launchers tab - animate cards on switch
            launcher_cards = list(self.launcher_cards.values())
            if launcher_cards:
                # Reset opacity to 0 for animation
                for card in launcher_cards:
                    card.opacity = 0
                self._page_ref.update()
                # Trigger staggered fade-in animation
                register_task(
                    asyncio.create_task(self._animate_launcher_cards_in(launcher_cards)),
                    "animate_launchers_tab_switch"
                )

        elif index == 1:
            if not self.games_view._games_loaded:
                # Not loaded yet - show loading indicator and start background load
                self.games_view.loading_indicator.visible = True
                self.games_view.empty_state.visible = False
                self.games_view.tabs_container.visible = False
                needs_loading_update = True
                register_task(asyncio.create_task(self._load_games_background()), "load_games_background")
            # If already loaded, no task needed (view is cached)

        elif index == 2:
            if not self.backups_view._backups_loaded:
                # Not loaded yet - show loading indicator and start background load
                self.backups_view.loading_indicator.visible = True
                self.backups_view.empty_state.visible = False
                self.backups_view.backups_list_container.visible = False
                needs_loading_update = True
                register_task(asyncio.create_task(self._load_backups_background()), "load_backups_background")
            # If already loaded, no task needed (view is cached)

        # Update UI to show loading indicator if needed
        if needs_loading_update:
            self._page_ref.update()

        self.last_view_index = index

    async def _animate_launcher_cards_in(self, cards: list):
        """Animate launcher cards fade-in with staggered effect.

        Args:
            cards: List of LauncherCard instances to animate
        """
        await asyncio.sleep(0.02)  # Minimal initial delay
        batch_size = 4  # Animate 4 cards at a time for snappy feel
        for batch_start in range(0, len(cards), batch_size):
            batch_end = min(batch_start + batch_size, len(cards))
            for card in cards[batch_start:batch_end]:
                card.opacity = 1
            if self._page_ref:
                self._page_ref.update()
            await asyncio.sleep(0.025)  # 25ms between batches

    async def _load_games_background(self):
        """Load games in background - non-blocking for tab switch."""
        try:
            await self.games_view.load_games()
        except Exception as e:
            self.logger.error(f"Background games load error: {e}")

    async def _load_backups_background(self):
        """Load backups in background - non-blocking for tab switch."""
        try:
            await self.backups_view.load_backups()
        except Exception as e:
            self.logger.error(f"Background backups load error: {e}")

    def _update_tab_visuals(self, old_index: int, new_index: int):
        """Update tab button colors on selection change"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors

        is_dark = self._page_ref.theme_mode == ft.ThemeMode.DARK
        tab_colors = [TabColors.LAUNCHERS, TabColors.GAMES, TabColors.BACKUPS]

        # Deactivate old tab
        old_btn = self.tab_buttons[old_index]
        old_btn.bgcolor = "transparent"
        old_btn.content.controls[0].content.controls[0].color = MD3Colors.get_on_surface_variant(is_dark)
        old_btn.content.controls[0].content.controls[1].color = MD3Colors.get_on_surface_variant(is_dark)
        old_btn.content.controls[0].content.controls[1].weight = ft.FontWeight.W_500
        old_btn.content.controls[1].bgcolor = "transparent"

        # Activate new tab
        new_btn = self.tab_buttons[new_index]
        new_color = tab_colors[new_index]
        new_btn.bgcolor = new_color  # Full color background
        new_btn.content.controls[0].content.controls[0].color = ft.Colors.WHITE  # White icon
        new_btn.content.controls[0].content.controls[1].color = ft.Colors.WHITE  # White text
        new_btn.content.controls[0].content.controls[1].weight = ft.FontWeight.W_600
        new_btn.content.controls[1].bgcolor = new_color

    async def _handle_folder_selected(self, path: str, launcher: LauncherPathName, is_adding: bool = False):
        """Handle folder selection result (Flet 0.80.4+ direct call pattern)"""
        self.logger.info(f"{'Adding' if is_adding else 'Setting'} path for {launcher.name}: {path}")

        # Check if running in Flatpak and path is inaccessible
        if IS_LINUX and is_flatpak() and not os.access(path, os.R_OK):
            await self._show_flatpak_permission_dialog(path)
            return

        card = self.launcher_cards.get(launcher)

        if is_adding:
            # Add as sub-folder (append to existing paths)
            added = config_manager.add_launcher_path(launcher, path)
            if added:
                # Get updated paths and refresh card
                all_paths = config_manager.get_launcher_paths(launcher)
                if card:
                    await card.set_paths(all_paths)
                await self._show_snackbar(f"Sub-folder added to {card.name}")
            else:
                # Path already exists or limit reached
                from dlss_updater.models import MAX_PATHS_PER_LAUNCHER
                current_count = len(config_manager.get_launcher_paths(launcher))
                if current_count >= MAX_PATHS_PER_LAUNCHER:
                    await self._show_snackbar(f"Maximum {MAX_PATHS_PER_LAUNCHER} paths reached for {card.name}")
                else:
                    await self._show_snackbar(f"Path already exists for {card.name}")
        else:
            # Browse: set as primary path (keeps other paths if any)
            current_paths = config_manager.get_launcher_paths(launcher)
            if current_paths:
                # Replace first path, keep others
                current_paths[0] = path
                config_manager.set_launcher_paths(launcher, current_paths)
            else:
                # No existing paths, add this one
                config_manager.add_launcher_path(launcher, path)

            # Get updated paths and refresh card
            all_paths = config_manager.get_launcher_paths(launcher)
            if card:
                await card.set_paths(all_paths)
            await self._show_snackbar(f"Path updated for {card.name}")

    def _create_reset_handler(self, launcher: LauncherPathName):
        """Create reset click handler for specific launcher"""
        async def handler(e):
            await self._on_reset_clicked(launcher)
        return handler

    async def _on_reset_clicked(self, launcher: LauncherPathName):
        """Handle reset button click - clears all paths for the launcher"""
        card = self.launcher_cards.get(launcher)
        path_count = len(config_manager.get_launcher_paths(launcher))

        # Show confirmation dialog
        async def confirm_reset(e):
            self._page_ref.pop_dialog()
            # Clear all paths for this launcher
            config_manager.set_launcher_paths(launcher, [])

            if card:
                await card.set_paths([])
                await self._show_snackbar(f"All paths cleared for {card.name}")

        # Customize message based on path count
        if path_count > 1:
            content_text = f"This will clear all {path_count} paths for {card.name}. Continue?"
        else:
            content_text = f"This will clear the path for {card.name}. Continue?"

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Reset Paths?"),
            content=ft.Text(content_text),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._page_ref.pop_dialog()),
                ft.FilledButton("Reset", on_click=confirm_reset),
            ],
        )

        self._page_ref.show_dialog(dialog)

    async def _show_flatpak_permission_dialog(self, path: str):
        """
        Show dialog explaining how to grant Flatpak filesystem permissions.

        This is shown when running in Flatpak and the user selects a folder
        that is outside the sandbox's allowed filesystem paths.

        Args:
            path: The filesystem path that needs permission
        """
        override_cmd = get_flatpak_override_command(path)

        # Create a text field with the command for easy copying
        cmd_field = ft.TextField(
            value=override_cmd,
            read_only=True,
            multiline=True,
            min_lines=2,
            max_lines=3,
            text_size=12,
            border_color=ft.Colors.OUTLINE,
        )

        async def copy_command(e):
            """Copy the override command to clipboard"""
            await self._page_ref.set_clipboard_async(override_cmd)
            await self._show_snackbar("Command copied to clipboard")

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Flatpak Permission Required"),
            content=ft.Column([
                ft.Text(
                    "This folder is outside the Flatpak sandbox. "
                    "To grant access, run the following command in a terminal:",
                    size=14,
                ),
                ft.Container(height=8),
                cmd_field,
                ft.Container(height=8),
                ft.Text(
                    "Then restart the application for changes to take effect.",
                    size=12,
                    italic=True,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
            ], tight=True, spacing=4),
            actions=[
                ft.TextButton("Copy Command", on_click=copy_command),
                ft.FilledButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
            ],
        )

        self._page_ref.show_dialog(dialog)

    def _toggle_theme(self, e):
        """Handle theme toggle button click"""
        self.theme_manager.toggle_theme()
        # Update button icon and tooltip
        e.control.icon = self.theme_manager.get_icon()
        e.control.tooltip = self.theme_manager.get_tooltip()
        self._page_ref.update()

    def show_update_badge(self, show: bool = True):
        """
        Show or hide update available badge on menu

        Args:
            show: True to display red badge indicator, False to hide it
        """
        # Use ApplicationMenu API for menu badge
        if self.application_menu:
            self.application_menu.set_badge_visible("check_updates", show)
        if self._page_ref:
            self._page_ref.update()
            self.logger.info(f"Update badge {'shown' if show else 'hidden'}")

    async def _on_release_notes_clicked(self, e):
        """Handle release notes button click"""
        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        panel = ReleaseNotesPanel(self._page_ref, self.logger)
        await panel_manager.show_content(panel)

    async def _on_check_updates_clicked(self, e):
        """Handle check for updates button click"""
        dialog = AppUpdateDialog(self._page_ref, self.logger)
        await dialog.check_and_show()

    async def _on_blacklist_clicked(self, e):
        """Handle blacklist button click"""
        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        panel = BlacklistPanel(self._page_ref, self.logger)
        await panel_manager.show_content(panel)

    async def _on_dlss_overlay_clicked(self, e):
        """Handle DLSS overlay settings button click"""
        dialog = DLSSOverlayDialog(self._page_ref, self.logger)
        await dialog.show()

    async def _on_settings_clicked(self, e):
        """Handle settings button click"""
        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        panel = PreferencesPanel(self._page_ref, self.logger)
        await panel_manager.show_content(panel)

    async def _on_ui_preferences_clicked(self, e):
        """Handle UI preferences button click"""
        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        panel = UIPreferencesPanel(self._page_ref, self.logger)
        await panel_manager.show_content(panel)

    async def _on_scan_clicked(self, e):
        """Handle scan button click - scans for games and populates cards"""
        self.logger.info("Scan button clicked")

        # Check if DLL cache is ready (warn but don't block - scanning works without it)
        from dlss_updater.config import is_dll_cache_ready
        if not is_dll_cache_ready():
            self.logger.warning("Scanning before DLL cache initialized - version info may be incomplete")

        try:
            # Show loading overlay
            self.loading_overlay.show(self._page_ref, "Scanning for games...")

            # Progress callback
            async def on_progress(progress: UpdateProgress):
                await self.loading_overlay.set_progress_async(
                    progress.percentage,
                    self._page_ref,
                    progress.message
                )

            # Run scan only
            dll_dict = await self.update_coordinator.scan_for_games(on_progress)

            # SAVE SCAN RESULTS FOR LATER UPDATE (both in memory and to disk)
            self.last_scan_results = dll_dict
            await self._save_scan_cache()

            # Update the scan info display
            self._update_scan_info_text()
            self._page_ref.update()

            # Hide loading overlay
            self.loading_overlay.hide(self._page_ref)

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
            self.loading_overlay.hide(self._page_ref)

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
                        on_click=lambda e: self._page_ref.pop_dialog()
                    ),
                ],
            )
            self._page_ref.show_dialog(error_dialog)

    async def _populate_launcher_cards(self, dll_dict: dict):
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
                        on_click=lambda e: self._page_ref.pop_dialog()
                    ),
                ],
            )
            self._page_ref.show_dialog(error_dialog)
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
                        on_click=lambda e: self._page_ref.pop_dialog()
                    ),
                ],
            )
            self._page_ref.show_dialog(error_dialog)
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
            self.loading_overlay.show(self._page_ref, "Updating games...")

            # Progress callback
            async def on_progress(progress: UpdateProgress):
                await self.loading_overlay.set_progress_async(
                    progress.percentage,
                    self._page_ref,
                    progress.message
                )

            # Run update ONLY (use cached scan results)
            result = await self.update_coordinator.update_games(
                self.last_scan_results,
                on_progress
            )

            # Hide loading overlay
            self.loading_overlay.hide(self._page_ref)

            # Show results dialog
            summary_dialog = UpdateSummaryDialog(self._page_ref, self.logger, result)
            await summary_dialog.show()

        except Exception as ex:
            self.logger.error(f"Update failed: {ex}", exc_info=True)
            self.loading_overlay.hide(self._page_ref)

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
                        on_click=lambda e: self._page_ref.pop_dialog()
                    ),
                ],
            )
            self._page_ref.show_dialog(error_dialog)

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

            if hasattr(self, 'last_scan_info_text'):
                self.last_scan_info_text.value = f"Last scan: {time_str}"
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
        from dlss_updater.ui_flet.theme.colors import MD3Colors
        is_dark = self.theme_manager.is_dark
        snackbar = ft.SnackBar(
            content=ft.Text(message, color=ft.Colors.WHITE),
            bgcolor=MD3Colors.get_themed("snackbar_bg", is_dark),
            duration=duration,
        )
        self._page_ref.overlay.append(snackbar)
        snackbar.open = True
        self._page_ref.update()

    async def _cleanup_games_view(self):
        """Release Games view resources based on user preference.

        Note: on_view_hidden() handles conditional cleanup based on
        config_manager.get_keep_games_in_memory() - do NOT call clear_index()
        here as that would override the user's preference.
        """
        if hasattr(self, 'games_view') and self.games_view:
            await self.games_view.on_view_hidden()

    async def shutdown(self):
        """Graceful shutdown with timeout and comprehensive cleanup."""
        import sys
        import asyncio

        self.logger.info("Shutting down application...")
        SHUTDOWN_TIMEOUT = 5.0

        try:
            async with asyncio.timeout(SHUTDOWN_TIMEOUT):
                # 1. Cancel all registered background tasks
                try:
                    from dlss_updater.task_registry import cancel_all_tasks
                    await cancel_all_tasks(timeout=3.0)
                except Exception as e:
                    self.logger.warning(f"Error cancelling background tasks: {e}")

                # 2. Shutdown games view (clears card references, theme registration)
                try:
                    if hasattr(self, 'games_view') and self.games_view:
                        await self.games_view.on_shutdown()
                        self.logger.info("Games view shutdown complete")
                except Exception as e:
                    self.logger.warning(f"Error shutting down games view: {e}")

                # 3. Stop cache manager (releases memory maps, stops cleanup loop)
                try:
                    from dlss_updater.cache_manager import cache_manager
                    await cache_manager.stop()
                    self.logger.info("Cache manager stopped")
                except Exception as e:
                    self.logger.warning(f"Error stopping cache manager: {e}")

                # 4. Shutdown search service (saves history, releases indexes)
                try:
                    from dlss_updater.search_service import search_service
                    await search_service.shutdown()
                    self.logger.info("Search service shutdown complete")
                except Exception as e:
                    self.logger.warning(f"Error shutting down search service: {e}")

                # 5. Close HTTP session
                try:
                    from dlss_updater.dll_repository import close_http_session
                    await close_http_session()
                    self.logger.info("HTTP session closed")
                except Exception as e:
                    self.logger.warning(f"Error closing HTTP session: {e}")

                # 6. Close database connections
                try:
                    from dlss_updater.database import db_manager
                    await db_manager.close()
                    self.logger.info("Database connections closed")
                except Exception as e:
                    self.logger.warning(f"Error closing database: {e}")

                # 7. Shutdown thread pool executors (prevents orphaned threads)
                try:
                    from dlss_updater.updater import shutdown_version_executor
                    import asyncio
                    await asyncio.to_thread(shutdown_version_executor)
                    self.logger.info("Thread pool executors shutdown")
                except Exception as e:
                    self.logger.warning(f"Error shutting down executors: {e}")

                # 8. Cleanup logger panel handler (remove Flet handler reference)
                try:
                    if hasattr(self, 'logger_panel') and self.logger_panel:
                        self.logger_panel.cleanup()
                        self.logger.info("Logger panel handler cleaned up")
                except Exception as e:
                    self.logger.warning(f"Error cleaning up logger panel: {e}")

            self.logger.info("Application shutdown complete")

            # 9. Shutdown logging LAST (after all logging is done)
            try:
                from dlss_updater.logger import shutdown_logging
                shutdown_logging()
            except Exception:
                pass  # Can't log errors after logging shutdown

        except asyncio.TimeoutError:
            self.logger.error(f"Shutdown timed out after {SHUTDOWN_TIMEOUT}s")
            # Don't call sys.exit - let main.py's window.destroy() handle termination
        except Exception as e:
            self.logger.error(f"Shutdown error: {e}")
            # Don't call sys.exit - let main.py's window.destroy() handle termination

    def get_dll_cache_snackbar(self) -> DLLCacheProgressSnackbar:
        """Get the DLL cache progress snackbar for external use"""
        return self.dll_cache_snackbar
