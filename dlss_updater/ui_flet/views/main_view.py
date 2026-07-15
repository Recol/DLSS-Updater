"""
Main View for DLSS Updater Flet UI
Async-based Material Design interface with hub-based navigation
"""

import asyncio
import logging
import os
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable, Any

import anyio
import flet as ft

from dlss_updater.concurrency_limiters import thread_io


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
from dlss_updater.ui_flet.theme.colors import Shadows, MD3Colors, TabColors
from dlss_updater.ui_flet.components.hero_surface import (
    build_brand_wash,
    build_pill,
    build_watermark_icon,
    themed_accent,
)
from dlss_updater.ui_flet.dialogs.update_summary_dialog import UpdateSummaryDialog
from dlss_updater.ui_flet.components.slide_panel import PanelManager
from dlss_updater.ui_flet.panels import PreferencesPanel, ReleaseNotesPanel, BlacklistPanel, UIPreferencesPanel, ProtonUpscalerPanel, WindowsDLSSPresetsPanel, IgnoreListPanel
from dlss_updater.ui_flet.dialogs.app_update_dialog import AppUpdateDialog
from dlss_updater.ui_flet.dialogs.dlss_overlay_dialog import DLSSOverlayDialog
from dlss_updater.ui_flet.async_updater import AsyncUpdateCoordinator, UpdateProgress
from dlss_updater.platform_utils import FEATURES, IS_LINUX, IS_WINDOWS
from dlss_updater.linux_paths import is_flatpak, get_flatpak_override_command
from dlss_updater.ui_flet.views.games_view import GamesView
from dlss_updater.ui_flet.views.backups_view import BackupsView
from dlss_updater.ui_flet.views.hub_view import HubView
from dlss_updater.ui_flet.views.settings_view import SettingsView
from dlss_updater.ui_flet.navigation.navigation_controller import NavigationController
from dlss_updater.ui_flet.components.dll_cache_snackbar import DLLCacheProgressSnackbar
from dlss_updater.ui_flet.components.app_bar_menus import (
    CommunityMenu, create_app_bar_menus
)
from dlss_updater.version import __version__
from dlss_updater.utils import find_game_root


# Chrome brand-wash opacity for the app bar / view header bands — deliberately
# much lower than content-level hero washes (WASH_OPACITY_DARK/LIGHT in
# hero_surface.py) so the surrounding app chrome reads as tinted, not
# postered. Shared by the app bar and the Launchers view header.
_CHROME_WASH_OPACITY_DARK = 0.10
_CHROME_WASH_OPACITY_LIGHT = 0.06


