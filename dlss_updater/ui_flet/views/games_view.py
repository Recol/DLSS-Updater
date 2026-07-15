"""
Games View - Display all games organized by launcher with Steam images

PERFORMANCE NOTES:
- Uses GridView with virtualization (only visible cards are rendered)
- Progressive loading: first batch shown immediately, rest created in background
- Parallel data loading via HyperParallelLoader (anyio worker threads) for DLLs and backups
- ImageLoadCoordinator batches page.update() calls for images (~5x faster)
- Search filtering via visibility toggles (no grid rebuild)
"""

import asyncio
import math
import time
from typing import Callable, Any, TYPE_CHECKING
import anyio
import flet as ft

from dlss_updater.concurrency_limiters import thread_io, io_heavy

from dlss_updater.database import db_manager, Game, merge_games_by_name
from dlss_updater.models import MergedGame, GameDLL, DLLBackup
from dlss_updater.ui_flet.components.game_card import GameCard
from dlss_updater.ui_flet.components.search_bar import GameSearchBar
from dlss_updater.ui_flet.components.floating_pill import PILL_CLEARANCE
from dlss_updater.ui_flet.components.hero_surface import build_brand_wash, build_pill, themed_accent
from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.async_updater import AsyncUpdateCoordinator
from dlss_updater.ui_flet.hyper_parallel_loader import HyperParallelLoader, LoadTask, BatchedImageLoader
from dlss_updater.config import is_dll_cache_ready, config_manager
from dlss_updater.search_service import search_service
from dlss_updater.task_registry import register_task

# PERFORMANCE: Progressive loading constants
# First batch shows immediately, rest loads in background
GAMES_INITIAL_BATCH_SIZE = 16  # Visible cards on typical screen
GAMES_BACKGROUND_BATCH_SIZE = 24  # Cards per background batch

# Grid density: (max_extent, child_aspect_ratio, image_size)
# Card layout is a flexible banner + a FIXED 52 px footer (see game_card.py:
# HERO_HEIGHT=204 target banner + FOOTER_HEIGHT=52 → 256 px total at the dominant cell
# width). child_aspect_ratio = max_extent / total_card_height = 320 / 256 = 1.25 makes a
# maximised-window cell (~320 px wide) exactly 256 px tall, so the banner sits at its 204
# px target. At other widths the banner flexes (BoxFit.COVER crops) — the fixed footer is
# never clipped and there is never a grey gap, because it always fills the cell exactly.
GRID_DENSITY_DEFAULT = (320, 1.25, 140)

if TYPE_CHECKING:
    from dlss_updater.ui_flet.components.game_card import GameCard


class ImageLoadCoordinator:
    """
    Batches image loading UI updates to minimize page.update() calls.

    Flet 0.80.4's single-threaded UI model serializes updates, so calling
    page.update() 11 times takes 11x as long. This coordinator collects
    pending updates and flushes them in a single batch.

    Performance improvement: ~5x faster (1.9s -> 350-400ms for 11 images)
    """

    def __init__(self, page: ft.Page, logger=None, view_ref: ft.Control | None = None):
        self._page_ref = page
        self._view_ref = view_ref  # Isolated view for targeted updates
        self._logger = logger
        self._pending_cards: list[tuple['GameCard', str]] = []
        self._batch_task: asyncio.Task | None = None
        self._lock = anyio.Lock()
        self._debounce_ms = 50  # Wait 50ms for more cards to complete
        self._max_batch_size = 20  # Cap batch size to prevent memory issues

    async def schedule_image_update(self, card: 'GameCard', image_path: str):
        """Schedule a card's image to be updated in the next batch."""
        async with self._lock:
            self._pending_cards.append((card, image_path))

            # Start debounce timer if not already running
            if self._batch_task is None or self._batch_task.done():
                self._batch_task = asyncio.create_task(self._flush_batch())

            # If we hit max batch size, flush immediately
            if len(self._pending_cards) >= self._max_batch_size:
                if self._batch_task and not self._batch_task.done():
                    self._batch_task.cancel()
                self._batch_task = asyncio.create_task(self._flush_batch_immediate())

    async def _flush_batch(self):
        """Flush all pending image updates with minimal page.update() calls after debounce."""
        # Debounce: wait for more cards to complete
        await anyio.sleep(self._debounce_ms / 1000)
        await self._flush_batch_immediate()

    async def _flush_batch_immediate(self):
        """Flush batch immediately without debounce delay.

        Uses ft.context.disable_auto_update() to ensure explicit control over updates.
        This prevents any automatic updates during batch operations.
        """
        import time

        async with self._lock:
            if not self._pending_cards:
                return

            cards_to_update = self._pending_cards.copy()
            self._pending_cards.clear()

        start_total = time.perf_counter()
        if self._logger:
            self._logger.debug(f"[ImageLoadCoordinator] Flushing batch of {len(cards_to_update)} images")

        # Disable auto-update to prevent any intermediate updates during batch setup
        ft.context.disable_auto_update()

        try:
            # Phase 1: Setup all images (opacity=0) - no UI update yet
            start_setup = time.perf_counter()
            for card, image_path in cards_to_update:
                try:
                    card.image_widget.src = image_path
                    card.image_container.opacity = 0
                    card.image_container.animate_opacity = ft.Animation(300, ft.AnimationCurve.EASE_IN)
                    card.image_container.content = card.image_widget
                except Exception as e:
                    if self._logger:
                        self._logger.debug(f"[ImageLoadCoordinator] Error setting up image for card: {e}")
            setup_ms = (time.perf_counter() - start_setup) * 1000

            # Re-enable before update so the explicit update() is processed normally
            ft.context.enable_auto_update()

            # SINGLE update to attach all controls to render tree
            # Use view_ref.update() for isolated views (serializes only GamesView subtree)
            start_update1 = time.perf_counter()
            try:
                update_target = self._view_ref or self._page_ref
                if update_target:
                    update_target.update()
            except Exception as e:
                if self._logger:
                    self._logger.debug(f"[ImageLoadCoordinator] Error during first update(): {e}")
                return
            update1_ms = (time.perf_counter() - start_update1) * 1000

            # Brief delay for render tree attachment (30ms)
            await anyio.sleep(0.03)

            # Phase 2: Trigger all fade-in animations simultaneously
            start_anim = time.perf_counter()
            for card, _ in cards_to_update:
                try:
                    card.image_container.opacity = 1
                    card._image_loaded = True
                except Exception:
                    pass  # Card may have been disposed
            anim_ms = (time.perf_counter() - start_anim) * 1000

            # SINGLE update to trigger all animations together
            start_update2 = time.perf_counter()
            try:
                update_target = self._view_ref or self._page_ref
                if update_target:
                    update_target.update()
            except Exception as e:
                if self._logger:
                    self._logger.debug(f"[ImageLoadCoordinator] Error during animation update(): {e}")
            update2_ms = (time.perf_counter() - start_update2) * 1000
        except Exception:
            # Always re-enable auto-update even if something fails mid-batch
            ft.context.enable_auto_update()
            raise

        total_ms = (time.perf_counter() - start_total) * 1000
        if self._logger:
            self._logger.debug(
                f"[ImageLoadCoordinator] Batch complete - {len(cards_to_update)} images "
                f"(setup={setup_ms:.1f}ms, update1={update1_ms:.1f}ms, anim={anim_ms:.1f}ms, update2={update2_ms:.1f}ms, total={total_ms:.1f}ms)"
            )


