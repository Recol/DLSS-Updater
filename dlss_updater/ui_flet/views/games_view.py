"""
Games View - Display all games organized by launcher with Steam images

PERFORMANCE NOTES:
- Uses GridView with virtualization (only visible cards are rendered)
- Progressive loading: first batch shown immediately, rest created in background
- Parallel data loading with asyncio.gather() for DLLs and backups
- ImageLoadCoordinator batches page.update() calls for images (~5x faster)
- Search filtering via visibility toggles (no grid rebuild)
"""

import asyncio
import math
import time
from typing import Callable, Any, TYPE_CHECKING
import flet as ft

from dlss_updater.database import db_manager, Game, merge_games_by_name
from dlss_updater.models import MergedGame
from dlss_updater.ui_flet.components.game_card import GameCard
from dlss_updater.ui_flet.components.search_bar import SearchBar
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.async_updater import AsyncUpdateCoordinator
from dlss_updater.config import is_dll_cache_ready
from dlss_updater.search_service import search_service
from dlss_updater.task_registry import register_task

# PERFORMANCE: Progressive loading constants
# First batch shows immediately, rest loads in background
GAMES_INITIAL_BATCH_SIZE = 16  # Visible cards on typical screen
GAMES_BACKGROUND_BATCH_SIZE = 24  # Cards per background batch

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

    def __init__(self, page: ft.Page, logger=None):
        self._page_ref = page
        self._logger = logger
        self._pending_cards: list[tuple['GameCard', str]] = []
        self._batch_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
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
        await asyncio.sleep(self._debounce_ms / 1000)
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

        # SINGLE page.update() to attach all controls to render tree
        start_update1 = time.perf_counter()
        try:
            if self._page_ref:
                self._page_ref.update()
        except Exception as e:
            if self._logger:
                self._logger.debug(f"[ImageLoadCoordinator] Error during first page.update(): {e}")
            return
        update1_ms = (time.perf_counter() - start_update1) * 1000

        # Brief delay for render tree attachment (30ms)
        await asyncio.sleep(0.03)

        # Phase 2: Trigger all fade-in animations simultaneously
        start_anim = time.perf_counter()
        for card, _ in cards_to_update:
            try:
                card.image_container.opacity = 1
                card._image_loaded = True
            except Exception:
                pass  # Card may have been disposed
        anim_ms = (time.perf_counter() - start_anim) * 1000

        # SINGLE page.update() to trigger all animations together
        start_update2 = time.perf_counter()
        try:
            if self._page_ref:
                self._page_ref.update()
        except Exception as e:
            if self._logger:
                self._logger.debug(f"[ImageLoadCoordinator] Error during animation page.update(): {e}")
        update2_ms = (time.perf_counter() - start_update2) * 1000

        total_ms = (time.perf_counter() - start_total) * 1000
        if self._logger:
            self._logger.debug(
                f"[ImageLoadCoordinator] Batch complete - {len(cards_to_update)} images "
                f"(setup={setup_ms:.1f}ms, update1={update1_ms:.1f}ms, anim={anim_ms:.1f}ms, update2={update2_ms:.1f}ms, total={total_ms:.1f}ms)"
            )


