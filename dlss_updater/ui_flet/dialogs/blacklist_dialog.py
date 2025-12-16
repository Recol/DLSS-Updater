"""
Blacklist Manager Dialog
Manage games that are blacklisted from automatic updates
"""

import logging
import flet as ft

from dlss_updater.config import config_manager
from dlss_updater.whitelist import get_all_blacklisted_games


class BlacklistDialog:
    """
    Dialog for managing blacklisted games
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger
        self.blacklisted_games = []
        self.skip_list = set()

    async def show(self):
        """Show the blacklist manager dialog"""

        # Load blacklisted games
        try:
            self.blacklisted_games = get_all_blacklisted_games()
            self.skip_list = set(config_manager.get_all_blacklist_skips())
            self.logger.info(f"Loaded {len(self.blacklisted_games)} blacklisted games")
        except Exception as e:
            self.logger.error(f"Failed to load blacklisted games: {e}")
            self.blacklisted_games = []

        # Create game list with switches
        game_controls = []

        if not self.blacklisted_games:
            game_controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.SHIELD_OUTLINED, size=48, color=ft.Colors.GREY),
                            ft.Text("No blacklisted games", color=ft.Colors.GREY),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.all(32),
                    alignment=ft.alignment.center,
                )
            )
        else:
            for game in self.blacklisted_games:
                override_enabled = game in self.skip_list

                switch = ft.Switch(
                    value=override_enabled,
                    data=game,  # Store game name in data
                )

                game_controls.append(
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Column(
                                    controls=[
                                        ft.Text(game, weight=ft.FontWeight.BOLD),
                                        ft.Text(
                                            "Override: Update anyway" if override_enabled else "Blacklisted: Skip updates",
                                            size=12,
                                            color=ft.Colors.GREY,
                                        ),
                                    ],
                                    expand=True,
                                ),
                                switch,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        padding=ft.padding.all(8),
                        border=ft.border.all(1, "#5A5A5A"),
                        border_radius=4,
                    )
                )

        async def save_clicked(e):
            # Collect enabled overrides
            new_skip_list = set()

            for control in game_controls:
                if isinstance(control.content, ft.Row):
                    # Find the switch
                    for child in control.content.controls:
                        if isinstance(child, ft.Switch):
                            if child.value:
                                new_skip_list.add(child.data)

            # Update config
            config_manager.clear_all_blacklist_skips()
            for game in new_skip_list:
                config_manager.add_blacklist_skip(game)

            self.logger.info(f"Saved {len(new_skip_list)} blacklist overrides")
            self.page.close(dialog)

            # Show success
            snackbar = ft.SnackBar(
                content=ft.Text("Blacklist settings saved"),
                bgcolor="#2D6E88",
            )
            self.page.overlay.append(snackbar)
            snackbar.open = True
            self.page.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Blacklist Manager"),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Container(
                            content=ft.Text(
                                "These games are blacklisted by default. Enable the toggle to override and allow updates.",
                                size=12,
                                color=ft.Colors.GREY,
                            ),
                            bgcolor="#3C3C3C",
                            padding=ft.padding.all(12),
                            border_radius=4,
                        ),
                        ft.Container(height=8),
                        ft.Container(
                            content=ft.Column(
                                controls=game_controls,
                                spacing=8,
                                scroll=ft.ScrollMode.AUTO,
                            ),
                            height=400,
                        ),
                    ],
                    spacing=8,
                ),
                width=600,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("Save", on_click=save_clicked),
            ],
        )

        self.page.open(dialog)
