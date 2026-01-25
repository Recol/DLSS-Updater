"""
Backups View - Browse and restore DLL backups

PERFORMANCE NOTES:
- Uses GridView with virtualization (only visible cards are rendered)
- Progressive loading: first batch shown immediately, rest created in background
- Data preparation runs in thread pool for parallel processing
- Batch UI updates minimize page.update() calls
"""

import asyncio
import math
import time
from typing import Callable, Any
from concurrent.futures import ThreadPoolExecutor
import flet as ft

from dlss_updater.database import db_manager, DLLBackup
from dlss_updater.models import GameWithBackupCount, GameDLLBackup
from dlss_updater.backup_manager import restore_dll_from_backup, delete_backup
from dlss_updater.ui_flet.components.backup_card import BackupCard
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.task_registry import register_task

# Number of cards to create in first batch (shown immediately)
INITIAL_BATCH_SIZE = 12
# Number of cards per background batch
BACKGROUND_BATCH_SIZE = 20


class BackupsView(ThemeAwareMixin, ft.Column):
    """Backup management view"""

    def __init__(self, page: ft.Page, logger):
        super().__init__()
        self._page_ref = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        # State
        self.backups: list[GameDLLBackup] = []
        self.is_loading = False
        self.refresh_button_ref = ft.Ref[ft.IconButton]()

        # Button references for state management
        self.clear_all_button: ft.ElevatedButton | None = None

        # Game filter state
        self.selected_game_id: int | None = None
        self.game_filter_dropdown: ft.Dropdown | None = None
        self.games_with_backups: list[GameWithBackupCount] = []

        # PERFORMANCE: Track if backups are already loaded to prevent redundant rebuilds
        self._backups_loaded = False

        # Initialize theme system reference before building UI
        self._registry = get_theme_registry()
        self._theme_priority = 10  # Views are high priority (animate early)

        # Build initial UI
        self._build_ui()

        # Register with theme system after UI is built
        self._register_theme_aware()

    def _create_clear_all_button(self) -> ft.ElevatedButton:
        """Create and store reference to Clear All Backups button"""
        self.clear_all_button = ft.ElevatedButton(
            "Clear All Backups",
            icon=ft.Icons.DELETE_SWEEP,
            on_click=self._on_clear_all_clicked,
            disabled=True,  # Initially disabled until backups are loaded
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.RED_400,
                color=ft.Colors.WHITE,
            ),
        )
        return self.clear_all_button

    def _update_clear_button_state(self, has_backups: bool):
        """Update clear button enabled/disabled state"""
        if self.clear_all_button:
            self.clear_all_button.disabled = not has_backups

    def _create_game_filter_dropdown(self) -> ft.Dropdown:
        """Create dropdown for filtering backups by game with MD3 theme styling"""
        is_dark = self._get_is_dark()
        self.game_filter_dropdown = ft.Dropdown(
            label="Filter by Game",
            hint_text="All Games",
            options=[ft.dropdown.Option(key="all", text="All Games")],
            value="all",
            on_select=self._on_game_filter_changed,
            width=250,
            dense=True,
            text_size=14,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            color=MD3Colors.get_on_surface(is_dark),
            border_color=MD3Colors.get_outline(is_dark),
            focused_border_color=MD3Colors.get_primary(is_dark),
            border_radius=8,
            label_style=ft.TextStyle(color=MD3Colors.get_on_surface_variant(is_dark)),
            text_style=ft.TextStyle(color=MD3Colors.get_on_surface(is_dark)),
            fill_color=MD3Colors.get_surface_variant(is_dark),
        )
        return self.game_filter_dropdown

    async def _on_game_filter_changed(self, e):
        """Handle game filter selection change"""
        value = e.control.value
        if value == "all":
            self.selected_game_id = None
        else:
            self.selected_game_id = int(value)
        # Filter change requires reload
        await self.load_backups(force=True)

    def _update_game_filter_options(self):
        """Update game filter dropdown with available games"""
        if not self.game_filter_dropdown:
            return

        options = [ft.dropdown.Option(key="all", text="All Games")]

        for game in self.games_with_backups:
            options.append(
                ft.dropdown.Option(
                    key=str(game.game_id),
                    text=f"{game.game_name} ({game.backup_count})"
                )
            )

        self.game_filter_dropdown.options = options

        # Preserve current selection if still valid
        if self.selected_game_id:
            valid_ids = [g.game_id for g in self.games_with_backups]
            if self.selected_game_id not in valid_ids:
                self.selected_game_id = None
                self.game_filter_dropdown.value = "all"

    def set_game_filter(self, game_id: int | None):
        """Set game filter programmatically (for navigation from Games view)"""
        self.selected_game_id = game_id
        if self.game_filter_dropdown:
            self.game_filter_dropdown.value = str(game_id) if game_id else "all"

    def _build_ui(self):
        """Build initial UI"""
        # Get theme preference from registry
        is_dark = self._get_is_dark()

        # Store themed element references
        self.header_title = ft.Text(
            "DLL Backup History",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
        )

        self.loading_text = ft.Text(
            "Loading backups...",
            color=MD3Colors.get_text_primary(is_dark),
        )

        self.divider = ft.Divider(height=1, color=MD3Colors.get_outline(is_dark))

        # Header with game filter
        self.header = ft.Container(
            content=ft.Row(
                controls=[
                    self.header_title,
                    ft.Container(expand=True),  # Spacer
                    self._create_game_filter_dropdown(),
                    self._create_clear_all_button(),
                    ft.IconButton(
                        icon=ft.Icons.REFRESH,
                        tooltip="Refresh Backups",
                        on_click=self._on_refresh_clicked,
                        animate_rotation=ft.Animation(400, ft.AnimationCurve.EASE_IN_OUT),
                        rotate=0,
                        ref=self.refresh_button_ref,
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                spacing=8,
            ),
            padding=16,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
        )

        # Empty state
        self.empty_state = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.RESTORE_FROM_TRASH, size=64, color=ft.Colors.GREY),
                    ft.Text(
                        "No backups found",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.GREY,
                    ),
                    ft.Text(
                        "Backups will appear here after updating DLLs",
                        size=14,
                        color=ft.Colors.GREY,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

        # Loading indicator
        self.loading_indicator = ft.Container(
            content=ft.Column(
                controls=[
                    ft.ProgressRing(),
                    self.loading_text,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
            visible=False,
        )

        # Backups grid - VIRTUALIZED GridView for performance
        # Only visible cards are rendered (typically 4-8 at once)
        # max_extent=400 gives 2-3 columns on typical screens
        # BackupCard has fixed height=220px (same as GameCard), aspect_ratio = 400/220 ≈ 1.82
        self.backups_grid = ft.GridView(
            controls=[],
            max_extent=400,  # Responsive columns based on 400px max width
            child_aspect_ratio=1.82,  # Matches BackupCard fixed height (400/220)
            padding=16,
            spacing=12,
            run_spacing=12,
            expand=True,
        )
        self.backups_grid_container = ft.Container(
            content=self.backups_grid,
            expand=True,
            visible=False,
        )

        # Assemble
        self.controls = [
            self.header,
            self.divider,
            ft.Stack(
                controls=[
                    self.empty_state,
                    self.loading_indicator,
                    self.backups_grid_container,
                ],
                expand=True,
            ),
        ]

    def _get_is_dark(self) -> bool:
        """Get current theme mode from registry or session"""
        if hasattr(self, '_registry') and self._registry:
            return self._registry.is_dark
        if self._page_ref and self._page_ref.session.contains_key("is_dark_theme"):
            return self._page_ref.session.get("is_dark_theme")
        return True

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware system"""
        return {
            "header.bgcolor": (MD3Colors.SURFACE_VARIANT, MD3Colors.SURFACE_VARIANT_LIGHT),
            "header_title.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "loading_text.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "divider.color": (MD3Colors.get_outline(True), MD3Colors.get_outline(False)),
            "game_filter_dropdown.bgcolor": (MD3Colors.SURFACE_VARIANT, MD3Colors.SURFACE_VARIANT_LIGHT),
            "game_filter_dropdown.color": (MD3Colors.ON_SURFACE, MD3Colors.ON_SURFACE_LIGHT),
            "game_filter_dropdown.border_color": (MD3Colors.OUTLINE, MD3Colors.OUTLINE_LIGHT),
            "game_filter_dropdown.focused_border_color": (MD3Colors.PRIMARY, MD3Colors.PRIMARY_LIGHT),
            "game_filter_dropdown.fill_color": (MD3Colors.SURFACE_VARIANT, MD3Colors.SURFACE_VARIANT_LIGHT),
        }

    async def load_backups(self, force: bool = False):
        """Load backups from database with optional game filter.

        PERFORMANCE: Skips full reload if backups are already loaded (tab switching).
        Use force=True to trigger a full refresh (explicit refresh button).

        Args:
            force: If True, forces a full reload even if backups are already loaded.
        """
        if self.is_loading:
            return

        # PERFORMANCE: Skip full reload if already loaded (fast tab switching)
        if self._backups_loaded and not force:
            self.logger.debug("Backups already loaded - skipping redundant reload")
            # Just ensure the view is visible
            if self.backups:
                self.backups_grid_container.visible = True
                self.empty_state.visible = False
            else:
                self.empty_state.visible = True
                self.backups_grid_container.visible = False
            self.loading_indicator.visible = False
            if self._page_ref:
                self._page_ref.update()
            return

        self.is_loading = True
        self.loading_indicator.visible = True
        self.empty_state.visible = False
        self.backups_grid_container.visible = False
        # Clear existing cards for fresh reload
        self.backups_grid.controls.clear()
        if self._page_ref:
            self._page_ref.update()

        try:
            start_total = time.perf_counter()

            # Ensure database pool is ready
            await db_manager.ensure_pool()

            self.logger.info("Loading backups from database...")

            # PERFORMANCE: Run both database queries in parallel
            start_db = time.perf_counter()
            games_task = asyncio.create_task(db_manager.get_games_with_backups())
            backups_task = asyncio.create_task(db_manager.get_all_backups_filtered(self.selected_game_id))
            self.games_with_backups, self.backups = await asyncio.gather(games_task, backups_task)
            self._update_game_filter_options()
            db_ms = (time.perf_counter() - start_db) * 1000
            self.logger.debug(f"[PERF] Database queries (parallel): {db_ms:.1f}ms")

            if not self.backups:
                self.logger.info("No backups found")
                self.empty_state.visible = True
                self.loading_indicator.visible = False
                self._update_clear_button_state(False)
                self._backups_loaded = True
                if self._page_ref:
                    self._page_ref.update()
                return

            # PERFORMANCE: Progressive loading with virtualized GridView
            # 1. Convert data to DLLBackup objects (can be parallelized)
            # 2. Create first batch of cards immediately (visible cards)
            # 3. Show UI immediately
            # 4. Create remaining cards in background batches

            start_cards = time.perf_counter()

            # Step 1: Convert all backup data (lightweight, mostly data copying)
            dll_backups = [
                DLLBackup(
                    id=b.id,
                    game_dll_id=b.game_dll_id,
                    game_name=b.game_name,
                    dll_filename=b.dll_filename,
                    backup_path=b.backup_path,
                    backup_size=b.backup_size,
                    original_version=b.original_version,
                    backup_created_at=b.backup_created_at,
                    is_active=b.is_active,
                )
                for b in self.backups
            ]

            # Step 2: Create first batch immediately (these are visible)
            first_batch = dll_backups[:INITIAL_BATCH_SIZE]
            cards = []
            for backup in first_batch:
                card = BackupCard(
                    backup=backup,
                    page=self._page_ref,
                    logger=self.logger,
                    on_restore=self._on_restore_backup,
                    on_delete=self._on_delete_backup,
                )
                cards.append(card)

            first_batch_ms = (time.perf_counter() - start_cards) * 1000
            self.logger.debug(f"[PERF] First batch ({len(first_batch)} cards): {first_batch_ms:.1f}ms")

            # Step 3: Show UI immediately with first batch
            self.backups_grid.controls = cards
            self.backups_grid_container.visible = True
            self.empty_state.visible = False
            self.loading_indicator.visible = False
            self._update_clear_button_state(True)
            self._backups_loaded = True

            if self._page_ref:
                self._page_ref.update()

            # Step 4: Create remaining cards in background batches (non-blocking)
            remaining = dll_backups[INITIAL_BATCH_SIZE:]
            if remaining:
                task = asyncio.create_task(self._load_remaining_cards(remaining))
                register_task(task, "load_remaining_backup_cards")

            total_ms = (time.perf_counter() - start_total) * 1000
            self.logger.info(f"Loaded {len(first_batch)} backups instantly, {len(remaining)} loading in background ({total_ms:.1f}ms)")

        except Exception as e:
            self.logger.error(f"Error loading backups: {e}", exc_info=True)
            self.empty_state.visible = True
            self.loading_indicator.visible = False
            self._update_clear_button_state(False)
            self._backups_loaded = False  # Allow retry on next tab switch

        finally:
            self.is_loading = False
            if self._page_ref:
                self._page_ref.update()

    async def _load_remaining_cards(self, remaining_backups: list[DLLBackup]):
        """Load remaining backup cards in background batches.

        PERFORMANCE: Creates cards in batches with yields to keep UI responsive.
        Each batch adds cards to the virtualized GridView which only renders
        visible items, so adding 100+ cards has minimal performance impact.
        """
        try:
            total_remaining = len(remaining_backups)
            loaded = 0

            for i in range(0, total_remaining, BACKGROUND_BATCH_SIZE):
                batch = remaining_backups[i:i + BACKGROUND_BATCH_SIZE]

                # Create cards for this batch
                new_cards = []
                for backup in batch:
                    card = BackupCard(
                        backup=backup,
                        page=self._page_ref,
                        logger=self.logger,
                        on_restore=self._on_restore_backup,
                        on_delete=self._on_delete_backup,
                    )
                    new_cards.append(card)

                # Add to grid (virtualized - only visible cards render)
                self.backups_grid.controls.extend(new_cards)
                loaded += len(new_cards)

                # Single update per batch
                if self._page_ref:
                    self._page_ref.update()

                # Yield to event loop to keep UI responsive
                await asyncio.sleep(0.01)

            self.logger.debug(f"[PERF] Background loaded {loaded} additional backup cards")

        except Exception as e:
            self.logger.error(f"Error loading remaining backup cards: {e}", exc_info=True)

    async def _on_refresh_clicked(self, e):
        """Handle refresh button click with rotation animation"""
        # Rotate refresh button
        if self.refresh_button_ref.current:
            self.refresh_button_ref.current.rotate += math.pi * 2  # 360 degrees
            self._page_ref.update()

        # Force=True to bypass the "already loaded" optimization
        await self.load_backups(force=True)

    async def _on_clear_all_clicked(self, e):
        """Handle clear all backups button click"""
        # Count current backups
        if not self.backups or len(self.backups) == 0:
            # Show info dialog if no backups - create without actions first
            info_dialog = ft.AlertDialog(
                title=ft.Text("No Backups"),
                content=ft.Text("There are no backups to clear."),
            )
            # Add actions after dialog variable exists
            info_dialog.actions = [
                ft.TextButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
            ]
            self._page_ref.show_dialog(info_dialog)
            return

        # Show confirmation dialog - create without actions first
        backup_count = len(self.backups)
        confirm_dialog = ft.AlertDialog(
            title=ft.Text("Clear All Backups?"),
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.WARNING, color=ft.Colors.ORANGE, size=48),
                    ft.Text(
                        f"This will mark all {backup_count} backup(s) as inactive.",
                        size=14,
                    ),
                    ft.Text(
                        "This action cannot be undone.",
                        size=12,
                        color=ft.Colors.RED_400,
                        weight=ft.FontWeight.BOLD,
                    ),
                ],
                tight=True,
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            actions_alignment=ft.MainAxisAlignment.END,
        )

        # Now add actions that reference the dialog
        confirm_dialog.actions = [
            ft.TextButton(
                "Cancel",
                on_click=lambda e: self._page_ref.pop_dialog(),
            ),
            ft.ElevatedButton(
                "Clear All",
                on_click=self._create_clear_all_handler(confirm_dialog),
                style=ft.ButtonStyle(
                    bgcolor=ft.Colors.RED_400,
                    color=ft.Colors.WHITE,
                ),
            ),
        ]

        self._page_ref.show_dialog(confirm_dialog)

    def _create_clear_all_handler(self, dialog: ft.AlertDialog):
        """Create async clear all handler"""
        async def handler(e):
            await self._perform_clear_all(dialog)
        return handler

    async def _perform_clear_all(self, dialog: ft.AlertDialog):
        """Perform the clear all operation"""
        self._page_ref.pop_dialog()

        # Show progress indicator
        progress_dialog = ft.AlertDialog(
            title=ft.Text("Clearing Backups..."),
            content=ft.Column(
                controls=[
                    ft.ProgressRing(),
                    ft.Text("Clearing all backups...", size=12),
                ],
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self._page_ref.show_dialog(progress_dialog)
        self._page_ref.update()

        try:
            # Delete all backups from database
            deleted_count = await db_manager.delete_all_backups()

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show success dialog
            success_dialog = ft.AlertDialog(
                title=ft.Text("Success"),
                content=ft.Text(f"Successfully cleared {deleted_count} backup(s)."),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self._page_ref.pop_dialog(),
                    ),
                ],
            )
            self._page_ref.show_dialog(success_dialog)

            # Reload backups list
            await self.load_backups()

        except Exception as ex:
            self.logger.error(f"Error clearing all backups: {ex}", exc_info=True)

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show error dialog
            error_dialog = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(f"Failed to clear backups: {str(ex)}"),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self._page_ref.pop_dialog(),
                    ),
                ],
            )
            self._page_ref.show_dialog(error_dialog)

    def _on_restore_backup(self, backup: DLLBackup):
        """Handle restore backup button click"""
        # Create dialog first without actions
        dialog = ft.AlertDialog(
            title=ft.Text("Restore DLL Backup?"),
            content=ft.Column(
                controls=[
                    ft.Text(f"Game: {backup.game_name}"),
                    ft.Text(f"DLL: {backup.dll_filename}"),
                    ft.Text(f"Backup Version: {backup.original_version or 'Unknown'}"),
                    ft.Divider(),
                    ft.Text(
                        "This will replace the current DLL with the backup version.",
                        color=ft.Colors.ORANGE,
                        size=12,
                    ),
                    ft.Text(
                        "Make sure the game is closed before restoring.",
                        color=ft.Colors.ORANGE,
                        size=12,
                    ),
                ],
                tight=True,
                spacing=8,
            ),
            actions_alignment=ft.MainAxisAlignment.END,
        )

        # Now add actions that reference the dialog
        dialog.actions = [
            ft.TextButton(
                "Cancel",
                on_click=lambda e: self._page_ref.pop_dialog(),
            ),
            ft.ElevatedButton(
                "Restore",
                on_click=self._create_restore_handler(backup, dialog),
                style=ft.ButtonStyle(
                    bgcolor="#2D6E88",
                    color=ft.Colors.WHITE,
                ),
            ),
        ]

        self._page_ref.show_dialog(dialog)

    def _create_restore_handler(self, backup: DLLBackup, dialog: ft.AlertDialog):
        """Create async restore handler for specific backup"""
        async def handler(e):
            await self._perform_restore(backup, dialog)
        return handler

    async def _perform_restore(self, backup: DLLBackup, dialog: ft.AlertDialog):
        """Perform the restore operation"""
        self._page_ref.pop_dialog()

        # Show progress indicator
        progress_dialog = ft.AlertDialog(
            title=ft.Text("Restoring..."),
            content=ft.Column(
                controls=[
                    ft.ProgressRing(),
                    ft.Text("Restoring DLL from backup...", size=12),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
                tight=True,
            ),
        )
        self._page_ref.show_dialog(progress_dialog)

        try:
            # Perform restore
            success, message = await restore_dll_from_backup(backup.id)

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show result
            result_dialog = ft.AlertDialog(
                title=ft.Text("Restore Complete" if success else "Restore Failed"),
                content=ft.Text(message),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self._page_ref.pop_dialog(),
                    ),
                ],
            )
            self._page_ref.show_dialog(result_dialog)

            # Refresh backups list if successful
            if success:
                await self.load_backups()

        except Exception as e:
            self.logger.error(f"Error restoring backup: {e}", exc_info=True)
            self._page_ref.pop_dialog()

            error_dialog = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(f"Failed to restore backup: {str(e)}"),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self._page_ref.pop_dialog(),
                    ),
                ],
            )
            self._page_ref.show_dialog(error_dialog)

    def _on_delete_backup(self, backup: DLLBackup):
        """Handle delete backup button click"""
        # Create dialog first without actions
        dialog = ft.AlertDialog(
            title=ft.Text("Delete Backup?"),
            content=ft.Column(
                controls=[
                    ft.Text(f"Game: {backup.game_name}"),
                    ft.Text(f"DLL: {backup.dll_filename}"),
                    ft.Divider(),
                    ft.Text(
                        "This will permanently delete the backup file.",
                        color=ft.Colors.RED_400,
                        size=12,
                    ),
                ],
                tight=True,
                spacing=8,
            ),
            actions_alignment=ft.MainAxisAlignment.END,
        )

        # Now add actions that reference the dialog
        dialog.actions = [
            ft.TextButton(
                "Cancel",
                on_click=lambda e: self._page_ref.pop_dialog(),
            ),
            ft.ElevatedButton(
                "Delete",
                on_click=self._create_delete_handler(backup, dialog),
                style=ft.ButtonStyle(
                    bgcolor=ft.Colors.RED_400,
                    color=ft.Colors.WHITE,
                ),
            ),
        ]

        self._page_ref.show_dialog(dialog)

    def _create_delete_handler(self, backup: DLLBackup, dialog: ft.AlertDialog):
        """Create async delete handler for specific backup"""
        async def handler(e):
            await self._perform_delete(backup, dialog)
        return handler

    async def _perform_delete(self, backup: DLLBackup, dialog: ft.AlertDialog):
        """Perform the delete operation"""
        self._page_ref.pop_dialog()

        try:
            # Perform delete
            success, message = await delete_backup(backup.id)

            # Show result
            result_dialog = ft.AlertDialog(
                title=ft.Text("Delete Complete" if success else "Delete Failed"),
                content=ft.Text(message),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self._page_ref.pop_dialog(),
                    ),
                ],
            )
            self._page_ref.show_dialog(result_dialog)

            # Refresh backups list if successful
            if success:
                await self.load_backups()

        except Exception as e:
            self.logger.error(f"Error deleting backup: {e}", exc_info=True)

            error_dialog = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(f"Failed to delete backup: {str(e)}"),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self._page_ref.pop_dialog(),
                    ),
                ],
            )
            self._page_ref.show_dialog(error_dialog)
