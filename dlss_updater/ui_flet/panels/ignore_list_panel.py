"""
IgnoreListPanel - Manage personal game ignore list
Panel for excluding specific games from automatic DLL updates
"""

import logging
import asyncio
import flet as ft
from dlss_updater.ui_flet.components.slide_panel import PanelContentBase
from dlss_updater.database import db_manager
from dlss_updater.models import Game
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors


class IgnoreListPanel(ThemeAwareMixin, PanelContentBase):
    """
    Panel for managing the personal game ignore list.

    Unlike BlacklistPanel (which manages community-maintained blacklist overrides),
    this panel manages the user's personal list of games to skip during updates.
    Changes are saved immediately per-toggle (no batch Save workflow).
    """

    def __init__(self, page: ft.Page, logger: logging.Logger,
                 on_ignore_changed=None):
        """
        Args:
            page: Flet Page instance
            logger: Logger instance
            on_ignore_changed: Optional callback(game_id, is_ignored) fired after
                              each toggle so the caller can update card state.
        """
        super().__init__(page, logger)

        self._registry = get_theme_registry()
        self._theme_priority = 60

        self._on_ignore_changed = on_ignore_changed
        self._all_games: list[Game] = []
        self._ignored_ids: set[int] = set()
        self._filtered_games: list[Game] = []
        self.game_switches: dict[int, ft.Switch] = {}
        self.search_field: ft.TextField | None = None
        self.games_column: ft.Column | None = None
        self._count_text: ft.Text | None = None

        self._info_box: ft.Container | None = None
        self._info_text: ft.Text | None = None

        self._register_theme_aware()

    @property
    def title(self) -> str:
        return "Ignored Games"

    @property
    def subtitle(self) -> str | None:
        return "Games excluded from updates"

    @property
    def width(self) -> int:
        return 600

    async def on_open(self):
        """Load all games and ignored status when panel opens."""
        try:
            all_games_by_launcher = await db_manager.get_all_games_by_launcher()
            self._all_games = sorted(
                [g for games in all_games_by_launcher.values() for g in games],
                key=lambda g: g.name.lower()
            )
            self._ignored_ids = await asyncio.to_thread(
                db_manager.batch_get_ignored_game_ids_sync
            )
            self._filtered_games = self._all_games.copy()
            self.logger.info(
                f"Loaded {len(self._all_games)} games, "
                f"{len(self._ignored_ids)} ignored"
            )
        except Exception as e:
            self.logger.error(f"Failed to load games for ignore list: {e}")
            self._all_games = []
            self._filtered_games = []

        self._update_games_list()
        self._update_count_text()
        self._page_ref.update()

    def _on_search_change(self, e):
        """Filter the game list based on search query."""
        query = e.control.value.lower().strip()
        if query:
            self._filtered_games = [
                g for g in self._all_games
                if query in g.name.lower()
            ]
        else:
            self._filtered_games = self._all_games.copy()

        self._update_games_list()
        self._page_ref.update()

    def _update_games_list(self):
        """Rebuild games column with filtered results."""
        if self.games_column:
            self.games_column.controls = self._build_game_rows()

    def _update_count_text(self):
        """Update the count summary text."""
        if self._count_text:
            count = len(self._ignored_ids)
            total = len(self._all_games)
            self._count_text.value = f"{count} of {total} games ignored"

    def _build_game_rows(self) -> list[ft.Control]:
        """Build game rows for the filtered game list."""
        is_dark = self._registry.is_dark

        if not self._filtered_games:
            if self._all_games:
                message = "No games match your search"
                icon = ft.Icons.SEARCH_OFF
            else:
                message = "No games found"
                icon = ft.Icons.GAMEPAD_OUTLINED

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

        rows = []
        for game in self._filtered_games:
            is_ignored = game.id in self._ignored_ids

            if game.id not in self.game_switches:
                self.game_switches[game.id] = ft.Switch(
                    value=is_ignored,
                    data=game.id,
                    active_color=MD3Colors.get_primary(is_dark),
                    on_change=self._on_switch_change,
                )
            else:
                self.game_switches[game.id].value = is_ignored

            switch = self.game_switches[game.id]

            rows.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        game.name,
                                        weight=ft.FontWeight.BOLD,
                                        color=MD3Colors.get_text_primary(is_dark),
                                    ),
                                    ft.Text(
                                        game.launcher,
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

        return rows

    def _on_switch_change(self, e):
        """Handle ignore toggle — persist to DB immediately."""
        game_id: int = e.control.data
        ignored: bool = e.control.value

        if self._page_ref:
            self._page_ref.run_task(self._persist_switch_change, game_id, ignored)

    async def _persist_switch_change(self, game_id: int, ignored: bool):
        """Persist the switch change to the database."""
        success = await db_manager.set_game_ignored(game_id, ignored)
        if success:
            if ignored:
                self._ignored_ids.add(game_id)
            else:
                self._ignored_ids.discard(game_id)

            if self._on_ignore_changed:
                self._on_ignore_changed(game_id, ignored)

            self._update_count_text()
            self._page_ref.update()
        else:
            # Revert switch on failure
            switch = self.game_switches.get(game_id)
            if switch:
                switch.value = not ignored
                self._page_ref.update()

    def build(self) -> ft.Control:
        """Build the ignore list panel content."""
        is_dark = self._registry.is_dark

        self.search_field = ft.TextField(
            hint_text="Search games...",
            prefix_icon=ft.Icons.SEARCH,
            on_change=self._on_search_change,
            border_radius=8,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
        )

        self._info_text = ft.Text(
            "Toggle the switch to ignore a game. Ignored games will be skipped during updates.",
            size=12,
            color=MD3Colors.get_text_secondary(is_dark),
        )
        self._info_box = ft.Container(
            content=self._info_text,
            bgcolor=MD3Colors.get_themed("surface_bright", is_dark),
            padding=ft.padding.all(12),
            border_radius=4,
        )

        self._count_text = ft.Text(
            "0 of 0 games ignored",
            size=12,
            weight=ft.FontWeight.W_500,
            color=MD3Colors.get_text_secondary(is_dark),
        )

        self.games_column = ft.Column(
            controls=self._build_game_rows(),
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

        return ft.Column(
            controls=[
                self.search_field,
                self._info_box,
                self._count_text,
                self.games_column,
            ],
            spacing=12,
            expand=True,
        )

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for cascade animation."""
        props = {}
        if self.search_field:
            props["search_field.bgcolor"] = MD3Colors.get_themed_pair("surface_variant")
        if self._info_box:
            props["_info_box.bgcolor"] = MD3Colors.get_themed_pair("surface_bright")
        if self._info_text:
            props["_info_text.color"] = MD3Colors.get_themed_pair("text_secondary")
        return props

    async def on_save(self) -> bool:
        """Changes are saved per-toggle, so this just returns True."""
        self._show_snackbar("Ignore list updated")
        return True

    def on_cancel(self):
        """Clean up on cancel."""
        self.logger.debug("Ignore list panel closed")
        self.game_switches.clear()