class GamesView(ThemeAwareMixin, ft.Column):
    """Games library view with launcher tabs

    NOTE: GamesView is NOT isolated. Isolation conflicts with page-level
    navigation (content detachment) and causes deadlocks when filter handlers
    call self.update() followed by navigation's page.update(). With content
    detachment, GamesView is detached before page.update() on nav-away, so
    page.update() cost is only incurred on nav-to-games.
    """

    def __init__(self, page: ft.Page, logger):
        super().__init__()
        self._page_ref = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        # State
        self.games_by_launcher: dict[str, list[Game]] = {}
        self.is_loading = False
        self.refresh_button_ref = ft.Ref[ft.IconButton]()

        # Game card tracking for single-game updates
        self.game_cards: dict[int, GameCard] = {}  # game_id -> GameCard
        self.game_card_containers: dict[int, ft.Container] = {}  # game_id -> container wrapper
        self.update_coordinator: AsyncUpdateCoordinator | None = None

        # Search state
        self.search_query: str = ""
        self._search_generation: int = 0
        self.search_bar: GameSearchBar | None = None

        # Status filter chips state
        self._filter_needs_update: bool = False
        self._filter_up_to_date: bool = False
        self._filter_has_backups: bool = False

        # Personal ignore list state
        self._ignored_game_ids: set[int] = set()
        self._show_ignored_games: bool = True  # Default: show ignored games (dimmed)

        # Options menu state
        self._has_games: bool = False
        self.options_menu: ft.PopupMenuButton | None = None
        self._options_icon: ft.Icon | None = None
        self._delete_menu_item: ft.PopupMenuItem | None = None

        # PERFORMANCE: Track if games are already loaded to prevent redundant rebuilds
        # on tab switching. Only reload on explicit refresh or when forced=True
        self._games_loaded = False

        # Set when a global/batch update completes while this view hasn't been
        # loaded yet (high_performance_updater.py writes new DLL files without
        # updating GameDLL.version in the DB). Consumed on the next load_games()
        # to reconcile badges from the filesystem instead of showing stale
        # "needs update" state from a session where Games was never visited.
        self._pending_dll_reconcile = False

        # Debug: track reentrant updates
        self._update_in_progress = False

        # Initialize theme system reference before building UI
        self._registry = get_theme_registry()
        self._theme_priority = 10  # Views are high priority (animate early)

        # Build initial UI
        self._build_ui()

        # Register with theme system after UI is built
        self._register_theme_aware()

    def _build_options_menu_items(self) -> list[ft.PopupMenuItem]:
        """Build options popup menu items reflecting current state."""
        is_dark = self._get_is_dark()
        on_surface = MD3Colors.get_on_surface(is_dark)
        icon_default = MD3Colors.get_themed("icon_default", is_dark)

        ignore_icon = ft.Icons.VISIBILITY_OFF if self._show_ignored_games else ft.Icons.VISIBILITY
        ignore_label = "Hide ignored games" if self._show_ignored_games else "Show ignored games"

        self._delete_menu_item = ft.PopupMenuItem(
            content=ft.Row([
                ft.Icon(ft.Icons.DELETE_SWEEP, size=18,
                        color=ft.Colors.RED_400 if self._has_games else ft.Colors.GREY_600),
                ft.Text("Delete Database", size=14,
                        color=ft.Colors.RED_400 if self._has_games else ft.Colors.GREY_600),
            ], spacing=8),
            on_click=self._on_delete_all_clicked,
            disabled=not self._has_games,
        )

        return [
            ft.PopupMenuItem(
                content=ft.Row([
                    ft.Icon(ignore_icon, size=18, color=icon_default),
                    ft.Text(ignore_label, size=14, color=on_surface),
                ], spacing=8),
                on_click=self._on_ignore_filter_toggle,
            ),
            ft.PopupMenuItem(),  # Divider
            self._delete_menu_item,
        ]

    def _update_delete_button_state(self, has_games: bool):
        """Update delete menu item enabled/disabled state."""
        self._has_games = has_games
        if self.options_menu:
            self.options_menu.items = self._build_options_menu_items()
            try:
                self.options_menu.update()
            except Exception:
                pass

    def _build_ui(self):
        """Build initial UI with empty state"""
        # Get theme preference from registry
        is_dark = self._get_is_dark()

        # Native Material search bar with live game-name suggestions + history
        self.search_bar = GameSearchBar(
            on_search=self._on_search_changed,
            on_clear=self._on_search_cleared,
            on_history_selected=self._on_history_selected,
            get_suggestions=self._get_search_suggestions,
            placeholder="Search games...",
            width=260,
        )

        # Store themed element references
        self.header_title = ft.Text(
            "Games Library",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
        )

        # Backup stats (populated after loading)
        self.backup_stats_text = ft.Text(
            "",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            italic=True,
        )

        self.loading_text = ft.Text(
            "Loading games...",
            color=MD3Colors.get_text_primary(is_dark),
        )

        self.divider = ft.Divider(height=1, color=MD3Colors.get_outline(is_dark))

        # Options popup menu (contains hide ignored + delete database)
        self._options_icon = ft.Icon(
            ft.Icons.TUNE,
            size=20,
        )
        self.options_menu = ft.PopupMenuButton(
            content=ft.Container(
                content=self._options_icon,
                width=40,
                height=40,
                border_radius=8,
                alignment=ft.Alignment.CENTER,
                tooltip="More options",
            ),
            items=self._build_options_menu_items(),
        )

        # Steam API card — built BEFORE the header pill below so the pill can
        # read its real initial _api_key_valid state (existing key -> success)
        # instead of transiently defaulting to "not configured". No longer
        # placed inline in the layout (see DESIGN SPEC #1): it lives
        # permanently off-screen as a reusable control and is only attached
        # to the page inside the dialog opened by self.steam_api_pill (see
        # _open_steam_api_dialog).
        from dlss_updater.ui_flet.components.steam_api_card import SteamAPICard

        self.steam_api_card = SteamAPICard(
            page=self._page_ref,
            on_reresolution_complete=self._on_reresolution_complete,
        )
        self._steam_api_dialog: ft.AlertDialog | None = None

        # Status filter chips (visibility-toggle filtering — no grid rebuild)
        # Label Text controls are kept as refs so live counts can be patched
        # in-place (self._update_filter_chip_counts()) without rebuilding the
        # Chip's label subtree.
        self._needs_update_label = ft.Text("Needs update", size=12)
        self._needs_update_icon = ft.Icon(ft.Icons.ARROW_UPWARD, size=14)
        self._needs_update_chip = ft.Chip(
            label=self._needs_update_label,
            leading=self._needs_update_icon,
            selected=False,
            on_select=self._on_status_chip_select,
            data="needs_update",
        )
        self._up_to_date_label = ft.Text("Up to date", size=12)
        self._up_to_date_icon = ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE, size=14)
        self._up_to_date_chip = ft.Chip(
            label=self._up_to_date_label,
            leading=self._up_to_date_icon,
            selected=False,
            on_select=self._on_status_chip_select,
            data="up_to_date",
        )
        self._has_backups_label = ft.Text("Has backups", size=12)
        self._has_backups_icon = ft.Icon(ft.Icons.RESTORE, size=14)
        self._has_backups_chip = ft.Chip(
            label=self._has_backups_label,
            leading=self._has_backups_icon,
            selected=False,
            on_select=self._on_status_chip_select,
            data="has_backups",
        )
        self.filter_chips_row = ft.Row(
            controls=[
                self._needs_update_chip,
                self._up_to_date_chip,
                self._has_backups_chip,
            ],
            spacing=8,
            wrap=True,
        )
        self._apply_filter_chip_theme(is_dark)

        # Compact Steam API status pill — clicking opens the full config UI
        # in a dialog (see _open_steam_api_dialog). Kept as a ref so its
        # colors/icon can be patched in place after the dialog closes.
        self.steam_api_pill = self._build_steam_api_pill(is_dark)

        # Header (brand-washed surface: subtle diagonal GAMES-blue tint over
        # the existing surface_variant fill, matching the hero-card wash
        # language used elsewhere — see hero_surface.build_brand_wash).
        header_accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)
        self._header_wash = ft.Container(
            gradient=build_brand_wash(header_accent, is_dark),
            left=0,
            top=0,
            right=0,
            bottom=0,
        )
        header_foreground = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self.header_title,
                            self.backup_stats_text,
                            ft.Container(expand=True),  # Spacer
                            self.search_bar,
                            self.steam_api_pill,
                            self.options_menu,
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
                    self.filter_chips_row,
                ],
                spacing=8,
            ),
            padding=16,
        )
        self.header = ft.Container(
            content=ft.Stack(controls=[self._header_wash, header_foreground]),
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

        # Tabs container (will be populated with launcher tabs)
        self.tabs_container = ft.Container(
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
                    self.tabs_container,
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

    # ===== Steam API status pill (header) =====

    def _steam_pill_style(self, is_dark: bool) -> tuple[str, str, str]:
        """Derive (icon, bgcolor, fgcolor) for the Steam API pill.

        Mirrors SteamAPICard._update_status_badge()'s three states exactly
        (connected / invalid key / not configured) so the header pill never
        disagrees with the dialog it opens.
        """
        valid = self.steam_api_card._api_key_valid if getattr(self, "steam_api_card", None) else None
        if valid is True:
            return ft.Icons.CLOUD_DONE, MD3Colors.get_success(is_dark), ft.Colors.WHITE
        if valid is False:
            return ft.Icons.CLOUD_OFF, MD3Colors.get_error(is_dark), ft.Colors.WHITE
        # Not configured: neutral/dim, not an error state
        return ft.Icons.CLOUD_OFF, MD3Colors.get_surface(is_dark), MD3Colors.get_on_surface_variant(is_dark)

    def _steam_pill_border(self, is_dark: bool) -> ft.Border | None:
        """Faint outline for the neutral "not configured" pill state only —
        the connected/invalid states already read clearly via their solid
        fill, so an outline there would be redundant."""
        if self.steam_api_card._api_key_valid is None:
            return ft.Border.all(1, MD3Colors.get_outline(is_dark))
        return None

    def _build_steam_api_pill(self, is_dark: bool) -> ft.Container:
        """Build the compact clickable Steam API status pill for the header."""
        icon, bgcolor, fgcolor = self._steam_pill_style(is_dark)
        pill = build_pill("Steam API", icon=icon, bgcolor=bgcolor, text_color=fgcolor, icon_color=fgcolor)
        # build_pill's content is a tight Row([Icon, Text]) — keep refs so
        # _refresh_steam_api_pill() can patch colors/icon in place.
        row = pill.content
        self._steam_pill_icon: ft.Icon = row.controls[0]
        self._steam_pill_text: ft.Text = row.controls[1]
        pill.border = self._steam_pill_border(is_dark)
        pill.on_click = self._on_steam_api_pill_click
        pill.ink = True
        pill.tooltip = "Configure Steam API"
        return pill

    def _refresh_steam_api_pill(self) -> None:
        """Re-derive the pill's state (post dialog-close) and repaint in place."""
        if not getattr(self, "steam_api_pill", None):
            return
        is_dark = self._get_is_dark()
        icon, bgcolor, fgcolor = self._steam_pill_style(is_dark)
        self._steam_pill_icon.name = icon
        self._steam_pill_icon.color = fgcolor
        self._steam_pill_text.color = fgcolor
        self.steam_api_pill.bgcolor = bgcolor
        self.steam_api_pill.border = self._steam_pill_border(is_dark)
        try:
            self.update()
        except Exception:
            pass

    def _on_steam_api_pill_click(self, e) -> None:
        self._open_steam_api_dialog()

    def _open_steam_api_dialog(self) -> None:
        """Open the full Steam API configuration UI (the existing SteamAPICard,
        unmodified) inside a dialog. The pill refreshes its state once the
        dialog closes via any path (Close button, backdrop click, ESC)."""
        # Auto-expand: the dialog IS the configuration surface now, so there's
        # no reason to make the user click the ExpansionTile a second time.
        self.steam_api_card.expansion_tile.expanded = True

        dialog = ft.AlertDialog(
            modal=False,
            title=ft.Text("Steam API Configuration"),
            content=ft.Container(content=self.steam_api_card, width=460),
            actions=[
                ft.TextButton("Close", on_click=lambda e: self._close_steam_api_dialog()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=lambda e: self._refresh_steam_api_pill(),
        )
        self._steam_api_dialog = dialog
        self._page_ref.show_dialog(dialog)

    def _close_steam_api_dialog(self) -> None:
        self._page_ref.pop_dialog()
        self._refresh_steam_api_pill()

    # ===== Filter chip theming + live counts =====

    def _apply_filter_chip_theme(self, is_dark: bool) -> None:
        """Tint the three status filter chips with semantic accents.

        Needs update -> WARNING amber, Up to date -> SUCCESS green,
        Has backups -> BACKUPS orange (themed_accent picks the _LIGHT variant
        in light mode). Unselected chips stay on the neutral surface with a
        subtle colored outline; selecting fills with a translucent accent tint.
        """
        needs_update_accent = MD3Colors.get_warning(is_dark)
        up_to_date_accent = MD3Colors.get_success(is_dark)
        has_backups_accent = themed_accent((TabColors.BACKUPS, TabColors.BACKUPS_LIGHT), is_dark)

        fill_opacity = 0.28 if is_dark else 0.16
        border_opacity = 0.5 if is_dark else 0.4

        for chip, icon, accent in (
            (self._needs_update_chip, self._needs_update_icon, needs_update_accent),
            (self._up_to_date_chip, self._up_to_date_icon, up_to_date_accent),
            (self._has_backups_chip, self._has_backups_icon, has_backups_accent),
        ):
            chip.selected_color = ft.Colors.with_opacity(fill_opacity, accent)
            chip.check_color = accent
            chip.border_side = ft.BorderSide(1, ft.Colors.with_opacity(border_opacity, accent))
            icon.color = accent

    def _update_filter_chip_counts(self) -> None:
        """Recompute live "(N)" counts on the status filter chip labels.

        Purely derived from already-loaded card state (card._check_for_updates()
        / card.has_backups) — no new queries. Called whenever game_cards
        changes shape or a card's DLL/backup state changes.
        """
        needs_update = 0
        has_backups = 0
        for card in self.game_cards.values():
            if card._check_for_updates():
                needs_update += 1
            if card.has_backups:
                has_backups += 1
        up_to_date = len(self.game_cards) - needs_update

        self._needs_update_label.value = f"Needs update ({needs_update})"
        self._up_to_date_label.value = f"Up to date ({up_to_date})"
        self._has_backups_label.value = f"Has backups ({has_backups})"

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware system"""
        return {
            "header.bgcolor": (MD3Colors.SURFACE_VARIANT, MD3Colors.SURFACE_VARIANT_LIGHT),
            "header_title.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "loading_text.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "divider.color": (MD3Colors.get_outline(True), MD3Colors.get_outline(False)),
            "backup_stats_text.color": (MD3Colors.get_on_surface_variant(True), MD3Colors.get_on_surface_variant(False)),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme."""
        await super().apply_theme(is_dark, delay_ms)
        # Rebuild options menu items to apply new theme colors
        if self.options_menu:
            self.options_menu.items = self._build_options_menu_items()
            try:
                self.options_menu.update()
            except Exception:
                pass

        # Header brand wash — rebuild the diagonal GAMES-blue gradient at the
        # new theme's opacity/accent.
        if getattr(self, "_header_wash", None):
            header_accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)
            self._header_wash.gradient = build_brand_wash(header_accent, is_dark)

        # Status filter chip semantic tints (WARNING/SUCCESS/BACKUPS accents
        # differ between light and dark mode).
        self._apply_filter_chip_theme(is_dark)

        # Steam API pill — repaint using the current connection state at the
        # new theme's colors (handles the neutral-state outline color too).
        self._refresh_steam_api_pill()

        # Launcher tabs indicator/label accent (if tabs are currently built).
        if getattr(self, "_tab_bar_ref", None):
            tab_accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)
            self._tab_bar_ref.indicator_color = tab_accent
            self._tab_bar_ref.label_color = tab_accent

        try:
            self.update()
        except Exception:
            pass

    def mark_pending_dll_reconcile(self) -> None:
        """Flag that a global update wrote new DLL files while this view wasn't
        loaded, so the next load_games() reconciles versions from disk instead
        of displaying stale DB-cached badges."""
        self._pending_dll_reconcile = True

    async def load_games(self, force: bool = False):
        """Load games from database and display.

        PERFORMANCE: Skips full reload if games are already loaded (tab switching).
        Use force=True to trigger a full refresh (explicit refresh button).

        Args:
            force: If True, forces a full reload even if games are already loaded.
        """
        if self.is_loading:
            return

        # PERFORMANCE: Skip full reload if already loaded (fast tab switching)
        # Only rebuild on explicit refresh (force=True) or first load
        if self._games_loaded and not force:
            self.logger.debug("Games already loaded - animating cards on tab switch")
            # Ensure the view is visible
            self.tabs_container.visible = True
            self.empty_state.visible = False
            self.loading_indicator.visible = False

            # Animate cards progressively on tab switch for better UX
            visible_cards = list(self.game_cards.values())[:GAMES_INITIAL_BATCH_SIZE]
            if visible_cards:
                # Reset opacity for animation (ignored cards stay dimmed, not hidden)
                for card in visible_cards:
                    card.opacity = 0 if not card.is_ignored else 0.5
                self.update()
                # Trigger staggered fade-in
                anim_task = asyncio.create_task(self._animate_cards_in(visible_cards))
                register_task(anim_task, "animate_cards_tab_switch")
            else:
                self.update()
            return

        self.is_loading = True
        self.loading_indicator.visible = True
        self.empty_state.visible = False
        self.tabs_container.visible = False
        self.update()

        try:
            # Ensure database pool is ready
            await db_manager.ensure_pool()

            self.logger.info("Loading games from database...")

            # Get all games grouped by launcher (without merging duplicates)
            self.games_by_launcher = await db_manager.get_all_games_by_launcher()

            if not self.games_by_launcher or sum(len(games) for games in self.games_by_launcher.values()) == 0:
                self.logger.info("No games found in database")
                self.empty_state.visible = True
                self.loading_indicator.visible = False
                self._update_delete_button_state(False)
                self._games_loaded = False  # Allow retry on next tab switch
                self.update()
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

            # Mark as loaded for fast tab switching
            self._games_loaded = True

            # A global update may have completed while this view wasn't loaded
            # (see mark_pending_dll_reconcile) -- reconcile badges from the
            # filesystem now instead of showing the stale DB-cached versions
            # we just loaded.
            if self._pending_dll_reconcile:
                self._pending_dll_reconcile = False
                self.logger.info("Reconciling DLL versions from filesystem after a batch update that ran before this view loaded")
                await self.refresh_all_badges()

            # Initialize Steam API card (check improvement count, auto-detect ID)
            if hasattr(self, 'steam_api_card') and self.steam_api_card:
                init_task = asyncio.create_task(self.steam_api_card.initialize())
                register_task(init_task, "steam_api_card_init")

            self.logger.info(f"Loaded {sum(len(games) for games in self.games_by_launcher.values())} games from {len(self.games_by_launcher)} launchers")

        except Exception as e:
            self.logger.error(f"Error loading games: {e}", exc_info=True)
            self.empty_state.visible = True
            self.loading_indicator.visible = False
            self._update_delete_button_state(False)
            self._games_loaded = False  # Allow retry on next tab switch

        finally:
            self.is_loading = False
            self.update()

    async def _build_launcher_tabs(self):
        """Build tabs for each launcher with games (Flet 0.80.4 TabBar/TabBarView pattern)

        PERFORMANCE OPTIMIZATION (Flet 0.80.4):
        - Uses HyperParallelLoader (anyio task group + thread_io limiter) for true parallel I/O
        - Batch queries reduce N+1 problem from 200+ queries to 2-3 queries
        - Single page.update() call after all cards created
        - Staggered animation runs after initial render
        """
        start_total = time.perf_counter()
        tabs = []  # Tab headers (label/icon)
        tab_contents = []  # Tab content controls
        self._tab_launchers = []  # Track launcher name per tab index

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

        # ========== PHASE 1: Collect all game IDs across all launchers ==========
        # This enables batch database queries (O(1) vs O(n))
        start_collect = time.perf_counter()
        all_merged_games: list[tuple[str, MergedGame]] = []  # (launcher, merged_game)
        all_game_ids: list[int] = []
        all_steam_app_ids: list[int] = []

        for launcher, games in self.games_by_launcher.items():
            if not games:
                continue

            merged_games = merge_games_by_name(games)
            for mg in merged_games:
                all_merged_games.append((launcher, mg))
                all_game_ids.extend(mg.all_game_ids)
                eff = mg.primary_game.effective_steam_app_id
                if eff:
                    all_steam_app_ids.append(eff)

        collect_ms = (time.perf_counter() - start_collect) * 1000
        self.logger.debug(f"[PERF] Collected {len(all_merged_games)} merged games, {len(all_game_ids)} game_ids: {collect_ms:.1f}ms")

        # ========== PHASE 2: Hyper-parallel batch database queries ==========
        # Uses anyio worker threads (shared thread_io limiter) for true parallelism
        start_data = time.perf_counter()
        loader = HyperParallelLoader()

        # Run all database queries in parallel on worker threads (anyio task group)
        results = await loader.load_all([
            LoadTask("dlls", lambda: db_manager.batch_get_dlls_for_games_sync(all_game_ids)),
            LoadTask("backups", lambda: db_manager.batch_get_backups_grouped_sync(all_game_ids)),
            LoadTask("images", lambda: db_manager._batch_get_cached_image_paths(all_steam_app_ids)),
            LoadTask("ignored", lambda: db_manager.batch_get_ignored_game_ids_sync()),
            LoadTask("backup_stats", lambda: db_manager.get_backup_summary_stats_sync()),
        ])

        dlls_by_game: dict[int, list[GameDLL]] = results.get("dlls", {})
        backups_by_game: dict[int, dict[str, list[DLLBackup]]] = results.get("backups", {})
        cached_image_paths: dict[int, str] = results.get("images", {})
        self._ignored_game_ids = results.get("ignored", set())
        backup_stats: tuple[int, int] = results.get("backup_stats", (0, 0))
        self._update_backup_stats(backup_stats[0], backup_stats[1])

        data_ms = (time.perf_counter() - start_data) * 1000
        self.logger.debug(f"[PERF] Batch data loading ({len(all_game_ids)} games): {data_ms:.1f}ms")

        # ========== PHASE 3: Group games by launcher ==========
        start_cards = time.perf_counter()

        # Group merged games by launcher for tab creation
        games_by_launcher_merged: dict[str, list[tuple[MergedGame, list[GameDLL], dict[str, list[DLLBackup]]]]] = {}

        for launcher, mg in all_merged_games:
            # Aggregate DLLs and backups for all game_ids in this merged game
            all_dlls: list[GameDLL] = []
            all_backup_groups: dict[str, list[DLLBackup]] = {}

            for game_id in mg.all_game_ids:
                all_dlls.extend(dlls_by_game.get(game_id, []))

                game_backups = backups_by_game.get(game_id, {})
                for dll_type, backups in game_backups.items():
                    if dll_type not in all_backup_groups:
                        all_backup_groups[dll_type] = []
                    all_backup_groups[dll_type].extend(backups)

            if launcher not in games_by_launcher_merged:
                games_by_launcher_merged[launcher] = []
            games_by_launcher_merged[launcher].append((mg, all_dlls, all_backup_groups))

        # Card creation helper
        def create_card(merged: MergedGame, dlls: list[GameDLL], backup_groups: dict[str, list[DLLBackup]]) -> GameCard:
            is_ignored = bool(set(merged.all_game_ids) & self._ignored_game_ids)
            merged.is_ignored = is_ignored
            card = GameCard(
                game=merged,
                dlls=dlls,
                page=self._page_ref,
                logger=self.logger,
                on_update=self._on_game_update,
                on_restore=self._on_game_restore,
                backup_groups=backup_groups,
                is_ignored=is_ignored,
                on_ignore_toggle=self._on_game_ignore_toggle,
                on_resolve=self._on_game_resolve,
            )
            card.opacity = 0 if not is_ignored else 0.5
            card.animate_opacity = ft.Animation(400, ft.AnimationCurve.EASE_OUT)
            return card

        # ========== PHASE 4: Progressive card creation ==========
        # Create first batch immediately for instant UI feedback
        # Remaining cards load in background without blocking
        all_cards_for_animation: list[GameCard] = []
        remaining_to_create: list[tuple[str, MergedGame, list[GameDLL], dict[str, list[DLLBackup]]]] = []
        grids_by_launcher: dict[str, ft.GridView] = {}
        initial_card_count = 0

        for launcher in self.games_by_launcher.keys():
            if launcher not in games_by_launcher_merged:
                continue

            merged_data = games_by_launcher_merged[launcher]
            game_count = len(self.games_by_launcher[launcher])

            # Create GridView first (will be populated progressively)
            max_extent, aspect_ratio, _ = GRID_DENSITY_DEFAULT

            game_grid = ft.GridView(
                controls=[],
                max_extent=max_extent,
                child_aspect_ratio=aspect_ratio,
                # Bottom padding lets the last row scroll clear of the floating pill
                padding=ft.Padding.only(left=16, right=16, top=16, bottom=PILL_CLEARANCE),
                spacing=12,
                run_spacing=12,
                expand=True,
            )
            grids_by_launcher[launcher] = game_grid

            # Create first batch of cards for this launcher
            first_batch = merged_data[:GAMES_INITIAL_BATCH_SIZE]
            remaining = merged_data[GAMES_INITIAL_BATCH_SIZE:]

            for mg, dlls, backup_groups in first_batch:
                card = create_card(mg, dlls, backup_groups)
                self.game_cards[mg.primary_game.id] = card
                self.game_card_containers[mg.primary_game.id] = card
                game_grid.controls.append(card)
                all_cards_for_animation.append(card)
                initial_card_count += 1

                # Pre-set cached image if available
                eff_id = mg.primary_game.effective_steam_app_id
                if eff_id and eff_id in cached_image_paths:
                    card.image_widget.src = cached_image_paths[eff_id]
                    card.image_container.content = card.image_widget
                    card._image_loaded = True

            # Queue remaining cards for background loading
            for mg, dlls, backup_groups in remaining:
                remaining_to_create.append((launcher, mg, dlls, backup_groups))

            # Create tab header
            tab_header = ft.Tab(
                label=f"{launcher} ({game_count})",
                icon=launcher_icons.get(launcher, ft.Icons.FOLDER),
            )
            tabs.append(tab_header)
            tab_contents.append(game_grid)
            self._tab_launchers.append(launcher)

        cards_ms = (time.perf_counter() - start_cards) * 1000
        self.logger.debug(f"[PERF] Initial card creation ({initial_card_count} cards): {cards_ms:.1f}ms")

        # ========== PHASE 5: Create tabs control and show UI ==========
        # Indicator/label accent = GAMES blue, matching the header wash.
        is_dark = self._get_is_dark()
        tab_accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)
        self._tab_bar_ref = ft.TabBar(tabs=tabs, indicator_color=tab_accent, label_color=tab_accent)
        self.tabs_control = ft.Tabs(
            length=len(tabs),
            selected_index=0,
            animation_duration=300,
            expand=True,
            on_change=self._on_tab_changed,
            content=ft.Column(
                expand=True,
                controls=[
                    self._tab_bar_ref,
                    ft.TabBarView(expand=True, controls=tab_contents),
                ],
            ),
        )
        self.tabs_container.content = self.tabs_control

        # Live filter-chip counts reflect the initial (first-batch) cards now;
        # refreshed again once background progressive loading finishes below.
        self._update_filter_chip_counts()

        # ========== PHASE 6: Background tasks ==========
        # Trigger staggered fade-in animation for initial cards
        anim_task = asyncio.create_task(self._animate_cards_in(all_cards_for_animation))
        register_task(anim_task, "animate_all_cards")

        # Load uncached images in background
        uncached_cards = [c for c in all_cards_for_animation if not c._image_loaded and c.game.effective_steam_app_id]
        if uncached_cards:
            img_task = asyncio.create_task(self._load_uncached_images(uncached_cards))
            register_task(img_task, "load_uncached_images")

        # Load remaining cards progressively in background
        if remaining_to_create:
            bg_task = asyncio.create_task(
                self._load_remaining_cards_progressive(
                    remaining_to_create, grids_by_launcher, cached_image_paths, create_card
                )
            )
            register_task(bg_task, "load_remaining_game_cards")
            self.logger.debug(f"[PERF] Queued {len(remaining_to_create)} cards for background loading")

        total_ms = (time.perf_counter() - start_total) * 1000
        self.logger.info(f"[PERF] _build_launcher_tabs total: {total_ms:.1f}ms ({initial_card_count} initial, {len(remaining_to_create)} queued)")

    async def _load_uncached_images(self, cards: list['GameCard']):
        """Load images for cards without cached paths using concurrent async I/O.

        Uses an anyio task group (gated by io_heavy) for parallel HTTP requests (I/O-bound).
        Single page.update() after all images are fetched and applied.
        """
        from dlss_updater.steam_integration import fetch_steam_image

        start_time = time.perf_counter()

        try:
            # Collect unique app_ids to fetch (avoid duplicate requests)
            app_id_to_cards: dict[int, list[GameCard]] = {}
            for card in cards:
                app_id = card.game.effective_steam_app_id
                if app_id:
                    if app_id not in app_id_to_cards:
                        app_id_to_cards[app_id] = []
                    app_id_to_cards[app_id].append(card)

            if not app_id_to_cards:
                return

            # Fetch each unique app_id concurrently via an anyio task group.
            # HTTP concurrency is gated app-wide by io_heavy; the binding ceiling
            # remains steam_integration's internal per-download semaphore
            # (IMAGE_SEMAPHORE), which io_heavy sits above.
            app_ids = list(app_id_to_cards.keys())
            fetched_paths: dict[int, str | None] = {}

            async def fetch_with_id(app_id: int) -> None:
                """Fetch one image and record its path (or None on failure)."""
                try:
                    async with io_heavy:
                        path = await fetch_steam_image(app_id)
                    fetched_paths[app_id] = path
                except Exception as e:
                    self.logger.debug(f"Failed to fetch image for app {app_id}: {e}")

            async with anyio.create_task_group() as tg:
                for app_id in app_ids:
                    tg.start_soon(fetch_with_id, app_id)

            # Apply fetched images to cards
            cards_updated = 0
            for app_id, path in fetched_paths.items():
                if path:
                    for card in app_id_to_cards.get(app_id, []):
                        card.image_widget.src = str(path)
                        card.image_container.content = card.image_widget  # Replace skeleton
                        card._image_loaded = True
                        cards_updated += 1

            # Single self.update() for all image updates (isolated view)
            if cards_updated > 0:
                self.update()

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self.logger.debug(f"[PERF] Loaded {cards_updated} uncached images in {elapsed_ms:.1f}ms")

        except Exception as e:
            self.logger.warning(f"Error loading uncached images: {e}")

    async def _on_tab_changed(self, e):
        """Handle tab change - reapply all filters to new tab."""
        self._apply_visibility()
        self.update()

    async def _load_remaining_game_cards(
        self,
        remaining_results: list[tuple],
        grid: ft.GridView,
        coordinator: 'ImageLoadCoordinator',
        create_card_fn,
        launcher: str,
    ):
        """Load remaining game cards in background batches.

        PERFORMANCE: Creates cards in batches with yields to keep UI responsive.
        GridView virtualization means adding 100+ cards has minimal render cost.
        """
        try:
            total = len(remaining_results)
            loaded = 0

            for i in range(0, total, GAMES_BACKGROUND_BATCH_SIZE):
                batch = remaining_results[i:i + GAMES_BACKGROUND_BATCH_SIZE]

                # Create cards for this batch
                new_cards = []
                for merged, dlls, backup_groups in batch:
                    card = create_card_fn(merged, dlls, backup_groups)
                    self.game_cards[merged.primary_game.id] = card
                    self.game_card_containers[card.game.id] = card
                    new_cards.append(card)

                # Add to grid (virtualized - only visible cards render)
                grid.controls.extend(new_cards)
                loaded += len(new_cards)

                # Load images for new cards
                steam_ids = [c.game.effective_steam_app_id for c in new_cards if c.game.effective_steam_app_id]
                if steam_ids:
                    cached_paths = await db_manager.batch_get_cached_image_paths(steam_ids)
                    for card in new_cards:
                        eff = card.game.effective_steam_app_id
                        if eff:
                            path = cached_paths.get(eff)
                            task = asyncio.create_task(card.load_image(path, coordinator=coordinator))
                            register_task(task, f"load_image_bg_{card.game.name[:15]}")

                # Make cards visible immediately (respect ignored state)
                for card in new_cards:
                    card.opacity = 0.5 if card.is_ignored else 1

                # Single update per batch (isolated view)
                self.update()

                # Yield to event loop
                await anyio.sleep(0.02)

            self.logger.debug(f"[PERF] Background loaded {loaded} additional {launcher} cards")

        except Exception as e:
            self.logger.error(f"Error loading remaining game cards: {e}", exc_info=True)

    async def _load_remaining_cards_progressive(
        self,
        remaining: list[tuple[str, MergedGame, list[GameDLL], dict[str, list[DLLBackup]]]],
        grids_by_launcher: dict[str, ft.GridView],
        cached_image_paths: dict[int, str],
        create_card_fn,
    ):
        """Load remaining game cards progressively in background.

        PERFORMANCE: Creates cards in small batches with yields to keep UI responsive.
        Shows partial content immediately while rest loads in background.
        """
        total = len(remaining)
        loaded = 0

        for i in range(0, total, GAMES_BACKGROUND_BATCH_SIZE):
            try:
                batch = remaining[i:i + GAMES_BACKGROUND_BATCH_SIZE]

                # Create cards for this batch
                new_cards_by_launcher: dict[str, list[GameCard]] = {}
                for launcher, mg, dlls, backup_groups in batch:
                    card = create_card_fn(mg, dlls, backup_groups)
                    self.game_cards[mg.primary_game.id] = card
                    self.game_card_containers[mg.primary_game.id] = card

                    # Pre-set cached image if available
                    eff_id = mg.primary_game.effective_steam_app_id
                    if eff_id and eff_id in cached_image_paths:
                        card.image_widget.src = cached_image_paths[eff_id]
                        card.image_container.content = card.image_widget
                        card._image_loaded = True

                    # Make visible immediately (respect ignored state)
                    card.opacity = 0.5 if card.is_ignored else 1

                    if launcher not in new_cards_by_launcher:
                        new_cards_by_launcher[launcher] = []
                    new_cards_by_launcher[launcher].append(card)

                # Add cards to their respective grids
                for launcher, cards in new_cards_by_launcher.items():
                    if launcher in grids_by_launcher:
                        grids_by_launcher[launcher].controls.extend(cards)
                        loaded += len(cards)

                # Single update per batch (isolated view); guard against view detach
                try:
                    self.update()
                except RuntimeError:
                    # View detached from page tree (user navigated away).
                    # Cards are already in controls list and will render on next update.
                    pass

                # Trigger image loading for uncached cards in this batch
                uncached_in_batch = [
                    c
                    for cards in new_cards_by_launcher.values()
                    for c in cards
                    if not c._image_loaded and c.game.effective_steam_app_id
                ]
                if uncached_in_batch:
                    img_task = asyncio.create_task(self._load_uncached_images(uncached_in_batch))
                    register_task(img_task, "load_uncached_bg_images")

                # Yield to event loop to keep UI responsive
                await anyio.sleep(0.02)

            except Exception as e:
                self.logger.error(f"Error in progressive batch {i}: {e}", exc_info=True)
                # Continue to next batch — don't abandon remaining cards

        self.logger.debug(f"[PERF] Progressive loading complete: {loaded} additional cards")

        # Final, complete-dataset recount now that every card has loaded.
        self._update_filter_chip_counts()
        try:
            self.update()
        except RuntimeError:
            pass

    async def _animate_cards_in(self, game_cards: list[GameCard]):
        """Animate game cards with staggered fade-in for grid layout (optimized)"""
        # Small initial delay
        await anyio.sleep(0.1)

        # For grid layout, animate first 12 cards in batches of 4 to reduce update calls
        cards_to_animate = game_cards[:12]
        batch_size = 4

        for batch_start in range(0, len(cards_to_animate), batch_size):
            batch_end = min(batch_start + batch_size, len(cards_to_animate))
            # Set opacity for entire batch (respect ignored state)
            for card in cards_to_animate[batch_start:batch_end]:
                card.opacity = 0.5 if card.is_ignored else 1
            # Single update per batch instead of per card (isolated view)
            self.update()
            await anyio.sleep(0.08)  # 80ms delay per batch (smoother than 40ms per card)

        # Set remaining cards to visible immediately
        for card in game_cards[12:]:
            card.opacity = 0.5 if card.is_ignored else 1
        self.update()

    async def _on_refresh_clicked(self, e):
        """Handle refresh button click with rotation animation"""
        # Rotate refresh button
        if self.refresh_button_ref.current:
            self.refresh_button_ref.current.rotate += math.pi * 2  # 360 degrees
            self.update()

        # Refresh DLL versions from filesystem before rebuilding cards
        # This ensures the DB has current versions after any external updates
        await self.refresh_all_badges()

        # Force=True to bypass the "already loaded" optimization
        await self.load_games(force=True)

    def _update_backup_stats(self, count: int, total_size: int):
        """Update the backup stats display in the header."""
        if count == 0:
            self.backup_stats_text.value = ""
        else:
            if total_size < 1024:
                size_str = f"{total_size} B"
            elif total_size < 1024 * 1024:
                size_str = f"{total_size / 1024:.1f} KB"
            elif total_size < 1024 * 1024 * 1024:
                size_str = f"{total_size / (1024 * 1024):.1f} MB"
            else:
                size_str = f"{total_size / (1024 * 1024 * 1024):.1f} GB"
            self.backup_stats_text.value = f"{count} DLL backup{'s' if count != 1 else ''} · {size_str}"

    async def _on_reresolution_complete(self):
        """Called after re-resolution updates game app IDs.

        Reloads the games view to show updated images.
        """
        self.logger.info("Re-resolution complete, reloading games view...")
        await self.load_games(force=True)

    # ===== Filter Methods =====

    def _card_passes_filters(self, card: GameCard) -> bool:
        """Check if a card passes all active filters (search, ignore, status chips)."""
        # Ignore filter
        if not self._show_ignored_games and card.is_ignored:
            return False

        # Search query filter (match display name, like the suggestions do)
        if self.search_query:
            query = self.search_query.lower()
            if query not in card.game.name.lower() and query not in card.game.display_name.lower():
                return False

        # Status chip filters
        if self._filter_needs_update and not card._check_for_updates():
            return False
        if self._filter_up_to_date and card._check_for_updates():
            return False
        if self._filter_has_backups and not card.has_backups:
            return False

        return True

    def _on_status_chip_select(self, e):
        """Handle a status filter chip toggle (visibility-only, no rebuild)."""
        kind = e.control.data
        selected = bool(e.control.selected)

        if kind == "needs_update":
            self._filter_needs_update = selected
            # Mutually exclusive with "Up to date"
            if selected and self._filter_up_to_date:
                self._filter_up_to_date = False
                self._up_to_date_chip.selected = False
        elif kind == "up_to_date":
            self._filter_up_to_date = selected
            if selected and self._filter_needs_update:
                self._filter_needs_update = False
                self._needs_update_chip.selected = False
        elif kind == "has_backups":
            self._filter_has_backups = selected

        self._apply_visibility()
        self.update()

    def _get_search_suggestions(self, query: str) -> list[str]:
        """Return game display names matching the query (for search dropdown)."""
        query = query.lower()
        matches: list[str] = []
        seen: set[str] = set()
        for card in self.game_cards.values():
            name = card.game.display_name
            if query in name.lower() and name.lower() not in seen:
                matches.append(name)
                seen.add(name.lower())
        # Prefix matches first, then alphabetical
        matches.sort(key=lambda n: (not n.lower().startswith(query), n.lower()))
        return matches

    def _apply_visibility(self) -> int:
        """Apply all filters to set card visibility. Returns matching count."""
        current_launcher = self._get_current_launcher()
        matching = 0

        for game_id, card in self.game_cards.items():
            # Launcher tab filter
            if current_launcher and card.game.launcher != current_launcher:
                card.visible = False
                continue

            visible = self._card_passes_filters(card)
            card.visible = visible
            if visible:
                matching += 1

        return matching

    # ===== Ignore List Methods =====

    def _on_ignore_filter_toggle(self, e):
        """Toggle visibility of ignored games."""
        self._show_ignored_games = not self._show_ignored_games
        if self.options_menu:
            self.options_menu.items = self._build_options_menu_items()
            try:
                self.options_menu.update()
            except Exception:
                pass
        self._apply_visibility()
        self.update()

    def _on_game_ignore_toggle(self, game, ignored: bool):
        """Handle ignore toggle from GameCard — launches async DB update."""
        if self._page_ref:
            self._page_ref.run_task(self._perform_ignore_toggle, game, ignored)

    async def _perform_ignore_toggle(self, game, ignored: bool):
        """Persist ignore status to database and update card UI."""
        # For MergedGame, use primary_game.id; for Game, use .id directly
        game_id = game.primary_game.id if isinstance(game, MergedGame) else game.id
        game_name = game.primary_game.name if isinstance(game, MergedGame) else game.name

        success = await db_manager.set_game_ignored(game_id, ignored)
        if not success:
            self.logger.error(f"Failed to set ignore status for {game_name}")
            return

        # Update local tracking set
        if ignored:
            self._ignored_game_ids.add(game_id)
        else:
            self._ignored_game_ids.discard(game_id)

        # Update the card visually
        card = self.game_cards.get(game_id)
        if card:
            card.set_ignored(ignored)
            self._apply_visibility()
            self.update()

        action = "ignored" if ignored else "un-ignored"
        self.logger.info(f"Game '{game_name}' {action}")

        # Confirmation snackbar with Undo (reverts the ignore change)
        async def on_undo(e):
            await self._perform_ignore_toggle(game, not ignored)

        is_dark = self._get_is_dark()
        snackbar = ft.SnackBar(
            content=ft.Text(f"'{game_name}' {action}", color=ft.Colors.WHITE),
            bgcolor=MD3Colors.get_themed("snackbar_bg", is_dark),
            duration=5000,
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

    def _on_game_resolve(self, game, override_steam_app_id: int, display_name_override: str):
        """Handle Steam resolve callback from GameCard — fires after DB write succeeds."""
        # The card already updated its own UI via apply_resolution().
        # GamesView just needs to log; no additional DB work needed here since
        # the dialog already called db_manager.set_game_override().
        game_name = game.primary_game.name if isinstance(game, MergedGame) else game.name
        if override_steam_app_id:
            self.logger.info(
                f"Game '{game_name}' linked to Steam App ID {override_steam_app_id} "
                f"({display_name_override})"
            )
        else:
            self.logger.info(f"Cleared Steam override for '{game_name}'")

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
        """Execute search filtering on game cards (composes with sort/filter)."""
        import time
        from dlss_updater.ui_flet.perf_monitor import perf_logger

        start_total = time.perf_counter()

        # Check if this search has been superseded
        if generation != self._search_generation:
            return

        # Use unified visibility system (respects tech/status filters too)
        start_filter = time.perf_counter()
        matching_count = self._apply_visibility()
        filter_ms = (time.perf_counter() - start_filter) * 1000

        start_update = time.perf_counter()
        self.update()
        update_ms = (time.perf_counter() - start_update) * 1000

        total_ms = (time.perf_counter() - start_total) * 1000
        perf_logger.debug(f"[PERF] search '{query}': filter={filter_ms:.1f}ms, update={update_ms:.1f}ms, total={total_ms:.1f}ms, matches={matching_count}")

        # Save to search history AFTER logging (non-blocking, fire-and-forget)
        current_launcher = self._get_current_launcher()
        if matching_count > 0 and len(query) >= 2:
            asyncio.create_task(self._save_search_history_background(query, current_launcher, matching_count))

    async def _save_search_history_background(self, query: str, launcher: str | None, count: int):
        """Save search history in background without blocking UI."""
        try:
            await db_manager.add_search_history(query, launcher, count)
            await self._load_search_history()
        except Exception as e:
            self.logger.debug(f"Error saving search history: {e}")

    async def _show_all_games(self):
        """Show all games (clear search filter, respects other active filters)."""
        self._apply_visibility()
        self.update()

    def _get_current_launcher(self) -> str | None:
        """Get the currently selected launcher tab."""
        if not hasattr(self, 'tabs_control') or not self.tabs_control:
            return None

        if self.tabs_control.selected_index is None:
            return None

        # Use tracked launcher names list (Flet 0.80.4 compatible)
        if hasattr(self, '_tab_launchers') and self._tab_launchers:
            idx = self.tabs_control.selected_index
            if 0 <= idx < len(self._tab_launchers):
                return self._tab_launchers[idx]
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
                ft.TextButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
            ]
            self._page_ref.show_dialog(info_dialog)
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
                on_click=lambda e: self._page_ref.pop_dialog(),
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

        self._page_ref.show_dialog(confirm_dialog)

    def _create_delete_all_handler(self, dialog: ft.AlertDialog):
        """Create async delete all handler"""
        async def handler(e):
            await self._perform_delete_all(dialog)
        return handler

    async def _perform_delete_all(self, dialog: ft.AlertDialog):
        """Perform the delete all operation"""
        self._page_ref.pop_dialog()

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
        self._page_ref.show_dialog(progress_dialog)
        self._page_ref.update()

        try:
            # Delete all games from database
            from dlss_updater.database import db_manager
            deleted_count = await db_manager.delete_all_games()

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show success dialog - create without actions first
            success_dialog = ft.AlertDialog(
                title=ft.Text("Success"),
                content=ft.Text(f"Successfully deleted {deleted_count} game(s)."),
            )
            # Add actions after dialog variable exists
            success_dialog.actions = [
                ft.TextButton(
                    "OK",
                    on_click=lambda e: self._page_ref.pop_dialog(),
                ),
            ]
            self._page_ref.show_dialog(success_dialog)

            # Force reload games list (database was cleared)
            self._games_loaded = False
            await self.load_games()

        except Exception as ex:
            self.logger.error(f"Error deleting all games: {ex}", exc_info=True)

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show error dialog - create without actions first
            error_dialog = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(f"Failed to delete games: {str(ex)}"),
            )
            # Add actions after dialog variable exists
            error_dialog.actions = [
                ft.TextButton(
                    "OK",
                    on_click=lambda e: self._page_ref.pop_dialog(),
                ),
            ]
            self._page_ref.show_dialog(error_dialog)

    async def refresh_all_badges(self):
        """Refresh DLL badges on all game cards.

        Re-reads actual DLL versions from the filesystem (not just the DB),
        updates the database, then refreshes each card's badge. This handles
        the case where a global update wrote new DLL files but the DB's
        current_version wasn't updated.
        """
        if not self.game_cards:
            return

        game_ids = list(self.game_cards.keys())

        # Refresh versions from filesystem -> DB for all games in parallel
        # (anyio task group; results kept index-aligned with game_ids).
        results: list[Any] = [None] * len(game_ids)

        async def _refresh(i: int, gid: int) -> None:
            try:
                results[i] = await db_manager.refresh_dll_versions_for_game(gid)
            except Exception as e:
                results[i] = e

        async with anyio.create_task_group() as tg:
            for i, gid in enumerate(game_ids):
                tg.start_soon(_refresh, i, gid)

        # Batch-fetch fresh backup groups so restore menus also re-sync after bulk update
        try:
            all_backup_groups = await anyio.to_thread.run_sync(
                db_manager.batch_get_backups_grouped_sync, game_ids, limiter=thread_io
            )
        except Exception as ex:
            self.logger.warning(f"Failed to batch-fetch backup groups: {ex}")
            all_backup_groups = {}

        refreshed = 0
        for game_id, result in zip(game_ids, results):
            if isinstance(result, Exception):
                self.logger.warning(f"Failed to refresh DLLs for game {game_id}: {result}")
                continue
            card = self.game_cards.get(game_id)
            if card and result:
                await card.refresh_dlls(result)
                await card.refresh_restore_button(all_backup_groups.get(game_id, {}))
                refreshed += 1

        self.logger.info(f"Refreshed DLL badges for {refreshed}/{len(game_ids)} game cards")

        # Badge refresh can flip needs_update/has_backups for many cards at
        # once (bulk update reconciliation) — recount the filter chips.
        if refreshed:
            self._update_filter_chip_counts()

    def _on_game_update(self, game, dll_group: str = "all"):
        """Handle game update button click - launches async update"""
        self.logger.info(f"Update requested for game: {game.name}, group: {dll_group}")
        # Launch the async update using Flet's page.run_task for proper event loop handling
        if self._page_ref:
            self._page_ref.run_task(self._perform_game_update_with_warning, game, dll_group)

    async def _perform_game_update_with_warning(self, game, dll_group: str = "all"):
        """Check rollback-compat flags, optionally show warning dialog, then run update.

        Flagged versions are those the user has rolled back from in >=2 other games
        recently — an empirical signal that the same version may be problematic here.
        """
        skip_dll_filenames: set[str] | None = None
        try:
            from dlss_updater.constants import DLL_GROUPS
            from dlss_updater.config import LATEST_DLL_VERSIONS

            flagged_map = await db_manager.get_flagged_dll_versions()
            if flagged_map:
                game_dlls = await db_manager.get_dlls_for_game(game.id)

                # Determine target DLL filenames for this update (respect group filter)
                target_filenames: set[str] = set()
                for gdll in game_dlls:
                    fname = (gdll.dll_filename or "").lower()
                    if not fname:
                        continue
                    if dll_group != "all":
                        allowed = {d.lower() for d in DLL_GROUPS.get(dll_group, [])}
                        if fname not in allowed:
                            continue
                    target_filenames.add(fname)

                # Cross-reference (filename, latest_version) against flagged set.
                # DLLs are vendor-signed → a flagged version is bad regardless of which
                # game rolled back from it, so we don't exclude the current game here.
                flagged_for_this_update: list[dict] = []
                for fname in target_filenames:
                    latest = LATEST_DLL_VERSIONS.get(fname)
                    if not latest:
                        continue
                    key = (fname, latest)
                    entry = flagged_map.get(key)
                    if entry:
                        flagged_for_this_update.append({
                            "dll_filename": fname,
                            "target_version": latest,
                            "event_count": entry.get("count", 0),
                            "affected_games": entry.get("games", []),
                            "from_versions": entry.get("from_versions", []),
                        })

                if flagged_for_this_update:
                    from dlss_updater.ui_flet.dialogs.rollback_warning_dialog import RollbackWarningDialog
                    dialog = RollbackWarningDialog(
                        self._page_ref, self.logger, game.name, flagged_for_this_update
                    )
                    result = await dialog.show()
                    if result == "cancel":
                        self.logger.info(f"Update cancelled by user (rollback warning): {game.name}")
                        return
                    if result == "skip":
                        skip_dll_filenames = {e["dll_filename"] for e in flagged_for_this_update}
                        self.logger.info(
                            f"User chose to skip flagged DLLs: {skip_dll_filenames}"
                        )
        except Exception as ex:
            # Never block an update on the warning path — fail open
            self.logger.warning(f"Rollback warning check failed: {ex}", exc_info=True)

        await self._perform_game_update(game, dll_group, skip_dll_filenames=skip_dll_filenames)

    async def _perform_game_update(self, game, dll_group: str = "all", skip_dll_filenames: set[str] | None = None):
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
        self._page_ref.show_dialog(progress_dialog)

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

            # Run update with optional group filter and flagged-DLL skip set
            result = await self.update_coordinator.update_single_game(
                game.id,
                game.name,
                dll_groups=[dll_group] if dll_group != "all" else None,
                progress_callback=on_progress,
                skip_dll_filenames=skip_dll_filenames,
            )

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show results
            await self._show_update_results_dialog(game.name, result)

            # Refresh the game card's DLL badges AND restore button if update succeeded
            # (updates create new backups, which must appear in the restore menu)
            if result['success'] and game_card:
                new_dlls = await db_manager.get_dlls_for_game(game.id)
                await game_card.refresh_dlls(new_dlls)
                new_backup_groups = await db_manager.get_backups_grouped_by_dll_type(game.id)
                await game_card.refresh_restore_button(new_backup_groups)
                # This card's needs_update/has_backups may have just flipped.
                self._update_filter_chip_counts()
                self.update()

        except Exception as ex:
            self.logger.error(f"Update failed for {game.name}: {ex}", exc_info=True)
            self._page_ref.pop_dialog()
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
        if self._page_ref:
            self._page_ref.update()

    async def _show_update_results_dialog(self, game_name: str, result: dict[str, Any]):
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
            ft.TextButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
        ]
        self._page_ref.show_dialog(results_dialog)

    async def _show_error_dialog(self, title: str, message: str, color=ft.Colors.RED):
        """Show error dialog"""
        error_dialog = ft.AlertDialog(
            title=ft.Text(title, color=color),
            content=ft.Text(message),
        )
        error_dialog.actions = [
            ft.TextButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
        ]
        self._page_ref.show_dialog(error_dialog)

    def _on_game_restore(self, game, dll_group: str = "all"):
        """Handle game restore button click - launches async restore"""
        self.logger.info(f"Restore requested for game: {game.name}, group: {dll_group}")
        if self._page_ref:
            self._page_ref.run_task(self._perform_game_restore, game, dll_group)

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
        self._page_ref.show_dialog(progress_dialog)

        try:
            # Perform restore
            success, summary, results = await restore_group_for_game(game.id, dll_group)

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show results
            await self._show_restore_results_dialog(game.name, success, summary, results)

            # Refresh the game card's DLL badges and backup groups
            if game_card:
                new_dlls = await db_manager.get_dlls_for_game(game.id)
                await game_card.refresh_dlls(new_dlls)
                new_backup_groups = await db_manager.get_backups_grouped_by_dll_type(game.id)
                await game_card.refresh_restore_button(new_backup_groups)
                # Restoring a backup flips needs_update/has_backups for this card.
                self._update_filter_chip_counts()
                self.update()

        except Exception as ex:
            self.logger.error(f"Restore failed for {game.name}: {ex}", exc_info=True)
            self._page_ref.pop_dialog()
            await self._show_error_dialog(
                "Restore Failed",
                f"Failed to restore {game.name}: {str(ex)}",
                ft.Colors.RED
            )

    async def _show_restore_confirmation_dialog(self, game, dll_group: str) -> bool:
        """Show confirmation dialog before restore, returns True if confirmed"""
        confirmed = anyio.Event()
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
            self._page_ref.pop_dialog()
            confirmed.set()

        def on_confirm(e):
            result[0] = True
            self._page_ref.pop_dialog()
            confirmed.set()

        dialog.actions = [
            ft.TextButton("Cancel", on_click=on_cancel),
            ft.ElevatedButton(
                "Restore",
                on_click=on_confirm,
                style=ft.ButtonStyle(bgcolor="#4CAF50", color=ft.Colors.WHITE),
            ),
        ]

        self._page_ref.show_dialog(dialog)
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
            ft.TextButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
        ]
        self._page_ref.show_dialog(results_dialog)

    async def on_view_hidden(self):
        """Called when view is hidden (tab switch) - minimal cleanup for fast switching.

        PERFORMANCE: When keep_games_in_memory is True (default), game cards and
        search index are preserved for instant tab switching. This avoids the
        ~1.5s rebuild cost on every tab switch.
        """
        from dlss_updater.config import config_manager
        from dlss_updater.search_service import search_service

        # Read config off the event loop to avoid deadlock with _config_lock
        # (filter handlers may have fire-and-forget config writes in flight)
        keep_in_memory = await anyio.to_thread.run_sync(
            config_manager.get_keep_games_in_memory, limiter=thread_io
        )

        if not keep_in_memory:
            search_service.clear_index()
            self._games_loaded = False  # Force reload on next tab switch
            self.logger.debug("Games view hidden - search index cleared, will reload on next visit")
        else:
            # Keep _games_loaded = True for fast tab switching
            self.logger.debug("Games view hidden - keeping in memory for fast switching")

    async def on_shutdown(self):
        """Called during application shutdown - full resource cleanup"""
        from dlss_updater.search_service import search_service

        self.logger.debug("Games view shutdown - releasing all resources")

        # Reset loaded flag
        self._games_loaded = False

        # Clear game card references to allow garbage collection
        self.game_cards.clear()
        self.game_card_containers.clear()

        # Cancel update coordinator if exists
        if self.update_coordinator:
            self.update_coordinator.cancel()
            self.update_coordinator = None

        # Cleanup search bar
        if self.search_bar:
            await self.search_bar.cleanup()

        # Clear search index
        search_service.clear_index()

        # Unregister from theme system
        self._unregister_theme_aware()