class GamesView(ThemeAwareMixin, ft.Column):
    """Games library view with launcher tabs

    Note: Cannot use is_isolated=True because view content needs to be updated
    via page.update() for tab switching and game loading operations.
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

        # Button references for state management
        self.delete_db_button: ft.ElevatedButton | None = None

        # Search state
        self.search_query: str = ""
        self._search_generation: int = 0
        self.search_bar: SearchBar | None = None

        # PERFORMANCE: Track if games are already loaded to prevent redundant rebuilds
        # on tab switching. Only reload on explicit refresh or when forced=True
        self._games_loaded = False

        # Initialize theme system reference before building UI
        self._registry = get_theme_registry()
        self._theme_priority = 10  # Views are high priority (animate early)

        # Build initial UI
        self._build_ui()

        # Register with theme system after UI is built
        self._register_theme_aware()

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
        # Get theme preference from registry
        is_dark = self._get_is_dark()

        # Create search bar
        self.search_bar = SearchBar(
            on_search=self._on_search_changed,
            on_clear=self._on_search_cleared,
            on_history_selected=self._on_history_selected,
            placeholder="Search games...",
            width=300,
        )

        # Store themed element references
        self.header_title = ft.Text(
            "Games Library",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
        )

        self.loading_text = ft.Text(
            "Loading games...",
            color=MD3Colors.get_text_primary(is_dark),
        )

        self.divider = ft.Divider(height=1, color=MD3Colors.get_outline(is_dark))

        # Header
        self.header = ft.Container(
            content=ft.Row(
                controls=[
                    self.header_title,
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

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware system"""
        return {
            "header.bgcolor": (MD3Colors.SURFACE_VARIANT, MD3Colors.SURFACE_VARIANT_LIGHT),
            "header_title.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "loading_text.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "divider.color": (MD3Colors.get_outline(True), MD3Colors.get_outline(False)),
        }

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
            self.logger.debug("Games already loaded - skipping redundant reload")
            # Just ensure the view is visible
            self.tabs_container.visible = True
            self.empty_state.visible = False
            self.loading_indicator.visible = False
            if self._page_ref:
                self._page_ref.update()
            return

        self.is_loading = True
        self.loading_indicator.visible = True
        self.empty_state.visible = False
        self.tabs_container.visible = False
        if self._page_ref:
            self._page_ref.update()

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
                if self._page_ref:
                    self._page_ref.update()
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

            self.logger.info(f"Loaded {sum(len(games) for games in self.games_by_launcher.values())} games from {len(self.games_by_launcher)} launchers")

        except Exception as e:
            self.logger.error(f"Error loading games: {e}", exc_info=True)
            self.empty_state.visible = True
            self.loading_indicator.visible = False
            self._update_delete_button_state(False)
            self._games_loaded = False  # Allow retry on next tab switch

        finally:
            self.is_loading = False
            if self._page_ref:
                self._page_ref.update()

    async def _build_launcher_tabs(self):
        """Build tabs for each launcher with games (Flet 0.80.4 TabBar/TabBarView pattern)"""
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

        for launcher, games in self.games_by_launcher.items():
            if not games:
                continue

            # Merge games with same name into single entries
            merged_games = merge_games_by_name(games)

            # Load DLLs and backup groups for ALL game IDs in each merged game
            async def load_merged_game_data(merged: MergedGame):
                all_dlls = []
                all_backup_groups = {}

                for game_id in merged.all_game_ids:
                    dlls = await db_manager.get_dlls_for_game(game_id)
                    all_dlls.extend(dlls)

                    backup_groups = await db_manager.get_backups_grouped_by_dll_type(game_id)
                    for dll_type, backups in backup_groups.items():
                        if dll_type not in all_backup_groups:
                            all_backup_groups[dll_type] = []
                        all_backup_groups[dll_type].extend(backups)

                return merged, all_dlls, all_backup_groups

            # PERFORMANCE: Load all game data in parallel
            start_data = time.perf_counter()
            tasks = [load_merged_game_data(mg) for mg in merged_games]
            results = await asyncio.gather(*tasks)
            data_ms = (time.perf_counter() - start_data) * 1000
            self.logger.debug(f"[PERF] Data loading for {launcher} ({len(results)} games): {data_ms:.1f}ms")

            # PERFORMANCE: Create game cards with helper function
            def create_card(merged, dlls, backup_groups):
                card = GameCard(
                    game=merged,
                    dlls=dlls,
                    page=self._page_ref,
                    logger=self.logger,
                    on_update=self._on_game_update,
                    on_restore=self._on_game_restore,
                    backup_groups=backup_groups,
                )
                card.opacity = 0
                card.animate_opacity = ft.Animation(400, ft.AnimationCurve.EASE_OUT)
                return card

            # PERFORMANCE: Progressive card creation for 200+ games
            # Create first batch immediately (visible cards)
            start_cards = time.perf_counter()
            first_batch_results = results[:GAMES_INITIAL_BATCH_SIZE]
            remaining_results = results[GAMES_INITIAL_BATCH_SIZE:]

            game_cards = []
            for merged, dlls, backup_groups in first_batch_results:
                card = create_card(merged, dlls, backup_groups)
                self.game_cards[merged.primary_game.id] = card
                game_cards.append(card)

            first_batch_ms = (time.perf_counter() - start_cards) * 1000
            self.logger.debug(f"[PERF] First batch cards ({len(first_batch_results)}): {first_batch_ms:.1f}ms")

            # Batch fetch all cached image paths (single query vs N queries)
            steam_app_ids = [
                card.game.steam_app_id
                for card in game_cards
                if card.game.steam_app_id
            ]
            cached_paths = await db_manager.batch_get_cached_image_paths(steam_app_ids)

            # Create coordinator for batched image loading (reduces page.update() calls)
            # This improves loading from ~1.9s to ~350-400ms for 11 images
            coordinator = ImageLoadCoordinator(self._page_ref, self.logger)

            # Load images asynchronously with coordinator for batched UI updates
            for card in game_cards:
                prefetched_path = cached_paths.get(card.game.steam_app_id) if card.game.steam_app_id else None
                task = asyncio.create_task(card.load_image(prefetched_path, coordinator=coordinator))
                register_task(task, f"load_image_{card.game.name[:20]}")

            # Trigger staggered fade-in animation - REGISTER THE TASK
            anim_task = asyncio.create_task(self._animate_cards_in(game_cards))
            register_task(anim_task, f"animate_cards_{launcher}")

            # PERFORMANCE: Use GridView with max_extent for virtualization + responsive columns
            # GridView only renders visible items (unlike Column+ResponsiveRow which renders ALL)
            # max_extent=320 gives responsive columns: 4 on 1280px, 3 on 960px, 2 on 640px, 1 on 320px
            # This is critical for 50+ game cards - 10x faster scrolling
            for card in game_cards:
                # Track card directly for search visibility control
                self.game_card_containers[card.game.id] = card

            # Create GridView with virtualization (only visible items rendered)
            # Aspect ratio 1.45 = 320/220 to match new card height (was 1.78 for 180px)
            game_grid = ft.GridView(
                controls=game_cards,
                max_extent=320,  # Max card width - responsive columns
                child_aspect_ratio=1.45,  # Width/height ratio (320/220)
                padding=16,
                spacing=12,
                run_spacing=12,
                expand=True,
            )

            # PERFORMANCE: Load remaining cards in background (for 200+ games)
            if remaining_results:
                bg_task = asyncio.create_task(
                    self._load_remaining_game_cards(
                        remaining_results, game_grid, coordinator, create_card, launcher
                    )
                )
                register_task(bg_task, f"load_remaining_cards_{launcher}")

            # Create tab header (label/icon only - content goes in TabBarView)
            tab_header = ft.Tab(
                label=f"{launcher} ({len(games)})",
                icon=launcher_icons.get(launcher, ft.Icons.FOLDER),
            )
            tabs.append(tab_header)
            tab_contents.append(game_grid)  # GridView directly as tab content
            self._tab_launchers.append(launcher)  # Track launcher name by tab index

        # Create Tabs control with TabBar + TabBarView (Flet 0.80.4 pattern)
        self.tabs_control = ft.Tabs(
            length=len(tabs),
            selected_index=0,
            animation_duration=300,
            expand=True,
            on_change=self._on_tab_changed,
            content=ft.Column(
                expand=True,
                controls=[
                    ft.TabBar(tabs=tabs),
                    ft.TabBarView(expand=True, controls=tab_contents),
                ],
            ),
        )

        self.tabs_container.content = self.tabs_control

    async def _on_tab_changed(self, e):
        """Handle tab change - reapply search filter to new tab."""
        if self.search_query:
            await self._execute_search(self.search_query, self._search_generation)
        else:
            await self._show_all_games()

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
                steam_ids = [c.game.steam_app_id for c in new_cards if c.game.steam_app_id]
                if steam_ids:
                    cached_paths = await db_manager.batch_get_cached_image_paths(steam_ids)
                    for card in new_cards:
                        if card.game.steam_app_id:
                            path = cached_paths.get(card.game.steam_app_id)
                            task = asyncio.create_task(card.load_image(path, coordinator=coordinator))
                            register_task(task, f"load_image_bg_{card.game.name[:15]}")

                # Make cards visible immediately (no stagger for background cards)
                for card in new_cards:
                    card.opacity = 1

                # Single update per batch
                if self._page_ref:
                    self._page_ref.update()

                # Yield to event loop
                await asyncio.sleep(0.02)

            self.logger.debug(f"[PERF] Background loaded {loaded} additional {launcher} cards")

        except Exception as e:
            self.logger.error(f"Error loading remaining game cards: {e}", exc_info=True)

    async def _animate_cards_in(self, game_cards: list[GameCard]):
        """Animate game cards with staggered fade-in for grid layout (optimized)"""
        # Small initial delay
        await asyncio.sleep(0.1)

        # For grid layout, animate first 12 cards in batches of 4 to reduce update calls
        cards_to_animate = game_cards[:12]
        batch_size = 4

        for batch_start in range(0, len(cards_to_animate), batch_size):
            batch_end = min(batch_start + batch_size, len(cards_to_animate))
            # Set opacity for entire batch
            for card in cards_to_animate[batch_start:batch_end]:
                card.opacity = 1
            # Single update per batch instead of per card
            if self._page_ref:
                self._page_ref.update()
            await asyncio.sleep(0.08)  # 80ms delay per batch (smoother than 40ms per card)

        # Set remaining cards to visible immediately
        for card in game_cards[12:]:
            card.opacity = 1
        if self._page_ref:
            self._page_ref.update()

    async def _on_refresh_clicked(self, e):
        """Handle refresh button click with rotation animation"""
        # Rotate refresh button
        if self.refresh_button_ref.current:
            self.refresh_button_ref.current.rotate += math.pi * 2  # 360 degrees
            self._page_ref.update()

        # Force=True to bypass the "already loaded" optimization
        await self.load_games(force=True)

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
        import time
        from dlss_updater.ui_flet.perf_monitor import perf_logger

        start_total = time.perf_counter()

        # Check if this search has been superseded
        if generation != self._search_generation:
            return

        query_lower = query.lower()

        # Get current tab launcher
        current_launcher = self._get_current_launcher()

        # Filter cards by visibility (set on card directly for GridView)
        start_filter = time.perf_counter()
        matching_count = 0
        for game_id, card in self.game_cards.items():
            if current_launcher and card.game.launcher != current_launcher:
                continue

            # Check if game name matches query
            matches = query_lower in card.game.name.lower()

            # Set visibility on card directly (GridView handles layout)
            card.visible = matches
            if matches:
                matching_count += 1
        filter_ms = (time.perf_counter() - start_filter) * 1000

        start_update = time.perf_counter()
        if self._page_ref:
            self._page_ref.update()
        update_ms = (time.perf_counter() - start_update) * 1000

        # Save to search history after search (if results exist)
        if matching_count > 0 and len(query) >= 2:
            await db_manager.add_search_history(query, current_launcher, matching_count)
            # Refresh history button to show new entry
            await self._load_search_history()

        total_ms = (time.perf_counter() - start_total) * 1000
        perf_logger.debug(f"[PERF] search '{query}': filter={filter_ms:.1f}ms, update={update_ms:.1f}ms, total={total_ms:.1f}ms, matches={matching_count}")

    async def _show_all_games(self):
        """Show all games (clear search filter)."""
        current_launcher = self._get_current_launcher()

        for game_id, card in self.game_cards.items():
            if current_launcher:
                card.visible = card.game.launcher == current_launcher
            else:
                card.visible = True

        if self._page_ref:
            self._page_ref.update()

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

    def _on_game_update(self, game, dll_group: str = "all"):
        """Handle game update button click - launches async update"""
        self.logger.info(f"Update requested for game: {game.name}, group: {dll_group}")
        # Launch the async update using Flet's page.run_task for proper event loop handling
        if self._page_ref:
            self._page_ref.run_task(self._perform_game_update, game, dll_group)

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

            # Run update with optional group filter
            result = await self.update_coordinator.update_single_game(
                game.id,
                game.name,
                dll_groups=[dll_group] if dll_group != "all" else None,
                progress_callback=on_progress
            )

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show results
            await self._show_update_results_dialog(game.name, result)

            # Refresh the game card's DLL badges if update succeeded
            if result['success'] and game_card:
                new_dlls = await db_manager.get_dlls_for_game(game.id)
                await game_card.refresh_dlls(new_dlls)

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

        # Only clear resources if user preference says so
        if not config_manager.get_keep_games_in_memory():
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
