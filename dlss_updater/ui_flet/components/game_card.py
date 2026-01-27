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

    def __init__(self, game: Game | MergedGame, dlls: list[GameDLL], page: ft.Page, logger, on_update=None, on_view_backups=None, on_restore=None, backup_groups: dict[str, list] | None = None):
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
        self.backup_groups = backup_groups or {}
        self.has_backups = bool(backup_groups)

        # Button references for loading state
        self.update_button: ft.PopupMenuButton | None = None
        self.restore_button: ft.PopupMenuButton | None = None
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
        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        for dll in self.dlls:
            if not dll.current_version or not dll.dll_filename:
                continue
            latest_version = LATEST_DLL_VERSIONS.get(dll.dll_filename.lower())
            if not latest_version:
                continue
            try:
                current_parsed = parse_version(dll.current_version)
                latest_parsed = parse_version(latest_version)
                if current_parsed < latest_parsed:
                    return True
            except Exception:
                continue
        return False

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
            border_radius=ft.border_radius.all(8),
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
            self.game.name,
            size=16,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            no_wrap=True,  # Prevent wrapping to maintain consistent card height
            tooltip=self.game.name,  # Show full name on hover
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

        # PERFORMANCE: Flattened action buttons row (removed Container wrapper)
        # Row with wrap handles overflow without needing clip_behavior
        action_buttons_row = ft.Row(
            controls=[
                self.update_button,
                self.restore_button,
            ],
            spacing=8,
            wrap=True,  # Allow buttons to wrap when narrow
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

        # Card content layout
        # Height 220px to accommodate wrapped buttons at narrow widths (was 180px)
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
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=8,
                height=28,
            )

        dll_count = len(self.dlls)
        has_updates = self._check_for_updates()
        badge_text = f"+{dll_count} DLL" if dll_count == 1 else f"+{dll_count} DLLs"
        badge_color = MD3Colors.get_warning(is_dark) if has_updates else MD3Colors.get_primary(is_dark)

        # PERFORMANCE: Flattened badge structure (GestureDetector > Container > Row)
        # Reduced from Container > GestureDetector > Container > Row to GestureDetector > Container
        return ft.GestureDetector(
            content=ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.ARROW_UPWARD if has_updates else ft.Icons.EXTENSION,
                            size=14,
                            color=ft.Colors.WHITE,
                        ),
                        ft.Text(badge_text, size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ],
                    spacing=4,
                    tight=True,
                ),
                bgcolor=badge_color,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=8,
                height=28,
                tooltip=f"View {dll_count} DLL{'s' if dll_count != 1 else ''} - click for details",
            ),
            on_tap=lambda e: self._page_ref.run_task(self._show_dll_dialog),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def _get_dll_groups_for_game(self) -> list[str]:
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
        """Create popup menu button for selective DLL updates.

        Menu items are populated upfront since Flet's on_open fires after rendering.
        """
        is_dark = self._registry.is_dark
        primary_color = MD3Colors.get_primary(is_dark)

        # Store references for theming
        self.update_button_icon = ft.Icon(ft.Icons.UPDATE, size=18, color=primary_color)
        self.update_button_text = ft.Text("Update", size=14, color=primary_color)
        self.update_button_arrow = ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color=primary_color)

        # Use content property to show "Update" text instead of just an icon
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
            tooltip="Select DLLs to update",
            items=self._build_update_menu_items(),
        )

    def _build_update_menu_items(self) -> list[ft.PopupMenuItem]:
        """Build the actual update menu items (extracted from _create_update_popup_menu)."""
        is_dark = self._registry.is_dark
        groups = self._get_dll_groups_for_game()

        menu_items = [
            ft.PopupMenuItem(
                content="Update All",
                icon=ft.Icons.UPDATE,
                on_click=lambda e: self._on_update_group_selected("all"),
            ),
        ]

        # Add divider and group-specific options if we have groups
        if groups:
            menu_items.append(ft.PopupMenuItem())  # Divider

            for group in groups:
                color = TechnologyColors.get_themed_color(group, is_dark)
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

    async def _show_dll_dialog(self):
        """Show the grouped DLL dialog with technology categories"""
        from dlss_updater.ui_flet.dialogs.dll_group_dialog import DLLGroupDialog

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

        if not self.game.steam_app_id:
            self.logger.debug(f"No Steam app ID for {self.game.name}, skipping image")
            return

        try:
            from dlss_updater.database import db_manager
            from pathlib import Path

            # Use prefetched path or check cache
            cached_path = prefetched_path
            if cached_path is None:
                cached_path = await db_manager.get_cached_image_path(self.game.steam_app_id)

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
                self.logger.info(f"Fetching Steam image for {self.game.name} (app_id: {self.game.steam_app_id})")
                fetched_path = await fetch_steam_image(self.game.steam_app_id)
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

        # Phase 2: Trigger fade-in with single page update
        async with self._ui_lock:
            if not self._page_ref:
                return
            # Set final opacity and trigger animation
            self.image_container.opacity = 1
            try:
                # Use page.update() - more reliable for concurrent card loading
                self._page_ref.update()
            except Exception:
                pass  # Page may be closing

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
            self.border = ft.border.all(1, ft.Colors.with_opacity(0.3, primary_color))
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
        await self._page_ref.set_clipboard_async(copy_content)

        if len(self.all_paths) > 1:
            message = f"{len(self.all_paths)} paths copied to clipboard"
        else:
            message = "Path copied to clipboard"

        self._page_ref.show_dialog(ft.SnackBar(
            content=ft.Text(message),
            bgcolor=MD3Colors.get_themed("snackbar_bg", is_dark),
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