class MainView(ft.Column):
    """
    Main application view with hub-based navigation and launcher management
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        super().__init__()
        # Note: In Flet 0.80.4+, self._page_ref is a read-only property set when added to page
        # Store reference as _page_ref for use during initialization
        self._page_ref = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        # Note: In Flet 0.85, FilePicker is a Service (not an overlay control)
        # and is instantiated inline at the point of use — see the handler in
        # _create_add_subfolder_handler. The Service base class auto-attaches
        # to the active page so the underlying Win32 IFileOpenDialog is owned
        # by the Flet window (valid hwndOwner), which keeps Windows 11's
        # per-app HDR auto-arbitration from disabling HDR (Issue #216).
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
        self.application_menu = None  # Removed - Check for Updates moved to Settings

        # Discord banner
        self.discord_banner: ft.Banner | None = None

        # Scan state management - store last scan results for update operations
        self.last_scan_results: dict | None = None
        self.last_scan_timestamp: str | None = None
        self.scan_cache_path = Path(get_config_path()).parent / "scan_cache.json"

        # View instances (will be created in _build_ui)
        self.launchers_view = None
        self.games_view = None
        self.backups_view = None
        self.settings_view = None
        self.hub_view = None
        self.navigation_controller: NavigationController | None = None

        # Views whose content was rebuilt/re-themed while detached and is
        # therefore stale on the client (see CLAUDE.md's Flet 0.86
        # patch-drop note). Set on theme toggle to every view except the
        # currently-active one; each view's attach hook (_on_view_load /
        # _on_view_hidden's HUB branch) rebuilds fresh instances and clears
        # its own entry the next time it's visited.
        self._theme_stale_views: set[str] = set()

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

        # Note: loading_overlay is added/removed dynamically in show()/hide()

        # Create DLL cache progress notification and add wrapper to overlay
        self.dll_cache_snackbar = DLLCacheProgressSnackbar(self._page_ref)
        self._page_ref.overlay.append(self.dll_cache_snackbar.get_wrapper())

        # Load cached scan results if available
        await self._load_scan_cache()

        # Build UI components
        await self._build_ui()

        # Register keyboard handler for Escape navigation
        self._page_ref.on_keyboard_event = self._on_keyboard_event

        # Show Discord invite banner if not dismissed
        if not config_manager.get_discord_banner_dismissed():
            self.discord_banner = self._create_discord_banner()
            self._page_ref.show_dialog(self.discord_banner)
            self.logger.info("Discord invite banner displayed")

        # Load hub stats in background
        if self.hub_view:
            from dlss_updater.task_registry import register_task
            register_task(asyncio.create_task(self.hub_view.load_stats()), "load_hub_stats")

        self.logger.info("Main view initialized")

    async def _build_ui(self):
        """Build the main UI structure with hub-based navigation"""
        # Create app bar
        app_bar = await self._create_app_bar()

        # Create launcher view (with existing cards and action buttons)
        self.launchers_view = await self._create_launchers_view()

        # Create games view
        self.games_view = GamesView(self._page_ref, self.logger)

        # Create backups view
        self.backups_view = BackupsView(self._page_ref, self.logger)

        # Create settings view
        self.settings_view = SettingsView(
            page=self._page_ref,
            logger=self.logger,
            on_open_preferences=self._on_settings_clicked,
            on_open_ui_preferences=self._on_ui_preferences_clicked,
            on_open_blacklist=self._on_blacklist_clicked,
            on_open_ignore_list=self._on_ignore_list_clicked,
            on_open_dlss_overlay=self._on_dlss_overlay_clicked,
            on_open_dlss_sr_presets=self._on_dlss_sr_presets_clicked,
            on_toggle_theme=self._toggle_theme_from_menu,
            on_check_updates=self._on_check_updates_clicked,
        )

        # Create hub view
        self.hub_view = HubView(
            page=self._page_ref,
            logger=self.logger,
            on_navigate=self._on_hub_navigate,
            on_open_dlss_settings=self._on_dlss_settings_clicked,
        )

        # Create navigation controller (replaces tab bar)
        self.navigation_controller = NavigationController(
            page=self._page_ref,
            logger=self.logger,
            hub_view=self.hub_view,
            views={
                NavigationController.LAUNCHERS: self.launchers_view,
                NavigationController.GAMES: self.games_view,
                NavigationController.BACKUPS: self.backups_view,
                NavigationController.SETTINGS: self.settings_view,
            },
            on_view_load=self._on_view_load,
            on_view_hidden=self._on_view_hidden,
        )

        # Create logger panel
        self.logger_panel = LoggerPanel(self._page_ref, self.logger)

        # App bar lives inside a permanent wrapper Column ("slot"). Theme
        # heals swap a FRESH bar into the slot (slot.controls = [new_bar] +
        # slot.update()) - the same wrapper-swap mechanism the nav
        # controller uses for view heals, and the only operation class the
        # client reliably renders after a theme toggle (in-place property
        # mutation and positional same-class swaps both fail; see CLAUDE.md).
        self._app_bar_slot = ft.Column(controls=[app_bar], spacing=0)

        # Assemble main layout: AppBar + Divider + NavigationController + Logger
        self.controls = [
            self._app_bar_slot,
            ft.Container(
                height=1,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment.CENTER_LEFT,
                    end=ft.Alignment.CENTER_RIGHT,
                    colors=["transparent", "#5A5A5A", "transparent"],
                ),
            ),
            self.navigation_controller,
            self.logger_panel,
        ]

    async def _on_hub_navigate(self, view_name: str):
        """Handle hub card click navigation."""
        if self.navigation_controller:
            await self.navigation_controller.navigate_to(view_name)

    async def _on_view_load(self, view_name: str):
        """Handle view loading when navigated to."""
        from dlss_updater.task_registry import register_task

        theme_stale = view_name in self._theme_stale_views
        if theme_stale:
            self._theme_stale_views.discard(view_name)

        # App bar heal rides the first navigation after a theme toggle
        # (patches sent earlier are dropped - see _toggle_theme_from_menu).
        # Deferred to a background task: updates issued DURING the nav
        # transition's callback phase don't flush reliably.
        if getattr(self, '_app_bar_theme_stale', False):
            self._app_bar_theme_stale = False

            async def _heal_bar():
                await anyio.sleep(0.1)
                await self._rebuild_app_bar_for_theme()

            register_task(asyncio.create_task(_heal_bar()), "heal_app_bar")

        if view_name == NavigationController.GAMES:
            if not self.games_view._games_loaded or theme_stale:
                self.games_view.loading_indicator.visible = True
                self.games_view.empty_state.visible = False
                self.games_view.tabs_container.visible = False
                self.games_view.update()  # Targeted update (GamesView is isolated)
                register_task(
                    asyncio.create_task(self._load_games_background(force=theme_stale)),
                    "load_games_background"
                )

        elif view_name == NavigationController.BACKUPS:
            # BackupsView.load_backups() already gates on its own
            # _backups_loaded flag internally (full load when unloaded/stale,
            # a fast staggered re-animate when already loaded, reusing the
            # SAME BackupGroup instances). A theme toggle that fired while
            # this view was detached needs fresh instances (see CLAUDE.md's
            # Flet 0.86 patch-drop note), so force the full-rebuild path by
            # marking it unloaded rather than calling force=True directly -
            # this keeps the "already loaded" fast path as the default for
            # ordinary tab switches.
            if theme_stale:
                self.backups_view._backups_loaded = False
            register_task(
                asyncio.create_task(self._load_backups_background()),
                "load_backups_background"
            )

        elif view_name == NavigationController.LAUNCHERS:
            if theme_stale:
                await self._rebuild_launchers_view_for_theme()

    async def _on_view_hidden(self, old_view: str, new_view: str):
        """Handle view cleanup when navigated away."""
        # Returning to the hub: refresh its stat pills (game count, backups,
        # scan age) and Games mosaic so restores/updates/scans performed in
        # other views are reflected without an app restart. Cheap: the same
        # hyper-parallel batch used at startup (~4ms of queries).
        if new_view == NavigationController.HUB and self.hub_view:
            from dlss_updater.task_registry import register_task

            hub_theme_stale = NavigationController.HUB in self._theme_stale_views
            if hub_theme_stale:
                self._theme_stale_views.discard(NavigationController.HUB)

            # Bar heal also rides hub returns (nav skips _on_view_load for
            # the hub, so this hook covers the settings -> hub path).
            # Deferred like the _on_view_load variant.
            hub_bar_stale = getattr(self, '_app_bar_theme_stale', False)
            if hub_bar_stale:
                self._app_bar_theme_stale = False

                async def _heal_bar_hub():
                    await anyio.sleep(0.1)
                    await self._rebuild_app_bar_for_theme()

                register_task(asyncio.create_task(_heal_bar_hub()), "heal_app_bar")

            async def _refresh_hub():
                # Theme-stale heal: replace the ENTIRE HubView instance via
                # the nav controller's wrapper (a never-detached parent).
                # Swapping children INSIDE the remounted hub_view (the old
                # rebuild_for_theme approach) is dropped by the client just
                # like in-place mutations - verified live. The launchers
                # heal uses this same wrapper-level replace_view mechanism
                # and renders correctly.
                if hub_theme_stale:
                    old_hub = self.hub_view
                    for card in (
                        old_hub._launchers_card, old_hub._dlss_settings_card,
                        old_hub._backups_card, old_hub._settings_card,
                        old_hub._games_card,
                    ):
                        if card is not None:
                            card._unregister_theme_aware()
                    old_hub._unregister_theme_aware()

                    self.hub_view = HubView(
                        page=self._page_ref,
                        logger=self.logger,
                        on_navigate=self._on_hub_navigate,
                        on_open_dlss_settings=self._on_dlss_settings_clicked,
                    )
                    # Unique key so Flet's list differ treats this as a
                    # keyed ADD (full serialization) instead of a same-class
                    # positional MERGE against the stale cached child -
                    # merge-diff property patches are exactly what the
                    # client drops (see CLAUDE.md).
                    import uuid as _uuid
                    self.hub_view.key = f"hub-{_uuid.uuid4().hex[:8]}"
                    self.navigation_controller.replace_view(
                        NavigationController.HUB, self.hub_view
                    )
                await self.hub_view.load_stats()

            register_task(
                asyncio.create_task(_refresh_hub()),
                "refresh_hub_stats"
            )

        if old_view == NavigationController.GAMES and new_view != NavigationController.GAMES:
            from dlss_updater.task_registry import register_task
            register_task(
                asyncio.create_task(self._cleanup_games_view()),
                "cleanup_games_view"
            )

            # Per-game DLL restores can happen from within the Games view
            # (card menu -> restore/restore group), which create/consume
            # backup rows without notifying BackupsView. Mark it stale here
            # so the next visit reloads fresh instead of showing stale
            # groups. Cheap: just resets the existing loaded flag that
            # load_backups() already checks - no new lifecycle method.
            if self.backups_view:
                self.backups_view._backups_loaded = False

    def _on_keyboard_event(self, e: ft.KeyboardEvent):
        """Handle keyboard events for navigation."""
        if self.navigation_controller:
            self.navigation_controller.handle_keyboard(e)

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
            padding=ft.Padding.symmetric(horizontal=20, vertical=12),
            border_radius=12,
            bgcolor=MD3Colors.get_surface_container(is_dark),
            border=ft.Border.all(1, MD3Colors.get_outline(is_dark)),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            on_click=on_click,
        )

        # Add hover effect — read theme live so it stays correct after toggles
        def on_hover(e):
            current_dark = self.theme_manager.is_dark
            if e.data == "true":
                button_container.bgcolor = f"{color}15"
                button_container.border = ft.Border.all(1, f"{color}30")
            else:
                button_container.bgcolor = MD3Colors.get_surface_container(current_dark)
                button_container.border = ft.Border.all(1, MD3Colors.get_outline(current_dark))
            if self._page_ref:
                button_container.update()

        button_container.on_hover = on_hover

        # Register the themeable parts so theme toggles recolor them live
        if not hasattr(self, "_action_button_refs"):
            self._action_button_refs = []
        self._action_button_refs.append((button_container, label_text))

        return button_container

    async def _create_launchers_view(self):
        """Create the launchers view with cards and action buttons"""
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        is_dark = self.theme_manager.is_dark

        # Reset action-button refs (rebuilt below) so theme toggles only touch live buttons
        self._action_button_refs = []

        # Create launcher cards
        launcher_list = await self._create_launcher_list()

        # Header band: title + LAUNCHERS brand wash + rocket watermark + a
        # neutral "N configured" pill — mirrors the header treatment other
        # views already carry (see games_view.py's header) so Launchers
        # doesn't start "bare" straight into the grid. Kept shallow (~72px).
        self.launchers_header = self._build_launchers_header(is_dark)

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
            padding=ft.Padding.all(16),
            bgcolor=MD3Colors.get_background(is_dark),
            border=ft.Border.only(top=ft.BorderSide(1, MD3Colors.get_outline(is_dark))),
        )

        # Create launchers content area container with themed background
        # Store reference for theme updates
        self.launchers_content_container = ft.Container(
            content=ft.Column(
                controls=[
                    self.launchers_header,
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

    def _build_launchers_header(self, is_dark: bool) -> ft.Container:
        """Build the Launchers view header band.

        Title + LAUNCHERS-accent brand wash + small rocket watermark + a
        neutral "N configured" pill, matching the hero header language used
        by the Games view. The configured count is derived from
        ``self.launcher_cards`` (already-live state — no extra query).
        """
        header_accent = themed_accent(
            (TabColors.LAUNCHERS, TabColors.LAUNCHERS_LIGHT), is_dark
        )

        self._launchers_header_title = ft.Text(
            "Launchers",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_on_surface(is_dark),
        )

        configured_count = sum(1 for c in self.launcher_cards.values() if c.current_paths)
        self.launchers_configured_pill = build_pill(
            f"{configured_count} configured",
            bgcolor=MD3Colors.get_surface_container(is_dark),
            text_color=MD3Colors.get_on_surface_variant(is_dark),
        )

        self._launchers_watermark = build_watermark_icon(
            ft.Icons.ROCKET_LAUNCH, is_dark, size=64
        )
        # Small negative offset so the glyph "bleeds" slightly off the
        # header's bottom-right edge, matching the hero-card watermark
        # convention (see hub_card.py) instead of sitting fully inset.
        self._launchers_watermark.right = -6
        self._launchers_watermark.bottom = -10

        return ft.Container(
            content=ft.Stack(
                controls=[
                    self._launchers_watermark,
                    ft.Row(
                        controls=[
                            self._launchers_header_title,
                            ft.Container(expand=True),
                            self.launchers_configured_pill,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ]
            ),
            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
            bgcolor=MD3Colors.get_surface(is_dark),
            gradient=build_brand_wash(
                header_accent,
                is_dark,
                opacity=_CHROME_WASH_OPACITY_DARK if is_dark else _CHROME_WASH_OPACITY_LIGHT,
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

    def _update_launchers_configured_pill(self) -> None:
        """Refresh the launchers header's "N configured" pill: recompute the
        count from current launcher card state and restyle for the active
        theme. Called after any path add/remove/reset so the header never
        goes stale, and again on theme toggle.
        """
        if not hasattr(self, 'launchers_configured_pill') or not self.launchers_configured_pill:
            return
        is_dark = self.theme_manager.is_dark
        count = sum(1 for c in self.launcher_cards.values() if c.current_paths)
        # build_pill() wraps its Text in a Row([Text]) — reach the Text ref.
        text_widget = self.launchers_configured_pill.content.controls[-1]
        text_widget.value = f"{count} configured"
        text_widget.color = MD3Colors.get_on_surface_variant(is_dark)
        self.launchers_configured_pill.bgcolor = MD3Colors.get_surface_container(is_dark)

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
            "support": lambda _: open_url("https://buymeacoffee.com/decouk"),
            "bug_report": lambda _: open_url("https://github.com/Recol/DLSS-Updater/issues"),
            "twitter": lambda _: open_url("https://x.com/iDeco_UK"),
            "discord": lambda _: open_url("https://discord.com/users/162568099839606784"),
            "discord_invite": self._on_show_discord_invite_clicked,
            "release_notes": self._on_release_notes_clicked,
        }

        # Create community popup menu
        self.community_menu, _ = create_app_bar_menus(
            page=self._page_ref,
            is_dark=is_dark,
            callbacks=menu_callbacks,
        )

        # App title + version pill — store refs so theme toggles recolor them live
        self._app_title_text = ft.Text(
            "DLSS Updater",
            size=24,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_on_surface(is_dark),
        )
        self._app_version_pill = self._build_version_pill(is_dark)

        # Title row: name + compact neutral version pill side-by-side
        self._app_title_row = ft.Row(
            controls=[self._app_title_text, self._app_version_pill],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )

        # Compact top bar with 3 popup menu buttons on the right
        top_bar = ft.Row(
            controls=[
                self._app_title_row,
                # Right side: Community (Heart)
                self.community_menu.button,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # Return app bar with dark surface style (full width, no border)
        # Store reference for theme updates
        # PERF: Merged double Container into single (-1 control)
        # Subtle PRIMARY brand wash on top of the base surface — quieter than
        # any content-level hero wash, just enough to tie the chrome into the
        # hero design language (see _CHROME_WASH_OPACITY_DARK/LIGHT).
        self.app_bar_container = ft.Container(
            content=top_bar,
            padding=ft.Padding.symmetric(vertical=16, horizontal=16),
            bgcolor=MD3Colors.get_background(is_dark),
            gradient=build_brand_wash(
                MD3Colors.PRIMARY,
                is_dark,
                opacity=_CHROME_WASH_OPACITY_DARK if is_dark else _CHROME_WASH_OPACITY_LIGHT,
            ),
            shadow=Shadows.LEVEL_2,
        )
        return self.app_bar_container

    def _build_version_pill(self, is_dark: bool) -> ft.Container:
        """Build the compact, surface-tinted version pill shown in the app bar."""
        return build_pill(
            f"v{__version__}",
            bgcolor=MD3Colors.get_surface_container(is_dark),
            text_color=MD3Colors.get_on_surface_variant(is_dark),
        )

    async def _toggle_theme_from_menu(self, e):
        """Handle theme toggle from menu with cascade animation.

        Theme toggling is only ever wired from the Settings view's inline
        Switch (SettingsView -> on_toggle_theme -> here), so SETTINGS is
        always the active view at this point - it re-themes correctly live
        (attached + ThemeAwareMixin cascade). Every OTHER view - and the
        app bar, which is MainView's own chrome and needs a different fix -
        is handled below.
        """
        # Use async toggle for cascade animations to registered components
        # (ThemeAwareMixin components that are currently ATTACHED - e.g. the
        # Settings view itself, the floating pill, the logger panel - all
        # re-theme correctly through this cascade).
        await self.theme_manager.toggle_theme_async()

        # Every view except the one currently active during this toggle is
        # now theme-stale: Flet 0.86 silently drops property-only patches
        # sent to controls in a detached subtree (see CLAUDE.md), so those
        # views' cascade updates above never reached the client. Each is
        # rebuilt with fresh control instances on its next attach instead
        # (see _on_view_load / _on_view_hidden's HUB branch).
        active_view = (
            self.navigation_controller.current_view
            if self.navigation_controller else None
        )
        all_views = {
            NavigationController.HUB,
            NavigationController.LAUNCHERS,
            NavigationController.GAMES,
            NavigationController.BACKUPS,
            NavigationController.SETTINGS,
        }
        self._theme_stale_views |= (all_views - {active_view})

        # SINGLE batched page.update() for all theme changes
        if self._page_ref:
            self._page_ref.update()

        # App bar: patches flushed between the theme cascade and the NEXT
        # NAVIGATION are lost client-side (the cascade's patches to
        # detached-view controls stall the pipe; a nav transition's
        # page.update() is the observed recovery point - verified live:
        # neither container swaps nor property mutations on the bar render
        # in this window, while identical operations render fine right
        # after a navigation). Mark the bar stale; the next attach hook
        # heals it alongside the views.
        self._app_bar_theme_stale = True

        self.logger.info(f"Theme toggled to {'Dark' if self.theme_manager.is_dark else 'Light'} Mode")

    async def _rebuild_app_bar_for_theme(self) -> None:
        """Rebuild the app bar as a brand-new Container and swap it into
        MainView.controls.

        The app bar is always attached, but in-place property mutation
        performed from within the toggle flow doesn't reliably reach the
        client under Flet 0.86 (see CLAUDE.md) - only replacing the control
        does. _create_app_bar() is idempotent (it reassigns
        self.app_bar_container / self.community_menu / the title elements
        each time it's called), so simply calling it again and swapping the
        result into self.controls[0] gives a fully fresh, theme-correct bar.
        """
        if self.community_menu is not None:
            # Explicit unregister rather than relying on WeakSet GC timing -
            # _create_app_bar() below constructs a brand-new CommunityMenu.
            self.community_menu._unregister_theme_aware()

        new_app_bar = await self._create_app_bar()

        # Wrapper-slot swap: the ONLY operation class the client reliably
        # renders after a theme toggle (verified live; property mutation and
        # positional same-class swaps into MainView.controls both fail).
        if hasattr(self, '_app_bar_slot') and self._app_bar_slot:
            try:
                # Two-phase detach/attach across separate flushes - the
                # exact sequence the nav controller uses (which provably
                # renders). A single-flush swap merge-diffs same-class
                # children and the client drops it.
                self._app_bar_slot.controls = []
                self._page_ref.update()
                await anyio.sleep(0.03)
                self._app_bar_slot.controls = [new_app_bar]
                self._page_ref.update()
            except Exception as e:
                self.logger.warning(f"App bar theme heal flush failed: {e}")

    async def _rebuild_launchers_view_for_theme(self) -> None:
        """Rebuild the launchers view (cards + header + action bar) with
        fresh, theme-correct instances.

        Flet 0.86 silently drops property-only patches sent to controls in
        a detached subtree (see CLAUDE.md); LauncherCard's in-place
        apply_theme() mutations made while this view was detached therefore
        never reached the client, and the header/action-bar chrome (plain
        Containers, not ThemeAware) was never even attempted. Freshly
        constructed/replaced controls always render correctly, so this
        rebuilds the whole launchers subtree instead of healing it in place.

        Called from _on_view_load() right after LAUNCHERS has been attached
        by the nav controller, so navigation_controller.replace_view() below
        can flush the swap immediately.
        """
        # Capture per-launcher scan results (games_data) so the rebuild
        # doesn't wipe the "N games" count pills - this is the only place
        # that state lives (it isn't persisted anywhere outside the cards).
        old_games_data = {
            enum: card.games_data for enum, card in self.launcher_cards.items()
        }

        # Unregister the old cards from the theme registry before dropping
        # the only strong references to them.
        for card in self.launcher_cards.values():
            card._unregister_theme_aware()

        # Rebuild cards + header + action bar + content container from
        # scratch (also repopulates self.launcher_cards).
        new_view = await self._create_launchers_view()
        self.launchers_view = new_view

        # Restore the captured scan-result state onto the fresh instances.
        for enum, card in self.launcher_cards.items():
            data = old_games_data.get(enum)
            if data:
                await card.set_games(data)

        # Swap the fresh container into the nav controller's view registry -
        # LAUNCHERS is the currently-attached view at this point, so this
        # also flushes the swap to the client immediately.
        if self.navigation_controller:
            self.navigation_controller.replace_view(
                NavigationController.LAUNCHERS, new_view
            )

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
                on_path_removed=self._on_launcher_path_removed,
            )

            # Load existing paths from config (multi-path support)
            current_paths = config_manager.get_launcher_paths(config["enum"])
            if current_paths:
                await card.set_paths(current_paths)

            self.launcher_cards[config["enum"]] = card
            launcher_cards.append(card)

        # Banner-card grid (Option B): 1-up narrow, 2-up medium, 3-up wide —
        # matching the games grid's density rhythm. Every card has the same
        # fixed banner + footer height (see launcher_card.py), so equal-height
        # rows fall out automatically with no extra layout work here.
        for card in launcher_cards:
            card.col = {"xs": 12, "sm": 6, "xl": 4}

        launcher_grid = ft.ResponsiveRow(
            controls=launcher_cards,
            spacing=12,
            run_spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        # Scrollable wrapper around the responsive grid
        launcher_column = ft.Column(
            controls=[launcher_grid],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # Create responsive container that centers on large screens
        # Using ResponsiveRow to handle different window sizes
        responsive_content = ft.Column(
            controls=[launcher_column],
            col={"xs": 12, "sm": 12, "md": 11, "lg": 11, "xl": 10},
            expand=True,
        )

        return ft.Container(
            content=ft.ResponsiveRow(
                controls=[responsive_content],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=ft.Padding.all(16),
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
                path = await ft.FilePicker().get_directory_path(dialog_title="Add Sub-Folder")
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
            raw = path_input.value.strip()
            if not raw:
                path_input.error_text = "Please enter a path"
                self._page_ref.update()
                return

            # Expand ~ and environment variables so paths like ~/.steam work (Issue #228)
            path = os.path.expanduser(os.path.expandvars(raw))

            # Validate the path exists and is accessible
            if Path(path).is_dir():
                self._page_ref.pop_dialog()
                # Add the path to the launcher (config also normalizes)
                added = config_manager.add_launcher_path(launcher, path)
                if added:
                    all_paths = config_manager.get_launcher_paths(launcher)
                    card = self.launcher_cards.get(launcher)
                    if card:
                        await card.set_paths(all_paths)
                    self._update_launchers_configured_pill()
                    await self._show_snackbar(f"Path added: {path}")
                else:
                    await self._show_snackbar("Path already exists or limit reached")
            elif is_flatpak():
                # Inside the Flatpak sandbox a real folder outside the granted
                # filesystem looks like it "doesn't exist". Guide the user to
                # grant access rather than showing a misleading error (Issue #228).
                self._page_ref.pop_dialog()
                await self._show_flatpak_permission_dialog(path)
            else:
                path_input.error_text = "Directory does not exist"
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

    async def _load_games_background(self, force: bool = False):
        """Load games in background - non-blocking for tab switch."""
        try:
            await self.games_view.load_games(force=force)
        except Exception as e:
            self.logger.error(f"Background games load error: {e}")

    async def _load_backups_background(self):
        """Load backups in background - non-blocking for tab switch."""
        try:
            await self.backups_view.load_backups()
        except Exception as e:
            self.logger.error(f"Background backups load error: {e}")

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
                self._update_launchers_configured_pill()
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
            self._update_launchers_configured_pill()
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
                self._update_launchers_configured_pill()
                await self._show_rescan_snackbar(f"All paths cleared for {card.name}")

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
            try:
                await ft.Clipboard().set(override_cmd)
                await self._show_snackbar("Command copied to clipboard")
            except Exception as ex:
                self.logger.warning(f"Clipboard operation failed: {ex}")
                await self._show_snackbar("Failed to copy to clipboard")

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

    async def _on_ignore_list_clicked(self, e):
        """Handle ignore list button click"""
        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        panel = IgnoreListPanel(
            self._page_ref, self.logger,
            on_ignore_changed=self._on_ignore_changed_from_panel,
        )
        await panel_manager.show_content(panel)

    def _on_ignore_changed_from_panel(self, game_id: int, ignored: bool):
        """Sync ignore state from Settings panel to GamesView cards."""
        if not self.games_view or not self.games_view._games_loaded:
            return

        # Update the GamesView's tracking set
        if ignored:
            self.games_view._ignored_game_ids.add(game_id)
        else:
            self.games_view._ignored_game_ids.discard(game_id)

        # Find the card — may be keyed by primary_game.id for merged games
        card = self.games_view.game_cards.get(game_id)
        if not card:
            # Check if game_id belongs to a merged game card
            for c in self.games_view.game_cards.values():
                if c.merged_game and game_id in c.merged_game.all_game_ids:
                    card = c
                    break

        if card:
            # For merged games, check if ANY of its IDs are ignored
            if card.merged_game:
                is_ignored = bool(set(card.merged_game.all_game_ids) & self.games_view._ignored_game_ids)
            else:
                is_ignored = ignored
            card.set_ignored(is_ignored)
            self.games_view._apply_visibility()
            self.games_view.update()

    async def _on_dlss_overlay_clicked(self, e):
        """Handle DLSS overlay settings button click"""
        dialog = DLSSOverlayDialog(self._page_ref, self.logger)
        await dialog.show()

    async def _on_dlss_sr_presets_clicked(self, e):
        """Handle Proton upscalers settings button click (Linux)"""
        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        panel = ProtonUpscalerPanel(self._page_ref, self.logger)
        await panel_manager.show_content(panel)

    async def _on_dlss_settings_clicked(self, e=None):
        """Handle Windows DLSS Settings (global SR preset) button click"""
        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        panel = WindowsDLSSPresetsPanel(self._page_ref, self.logger)
        await panel_manager.show_content(panel)

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

            # Refresh game card DLL badges if games view is loaded; otherwise
            # flag it to reconcile from disk the next time it loads. The
            # high-performance update path writes new DLL files without
            # updating GameDLL.version in the DB, so a not-yet-loaded Games
            # view would otherwise show stale badges until manually refreshed.
            if self.games_view:
                if self.games_view._games_loaded:
                    await self.games_view.refresh_all_badges()
                else:
                    self.games_view.mark_pending_dll_reconcile()

            # DLL updates create new backups - mark BackupsView stale so its
            # next visit reloads fresh instead of showing a pre-update list.
            if self.backups_view:
                self.backups_view._backups_loaded = False

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

    # Scans older than this many days get a "rescan recommended" prompt
    STALE_SCAN_DAYS = 7

    def _update_scan_info_text(self):
        """Update the last scan info text displayed in UI.

        Stale scans (older than STALE_SCAN_DAYS) and missing scans render in
        the warning color with a rescan prompt, instead of passive grey text.
        """
        from dlss_updater.ui_flet.theme.colors import MD3Colors

        if not hasattr(self, 'last_scan_info_text'):
            return

        is_dark = self.theme_manager.is_dark

        if not self.last_scan_results or not self.last_scan_timestamp:
            self._scan_is_stale = True
            self.last_scan_info_text.value = "No scan performed yet — run a scan to find games"
            self.last_scan_info_text.color = MD3Colors.get_warning(is_dark)
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

            self._scan_is_stale = hours_ago >= self.STALE_SCAN_DAYS * 24
            if self._scan_is_stale:
                self.last_scan_info_text.value = f"Last scan: {time_str} — rescan recommended"
                self.last_scan_info_text.color = MD3Colors.get_warning(is_dark)
            else:
                self.last_scan_info_text.value = f"Last scan: {time_str}"
                self.last_scan_info_text.color = MD3Colors.get_on_surface_variant(is_dark)
        except Exception as e:
            self.logger.warning(f"Failed to format scan info: {e}")
            self._scan_is_stale = False
            self.last_scan_info_text.value = "Scan data available"
            self.last_scan_info_text.color = MD3Colors.get_on_surface_variant(is_dark)

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
            cache_data = await anyio.to_thread.run_sync(_read_cache_file, limiter=thread_io)
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
            await anyio.to_thread.run_sync(
                _write_cache_file, self.scan_cache_path, cache_data, limiter=thread_io
            )
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

    async def _show_rescan_snackbar(self, message: str):
        """Show a snackbar informing the user to rescan."""
        await self._show_snackbar(
            f"{message} — rescan to update the game list",
            duration=4000,
        )

    async def _on_launcher_path_removed(
        self,
        launcher_name: str,
        launcher_enum: LauncherPathName | None = None,
        path: str | None = None,
    ):
        """Callback when a path is removed from a launcher card.

        Shows a snackbar with an Undo action that re-adds the removed path.
        """
        # The card already mutated its own current_paths before this callback
        # fires — refresh the header's configured count now so it doesn't lag.
        self._update_launchers_configured_pill()

        if launcher_enum is None or path is None:
            await self._show_rescan_snackbar(f"Path removed from {launcher_name}")
            return

        async def on_undo(e):
            config_manager.add_launcher_path(launcher_enum, path)
            card = self.launcher_cards.get(launcher_enum)
            if card:
                await card.set_paths(config_manager.get_launcher_paths(launcher_enum))
            self._update_launchers_configured_pill()
            await self._show_snackbar(f"Path restored for {launcher_name}")

        from dlss_updater.ui_flet.theme.colors import MD3Colors
        is_dark = self.theme_manager.is_dark
        snackbar = ft.SnackBar(
            content=ft.Text(
                f"Path removed from {launcher_name} — rescan to update the game list",
                color=ft.Colors.WHITE,
            ),
            bgcolor=MD3Colors.get_themed("snackbar_bg", is_dark),
            duration=6000,
            persist=False,  # Auto-dismiss after the duration (default persists when action set)
            action=ft.SnackBarAction(
                label="Undo",
                text_color=MD3Colors.get_themed("snackbar_action", is_dark),
                on_click=on_undo,
            ),
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

    async def shutdown(self, progress_callback=None):
        """
        Graceful shutdown with timeout, comprehensive cleanup, and progress reporting.

        Args:
            progress_callback: Optional callback(step: int) to report shutdown progress.
                              Steps 1-9 correspond to different cleanup phases.
        """
        import sys

        async def report_progress(step: int):
            """Report progress to callback, handling errors gracefully."""
            if progress_callback:
                try:
                    progress_callback(step)
                except Exception:
                    pass  # Don't let callback errors block shutdown

        self.logger.info("Shutting down application...")
        SHUTDOWN_TIMEOUT = 5.0

        try:
            with anyio.fail_after(SHUTDOWN_TIMEOUT):
                # Step 1: Cancel all registered background tasks
                await report_progress(1)
                try:
                    from dlss_updater.task_registry import cancel_all_tasks
                    await cancel_all_tasks(timeout=3.0)
                except Exception as e:
                    self.logger.warning(f"Error cancelling background tasks: {e}")

                # Step 2: Shutdown games view (clears card references, theme registration)
                await report_progress(2)
                try:
                    if hasattr(self, 'games_view') and self.games_view:
                        await self.games_view.on_shutdown()
                        self.logger.info("Games view shutdown complete")
                except Exception as e:
                    self.logger.warning(f"Error shutting down games view: {e}")

                # Step 3: Stop cache manager (releases memory maps, stops cleanup loop)
                await report_progress(3)
                try:
                    from dlss_updater.cache_manager import cache_manager
                    await cache_manager.stop()
                    self.logger.info("Cache manager stopped")
                except Exception as e:
                    self.logger.warning(f"Error stopping cache manager: {e}")

                # Step 4: Shutdown search service (saves history, releases indexes)
                await report_progress(4)
                try:
                    from dlss_updater.search_service import search_service
                    await search_service.shutdown()
                    self.logger.info("Search service shutdown complete")
                except Exception as e:
                    self.logger.warning(f"Error shutting down search service: {e}")

                # Step 5: Close HTTP session
                await report_progress(5)
                try:
                    from dlss_updater.dll_repository import close_http_session
                    await close_http_session()
                    self.logger.info("HTTP session closed")
                except Exception as e:
                    self.logger.warning(f"Error closing HTTP session: {e}")

                # Step 6: Close database connections
                await report_progress(6)
                try:
                    from dlss_updater.database import db_manager
                    await db_manager.close()
                    self.logger.info("Database connections closed")
                except Exception as e:
                    self.logger.warning(f"Error closing database: {e}")

                # Step 7: Shutdown thread pool executors (prevents orphaned threads)
                await report_progress(7)
                try:
                    from dlss_updater.updater import shutdown_version_executor
                    await anyio.to_thread.run_sync(shutdown_version_executor, limiter=thread_io)
                    self.logger.info("Thread pool executors shutdown")
                except Exception as e:
                    self.logger.warning(f"Error shutting down executors: {e}")

                # Step 8: Cleanup logger panel handler (remove Flet handler reference)
                await report_progress(8)
                try:
                    if hasattr(self, 'logger_panel') and self.logger_panel:
                        self.logger_panel.cleanup()
                        self.logger.info("Logger panel handler cleaned up")
                except Exception as e:
                    self.logger.warning(f"Error cleaning up logger panel: {e}")

            # Step 9: Finalize
            await report_progress(9)
            self.logger.info("Application shutdown complete")

            # Shutdown logging LAST (after all logging is done)
            try:
                from dlss_updater.logger import shutdown_logging
                shutdown_logging()
            except Exception:
                pass  # Can't log errors after logging shutdown

        except TimeoutError:
            self.logger.error(f"Shutdown timed out after {SHUTDOWN_TIMEOUT}s")
            # Don't call sys.exit - let main.py's window.destroy() handle termination
        except Exception as e:
            self.logger.error(f"Shutdown error: {e}")
            # Don't call sys.exit - let main.py's window.destroy() handle termination

    def get_dll_cache_snackbar(self) -> DLLCacheProgressSnackbar:
        """Get the DLL cache progress snackbar for external use"""
        return self.dll_cache_snackbar
