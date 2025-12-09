"""
Games View - Display all games organized by launcher with Steam images
"""

import asyncio
import math
from typing import Dict, List
import flet as ft

from dlss_updater.database import db_manager, Game
from dlss_updater.ui_flet.components.game_card import GameCard
from dlss_updater.ui_flet.theme.colors import MD3Colors


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

        # Build initial UI
        self._build_ui()

    def _build_ui(self):
        """Build initial UI with empty state"""
        # Get theme preference
        is_dark = self.page.session.get("is_dark_theme") if self.page and self.page.session.contains_key("is_dark_theme") else True

        # Header
        self.header = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(
                        "Games Library",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.WHITE,
                        expand=True,
                    ),
                    ft.ElevatedButton(
                        "Delete All Games",
                        icon=ft.Icons.DELETE_SWEEP,
                        on_click=self._on_delete_all_clicked,
                        style=ft.ButtonStyle(
                            bgcolor=ft.Colors.RED_400,
                            color=ft.Colors.WHITE,
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.REFRESH,
                        tooltip="Refresh Games",
                        on_click=self._on_refresh_clicked,
                        animate_rotation=ft.Animation(400, ft.AnimationCurve.EASE_IN_OUT),
                        rotate=0,
                        ref=self.refresh_button_ref,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
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
            self.logger.info("Loading games from database...")

            # Get games grouped by launcher
            self.games_by_launcher = await db_manager.get_games_grouped_by_launcher()

            if not self.games_by_launcher or sum(len(games) for games in self.games_by_launcher.values()) == 0:
                self.logger.info("No games found in database")
                self.empty_state.visible = True
                self.loading_indicator.visible = False
                if self.page:
                    self.page.update()
                return

            # Build launcher tabs
            await self._build_launcher_tabs()

            self.tabs_container.visible = True
            self.empty_state.visible = False
            self.loading_indicator.visible = False

            self.logger.info(f"Loaded {sum(len(games) for games in self.games_by_launcher.values())} games from {len(self.games_by_launcher)} launchers")

        except Exception as e:
            self.logger.error(f"Error loading games: {e}", exc_info=True)
            self.empty_state.visible = True
            self.loading_indicator.visible = False

        finally:
            self.is_loading = False
            if self.page:
                self.page.update()

    async def _build_launcher_tabs(self):
        """Build tabs for each launcher with games"""
        tabs = []

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

            # Create game cards for this launcher
            game_cards = []
            for game in games:
                # Get DLLs for this game
                dlls = await db_manager.get_dlls_for_game(game.id)

                # Create game card
                card = GameCard(
                    game=game,
                    dlls=dlls,
                    page=self.page,
                    logger=self.logger,
                    on_update=self._on_game_update,
                    on_view_backups=self._on_view_backups,
                )

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
                responsive_card = ft.Column(
                    controls=[card],
                    col={"xs": 12, "sm": 6, "md": 4, "lg": 3},
                    tight=True,
                )
                responsive_cards.append(responsive_card)

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

        # Create Tabs control
        self.tabs_control = ft.Tabs(
            tabs=tabs,
            animation_duration=300,
            expand=True,
        )

        self.tabs_container.content = self.tabs_control

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

    def _on_game_update(self, game):
        """Handle game update button click"""
        self.logger.info(f"Update requested for game: {game.name}")
        # TODO: Implement update for individual game
        # This would trigger the update process for just this game's DLLs
        if self.page:
            self.page.open(
                ft.AlertDialog(
                    title=ft.Text("Update Game"),
                    content=ft.Text(f"Update functionality for {game.name} will be implemented soon.\n\nFor now, use 'Start Update' in the Launchers view."),
                    actions=[
                        ft.TextButton("OK", on_click=lambda e: self.page.close(e.control.parent) if self.page else None),
                    ],
                )
            )

    def _on_view_backups(self, game):
        """Handle view backups button click"""
        self.logger.info(f"View backups for game: {game.name}")
        # Navigate to Backups view and filter by this game
        # This would require passing the game filter to backups view
        if self.page:
            self.page.open(
                ft.AlertDialog(
                    title=ft.Text("View Backups"),
                    content=ft.Text(f"Navigate to the Backups view to see backups for {game.name}"),
                    actions=[
                        ft.TextButton("OK", on_click=lambda e: self.page.close(e.control.parent) if self.page else None),
                    ],
                )
            )
