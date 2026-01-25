"""
BlacklistPanel - Manage games excluded from automatic updates
Panel with search/filter capability for managing blacklisted games
"""

import logging
import flet as ft
from dlss_updater.ui_flet.components.slide_panel import PanelContentBase
from dlss_updater.config import config_manager
from dlss_updater.whitelist import get_all_blacklisted_games
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors


class BlacklistPanel(ThemeAwareMixin, PanelContentBase):
    """
    Panel for managing blacklisted games.

    Features:
    - Lists all blacklisted games with toggle switches
    - Search/filter capability to find games quickly
    - Override toggles to allow updates for specific blacklisted games
    - Persists settings to config
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        """
        Initialize blacklist panel.

        Args:
            page: Flet Page instance
            logger: Logger instance for diagnostics
        """
        super().__init__(page, logger)

        # Theme support
        self._registry = get_theme_registry()
        self._theme_priority = 60  # Panels animate later in cascade

        self.blacklisted_games: list[str] = []
        self.skip_list: set[str] = set()
        self.game_switches: dict[str, ft.Switch] = {}
        self.filtered_games: list[str] = []
        self.search_field: ft.TextField | None = None
        self.games_column: ft.Column | None = None

        # Store themed element references
        self._info_box: ft.Container | None = None
        self._info_text: ft.Text | None = None

        # Register for theme updates
        self._register_theme_aware()

    @property
    def title(self) -> str:
        """Panel title."""
        return "Blacklist Manager"

    @property
    def subtitle(self) -> str | None:
        """Panel subtitle."""
        return "Manage games excluded from updates"

    @property
    def width(self) -> int:
        """Panel width in pixels."""
        return 600

    async def on_open(self):
        """Load blacklisted games when panel opens."""
        try:
            self.blacklisted_games = get_all_blacklisted_games()
            self.skip_list = set(config_manager.get_all_blacklist_skips())
            self.filtered_games = self.blacklisted_games.copy()
            self.logger.info(f"Loaded {len(self.blacklisted_games)} blacklisted games")
        except Exception as e:
            self.logger.error(f"Failed to load blacklisted games: {e}")
            self.blacklisted_games = []
            self.filtered_games = []

        # Rebuild the games list now that data is loaded
        self._update_games_list()
        self._page_ref.update()

    def _on_search_change(self, e):
        """
        Handle search field changes.

        Filters the game list based on search query.

        Args:
            e: Change event from search field
        """
        query = e.control.value.lower().strip()
        if query:
            self.filtered_games = [
                g for g in self.blacklisted_games
                if query in g.lower()
            ]
        else:
            self.filtered_games = self.blacklisted_games.copy()

        self._update_games_list()
        self._page_ref.update()

    def _update_games_list(self):
        """Rebuild games column with filtered results."""
        if self.games_column:
            self.games_column.controls = self._build_game_cards()

    def _build_game_cards(self) -> list[ft.Control]:
        """
        Build game cards for the filtered game list.

        Returns:
            List of Container controls for each game
        """
        is_dark = self._registry.is_dark

        if not self.filtered_games:
            # Show empty state
            if self.blacklisted_games:
                # Has games but filter shows none
                message = "No games match your search"
                icon = ft.Icons.SEARCH_OFF
            else:
                # No blacklisted games at all
                message = "No blacklisted games"
                icon = ft.Icons.SHIELD_OUTLINED

            return [
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(icon, size=48, color=MD3Colors.get_text_secondary(is_dark)),
                            ft.Text(message, color=MD3Colors.get_text_secondary(is_dark)),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.all(32),
                    alignment=ft.Alignment.CENTER,
                )
            ]

        cards = []
        for game in self.filtered_games:
            override_enabled = game in self.skip_list

            # Create or reuse switch for this game
            if game not in self.game_switches:
                self.game_switches[game] = ft.Switch(
                    value=override_enabled,
                    data=game,
                    active_color=MD3Colors.get_primary(is_dark),
                    on_change=self._on_switch_change,
                )
            else:
                # Update switch value to match current state
                self.game_switches[game].value = override_enabled

            switch = self.game_switches[game]

            # Create game card
            cards.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        game,
                                        weight=ft.FontWeight.BOLD,
                                        color=MD3Colors.get_text_primary(is_dark),
                                    ),
                                    ft.Text(
                                        "Override: Update anyway" if switch.value else "Blacklisted: Skip updates",
                                        size=12,
                                        color=MD3Colors.get_text_secondary(is_dark),
                                    ),
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            switch,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    padding=ft.padding.all(12),
                    border=ft.border.all(1, MD3Colors.get_outline(is_dark)),
                    border_radius=8,
                )
            )

        return cards

    def _on_switch_change(self, e):
        """
        Handle switch toggle changes.

        Updates the subtitle text to reflect the new state.

        Args:
            e: Change event from switch
        """
        game = e.control.data
        if e.control.value:
            self.skip_list.add(game)
        else:
            self.skip_list.discard(game)

        # Update the card to show the new status
        self._update_games_list()
        self._page_ref.update()

    def build(self) -> ft.Control:
        """
        Build the blacklist panel content.

        Returns:
            Column containing search field, info, and game list
        """
        is_dark = self._registry.is_dark

        # Search field
        self.search_field = ft.TextField(
            hint_text="Search games...",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self._on_search_change,
            border_radius=8,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
        )

        # Info box
        self._info_text = ft.Text(
            "These games are blacklisted by default. Enable the toggle to override and allow updates.",
            size=12,
            color=MD3Colors.get_text_secondary(is_dark),
        )
        self._info_box = ft.Container(
            content=self._info_text,
            bgcolor=MD3Colors.get_themed("surface_bright", is_dark),
            padding=ft.padding.all(12),
            border_radius=4,
        )

        # Games list column
        self.games_column = ft.Column(
            controls=self._build_game_cards(),
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

        return ft.Column(
            controls=[
                self.search_field,
                self._info_box,
                ft.Container(height=8),
                self.games_column,
            ],
            spacing=8,
            expand=True,
        )

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """
        Return themed property mappings for cascade animation.

        Returns:
            Dict mapping property paths to (dark_value, light_value) tuples.
        """
        props = {}

        # Search field
        if self.search_field:
            props["search_field.bgcolor"] = MD3Colors.get_themed_pair("surface_variant")

        # Info box
        if self._info_box:
            props["_info_box.bgcolor"] = MD3Colors.get_themed_pair("surface_bright")
        if self._info_text:
            props["_info_text.color"] = MD3Colors.get_themed_pair("text_secondary")

        return props

    async def on_save(self) -> bool:
        """
        Save blacklist settings to config.

        Collects all enabled overrides and persists to config_manager.

        Returns:
            True if save succeeded
        """
        # Collect enabled overrides from all switches
        new_skip_list = set()
        for game, switch in self.game_switches.items():
            if switch.value:
                new_skip_list.add(game)

        # Update config
        config_manager.clear_all_blacklist_skips()
        for game in new_skip_list:
            config_manager.add_blacklist_skip(game)

        self.logger.info(f"Saved {len(new_skip_list)} blacklist overrides")

        # Show success feedback
        self._show_snackbar("Blacklist settings saved")

        return True

    def on_cancel(self):
        """
        Called when panel is cancelled.

        Resets switches to original state.
        """
        self.logger.debug("Blacklist panel cancelled, discarding changes")
        # Reset skip_list to original state on next open
        self.game_switches.clear()
