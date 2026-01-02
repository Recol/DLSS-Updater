"""
Backups View - Browse and restore DLL backups
"""

import asyncio
import math
from typing import List, Optional
import flet as ft

from dlss_updater.database import db_manager, DLLBackup
from dlss_updater.models import GameWithBackupCount, GameDLLBackup
from dlss_updater.backup_manager import restore_dll_from_backup, delete_backup
from dlss_updater.ui_flet.components.backup_card import BackupCard
from dlss_updater.ui_flet.theme.colors import MD3Colors


class BackupsView(ft.Column):
    """Backup management view"""

    def __init__(self, page: ft.Page, logger):
        super().__init__()
        self.page = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        # State
        self.backups: List[GameDLLBackup] = []
        self.is_loading = False
        self.refresh_button_ref = ft.Ref[ft.IconButton]()

        # Button references for state management
        self.clear_all_button: Optional[ft.ElevatedButton] = None

        # Game filter state
        self.selected_game_id: Optional[int] = None
        self.game_filter_dropdown: Optional[ft.Dropdown] = None
        self.games_with_backups: List[GameWithBackupCount] = []

        # Build initial UI
        self._build_ui()

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
        """Create dropdown for filtering backups by game with MD3 dark theme styling"""
        self.game_filter_dropdown = ft.Dropdown(
            label="Filter by Game",
            hint_text="All Games",
            options=[ft.dropdown.Option(key="all", text="All Games")],
            value="all",
            on_change=self._on_game_filter_changed,
            width=250,
            dense=True,
            text_size=14,
            bgcolor="#2E2E2E",
            color="#E4E2E0",
            border_color="#5A5A5A",
            focused_border_color="#2D6E88",
            border_radius=8,
            label_style=ft.TextStyle(color="#C4C7CA"),
            text_style=ft.TextStyle(color="#E4E2E0"),
            fill_color="#2E2E2E",
        )
        return self.game_filter_dropdown

    async def _on_game_filter_changed(self, e):
        """Handle game filter selection change"""
        value = e.control.value
        if value == "all":
            self.selected_game_id = None
        else:
            self.selected_game_id = int(value)
        await self.load_backups()

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

    def set_game_filter(self, game_id: Optional[int]):
        """Set game filter programmatically (for navigation from Games view)"""
        self.selected_game_id = game_id
        if self.game_filter_dropdown:
            self.game_filter_dropdown.value = str(game_id) if game_id else "all"

    def _build_ui(self):
        """Build initial UI"""
        # Get theme preference
        is_dark = self.page.session.get("is_dark_theme") if self.page and self.page.session.contains_key("is_dark_theme") else True

        # Header with game filter
        self.header = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(
                        "DLL Backup History",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.WHITE,
                    ),
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
            alignment=ft.alignment.center,
            expand=True,
        )

        # Loading indicator
        self.loading_indicator = ft.Container(
            content=ft.Column(
                controls=[
                    ft.ProgressRing(),
                    ft.Text("Loading backups...", color=ft.Colors.WHITE),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            alignment=ft.alignment.center,
            expand=True,
            visible=False,
        )

        # Backups grid (ResponsiveRow with 2 columns max for wider backup cards)
        self.backups_grid_container = ft.Container(
            content=ft.Column(
                controls=[],  # Will contain ResponsiveRow
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            padding=16,
            expand=True,
            visible=False,
        )

        # Assemble
        self.controls = [
            self.header,
            ft.Divider(height=1, color="#5A5A5A"),
            ft.Stack(
                controls=[
                    self.empty_state,
                    self.loading_indicator,
                    self.backups_grid_container,
                ],
                expand=True,
            ),
        ]

    async def load_backups(self):
        """Load backups from database with optional game filter"""
        if self.is_loading:
            return

        self.is_loading = True
        self.loading_indicator.visible = True
        self.empty_state.visible = False
        self.backups_grid_container.visible = False
        if self.page:
            self.page.update()

        try:
            # Ensure database pool is ready
            await db_manager.ensure_pool()

            self.logger.info("Loading backups from database...")

            # Get games with backups for filter dropdown
            self.games_with_backups = await db_manager.get_games_with_backups()
            self._update_game_filter_options()

            # Get backups with optional game filter
            self.backups = await db_manager.get_all_backups_filtered(self.selected_game_id)

            if not self.backups:
                self.logger.info("No backups found")
                self.empty_state.visible = True
                self.loading_indicator.visible = False
                self._update_clear_button_state(False)
                if self.page:
                    self.page.update()
                return

            # Build backup cards with ResponsiveRow grid (2 columns max)
            # Backups are wider cards, so xs=12 (1 col), sm=12 (1 col), md=6 (2 col), lg=6 (2 col)
            responsive_cards = []
            for backup in self.backups:
                # Convert GameDLLBackup to DLLBackup for BackupCard compatibility
                dll_backup = DLLBackup(
                    id=backup.id,
                    game_dll_id=backup.game_dll_id,
                    game_name=backup.game_name,
                    dll_filename=backup.dll_filename,
                    backup_path=backup.backup_path,
                    backup_size=backup.backup_size,
                    original_version=backup.original_version,
                    backup_created_at=backup.backup_created_at,
                    is_active=backup.is_active,
                )

                card = BackupCard(
                    backup=dll_backup,
                    page=self.page,
                    logger=self.logger,
                    on_restore=self._on_restore_backup,
                    on_delete=self._on_delete_backup,
                )
                # Wrap in responsive column
                responsive_card = ft.Column(
                    controls=[card],
                    col={"xs": 12, "sm": 12, "md": 6, "lg": 6},
                    tight=True,
                )
                responsive_cards.append(responsive_card)

            # Create ResponsiveRow and update container
            backup_grid = ft.ResponsiveRow(
                controls=responsive_cards,
                spacing=12,
                run_spacing=12,
            )

            # Update the grid container's content
            self.backups_grid_container.content.controls = [backup_grid]
            self.backups_grid_container.visible = True
            self.empty_state.visible = False
            self.loading_indicator.visible = False
            self._update_clear_button_state(True)

            self.logger.info(f"Loaded {len(self.backups)} backups")

        except Exception as e:
            self.logger.error(f"Error loading backups: {e}", exc_info=True)
            self.empty_state.visible = True
            self.loading_indicator.visible = False
            self._update_clear_button_state(False)

        finally:
            self.is_loading = False
            if self.page:
                self.page.update()

    async def _on_refresh_clicked(self, e):
        """Handle refresh button click with rotation animation"""
        # Rotate refresh button
        if self.refresh_button_ref.current:
            self.refresh_button_ref.current.rotate += math.pi * 2  # 360 degrees
            self.page.update()

        await self.load_backups()

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
                ft.TextButton("OK", on_click=lambda e: self.page.close(info_dialog)),
            ]
            self.page.open(info_dialog)
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
                on_click=lambda e: self.page.close(confirm_dialog),
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

        self.page.open(confirm_dialog)

    def _create_clear_all_handler(self, dialog: ft.AlertDialog):
        """Create async clear all handler"""
        async def handler(e):
            await self._perform_clear_all(dialog)
        return handler

    async def _perform_clear_all(self, dialog: ft.AlertDialog):
        """Perform the clear all operation"""
        self.page.close(dialog)

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
        self.page.open(progress_dialog)
        self.page.update()

        try:
            # Delete all backups from database
            deleted_count = await db_manager.delete_all_backups()

            # Close progress dialog
            self.page.close(progress_dialog)

            # Show success dialog
            success_dialog = ft.AlertDialog(
                title=ft.Text("Success"),
                content=ft.Text(f"Successfully cleared {deleted_count} backup(s)."),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self.page.close(success_dialog),
                    ),
                ],
            )
            self.page.open(success_dialog)

            # Reload backups list
            await self.load_backups()

        except Exception as ex:
            self.logger.error(f"Error clearing all backups: {ex}", exc_info=True)

            # Close progress dialog
            self.page.close(progress_dialog)

            # Show error dialog
            error_dialog = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(f"Failed to clear backups: {str(ex)}"),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self.page.close(error_dialog),
                    ),
                ],
            )
            self.page.open(error_dialog)

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
                on_click=lambda e: self.page.close(dialog),
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

        self.page.open(dialog)

    def _create_restore_handler(self, backup: DLLBackup, dialog: ft.AlertDialog):
        """Create async restore handler for specific backup"""
        async def handler(e):
            await self._perform_restore(backup, dialog)
        return handler

    async def _perform_restore(self, backup: DLLBackup, dialog: ft.AlertDialog):
        """Perform the restore operation"""
        self.page.close(dialog)

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
        self.page.open(progress_dialog)

        try:
            # Perform restore
            success, message = await restore_dll_from_backup(backup.id)

            # Close progress dialog
            self.page.close(progress_dialog)

            # Show result
            result_dialog = ft.AlertDialog(
                title=ft.Text("Restore Complete" if success else "Restore Failed"),
                content=ft.Text(message),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self.page.close(result_dialog),
                    ),
                ],
            )
            self.page.open(result_dialog)

            # Refresh backups list if successful
            if success:
                await self.load_backups()

        except Exception as e:
            self.logger.error(f"Error restoring backup: {e}", exc_info=True)
            self.page.close(progress_dialog)

            error_dialog = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(f"Failed to restore backup: {str(e)}"),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self.page.close(error_dialog),
                    ),
                ],
            )
            self.page.open(error_dialog)

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
                on_click=lambda e: self.page.close(dialog),
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

        self.page.open(dialog)

    def _create_delete_handler(self, backup: DLLBackup, dialog: ft.AlertDialog):
        """Create async delete handler for specific backup"""
        async def handler(e):
            await self._perform_delete(backup, dialog)
        return handler

    async def _perform_delete(self, backup: DLLBackup, dialog: ft.AlertDialog):
        """Perform the delete operation"""
        self.page.close(dialog)

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
                        on_click=lambda e: self.page.close(result_dialog),
                    ),
                ],
            )
            self.page.open(result_dialog)

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
                        on_click=lambda e: self.page.close(error_dialog),
                    ),
                ],
            )
            self.page.open(error_dialog)
