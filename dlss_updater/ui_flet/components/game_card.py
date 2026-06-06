"""
Game Card Component
Individual game card with Steam image, DLL badges, and action buttons
"""

import asyncio
from pathlib import Path
from typing import Callable, Any
import flet as ft

from dlss_updater.database import GameDLL
from dlss_updater.models import Game, MergedGame
from dlss_updater.steam_integration import fetch_steam_image
from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows, TechnologyColors
from dlss_updater.ui_flet.theme.md3_system import MD3Spacing
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.constants import DLL_GROUPS


class GameCard(ThemeAwareMixin, ft.Card):
    """Individual game card with image, DLL info, and actions

    Note: Cannot use is_isolated=True because cards need batch updates via
    ImageLoadCoordinator which uses page.update(). Isolated controls would
    not be included in page.update() and would require individual card.update() calls.
    """

    def __init__(self, game: Game | MergedGame, dlls: list[GameDLL], page: ft.Page, logger, on_update=None, on_view_backups=None, on_restore=None, backup_groups: dict[str, list] | None = None, is_ignored: bool = False, on_ignore_toggle=None, on_resolve=None):
        super().__init__()

        # Handle both Game and MergedGame
        if isinstance(game, MergedGame):
            self.merged_game = game
            self.game = game.primary_game
            self.all_paths = game.all_paths
        else:
            self.merged_game = None
            self.game = game
            self.all_paths = [game.path]

        self.dlls = dlls
        self._page_ref = page
        self.logger = logger
        self.on_update_callback = on_update
        self.on_view_backups_callback = on_view_backups
        self.on_restore_callback = on_restore
        self.on_ignore_toggle_callback = on_ignore_toggle
        self.on_resolve_callback = on_resolve
        self.backup_groups = backup_groups or {}
        self.has_backups = bool(backup_groups)
        self.is_ignored = is_ignored

        # Button references for loading state
        self.update_button: ft.PopupMenuButton | None = None
        self.restore_button: ft.PopupMenuButton | None = None
        self.ignore_button: ft.IconButton | None = None
        self.is_updating = False

        # Reference to dll_badges for refresh
        self.dll_badges_container: ft.Container | None = None
        self.right_content: ft.Column | None = None

        # Async lock for UI updates to prevent race conditions
        self._ui_lock = asyncio.Lock()
        self._image_loaded = False  # Prevent duplicate image loads

        # Get theme state and register
        self._registry = get_theme_registry()
        self._theme_priority = 25  # Cards are mid-priority
        is_dark = self._registry.is_dark

        # Card styling optimized for grid layout
        self.elevation = 2
        self.surface_tint_color = MD3Colors.get_primary(is_dark)
        self.margin = ft.Margin.all(0)  # ResponsiveRow handles spacing
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

        # Register with theme system after building UI
        self._register_theme_aware()

    def _create_skeleton_loader(self):
        """Create GPU-accelerated animated shimmer skeleton loader for image placeholder.

        Uses Flet 0.80.4 ft.Shimmer control for smooth, GPU-accelerated animation.
        This is lighter weight than custom gradient animations and provides
        consistent visual feedback while images load.
        """
        is_dark = self._registry.is_dark

        # Inner placeholder container with game icon
        placeholder_content = ft.Container(
            width=140,  # Match image container width
            height=140,  # Match image height
            bgcolor=MD3Colors.get_themed("skeleton_base", is_dark),
            border_radius=8,
            content=ft.Icon(
                ft.Icons.VIDEOGAME_ASSET,
                size=48,
                color=ft.Colors.with_opacity(0.3, ft.Colors.GREY),
            ),
            alignment=ft.Alignment.CENTER,
        )

        # Wrap in Shimmer for GPU-accelerated animation
        return ft.Shimmer(
            base_color=MD3Colors.get_themed("skeleton_start", is_dark),
            highlight_color=MD3Colors.get_themed("skeleton_highlight", is_dark),
            period=1200,  # 1.2 second animation cycle
            direction=ft.ShimmerDirection.LTR,  # Left-to-right sweep
            content=placeholder_content,
        )

    def _get_breakpoint(self) -> str:
        """Determine current breakpoint from page width"""
        if not self._page_ref or not self._page_ref.width:
            return "lg"
        width = self._page_ref.width
        if width < 576:
            return "xs"
        elif width < 768:
            return "sm"
        elif width < 992:
            return "md"
        return "lg"

    def _check_for_updates(self) -> bool:
        """Check if any DLLs have updates available"""
        outdated, _, _ = self._get_update_counts()
        return outdated > 0

    def _get_update_counts(self) -> tuple[int, int, int]:
        """Count outdated, current, and unknown DLLs.

        Returns:
            Tuple of (outdated_count, current_count, unknown_count)
        """
        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        outdated = 0
        current = 0
        unknown = 0

        for dll in self.dlls:
            if not dll.current_version or not dll.dll_filename:
                unknown += 1
                continue
            latest_version = LATEST_DLL_VERSIONS.get(dll.dll_filename.lower())
            if not latest_version:
                unknown += 1
                continue
            try:
                current_parsed = parse_version(dll.current_version)
                latest_parsed = parse_version(latest_version)
                if current_parsed < latest_parsed:
                    outdated += 1
                else:
                    current += 1
            except Exception:
                unknown += 1

        return (outdated, current, unknown)

    def _build_dll_popover_items(self) -> list[ft.PopupMenuItem]:
        """Build popup menu items for all DLLs with color coding and update status"""
        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        is_dark = self._registry.is_dark
        dll_colors = {
            "DLSS": "#76B900", "XeSS": "#0071C5", "FSR": "#ED1C24",
            "DLSS-G": "#76B900", "DLSS-D": "#76B900",
            "Streamline": "#76B900", "DirectStorage": "#FFB900",
        }

        items = []
        for dll in self.dlls:
            color = dll_colors.get(dll.dll_type, MD3Colors.get_text_secondary(is_dark))
            version_text = dll.current_version[:12] if dll.current_version else "N/A"

            # Check for update
            update_available = False
            if dll.current_version and dll.dll_filename:
                latest = LATEST_DLL_VERSIONS.get(dll.dll_filename.lower())
                if latest:
                    try:
                        update_available = parse_version(dll.current_version) < parse_version(latest)
                    except Exception:
                        pass

            status_icon = ft.Icon(
                ft.Icons.ARROW_UPWARD if update_available else ft.Icons.CHECK_CIRCLE,
                size=14,
                color=MD3Colors.get_warning(is_dark) if update_available else MD3Colors.get_success(is_dark),
            )

            items.append(ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Container(width=10, height=10, bgcolor=color, border_radius=5),
                        ft.Text(dll.dll_type, size=12, weight=ft.FontWeight.BOLD, width=90),
                        ft.Text(version_text, size=11, color=MD3Colors.get_on_surface_variant(is_dark), width=80),
                        status_icon,
                    ],
                    spacing=8,
                    tight=True,
                ),
            ))
        return items

    def _build_card_content(self):
        """Build card content layout"""
        is_dark = self._registry.is_dark

        # Image container with skeleton loader (responsive for grid)
        self.image_widget = ft.Image(
            src="/assets/placeholder_game.png",
            width=None,  # Full card width
            height=140,  # Slightly taller for better aspect ratio in grid
            fit=ft.BoxFit.COVER,
            border_radius=ft.BorderRadius.all(8),
            error_content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=48, color=ft.Colors.GREY),
        )

        self.image_container = ft.Container(
            content=self._create_skeleton_loader(),  # Start with skeleton
            width=140,  # Constrain image width for proper layout
            height=140,  # Match image height
            border_radius=8,
            bgcolor=MD3Colors.get_surface(is_dark),
            alignment=ft.Alignment.CENTER,
        )


        # Game name text - store reference for theming
        self.game_name_text = ft.Text(
            self.game.display_name,
            size=16,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            no_wrap=True,  # Prevent wrapping to maintain consistent card height
            tooltip=self.game.display_name,  # Show full name on hover
        )

        # Launcher text - store reference for theming
        self.launcher_text = ft.Text(
            self.game.launcher,
            size=12,
            color=MD3Colors.get_text_secondary(is_dark),
            no_wrap=True,
        )

        # Game name, launcher, and path
        game_info = ft.Column(
            controls=[
                self.game_name_text,
                self.launcher_text,
                # Path row with tooltip and copy button (supports multiple paths)
                self._build_path_display(),
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

        # Launch button (Steam games only)
        self.launch_button = self._create_launch_button()

        # Ignore toggle button — positioned as overlay on image (top-right)
        self.ignore_button = ft.IconButton(
            icon=ft.Icons.VISIBILITY if self.is_ignored else ft.Icons.VISIBILITY_OFF,
            icon_size=16,
            tooltip="Unignore this game" if self.is_ignored else "Ignore this game",
            on_click=self._on_ignore_clicked,
            width=28,
            height=28,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.DEFAULT: "#FF9800" if self.is_ignored else ft.Colors.WHITE,
                },
                bgcolor={
                    ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.7, "#FF9800") if self.is_ignored else ft.Colors.with_opacity(0.5, ft.Colors.BLACK),
                },
                padding=ft.Padding.all(4),
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        # Apply ignored visual state
        if self.is_ignored:
            self.opacity = 0.5
            if self.update_button:
                self.update_button.disabled = True

        # Resolve / link-to-Steam overlay button (bottom-right of image, like ignore button)
        self.resolve_button = ft.IconButton(
            icon=ft.Icons.EDIT,
            icon_size=14,
            tooltip="Edit display" if self.game.is_manually_resolved else "Edit display (image & name)",
            on_click=self._on_resolve_clicked,
            width=26,
            height=26,
            style=ft.ButtonStyle(
                color={ft.ControlState.DEFAULT: ft.Colors.WHITE},
                bgcolor={ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.55, ft.Colors.BLACK)},
                padding=ft.Padding.all(4),
                shape=ft.RoundedRectangleBorder(radius=7),
            ),
        )

        # PERFORMANCE: Flattened action buttons row (removed Container wrapper)
        action_buttons_controls = [
            self.update_button,
            self.restore_button,
        ]
        if self.launch_button:
            action_buttons_controls.append(self.launch_button)

        action_buttons_row = ft.Row(
            controls=action_buttons_controls,
            spacing=8,
            wrap=True,
            run_spacing=4,
        )

        # Right side content - spacer pushes buttons to bottom consistently
        # PERFORMANCE: Reduced nesting by removing unnecessary Container wrappers
        self.right_content = ft.Column(
            controls=[
                game_info,
                self.dll_badges_container,
                ft.Container(expand=True),  # Spacer pushes buttons to bottom
                action_buttons_row,  # Direct Row instead of Container > Row
            ],
            spacing=4,
            expand=True,
            alignment=ft.MainAxisAlignment.START,
        )

        # Image with overlays: ignore (top-right) + resolve/edit (bottom-right)
        self._image_stack = ft.Stack(
            controls=[
                self.image_container,
                ft.Container(
                    content=self.ignore_button,
                    right=2,
                    top=2,
                ),
                ft.Container(
                    content=self.resolve_button,
                    right=2,
                    bottom=2,
                ),
            ],
            width=140,
            height=140,
        )

        # Card content layout
        self.content = ft.Container(
            content=ft.Row(
                controls=[
                    self._image_stack,
                    self.right_content,
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=12,
            height=220,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

    def _create_dll_badges(self) -> ft.Container:
        """Create condensed DLL badge that opens grouped dialog on click"""
        is_dark = self._registry.is_dark

        # Edge case: No DLLs
        if not self.dlls:
            return ft.Container(
                content=ft.Text("0 DLLs", size=10, color=MD3Colors.get_themed("text_tertiary", is_dark)),
                bgcolor=MD3Colors.get_themed("badge_default_bg", is_dark),
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=8,
                height=28,
            )

        dll_count = len(self.dlls)
        outdated, current, unknown = self._get_update_counts()
        has_updates = outdated > 0

        # Build badge text with update status detail
        if has_updates:
            badge_text = f"{outdated}/{dll_count} outdated"
            badge_color = MD3Colors.get_warning(is_dark)
            badge_icon = ft.Icons.ARROW_UPWARD
            status_tooltip = f"{outdated} outdated, {current} current"
            if unknown:
                status_tooltip += f", {unknown} unknown"
        else:
            badge_text = f"{dll_count} DLL{'s' if dll_count != 1 else ''} current"
            badge_color = MD3Colors.get_success(is_dark)
            badge_icon = ft.Icons.CHECK_CIRCLE_OUTLINE
            status_tooltip = f"All {current} DLLs are up to date"
            if unknown:
                status_tooltip += f" ({unknown} unknown)"

        # PERFORMANCE: Flattened badge structure (GestureDetector > Container > Row)
        # Reduced from Container > GestureDetector > Container > Row to GestureDetector > Container
        return ft.GestureDetector(
            content=ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            badge_icon,
                            size=14,
                            color=ft.Colors.WHITE,
                        ),
                        ft.Text(badge_text, size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ],
                    spacing=4,
                    tight=True,
                ),
                bgcolor=badge_color,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=8,
                height=28,
                tooltip=f"{status_tooltip} - click for details",
            ),
            on_tap=lambda e: self._page_ref.run_task(self._show_dll_dialog),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def _get_dll_groups_for_game(self) -> list[str]:
        """Get DLL groups with at least one outdated DLL (unknown versions count as updatable)."""
        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        groups_present = set()
        for dll in self.dlls:
            dll_filename = dll.dll_filename.lower() if dll.dll_filename else ""
            if not dll_filename:
                continue

            needs_update = True
            if dll.current_version:
                latest = LATEST_DLL_VERSIONS.get(dll_filename)
                if latest:
                    try:
                        if parse_version(dll.current_version) >= parse_version(latest):
                            needs_update = False
                    except Exception:
                        pass
            if not needs_update:
                continue

            for group_name, group_dlls in DLL_GROUPS.items():
                if dll_filename in [d.lower() for d in group_dlls]:
                    groups_present.add(group_name)
                    break
        return sorted(list(groups_present))

    def _create_update_popup_menu(self) -> ft.PopupMenuButton:
        """Create popup menu button for selective DLL updates.

        Menu items are populated upfront since Flet's on_open fires after rendering.
        """
        is_dark = self._registry.is_dark
        has_outdated = self._check_for_updates()
        disabled_color = MD3Colors.get_themed("text_tertiary", is_dark)
        active_color = MD3Colors.get_primary(is_dark)
        color = active_color if has_outdated else disabled_color

        # Store references for theming
        self.update_button_icon = ft.Icon(ft.Icons.UPDATE, size=18, color=color)
        self.update_button_text = ft.Text("Update", size=14, color=color)
        self.update_button_arrow = ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color=color)

        return ft.PopupMenuButton(
            content=ft.Row(
                controls=[
                    self.update_button_icon,
                    self.update_button_text,
                    self.update_button_arrow,
                ],
                spacing=4,
                tight=True,
            ),
            tooltip="Select DLLs to update" if has_outdated else "All DLLs are up to date",
            items=self._build_update_menu_items(),
            disabled=not has_outdated,
        )

    def _build_update_menu_items(self) -> list[ft.PopupMenuItem]:
        """Build update menu items — only groups with at least one outdated DLL appear."""
        is_dark = self._registry.is_dark
        groups = self._get_dll_groups_for_game()

        if not groups:
            return []

        menu_items = [
            ft.PopupMenuItem(
                content="Update All",
                icon=ft.Icons.UPDATE,
                on_click=lambda e: self._on_update_group_selected("all"),
            ),
            ft.PopupMenuItem(),  # Divider
        ]

        for group in groups:
            color = TechnologyColors.get_themed_color(group, is_dark)
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

        return menu_items

    def _on_update_group_selected(self, group: str):
        """Handle DLL group selection from popup menu"""
        if self.is_updating:
            return
        if self.on_update_callback:
            # Pass both game and selected group
            self.on_update_callback(self.game, group)

    def _create_restore_popup_menu(self) -> ft.PopupMenuButton:
        """Create popup menu button for selective DLL restore from backups.

        Menu items are populated upfront since Flet's on_open fires after rendering.
        """
        is_dark = self._registry.is_dark
        disabled_color = MD3Colors.get_themed("text_tertiary", is_dark)
        success_color = MD3Colors.get_success(is_dark)

        if not self.backup_groups:
            # Store references for disabled state theming
            self.restore_button_icon = ft.Icon(ft.Icons.RESTORE, size=18, color=disabled_color)
            self.restore_button_text = ft.Text("Restore", size=14, color=disabled_color)
            self.restore_button_arrow = ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color=disabled_color)

            # Return disabled button if no backups
            return ft.PopupMenuButton(
                content=ft.Row(
                    controls=[
                        self.restore_button_icon,
                        self.restore_button_text,
                        self.restore_button_arrow,
                    ],
                    spacing=4,
                    tight=True,
                ),
                tooltip="No backups available",
                items=[],
                disabled=True,
            )

        # Store references for theming (enabled state)
        self.restore_button_icon = ft.Icon(ft.Icons.RESTORE, size=18, color=success_color)
        self.restore_button_text = ft.Text("Restore", size=14, color=success_color)
        self.restore_button_arrow = ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color=success_color)

        return ft.PopupMenuButton(
            content=ft.Row(
                controls=[
                    self.restore_button_icon,
                    self.restore_button_text,
                    self.restore_button_arrow,
                ],
                spacing=4,
                tight=True,
            ),
            tooltip="Restore DLLs from backup",
            items=self._build_restore_menu_items(),
        )

    def _build_restore_menu_items(self) -> list[ft.PopupMenuItem]:
        """Build the actual restore menu items (extracted from _create_restore_popup_menu)."""
        is_dark = self._registry.is_dark
        groups = sorted(self.backup_groups.keys())

        menu_items = [
            ft.PopupMenuItem(
                content="Restore All",
                icon=ft.Icons.RESTORE,
                on_click=lambda e: self._on_restore_group_selected("all"),
            ),
        ]

        if groups:
            menu_items.append(ft.PopupMenuItem())  # Divider

            for group in groups:
                color = TechnologyColors.get_themed_color(group, is_dark)
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

        return menu_items

    def _on_restore_group_selected(self, group: str):
        """Handle DLL group selection from restore popup menu"""
        if self.on_restore_callback:
            self.on_restore_callback(self.game, group)

    def _create_launch_button(self) -> ft.IconButton | None:
        """Create a launch button for Steam games.

        Returns an IconButton that opens the game via steam:// protocol,
        or None for non-Steam games (button is not shown).
        """
        is_steam = self.game.launcher == "Steam" and self.game.effective_steam_app_id
        if not is_steam:
            return None

        is_dark = self._registry.is_dark
        return ft.IconButton(
            icon=ft.Icons.PLAY_ARROW_ROUNDED,
            icon_size=20,
            icon_color=MD3Colors.get_primary(is_dark),
            tooltip=f"Launch via Steam",
            on_click=lambda e: self._launch_game(),
            style=ft.ButtonStyle(
                padding=ft.Padding.all(6),
            ),
            width=32,
            height=32,
        )

    def _launch_game(self):
        """Launch the game via Steam protocol."""
        import webbrowser
        import subprocess
        import sys

        if not self.game.effective_steam_app_id:
            return

        url = f"steam://rungameid/{self.game.effective_steam_app_id}"
        try:
            if sys.platform == "linux":
                subprocess.Popen(
                    ["xdg-open", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                webbrowser.open(url)
        except Exception as ex:
            self.logger.warning(f"Failed to launch game: {ex}")

    def _on_resolve_clicked(self, e):
        """Open the Steam resolve dialog."""
        if self._page_ref:
            self._page_ref.run_task(self._show_resolve_dialog)

    async def _show_resolve_dialog(self):
        from dlss_updater.ui_flet.dialogs.steam_resolve_dialog import SteamResolveDialog
        dialog = SteamResolveDialog(
            page=self._page_ref,
            logger=self.logger,
            game=self.game,
            on_resolved=self._on_resolved,
        )
        await dialog.show()

    def _on_resolved(self, override_steam_app_id: int, display_name_override: str):
        """Called by dialog after a successful save or clear."""
        if self._page_ref:
            self._page_ref.run_task(
                self.apply_resolution, override_steam_app_id, display_name_override
            )

    async def apply_resolution(self, override_steam_app_id: int, display_name_override: str):
        """Update card UI after the user links or clears a Steam override.

        Resets the image, updates the title, and refreshes the link button icon.
        Also fires on_resolve_callback so GamesView can reload the full card if needed.
        """
        async with self._ui_lock:
            # Update in-memory game fields (Game is a non-frozen msgspec.Struct)
            if override_steam_app_id:
                self.game.override_steam_app_id = override_steam_app_id
                self.game.display_name_override = display_name_override or None
            else:
                self.game.override_steam_app_id = None
                self.game.display_name_override = None

            # Refresh title text
            new_name = self.game.display_name
            self.game_name_text.value = new_name
            self.game_name_text.tooltip = new_name

            # Refresh resolve button tooltip
            self.resolve_button.tooltip = "Edit display" if self.game.is_manually_resolved else "Edit display (image & name)"

            # Reset image so it reloads with the new (or cleared) app_id
            self._image_loaded = False
            self.image_container.content = self._create_skeleton_loader()
            self.image_container.opacity = 1.0

        self.update()

        # Reload image for new app_id (non-blocking)
        if self.game.effective_steam_app_id:
            task = asyncio.create_task(self.load_image())
            task_name = f"resolve_image_{self.game.name[:15]}"
            try:
                from dlss_updater.ui_flet.task_registry import register_task
                register_task(task, task_name)
            except Exception:
                pass

        # Notify GamesView so it can persist any coordinated state
        if self.on_resolve_callback:
            self.on_resolve_callback(
                self.merged_game if self.merged_game else self.game,
                override_steam_app_id,
                display_name_override,
            )

    async def _show_dll_dialog(self):
        """Show the grouped DLL dialog with technology categories"""
        from dlss_updater.ui_flet.dialogs.dll_group_dialog import DLLGroupDialog
        from dlss_updater.database import db_manager

        # Refresh DLL versions and backup groups from DB before showing dialog
        # so the dialog reflects any updates/restores that happened since last render.
        try:
            refreshed_dlls = await db_manager.refresh_dll_versions_for_game(self.game.id)
            if refreshed_dlls:
                self.dlls = refreshed_dlls
                self.logger.debug(f"Refreshed {len(refreshed_dlls)} DLL versions for {self.game.name}")
        except Exception as e:
            self.logger.warning(f"Failed to refresh DLL versions: {e}")

        try:
            self.backup_groups = await db_manager.get_backups_grouped_by_dll_type(self.game.id)
            self.has_backups = bool(self.backup_groups)
        except Exception as e:
            self.logger.warning(f"Failed to refresh backup groups: {e}")

        # Determine which game object to pass (MergedGame or Game)
        game_to_show = self.merged_game if self.merged_game else self.game

        dialog = DLLGroupDialog(
            page=self._page_ref,
            logger=self.logger,
            game=game_to_show,
            dlls=self.dlls,
            backup_groups=self.backup_groups,
            on_update=self._on_dialog_update,
            on_restore=self._on_dialog_restore,
        )
        await dialog.show()

    def _on_dialog_update(self, game, group: str):
        """Handle update request from grouped DLL dialog"""
        if self.is_updating:
            return
        if self.on_update_callback:
            self.on_update_callback(game, group)

    def _on_dialog_restore(self, game, group: str):
        """Handle restore request from grouped DLL dialog"""
        if self.on_restore_callback:
            self.on_restore_callback(game, group)

    async def load_image(self, prefetched_path: str | None = None, coordinator: Any | None = None):
        """Async load Steam image with fade-in animation.

        Args:
            prefetched_path: Optional pre-fetched cached path from batch query.
                            If provided, skips database lookup for better performance.
            coordinator: Optional ImageLoadCoordinator for batched UI updates.
                        If provided, updates are batched for ~5x better performance.
                        Falls back to direct _fade_in_image() if not provided.
        """
        # Prevent duplicate image loads
        if self._image_loaded:
            return

        effective_app_id = self.game.effective_steam_app_id
        if not effective_app_id:
            self.logger.debug(f"No Steam app ID for {self.game.name}, skipping image")
            return

        try:
            from dlss_updater.database import db_manager
            from pathlib import Path

            # Use prefetched path or check cache
            cached_path = prefetched_path
            if cached_path is None:
                cached_path = await db_manager.get_cached_image_path(effective_app_id)

            image_path = None

            if cached_path:
                # Validate file exists before using
                try:
                    if Path(cached_path).exists():
                        image_path = cached_path
                        self.logger.debug(f"Using cached image for {self.game.name}")
                except OSError:
                    pass  # File access error, will fetch fresh

            # Fetch from Steam CDN if no valid cached path
            if image_path is None:
                self.logger.info(f"Fetching Steam image for {self.game.name} (app_id: {effective_app_id})")
                fetched_path = await fetch_steam_image(effective_app_id)
                if fetched_path:
                    image_path = str(fetched_path)
                    self.logger.info(f"Successfully fetched image for {self.game.name}")

            if image_path:
                if coordinator:
                    # Batched update via coordinator (preferred for concurrent loading)
                    await coordinator.schedule_image_update(self, image_path)
                else:
                    # Fallback: direct update (for single card refresh operations)
                    await self._fade_in_image(image_path)
                    self._image_loaded = True
            else:
                self.logger.debug(f"No image available for {self.game.name}")

        except Exception as e:
            self.logger.error(f"Error loading image for {self.game.name}: {e}", exc_info=True)

    async def _fade_in_image(self, image_path: str):
        """Fade in image smoothly with minimal UI updates.

        Optimized for Flet 0.80.4 performance:
        - Single 100ms wait for control attachment (vs 5x50ms retry loop)
        - Maximum 2 update calls (vs 6-7 previously)
        - Uses page.update() which is more reliable for batch operations

        Note: Lock is released during sleep to avoid blocking other concurrent UI updates.
        This is important for Python 3.14 free-threading compatibility.
        """
        # Phase 1: Set up image and prepare for fade-in (under lock)
        async with self._ui_lock:
            # Check if page is still available (may be None if view was closed)
            if not self._page_ref:
                return

            # Update image source and prepare fade-in animation
            self.image_widget.src = image_path
            self.image_container.opacity = 0
            self.image_container.animate_opacity = ft.Animation(300, ft.AnimationCurve.EASE_IN)
            self.image_container.content = self.image_widget

        # Single wait for control to be added to page (Flet 0.80.4 is faster)
        await asyncio.sleep(0.1)

        # Phase 2: Trigger fade-in with single card update
        async with self._ui_lock:
            if not self._page_ref:
                return
            # Set final opacity and trigger animation
            self.image_container.opacity = 1
            try:
                # Use self.update() — card-level update (isolated GamesView)
                self.update()
            except Exception:
                pass  # Page may be closing

    def _on_ignore_clicked(self, e):
        """Toggle ignore status and notify parent."""
        new_state = not self.is_ignored
        if self.on_ignore_toggle_callback:
            self.on_ignore_toggle_callback(
                self.merged_game if self.merged_game else self.game,
                new_state
            )

    def set_ignored(self, ignored: bool):
        """Update ignore visual state. Called by GamesView after DB confirms the change."""
        self.is_ignored = ignored
        self.opacity = 0.5 if ignored else 1.0
        if self.ignore_button:
            self.ignore_button.icon = ft.Icons.VISIBILITY if ignored else ft.Icons.VISIBILITY_OFF
            self.ignore_button.tooltip = "Unignore this game" if ignored else "Ignore this game"
            self.ignore_button.style = ft.ButtonStyle(
                color={
                    ft.ControlState.DEFAULT: "#FF9800" if ignored else ft.Colors.WHITE,
                },
                bgcolor={
                    ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.7, "#FF9800") if ignored else ft.Colors.with_opacity(0.5, ft.Colors.BLACK),
                },
                padding=ft.Padding.all(4),
                shape=ft.RoundedRectangleBorder(radius=8),
            )
        if self.update_button:
            self.update_button.disabled = ignored

    def _on_hover(self, e):
        """Handle hover effect with multi-layer shadow and border glow"""
        import time
        start = time.perf_counter()

        is_dark = self._registry.is_dark
        primary_color = MD3Colors.get_primary(is_dark)
        if e.data == "true":
            self.elevation = 8
            self.shadow = Shadows.LEVEL_3
            self.scale = 1.015
            self.border = ft.Border.all(1, ft.Colors.with_opacity(0.3, primary_color))
        else:
            self.elevation = 2
            self.shadow = Shadows.LEVEL_2
            self.scale = 1.0
            self.border = None

        start_update = time.perf_counter()
        self.update()
        update_ms = (time.perf_counter() - start_update) * 1000
        total_ms = (time.perf_counter() - start) * 1000

        # Only log if slow (>30ms)
        if total_ms > 30:
            from dlss_updater.ui_flet.perf_monitor import perf_logger
            perf_logger.warning(f"[SLOW] card_hover: update={update_ms:.1f}ms, total={total_ms:.1f}ms")

    def set_updating(self, is_updating: bool):
        """Set updating state - shows spinner and disables button"""
        is_dark = self._registry.is_dark
        self.is_updating = is_updating
        if self.update_button and self.update_button.content:
            row = self.update_button.content
            color = MD3Colors.get_text_secondary(is_dark) if is_updating else MD3Colors.get_primary(is_dark)
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

    async def refresh_dlls(self, new_dlls: list[GameDLL]):
        """Refresh DLL badges and update button with new data after update/restore."""
        async with self._ui_lock:
            self.dlls = new_dlls

            # Rebuild the DLL badges
            new_badges = self._create_dll_badges()

            # Replace the old badges in right_content
            if self.right_content and len(self.right_content.controls) >= 2:
                self.right_content.controls[1] = new_badges
                self.dll_badges_container = new_badges

            # Rebuild update button in-place so menu items and enabled state reflect
            # current DLL versions. Keep disabled if game is ignored.
            if self.update_button:
                has_outdated = self._check_for_updates()
                is_dark = self._registry.is_dark
                disabled_color = MD3Colors.get_themed("text_tertiary", is_dark)
                active_color = MD3Colors.get_primary(is_dark)
                color = active_color if has_outdated else disabled_color

                self.update_button.items = self._build_update_menu_items()
                self.update_button.disabled = self.is_ignored or not has_outdated
                self.update_button.tooltip = (
                    "Select DLLs to update" if has_outdated else "All DLLs are up to date"
                )
                if self.update_button_icon:
                    self.update_button_icon.color = color
                if self.update_button_text:
                    self.update_button_text.color = color
                if self.update_button_arrow:
                    self.update_button_arrow.color = color

            if self.right_content:
                self.right_content.update()

    async def refresh_restore_button(self, new_backup_groups: dict[str, list]):
        """Refresh restore button with new backup data after restore"""
        async with self._ui_lock:
            self.backup_groups = new_backup_groups
            self.has_backups = bool(new_backup_groups)

            # Rebuild restore button
            new_restore_button = self._create_restore_popup_menu()

            # Find and replace in action buttons row (index 3 due to spacer at index 2)
            # Structure: right_content.controls = [name_row, dll_badges, spacer, action_buttons_row]
            # action_buttons_row is a ft.Row with controls = [update_button, restore_button]
            if self.right_content and len(self.right_content.controls) >= 4:
                action_buttons_row = self.right_content.controls[3]
                if hasattr(action_buttons_row, 'controls') and len(action_buttons_row.controls) >= 2:
                    action_buttons_row.controls[1] = new_restore_button
                    self.restore_button = new_restore_button
                    action_buttons_row.update()

    def _build_path_display(self) -> ft.Row:
        """Build path display row, supporting multiple paths for merged games."""
        is_dark = self._registry.is_dark
        tertiary_color = MD3Colors.get_themed("text_tertiary", is_dark)

        if len(self.all_paths) == 1:
            # Single path - current behavior
            path_text = self.all_paths[0]
            path_tooltip = self.all_paths[0]
        else:
            # Multiple paths - show count with expandable detail
            path_text = f"{self.all_paths[0]}  (+{len(self.all_paths) - 1} more)"
            path_tooltip = "Installations:\n" + "\n".join(f"• {p}" for p in self.all_paths)

        # Store reference for theming
        # PERF: tooltip on Text directly instead of Container wrapper (-1 control)
        self.path_text = ft.Text(
            path_text,
            size=10,
            color=tertiary_color,
            no_wrap=True,
            italic=True,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
            tooltip=path_tooltip,
            expand=True,
        )
        self.copy_path_button = ft.IconButton(
            icon=ft.Icons.CONTENT_COPY,
            icon_size=12,
            icon_color=tertiary_color,
            tooltip="Copy path(s)" if len(self.all_paths) > 1 else "Copy path",
            on_click=self._on_copy_path_clicked,
            width=20,
            height=20,
        )

        return ft.Row(
            controls=[
                self.path_text,
                self.copy_path_button,
            ],
            spacing=4,
            tight=True,
        )

    async def _on_copy_path_clicked(self, e):
        """Copy game path(s) to clipboard with snackbar confirmation"""
        is_dark = self._registry.is_dark

        # Copy all paths, one per line
        copy_content = "\n".join(self.all_paths)

        try:
            await ft.Clipboard().set(copy_content)

            if len(self.all_paths) > 1:
                message = f"{len(self.all_paths)} paths copied to clipboard"
            else:
                message = "Path copied to clipboard"

            self._page_ref.show_dialog(ft.SnackBar(
                content=ft.Text(message),
                bgcolor=MD3Colors.get_themed("snackbar_bg", is_dark),
            ))
        except Exception as ex:
            self.logger.warning(f"Clipboard operation failed: {ex}")
            self._page_ref.show_dialog(ft.SnackBar(
                content=ft.Text("Failed to copy to clipboard"),
                bgcolor=MD3Colors.get_error(is_dark),
            ))

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware updates.

        Returns:
            Dict mapping property paths to (dark_value, light_value) tuples.
        """
        return {
            # Card surface tint
            "surface_tint_color": MD3Colors.get_themed_pair("primary"),
            # Image container
            "image_container.bgcolor": MD3Colors.get_themed_pair("surface"),
            # Text colors
            "game_name_text.color": MD3Colors.get_themed_pair("text_primary"),
            "launcher_text.color": MD3Colors.get_themed_pair("text_secondary"),
            "path_text.color": MD3Colors.get_themed_pair("text_tertiary"),
            # Copy button
            "copy_path_button.icon_color": MD3Colors.get_themed_pair("text_tertiary"),
            # Update button colors
            "update_button_icon.color": MD3Colors.get_themed_pair("primary"),
            "update_button_text.color": MD3Colors.get_themed_pair("primary"),
            "update_button_arrow.color": MD3Colors.get_themed_pair("primary"),
            # Restore button colors (when enabled - success color)
            "restore_button_icon.color": MD3Colors.get_themed_pair("success"),
            "restore_button_text.color": MD3Colors.get_themed_pair("success"),
            "restore_button_arrow.color": MD3Colors.get_themed_pair("success"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay.

        Overrides ThemeAwareMixin.apply_theme to also rebuild menu items with
        correct theme colors.

        Args:
            is_dark: Whether dark mode is active
            delay_ms: Milliseconds to wait before applying (for cascade effect)
        """
        # Rebuild menu items with new theme colors
        if self.update_button:
            self.update_button.items = self._build_update_menu_items()
        if self.restore_button and not self.restore_button.disabled:
            self.restore_button.items = self._build_restore_menu_items()

        # Call parent implementation for standard themed property updates
        await super().apply_theme(is_dark, delay_ms)
