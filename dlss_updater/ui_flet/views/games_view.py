"""
Games View - Display all games organized by launcher with Steam images
"""

import asyncio
import math
from typing import Dict, List, Optional, Any
import flet as ft

from dlss_updater.database import db_manager, Game
from dlss_updater.ui_flet.components.game_card import GameCard
from dlss_updater.ui_flet.components.search_bar import SearchBar
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.async_updater import AsyncUpdateCoordinator
from dlss_updater.config import is_dll_cache_ready
from dlss_updater.search_service import search_service


class GamesView(ft.Column):
    """Games library view with launcher tabs"""

    def __init__(self, page: ft.Page, logger):
        super().__init__()
        self.page = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        # State
        self.games_by_launcher: Dict[str, List[Game]] = {}
        self.is_loading = False
        self.refresh_button_ref = ft.Ref[ft.IconButton]()

        # Game card tracking for single-game updates
        self.game_cards: Dict[int, GameCard] = {}  # game_id -> GameCard
        self.game_card_containers: Dict[int, ft.Container] = {}  # game_id -> container wrapper
        self.update_coordinator: Optional[AsyncUpdateCoordinator] = None

        # Button references for state management
        self.delete_db_button: Optional[ft.ElevatedButton] = None

        # Search state
        self.search_query: str = ""
        self._search_generation: int = 0
        self.search_bar: Optional[SearchBar] = None

        # Build initial UI
        self._build_ui()

    def _create_delete_db_button(self) -> ft.ElevatedButton:
        """Create and store reference to Delete Database button"""
        self.delete_db_button = ft.ElevatedButton(
            "Delete Database",
            icon=ft.Icons.DELETE_SWEEP,
            on_click=self._on_delete_all_clicked,
            disabled=True,  # Initially disabled until games are loaded
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.RED_400,
                color=ft.Colors.WHITE,
            ),
        )
        return self.delete_db_button

    def _update_delete_button_state(self, has_games: bool):
        """Update delete button enabled/disabled state"""
        if self.delete_db_button:
            self.delete_db_button.disabled = not has_games

    def _build_ui(self):
        """Build initial UI with empty state"""
        # Get theme preference
        is_dark = self.page.session.get("is_dark_theme") if self.page and self.page.session.contains_key("is_dark_theme") else True

        # Create search bar
        self.search_bar = SearchBar(
            on_search=self._on_search_changed,
            on_clear=self._on_search_cleared,
            on_history_selected=self._on_history_selected,
            placeholder="Search games...",
            width=300,
        )

        # Header
        self.header = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(
                        "Games Library",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.WHITE,
                    ),
                    ft.Container(expand=True),  # Spacer
                    self.search_bar,
                    ft.Container(width=16),  # Spacing
                    self._create_delete_db_button(),
                    ft.IconButton(
                        icon=ft.Icons.REFRESH,
                        tooltip="Refresh Games",
                        on_click=self._on_refresh_clicked,
                        animate_rotation=ft.Animation(400, ft.AnimationCurve.EASE_IN_OUT),
                        rotate=0,
                        ref=self.refresh_button_ref,
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=16,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
        )

        # Empty state
        self.empty_state = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.VIDEOGAME_ASSET_OFF, size=64, color=ft.Colors.GREY),
                    ft.Text(
                        "No games found",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.GREY,
                    ),
                    ft.Text(
                        "Click 'Scan for Games' in the Launchers view",
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
                    ft.Text("Loading games...", color=ft.Colors.WHITE),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            alignment=ft.alignment.center,
            expand=True,
            visible=False,
        )

        # Tabs container (will be populated with launcher tabs)
        self.tabs_container = ft.Container(
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
                    self.tabs_container,
                ],
                expand=True,
            ),
        ]

    async def load_games(self):
        """Load games from database and display"""
        if self.is_loading:
            return

        self.is_loading = True
        self.loading_indicator.visible = True
        self.empty_state.visible = False
        self.tabs_container.visible = False
        if self.page:
            self.page.update()

        try:
            # Ensure database pool is ready
            await db_manager.ensure_pool()

            self.logger.info("Loading games from database...")

            # Get games grouped by launcher
            self.games_by_launcher = await db_manager.get_games_grouped_by_launcher()

            if not self.games_by_launcher or sum(len(games) for games in self.games_by_launcher.values()) == 0:
                self.logger.info("No games found in database")
                self.empty_state.visible = True
                self.loading_indicator.visible = False
                self._update_delete_button_state(False)
                if self.page:
                    self.page.update()
                return

            # Build launcher tabs
            await self._build_launcher_tabs()

            self.tabs_container.visible = True
            self.empty_state.visible = False
            self.loading_indicator.visible = False
            self._update_delete_button_state(True)

            # Build search index only if not already built, then load history
            if not search_service.is_index_built():
                await search_service.build_index(self.games_by_launcher)
            await self._load_search_history()

            self.logger.info(f"Loaded {sum(len(games) for games in self.games_by_launcher.values())} games from {len(self.games_by_launcher)} launchers")

        except Exception as e:
            self.logger.error(f"Error loading games: {e}", exc_info=True)
            self.empty_state.visible = True
            self.loading_indicator.visible = False
            self._update_delete_button_state(False)

        finally:
            self.is_loading = False
            if self.page:
                self.page.update()

    async def _build_launcher_tabs(self):
        """Build tabs for each launcher with games"""
        tabs = []

        # Clear existing game cards tracking
        self.game_cards.clear()
        self.game_card_containers.clear()

        # Launcher icons mapping
        launcher_icons = {
            "Steam": ft.Icons.VIDEOGAME_ASSET,
            "EA Launcher": ft.Icons.SPORTS_ESPORTS,
            "Epic Games Launcher": ft.Icons.GAMES,
            "Ubisoft Launcher": ft.Icons.GAMEPAD,
            "GOG Launcher": ft.Icons.VIDEOGAME_ASSET_OUTLINED,
            "Battle.net Launcher": ft.Icons.MILITARY_TECH,
            "Xbox Launcher": ft.Icons.SPORTS_ESPORTS_OUTLINED,
            "Custom Folder 1": ft.Icons.FOLDER_SPECIAL,
            "Custom Folder 2": ft.Icons.FOLDER_SPECIAL,
            "Custom Folder 3": ft.Icons.FOLDER_SPECIAL,
            "Custom Folder 4": ft.Icons.FOLDER_SPECIAL,
        }

        for launcher, games in self.games_by_launcher.items():
            if not games:
                continue

            # Load DLLs and backup groups in parallel for all games
            async def load_game_data(game):
                dlls = await db_manager.get_dlls_for_game(game.id)
                backup_groups = await db_manager.get_backups_grouped_by_dll_type(game.id)
                return game, dlls, backup_groups

            tasks = [load_game_data(game) for game in games]
            results = await asyncio.gather(*tasks)

            # Create game cards for this launcher
            game_cards = []
            for game, dlls, backup_groups in results:
                # Create game card
                card = GameCard(
                    game=game,
                    dlls=dlls,
                    page=self.page,
                    logger=self.logger,
                    on_update=self._on_game_update,
                    on_restore=self._on_game_restore,
                    backup_groups=backup_groups,
                )

                # Track card for single-game updates
                self.game_cards[game.id] = card

                # Set initial opacity for staggered animation
                card.opacity = 0
                card.animate_opacity = ft.Animation(400, ft.AnimationCurve.EASE_OUT)

                game_cards.append(card)

                # Load image asynchronously (non-blocking)
                asyncio.create_task(card.load_image())

            # Trigger staggered fade-in animation for game cards
            asyncio.create_task(self._animate_cards_in(game_cards))

            # Create ResponsiveRow grid for this launcher's games (Flet 0.28.3 compatible)
            # Wrap each card with responsive column sizing
            # Breakpoints: xs=12 (1 col), sm=6 (2 col), md=4 (3 col), lg=3 (4 col)
            responsive_cards = []
            for card in game_cards:
                responsive_card = ft.Container(
                    content=card,
                    col={"xs": 12, "sm": 6, "md": 4, "lg": 3},
                    height=180,  # Fixed height to match game card
                )
                responsive_cards.append(responsive_card)
                # Track container for search visibility control
                self.game_card_containers[card.game.id] = responsive_card

            # Create ResponsiveRow with scrollable container
            game_grid = ft.Column(
                controls=[
                    ft.ResponsiveRow(
                        controls=responsive_cards,
                        spacing=12,
                        run_spacing=12,
                    )
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )

            # Wrap in container for padding
            game_list = ft.Container(
                content=game_grid,
                padding=16,
                expand=True,
            )

            # Create tab
            tab = ft.Tab(
                text=f"{launcher} ({len(games)})",
                icon=launcher_icons.get(launcher, ft.Icons.FOLDER),
                content=game_list,
            )
            tabs.append(tab)

        # Create Tabs control with tab change handler for search reapplication
        self.tabs_control = ft.Tabs(
            tabs=tabs,
            animation_duration=300,
            expand=True,
            on_change=self._on_tab_changed,
        )

        self.tabs_container.content = self.tabs_control

    async def _on_tab_changed(self, e):
        """Handle tab change - reapply search filter to new tab."""
        if self.search_query:
            await self._execute_search(self.search_query, self._search_generation)
        else:
            await self._show_all_games()

    async def _animate_cards_in(self, game_cards: List[GameCard]):
        """Animate game cards with staggered fade-in for grid layout"""
        # Small initial delay
        await asyncio.sleep(0.1)

        # For grid layout, animate first 12 cards (3 rows of 4 columns)
        cards_to_animate = game_cards[:12]
        for i, card in enumerate(cards_to_animate):
            await asyncio.sleep(0.04)  # 40ms delay - slightly faster for grid
            card.opacity = 1
            if self.page:
                self.page.update()

        # Set remaining cards to visible immediately
        for card in game_cards[12:]:
            card.opacity = 1
        if self.page:
            self.page.update()

    async def _on_refresh_clicked(self, e):
        """Handle refresh button click with rotation animation"""
        # Rotate refresh button
        if self.refresh_button_ref.current:
            self.refresh_button_ref.current.rotate += math.pi * 2  # 360 degrees
            self.page.update()

        await self.load_games()

    # ===== Search Methods =====

    async def _on_search_changed(self, query: str):
        """Handle search input changes with generation token pattern."""
        # Increment generation to invalidate in-flight searches
        self._search_generation += 1
        current_gen = self._search_generation

        self.search_query = query.strip()

        if not self.search_query:
            await self._show_all_games()
            return

        # Execute search filtering
        await self._execute_search(self.search_query, current_gen)

    async def _on_search_cleared(self):
        """Handle search clear button click."""
        self.search_query = ""
        self._search_generation += 1
        await self._show_all_games()

    async def _on_history_selected(self, query: str):
        """Handle search history item selection."""
        self.search_query = query
        self._search_generation += 1
        current_gen = self._search_generation
        await self._execute_search(query, current_gen)

    async def _execute_search(self, query: str, generation: int):
        """Execute search filtering on game cards."""
        # Check if this search has been superseded
        if generation != self._search_generation:
            return

        query_lower = query.lower()

        # Get current tab launcher
        current_launcher = self._get_current_launcher()

        # Filter cards by visibility (set on container wrapper for proper reflow)
        matching_count = 0
        for game_id, card in self.game_cards.items():
            if current_launcher and card.game.launcher != current_launcher:
                continue

            # Check if game name matches query
            matches = query_lower in card.game.name.lower()

            # Set visibility on container wrapper for proper ResponsiveRow reflow
            container = self.game_card_containers.get(game_id)
            if container:
                container.visible = matches
            if matches:
                matching_count += 1

        if self.page:
            self.page.update()

        # Save to search history after search (if results exist)
        if matching_count > 0 and len(query) >= 2:
            await db_manager.add_search_history(query, current_launcher, matching_count)
            # Refresh history button to show new entry
            await self._load_search_history()

        self.logger.debug(f"Search '{query}' found {matching_count} matches")

    async def _show_all_games(self):
        """Show all games (clear search filter)."""
        current_launcher = self._get_current_launcher()

        for game_id, card in self.game_cards.items():
            container = self.game_card_containers.get(game_id)
            if container:
                if current_launcher:
                    container.visible = card.game.launcher == current_launcher
                else:
                    container.visible = True

        if self.page:
            self.page.update()

    def _get_current_launcher(self) -> Optional[str]:
        """Get the currently selected launcher tab."""
        if not hasattr(self, 'tabs_control') or not self.tabs_control:
            return None

        if self.tabs_control.selected_index is None:
            return None

        # Get launcher name from tab text (format: "Launcher (N)")
        tabs = self.tabs_control.tabs
        if 0 <= self.tabs_control.selected_index < len(tabs):
            tab_text = tabs[self.tabs_control.selected_index].text or ""
            # Extract launcher name (remove count suffix)
            if " (" in tab_text:
                return tab_text.rsplit(" (", 1)[0]
            return tab_text
        return None

    async def _load_search_history(self):
        """Load search history for dropdown."""
        if self.search_bar:
            history = await search_service.get_search_history(limit=10)
            self.search_bar.update_history(history)

    async def _on_delete_all_clicked(self, e):
        """Handle delete all games button click"""
        # Count current games
        total_games = sum(len(games) for games in self.games_by_launcher.values())

        if total_games == 0:
            # Show info dialog if no games - create without actions first
            info_dialog = ft.AlertDialog(
                title=ft.Text("No Games"),
                content=ft.Text("There are no games to delete."),
            )
            # Add actions after dialog variable exists
            info_dialog.actions = [
                ft.TextButton("OK", on_click=lambda e: self.page.close(info_dialog)),
            ]
            self.page.open(info_dialog)
            return

        # Show confirmation dialog - create without actions first
        confirm_dialog = ft.AlertDialog(
            title=ft.Text("Delete All Games?"),
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.WARNING, color=ft.Colors.ORANGE, size=48),
                    ft.Text(
                        f"This will delete all {total_games} game(s) from the database.",
                        size=14,
                    ),
                    ft.Text(
                        "All associated DLLs, backups, and update history will also be deleted.",
                        size=12,
                        color=ft.Colors.ORANGE,
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
                "Delete All",
                on_click=self._create_delete_all_handler(confirm_dialog),
                style=ft.ButtonStyle(
                    bgcolor=ft.Colors.RED_400,
                    color=ft.Colors.WHITE,
                ),
            ),
        ]

        self.page.open(confirm_dialog)

    def _create_delete_all_handler(self, dialog: ft.AlertDialog):
        """Create async delete all handler"""
        async def handler(e):
            await self._perform_delete_all(dialog)
        return handler

    async def _perform_delete_all(self, dialog: ft.AlertDialog):
        """Perform the delete all operation"""
        self.page.close(dialog)

        # Show progress indicator
        progress_dialog = ft.AlertDialog(
            title=ft.Text("Deleting Games..."),
            content=ft.Column(
                controls=[
                    ft.ProgressRing(),
                    ft.Text("Deleting all games...", size=12),
                ],
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        self.page.open(progress_dialog)
        self.page.update()

        try:
            # Delete all games from database
            from dlss_updater.database import db_manager
            deleted_count = await db_manager.delete_all_games()

            # Close progress dialog
            self.page.close(progress_dialog)

            # Show success dialog - create without actions first
            success_dialog = ft.AlertDialog(
                title=ft.Text("Success"),
                content=ft.Text(f"Successfully deleted {deleted_count} game(s)."),
            )
            # Add actions after dialog variable exists
            success_dialog.actions = [
                ft.TextButton(
                    "OK",
                    on_click=lambda e: self.page.close(success_dialog),
                ),
            ]
            self.page.open(success_dialog)

            # Reload games list
            await self.load_games()

        except Exception as ex:
            self.logger.error(f"Error deleting all games: {ex}", exc_info=True)

            # Close progress dialog
            self.page.close(progress_dialog)

            # Show error dialog - create without actions first
            error_dialog = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(f"Failed to delete games: {str(ex)}"),
            )
            # Add actions after dialog variable exists
            error_dialog.actions = [
                ft.TextButton(
                    "OK",
                    on_click=lambda e: self.page.close(error_dialog),
                ),
            ]
            self.page.open(error_dialog)

    def _on_game_update(self, game, dll_group: str = "all"):
        """Handle game update button click - launches async update"""
        self.logger.info(f"Update requested for game: {game.name}, group: {dll_group}")
        # Launch the async update using Flet's page.run_task for proper event loop handling
        if self.page:
            self.page.run_task(self._perform_game_update, game, dll_group)

    async def _perform_game_update(self, game, dll_group: str = "all"):
        """Perform the single-game DLL update"""
        self.logger.info(f"Starting update for game: {game.name} (id: {game.id}, group: {dll_group})")

        # Check if DLL cache is ready
        if not is_dll_cache_ready():
            self.logger.warning("Update attempted before DLL cache initialized")
            await self._show_error_dialog(
                "Please Wait",
                "DLL cache is still initializing. Please wait a moment and try again.",
                ft.Colors.ORANGE
            )
            return

        # Find the game card to update its state
        game_card = self.game_cards.get(game.id)

        # Create and show progress dialog
        progress_dialog = self._create_update_progress_dialog(game.name, dll_group)
        self.page.open(progress_dialog)

        # Set card to updating state
        if game_card:
            game_card.set_updating(True)

        try:
            # Create coordinator if not exists
            if not self.update_coordinator:
                self.update_coordinator = AsyncUpdateCoordinator(self.logger)

            # Progress callback to update dialog
            async def on_progress(progress):
                self._update_progress_dialog(progress_dialog, progress)

            # Run update with optional group filter
            result = await self.update_coordinator.update_single_game(
                game.id,
                game.name,
                dll_groups=[dll_group] if dll_group != "all" else None,
                progress_callback=on_progress
            )

            # Close progress dialog
            self.page.close(progress_dialog)

            # Show results
            await self._show_update_results_dialog(game.name, result)

            # Refresh the game card's DLL badges if update succeeded
            if result['success'] and game_card:
                new_dlls = await db_manager.get_dlls_for_game(game.id)
                await game_card.refresh_dlls(new_dlls)

        except Exception as ex:
            self.logger.error(f"Update failed for {game.name}: {ex}", exc_info=True)
            self.page.close(progress_dialog)
            await self._show_error_dialog(
                "Update Failed",
                f"Failed to update {game.name}: {str(ex)}",
                ft.Colors.RED
            )
        finally:
            # Reset card state
            if game_card:
                game_card.set_updating(False)

    def _create_update_progress_dialog(self, game_name: str, dll_group: str = "all") -> ft.AlertDialog:
        """Create progress dialog for single-game update"""
        self._progress_ring = ft.ProgressRing(width=40, height=40)
        self._progress_text = ft.Text("Preparing update...", size=14)
        self._progress_detail = ft.Text("", size=12, color="#888888")

        # Show which group is being updated in the title
        title_text = f"Updating {game_name}"
        if dll_group != "all":
            title_text = f"Updating {dll_group} - {game_name}"

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(title_text),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[self._progress_ring],
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        self._progress_text,
                        self._progress_detail,
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=12,
                    tight=True,
                ),
                width=300,
                padding=20,
            ),
        )
        return dialog

    def _update_progress_dialog(self, dialog: ft.AlertDialog, progress):
        """Update progress dialog with current progress"""
        if hasattr(self, '_progress_text') and self._progress_text:
            self._progress_text.value = progress.message
        if hasattr(self, '_progress_detail') and self._progress_detail:
            self._progress_detail.value = f"{progress.current}/{progress.total} DLLs processed"
        if self.page:
            self.page.update()

    async def _show_update_results_dialog(self, game_name: str, result: Dict[str, Any]):
        """Show results dialog after single-game update"""
        # Build result content
        content_controls = []

        if result['updated']:
            content_controls.append(ft.Text("Updated:", weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN))
            for item in result['updated']:
                content_controls.append(ft.Text(f"  - {item['dll_type']}", size=12))

        if result['skipped']:
            if content_controls:
                content_controls.append(ft.Container(height=8))
            content_controls.append(ft.Text("Skipped:", weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE))
            for item in result['skipped']:
                reason = item.get('reason', 'Already up to date')
                content_controls.append(ft.Text(f"  - {item['dll_type']}: {reason}", size=12))

        if result['errors']:
            if content_controls:
                content_controls.append(ft.Container(height=8))
            content_controls.append(ft.Text("Errors:", weight=ft.FontWeight.BOLD, color=ft.Colors.RED))
            for item in result['errors']:
                dll_type = item.get('dll_type', 'Unknown')
                content_controls.append(ft.Text(f"  - {dll_type}: {item['message']}", size=12))

        if not content_controls:
            content_controls.append(ft.Text("No DLLs were processed.", color="#888888"))

        # Determine title and icon based on results
        if result['success']:
            title = ft.Text(f"Update Complete - {game_name}", color=ft.Colors.GREEN)
            icon = ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=48)
        elif result['errors']:
            title = ft.Text(f"Update Failed - {game_name}", color=ft.Colors.RED)
            icon = ft.Icon(ft.Icons.ERROR, color=ft.Colors.RED, size=48)
        else:
            title = ft.Text(f"No Updates - {game_name}", color=ft.Colors.ORANGE)
            icon = ft.Icon(ft.Icons.INFO, color=ft.Colors.ORANGE, size=48)

        # Create dialog without actions first
        results_dialog = ft.AlertDialog(
            title=title,
            content=ft.Column(
                controls=[icon] + content_controls,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        )
        # Add actions after dialog exists
        results_dialog.actions = [
            ft.TextButton("OK", on_click=lambda e: self.page.close(results_dialog)),
        ]
        self.page.open(results_dialog)

    async def _show_error_dialog(self, title: str, message: str, color=ft.Colors.RED):
        """Show error dialog"""
        error_dialog = ft.AlertDialog(
            title=ft.Text(title, color=color),
            content=ft.Text(message),
        )
        error_dialog.actions = [
            ft.TextButton("OK", on_click=lambda e: self.page.close(error_dialog)),
        ]
        self.page.open(error_dialog)

    def _on_game_restore(self, game, dll_group: str = "all"):
        """Handle game restore button click - launches async restore"""
        self.logger.info(f"Restore requested for game: {game.name}, group: {dll_group}")
        if self.page:
            self.page.run_task(self._perform_game_restore, game, dll_group)

    async def _perform_game_restore(self, game, dll_group: str = "all"):
        """Perform the per-game DLL restore operation"""
        from dlss_updater.backup_manager import restore_group_for_game

        game_card = self.game_cards.get(game.id)

        # Show confirmation dialog
        confirmed = await self._show_restore_confirmation_dialog(game, dll_group)
        if not confirmed:
            return

        # Create and show progress dialog
        progress_dialog = self._create_restore_progress_dialog(game.name, dll_group)
        self.page.open(progress_dialog)

        try:
            # Perform restore
            success, summary, results = await restore_group_for_game(game.id, dll_group)

            # Close progress dialog
            self.page.close(progress_dialog)

            # Show results
            await self._show_restore_results_dialog(game.name, success, summary, results)

            # Refresh the game card's DLL badges and backup groups
            if game_card:
                new_dlls = await db_manager.get_dlls_for_game(game.id)
                await game_card.refresh_dlls(new_dlls)
                new_backup_groups = await db_manager.get_backups_grouped_by_dll_type(game.id)
                await game_card.refresh_restore_button(new_backup_groups)

        except Exception as ex:
            self.logger.error(f"Restore failed for {game.name}: {ex}", exc_info=True)
            self.page.close(progress_dialog)
            await self._show_error_dialog(
                "Restore Failed",
                f"Failed to restore {game.name}: {str(ex)}",
                ft.Colors.RED
            )

    async def _show_restore_confirmation_dialog(self, game, dll_group: str) -> bool:
        """Show confirmation dialog before restore, returns True if confirmed"""
        confirmed = asyncio.Event()
        result = [False]  # Use list to capture result in closure

        title = f"Restore {game.name}?"
        if dll_group != "all":
            title = f"Restore {dll_group} for {game.name}?"

        dialog = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.RESTORE, color="#4CAF50", size=48),
                    ft.Text(
                        "This will restore DLLs from backup.",
                        size=14,
                    ),
                    ft.Text(
                        "Make sure the game is closed before restoring.",
                        size=12,
                        color=ft.Colors.ORANGE,
                    ),
                ],
                tight=True,
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            actions_alignment=ft.MainAxisAlignment.END,
        )

        def on_cancel(e):
            result[0] = False
            self.page.close(dialog)
            confirmed.set()

        def on_confirm(e):
            result[0] = True
            self.page.close(dialog)
            confirmed.set()

        dialog.actions = [
            ft.TextButton("Cancel", on_click=on_cancel),
            ft.ElevatedButton(
                "Restore",
                on_click=on_confirm,
                style=ft.ButtonStyle(bgcolor="#4CAF50", color=ft.Colors.WHITE),
            ),
        ]

        self.page.open(dialog)
        await confirmed.wait()
        return result[0]

    def _create_restore_progress_dialog(self, game_name: str, dll_group: str = "all") -> ft.AlertDialog:
        """Create progress dialog for restore operation"""
        title_text = f"Restoring {game_name}"
        if dll_group != "all":
            title_text = f"Restoring {dll_group} - {game_name}"

        return ft.AlertDialog(
            modal=True,
            title=ft.Text(title_text),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[ft.ProgressRing(width=40, height=40)],
                            alignment=ft.MainAxisAlignment.CENTER,
                        ),
                        ft.Text("Restoring DLLs from backup...", size=14),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=12,
                    tight=True,
                ),
                width=300,
                padding=20,
            ),
        )

    async def _show_restore_results_dialog(self, game_name: str, success: bool, summary: str, results: list):
        """Show results dialog after restore"""
        content_controls = []

        # Successful restores
        successful = [r for r in results if r['success']]
        if successful:
            content_controls.append(ft.Text("Restored:", weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN))
            for item in successful:
                content_controls.append(ft.Text(f"  - {item['dll_filename']}", size=12))

        # Failed restores
        failed = [r for r in results if not r['success']]
        if failed:
            if content_controls:
                content_controls.append(ft.Container(height=8))
            content_controls.append(ft.Text("Failed:", weight=ft.FontWeight.BOLD, color=ft.Colors.RED))
            for item in failed:
                content_controls.append(ft.Text(f"  - {item['dll_filename']}: {item['message']}", size=12))

        if not content_controls:
            content_controls.append(ft.Text("No DLLs were restored.", color="#888888"))

        # Determine title and icon
        if success:
            title = ft.Text(f"Restore Complete - {game_name}", color=ft.Colors.GREEN)
            icon = ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN, size=48)
        else:
            title = ft.Text(f"Restore Failed - {game_name}", color=ft.Colors.RED)
            icon = ft.Icon(ft.Icons.ERROR, color=ft.Colors.RED, size=48)

        results_dialog = ft.AlertDialog(
            title=title,
            content=ft.Column(
                controls=[icon, ft.Text(summary, size=14, color="#888888")] + content_controls,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        )
        results_dialog.actions = [
            ft.TextButton("OK", on_click=lambda e: self.page.close(results_dialog)),
        ]
        self.page.open(results_dialog)

    async def on_view_hidden(self):
        """Called when view is hidden - release resources"""
        from dlss_updater.search_service import search_service
        search_service.clear_index()
        self.logger.debug("Games view hidden - resources released")
