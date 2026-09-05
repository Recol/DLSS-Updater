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
from typing import Any, TYPE_CHECKING
import anyio
import flet as ft

from dlss_updater.concurrency_limiters import thread_io, io_heavy

from dlss_updater import dll_repository
from dlss_updater.database import db_manager, Game, merge_games_by_name
from dlss_updater.models import MergedGame, GameDLL, DLLBackup, GameDLSSPresets
from dlss_updater.ui_flet.components.game_card import GameCard, FOOTER_HEIGHT, HERO_HEIGHT
from dlss_updater.ui_flet.components.search_bar import GameSearchBar
from dlss_updater.ui_flet.components.floating_pill import PILL_CLEARANCE
from dlss_updater.ui_flet.components.hero_surface import build_brand_wash, build_pill, themed_accent
from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.async_updater import AsyncUpdateCoordinator
from dlss_updater.ui_flet.hyper_parallel_loader import HyperParallelLoader, LoadTask
from dlss_updater.config import is_dll_cache_ready, config_manager
from dlss_updater.search_service import search_service
from dlss_updater.task_registry import register_task

# PERFORMANCE: Progressive loading constants
# First batch shows immediately, rest loads in background
GAMES_INITIAL_BATCH_SIZE = 16  # Visible cards on typical screen
GAMES_BACKGROUND_BATCH_SIZE = 24  # Cards per background batch

# ==================== GRID DENSITY ====================
# Card layout is a flexible banner + a FIXED 52 px footer (see game_card.py:
# HERO_HEIGHT=204 target banner + FOOTER_HEIGHT=52 → 256 px total at the dominant cell
# width). The aspect ratio must therefore always be max_extent / (banner + FOOTER_HEIGHT):
# that makes a cell exactly as tall as its contents, so the banner sits at its target and
# the fixed footer is never clipped and never trailed by a grey gap. At other widths the
# banner flexes (BoxFit.COVER crops).
#
# Because the aspect ratio is DERIVED from those two numbers rather than written out, a
# new density can never silently violate that invariant — the table is (max_extent,
# banner_target) and resolve_density() computes the rest. Banner targets keep the
# comfortable card's proportions (banner ≈ 0.6375 × cell width).
#
# Keys are config.toml's [ui_preferences].GridDensity vocabulary exactly (see
# config_manager.get/set_grid_density and models.UIPreferencesConfig.grid_density).
GRID_DENSITIES: dict[str, tuple[int, int]] = {
    "compact": (240, 153),
    "comfortable": (320, HERO_HEIGHT),  # 204 — the long-standing default geometry
    "large": (400, 255),
}
DEFAULT_DENSITY = "comfortable"

# Menu order == display order.
DENSITY_MODES: tuple[tuple[str, str, str], ...] = (
    ("compact", "Compact", ft.Icons.GRID_ON),
    ("comfortable", "Comfortable", ft.Icons.GRID_VIEW),
    ("large", "Large", ft.Icons.CROP_SQUARE),
)


def resolve_density(name: str | None) -> tuple[int, float, int]:
    """Return ``(max_extent, child_aspect_ratio, banner_height)`` for a density.

    Falls back to the default for an unknown name or None, so a hand-edited
    config.toml (or a fresh one, where the getter can return None) degrades to
    the previous grid rather than raising at build time.
    """
    max_extent, banner = GRID_DENSITIES.get(name or "", GRID_DENSITIES[DEFAULT_DENSITY])
    return max_extent, max_extent / (banner + FOOTER_HEIGHT), banner

# ==================== SORTING ====================
# Menu order == display order. Modes match config.toml's
# [ui_preferences].sort_preference vocabulary exactly (see
# config_manager.get/set_sort_preference and models.UIPreferencesConfig).
SORT_MODES: tuple[tuple[str, str, str], ...] = (
    ("name_asc", "Name (A-Z)", ft.Icons.SORT_BY_ALPHA),
    ("name_desc", "Name (Z-A)", ft.Icons.SORT_BY_ALPHA),
    ("dll_count", "Most DLLs", ft.Icons.LAYERS),
    ("outdated_first", "Outdated first", ft.Icons.ARROW_UPWARD),
)
DEFAULT_SORT = "name_asc"


def count_outdated_dlls(dlls: list[GameDLL]) -> int:
    """Count DLLs whose installed version is behind the bundled latest.

    Single source of truth for what "needs an update" means outside a built
    card: the grid's ``outdated_first`` sort (which has to order games whose
    cards don't exist yet, during progressive loading) and the hub's
    "N need updates" pill/CTA both call this.

    Mirrors ``GameCard._get_update_counts()``'s outdated branch exactly - that
    one caches per card and is unusable before the cards exist, so the logic
    is stated once here and referenced from there.
    """
    from dlss_updater.config import LATEST_DLL_VERSIONS
    from dlss_updater.updater import parse_version

    outdated = 0
    for dll in dlls:
        if not dll.current_version or not dll.dll_filename:
            continue
        latest = LATEST_DLL_VERSIONS.get(dll.dll_filename.lower())
        if not latest:
            continue
        try:
            if parse_version(dll.current_version) < parse_version(latest):
                outdated += 1
        except Exception:
            continue
    return outdated


# ==================== BULK SELECTION ====================


def build_dll_dict_for_selection(cards) -> dict[str, list[str]]:
    """Map selected game cards onto ``{launcher: [dll_path, ...]}``.

    That is exactly the shape ``AsyncUpdateCoordinator.update_games()`` already
    takes, so a hand-picked subset runs through the same high-performance
    pipeline, cancellable overlay and summary dialog as the library-wide
    update - no second update engine, and no scan cache required (the paths
    come from the cards' already-loaded DB rows, not from scan results).

    Takes anything with ``.game.launcher`` and ``.dlls``, so it is testable
    without constructing Flet-backed cards.

    Paths are de-duplicated per launcher, preserving order: a MergedGame
    aggregates DLLs across every merged game id, so the same path can appear
    twice and would otherwise be updated - and backed up - twice. A game with
    no DLLs contributes no launcher key at all, rather than an empty list the
    coordinator would report a launcher for and then do nothing with.
    """
    dll_dict: dict[str, list[str]] = {}
    for card in cards:
        launcher = card.game.launcher
        for dll in card.dlls:
            if not dll.dll_path:
                continue
            paths = dll_dict.setdefault(launcher, [])
            if dll.dll_path not in paths:
                paths.append(dll.dll_path)
    return {launcher: paths for launcher, paths in dll_dict.items() if paths}


def filter_dll_dict(
    dll_dict: dict[str, list[str]], skip_filenames
) -> dict[str, list[str]]:
    """Drop every path whose filename is in ``skip_filenames``.

    ``update_games()`` has no ``skip_dll_filenames`` parameter (only
    ``update_single_game()`` does), so the rollback dialog's "Skip flagged"
    outcome is applied by narrowing the dict before the coordinator sees it.

    Returns a new dict; a launcher left with no paths is dropped entirely
    rather than kept as an empty list.
    """
    skip = {name.lower() for name in skip_filenames}
    if not skip:
        return dict(dll_dict)

    filtered: dict[str, list[str]] = {}
    for launcher, paths in dll_dict.items():
        kept = [p for p in paths if p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower() not in skip]
        if kept:
            filtered[launcher] = kept
    return filtered


def collect_flagged_dlls(
    target_filenames,
    flagged_map: dict[tuple[str, str], dict],
    latest_versions: dict[str, str],
) -> list[dict]:
    """Cross-reference target DLLs against versions the user has rolled back from.

    ``flagged_map`` is ``db_manager.get_flagged_dll_versions()``: keyed by
    ``(dll_filename, version)`` for versions rolled back from in >=2 games
    recently. Returns the entry shape ``RollbackWarningDialog`` consumes.

    DLLs are vendor-signed, so a flagged version is bad regardless of which
    game carries it - the check is over the UNION of the selection's filenames
    and yields ONE entry per DLL however many selected games carry it, rather
    than one warning per game. That is what lets a 40-game selection show a
    single dialog.
    """
    entries: list[dict] = []
    for fname in dict.fromkeys(f.lower() for f in target_filenames if f):
        latest = latest_versions.get(fname)
        if not latest:
            continue
        flagged = flagged_map.get((fname, latest))
        if not flagged:
            continue
        entries.append({
            "dll_filename": fname,
            "target_version": latest,
            "event_count": flagged.get("count", 0),
            "affected_games": flagged.get("games", []),
            "from_versions": flagged.get("from_versions", []),
        })
    return entries


def _on_accent(is_dark: bool) -> str:
    """Legible foreground for text/icons on a FILLED warning accent.

    WARNING is a light amber (#FFB74D) in dark mode and a dark amber
    (#7A5800) in light mode, so a fixed white foreground is unreadable on the
    dark-mode fill. Same helper as hub_card._on_accent (duplicated rather
    than cross-imported: a view importing a hub component for a color rule
    would be worse coupling than four lines).
    """
    return ft.Colors.BLACK87 if is_dark else ft.Colors.WHITE


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
                    card.image_container.opacity = 0
                    card.image_container.animate_opacity = ft.Animation(300, ft.AnimationCurve.EASE_IN)
                    card.set_image(image_path)
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

    def __init__(
        self,
        page: ft.Page,
        logger,
        on_update_all=None,
        on_update_selected=None,
        get_scope=None,
        on_scope_changed=None,
    ):
        super().__init__()
        self._page_ref = page
        self.logger = logger
        # MainView.run_bulk_update - the header's "Update all (N)" CTA goes
        # through the exact same pipeline as the Launchers action bar rather
        # than reaching into MainView internals from here.
        self._on_update_all = on_update_all
        self._scope_deps = (get_scope, on_scope_changed)
        # MainView.run_bulk_update_for_selection - same pipeline again, narrowed
        # to the selected games' DLL paths.
        self._on_update_selected = on_update_selected
        self.expand = True
        self.spacing = 0

        # State
        self.games_by_launcher: dict[str, list[Game]] = {}
        self._total_games: int = 0  # Merged game total for the header subtitle
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

        # Sort state. The persisted preference (config.toml
        # [ui_preferences].sort_preference) is read once here; every later
        # change is written back by _on_sort_selected(). _grids_by_launcher is
        # kept so a sort change can reorder the live grids without a reload,
        # and _sort_applied_at_build catches a sort chosen WHILE progressive
        # loading is still appending cards in the previous order.
        self.sort_menu: ft.PopupMenuButton | None = None
        self._grids_by_launcher: dict[str, ft.GridView] = {}
        self._sort_applied_at_build: str = DEFAULT_SORT
        try:
            self._sort_preference: str = config_manager.get_sort_preference() or DEFAULT_SORT
        except Exception:
            self._sort_preference = DEFAULT_SORT

        # Grid density. Read once from config.toml ([ui_preferences].GridDensity);
        # every later change is written back by _on_density_selected(). Lives in
        # the options menu rather than its own header button - the header is
        # already at its width budget (see the subtitle comment in _build_ui).
        try:
            self._density: str = config_manager.get_grid_density() or DEFAULT_DENSITY
        except Exception:
            self._density = DEFAULT_DENSITY
        if self._density not in GRID_DENSITIES:
            self._density = DEFAULT_DENSITY

        # Bulk selection state. A single set of game ids shared across ALL
        # launcher tabs, so a selection survives tab switches and filter
        # changes (a card hidden by a filter stays selected; the bar's count is
        # the truth). Non-empty == "selection mode": every card shows a
        # persistent checkbox and the filter-chips row is replaced by the
        # selection bar.
        self._selected_game_ids: set[int] = set()
        self.selection_bar: ft.Container | None = None
        self._selection_count_text: ft.Text | None = None
        self._selection_update_button: ft.Container | None = None
        # Set when a theme change repaints the filter chips / selection bar
        # while that control is detached from the AnimatedSwitcher (the
        # OTHER control showing instead) — patches to detached subtrees are
        # silently dropped, so the repaint is deferred and the control is
        # rebuilt fresh the next time it is swapped back in as the
        # switcher's content. Symmetric pair: one flag + one guard per
        # control, same shape both times.
        self._chips_theme_stale: bool = False
        self._selection_theme_stale: bool = False
        # Last outdated count seen by _set_update_all_state(), so the "Update
        # all" CTA can be restored verbatim once a selection is cleared.
        self._needs_update_count: int = 0

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

        accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)

        # Card size group. Check-marks the active density exactly the way the
        # sort menu marks the active mode, and like it is built UPFRONT and
        # rebuilt after a selection / on theme change - never lazily via
        # on_open, which fires after the menu has rendered (CLAUDE.md).
        density_items = [
            ft.PopupMenuItem(
                content=ft.Row([
                    ft.Icon(
                        ft.Icons.CHECK if mode == self._density else icon,
                        size=18,
                        color=accent if mode == self._density else icon_default,
                    ),
                    ft.Text(
                        label,
                        size=14,
                        color=accent if mode == self._density else on_surface,
                        weight=ft.FontWeight.W_600 if mode == self._density else ft.FontWeight.NORMAL,
                    ),
                ], spacing=8),
                data=mode,
                on_click=self._on_density_selected,
            )
            for mode, label, icon in DENSITY_MODES
        ]

        return [
            ft.PopupMenuItem(
                content=ft.Row([
                    ft.Icon(ignore_icon, size=18, color=icon_default),
                    ft.Text(ignore_label, size=14, color=on_surface),
                ], spacing=8),
                on_click=self._on_ignore_filter_toggle,
            ),
            ft.PopupMenuItem(),  # Divider
            *density_items,
            ft.PopupMenuItem(),  # Divider
            self._delete_menu_item,
        ]

    # ===== Sorting =====

    def _build_sort_menu_items(self) -> list[ft.PopupMenuItem]:
        """Build the sort popup's items, check-marking the active mode.

        Populated upfront and rebuilt after a selection / on theme change -
        NEVER lazily via on_open, which fires after the menu has rendered
        (see CLAUDE.md's PopupMenuButton note).
        """
        is_dark = self._get_is_dark()
        on_surface = MD3Colors.get_on_surface(is_dark)
        icon_default = MD3Colors.get_themed("icon_default", is_dark)
        accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)

        items: list[ft.PopupMenuItem] = []
        for mode, label, icon in SORT_MODES:
            active = mode == self._sort_preference
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row([
                        ft.Icon(
                            ft.Icons.CHECK if active else icon,
                            size=18,
                            color=accent if active else icon_default,
                        ),
                        ft.Text(
                            label,
                            size=14,
                            color=accent if active else on_surface,
                            weight=ft.FontWeight.W_600 if active else ft.FontWeight.NORMAL,
                        ),
                    ], spacing=8),
                    data=mode,
                    on_click=self._on_sort_selected,
                )
            )
        return items

    def _entry_sort_fields(self, entry: tuple) -> tuple[str, int, int]:
        """(name, dll_count, outdated) for a raw (MergedGame, dlls, backups) tuple."""
        merged, dlls, _ = entry
        # display_name is a property on Game (override -> folder-derived name),
        # not on MergedGame; the merged entry exposes it via primary_game.
        return merged.primary_game.display_name, len(dlls), count_outdated_dlls(dlls)

    @staticmethod
    def _card_sort_fields(card: 'GameCard') -> tuple[str, int, int]:
        """(name, dll_count, outdated) for a live card (counts are card-cached)."""
        return card.game.display_name, len(card.dlls), card._get_update_counts()[0]

    def _sort_entries(self, entries: list, extract) -> list:
        """Order ``entries`` by the active sort preference.

        ``extract`` maps an entry to (display_name, dll_count, outdated_count),
        so the same ordering serves both raw merged-game tuples at build time
        (before any card exists) and live GameCards when the user changes the
        preference. Decorate-sort-undecorate: ``extract`` runs once per entry,
        not once per comparison, since the outdated count parses versions.
        """
        mode = self._sort_preference
        decorated = [(extract(entry), entry) for entry in entries]

        if mode == "name_desc":
            decorated.sort(key=lambda d: d[0][0].lower(), reverse=True)
        elif mode == "dll_count":
            decorated.sort(key=lambda d: (-d[0][1], d[0][0].lower()))
        elif mode == "outdated_first":
            # Most outdated DLLs first - the mode users actually reach for.
            decorated.sort(key=lambda d: (-d[0][2], d[0][0].lower()))
        else:  # name_asc (default)
            decorated.sort(key=lambda d: d[0][0].lower())

        return [entry for _, entry in decorated]

    def _apply_sort_to_grids(self) -> bool:
        """Reorder every launcher grid's cards to the active preference.

        Two-phase swap (clear -> flush -> reassign -> flush): re-ordering
        same-class children in place is exactly the positional merge diff the
        client can silently drop (CLAUDE.md rendering pitfall #3). The cards
        keep their Python identity across the swap, so artwork, badges,
        ignore state and visibility all survive.

        Returns False when there is nothing to reorder (caller then owns the
        single update() needed to flush the menu's new check-mark).
        """
        if not self._grids_by_launcher:
            return False

        ordered: dict[str, list] = {}
        for launcher, grid in self._grids_by_launcher.items():
            ordered[launcher] = self._sort_entries(list(grid.controls), self._card_sort_fields)
            grid.controls = []

        # Detached view (progressive-loading tail after a nav-away): the
        # reordered controls are still applied below and render on re-attach.
        try:
            self.update()
        except Exception:
            pass

        for launcher, grid in self._grids_by_launcher.items():
            grid.controls = ordered[launcher]

        try:
            self.update()
        except Exception:
            pass

        self._sort_applied_at_build = self._sort_preference
        return True

    async def _on_sort_selected(self, e):
        """Persist the chosen sort mode and reorder the live grids."""
        mode = getattr(e.control, "data", None)
        if not mode or mode == self._sort_preference:
            return

        self._sort_preference = mode
        if self.sort_menu:
            self.sort_menu.items = self._build_sort_menu_items()

        if not self._apply_sort_to_grids():
            # No grids yet (empty library) - still flush the new check-mark.
            try:
                self.update()
            except Exception:
                pass

        # Persist off the event loop: set_sort_preference takes _config_lock
        # and rewrites config.toml (blocking I/O).
        try:
            await anyio.to_thread.run_sync(
                config_manager.set_sort_preference, mode, limiter=thread_io
            )
        except Exception as ex:
            self.logger.debug(f"Failed to persist sort preference: {ex}")

        self.logger.debug(f"Games sort preference set to '{mode}'")

    # ===== Grid density =====

    async def _on_density_selected(self, e):
        """Persist the chosen card size and reflow the live grids."""
        mode = getattr(e.control, "data", None)
        if not mode or mode == self._density or mode not in GRID_DENSITIES:
            return

        self._density = mode
        if self.options_menu:
            self.options_menu.items = self._build_options_menu_items()

        max_extent, aspect_ratio, banner = resolve_density(mode)
        for grid in self._grids_by_launcher.values():
            grid.max_extent = max_extent
            grid.child_aspect_ratio = aspect_ratio

        # Cards not yet showing artwork size their shimmer placeholder from
        # this; already-loaded artwork is BoxFit.COVER and just re-crops.
        for card in self.game_cards.values():
            card.set_banner_height(banner)

        try:
            self.update()
        except Exception:
            pass

        # Persist off the event loop: set_grid_density takes _config_lock and
        # rewrites config.toml (blocking I/O).
        try:
            await anyio.to_thread.run_sync(
                config_manager.set_grid_density, mode, limiter=thread_io
            )
        except Exception as ex:
            self.logger.debug(f"Failed to persist grid density: {ex}")

        self.logger.debug(f"Games grid density set to '{mode}'")

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

        # Game-centric subtitle (populated after loading): "N games · M need updates
        # · scanned Xd ago". Derived entirely from already-loaded card/game state —
        # see _update_games_subtitle().
        self.games_subtitle_text = ft.Text(
            "",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            italic=True,
            # Truncate rather than wrap to a second line: this sits in the
            # expanding slot of the header row (see header_foreground), so at
            # the 820px minimum width it must give way to the action cluster.
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
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
            popup_animation_style=ft.AnimationStyle(
                duration=ft.Duration(milliseconds=180),
                curve=ft.AnimationCurve.EASE_OUT,
            ),
        )

        # Sort popup - same idiom as the options menu above (custom content +
        # items built upfront). Reflects/persists [ui_preferences].sort_preference.
        self._sort_icon = ft.Icon(
            ft.Icons.SORT,
            size=20,
        )
        self.sort_menu = ft.PopupMenuButton(
            content=ft.Container(
                content=self._sort_icon,
                width=40,
                height=40,
                border_radius=8,
                alignment=ft.Alignment.CENTER,
                tooltip="Sort games",
            ),
            items=self._build_sort_menu_items(),
            popup_animation_style=ft.AnimationStyle(
                duration=ft.Duration(milliseconds=180),
                curve=ft.AnimationCurve.EASE_OUT,
            ),
        )

        # Primary bulk-update CTA. Sits with the "N need updates" count in the
        # header and delegates to MainView's existing update pipeline
        # (cancellable overlay, "Scan and Update" fallback, summary dialog).
        # Hidden until _update_games_subtitle() sees something outdated.
        self.update_all_button = self._build_update_all_button(is_dark)

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
        self.filter_chips_row = self._build_filter_chips_row(is_dark)
        self._apply_filter_chip_theme(is_dark)

        # Selection bar — occupies the chips row's slot while a selection
        # exists (the two are mutually exclusive, so the header gains no
        # height). The two are mounted as alternating content of an
        # AnimatedSwitcher (see self._chips_switcher below), so
        # entering/leaving selection mode is a control-tree edit, not a
        # visibility flip.
        self.selection_bar = self._build_selection_bar(is_dark)

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
        # Mutually exclusive occupants of the same header slot (chips vs.
        # selection bar) mounted as alternating content of one
        # AnimatedSwitcher, so entering/leaving selection mode cross-fades as
        # one surface changing rather than blinking two controls via
        # `visible`. See `_sync_selection_ui`.
        self._chips_switcher = ft.AnimatedSwitcher(
            content=self.filter_chips_row,
            duration=ft.Duration(milliseconds=180),
            reverse_duration=ft.Duration(milliseconds=140),
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=ft.AnimationCurve.EASE_OUT,
            switch_out_curve=ft.AnimationCurve.EASE_IN,
        )

        header_foreground = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self.header_title,
                            # The subtitle is the row's ONLY flexible child, so
                            # it doubles as the spacer: at comfortable widths it
                            # expands and pushes the action cluster right (what
                            # a rigid Container(expand=True) spacer used to do),
                            # and as the window narrows it shrinks and ellipsizes
                            # instead of shoving the trailing controls off-screen.
                            # A rigid spacer can only absorb SURPLUS width - once
                            # the fixed children outgrew the row, the refresh
                            # button was clipped at the 820px minimum width.
                            # The subtitle is the safe thing to sacrifice: the
                            # filter chips directly below restate its counts.
                            ft.Container(
                                content=self.games_subtitle_text,
                                expand=True,
                                padding=ft.Padding.only(right=8),
                            ),
                            self.update_all_button,
                            self.search_bar,
                            self.steam_api_pill,
                            self.sort_menu,
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
                    self._chips_switcher,
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

    # ===== Bulk update CTA (header) =====

    def _build_update_all_button(self, is_dark: bool) -> ft.Container:
        """Build the header's "Update all (N)" action.

        A slightly taller build_pill() rather than a Material button so it
        sits in the same badge language as the Steam API pill beside it -
        filled with the WARNING accent (matching the "Needs update" filter
        chip) so the count and the action read as the same idea.
        """
        accent = MD3Colors.get_warning(is_dark)
        fg = _on_accent(is_dark)
        button = build_pill(
            "Update all",
            icon=ft.Icons.DOWNLOAD,
            bgcolor=accent,
            text_color=fg,
            icon_color=fg,
            text_size=12,
            icon_size=16,
        )
        # build_pill's content is a tight Row([Icon, Text]) - keep refs so the
        # count and themed colors can be patched in place.
        row = button.content
        self._update_all_icon: ft.Icon = row.controls[0]
        self._update_all_text: ft.Text = row.controls[1]
        button.height = 32
        button.padding = ft.Padding.symmetric(horizontal=12, vertical=6)
        button.ink = True
        button.visible = False
        self._update_all_pill = button

        get_scope, on_scope_changed = self._scope_deps
        if get_scope is None or on_scope_changed is None:
            # No scope wiring (standalone/test construction): keep the plain
            # button so the header still works.
            button.on_click = self._on_update_all_clicked
            button.tooltip = "Update every outdated DLL in your library"
            return button

        # The pill becomes the menu's trigger, so this header gains the same
        # scope control the Launchers bar and Hub CTA already had - it was the
        # one bulk-update entry point with none. No on_click on the pill: a
        # Container on_click would swallow the tap before the menu opened.
        from dlss_updater.ui_flet.components.update_scope_menu import UpdateScopeMenu

        self._scope_menu = UpdateScopeMenu(
            page=self._page_ref,
            get_scope=get_scope,
            on_scope_changed=on_scope_changed,
            accent=accent,
            trigger_content=button,
            on_run=lambda e: self._on_update_all_clicked(e),
            run_label=self._run_menu_label,
            radius=16,
        )
        # Hidden until a scan finds something outdated, exactly as the bare
        # pill was. Set after construction: `visible` is a Control field, not
        # an UpdateScopeMenu constructor argument.
        self._scope_menu.visible = False
        return self._scope_menu

    def _run_menu_label(self) -> str:
        n = getattr(self, "_needs_update_count", 0)
        return f"Update {n} game{'' if n == 1 else 's'}"

    # ===== Bulk selection =====

    def _build_selection_bar(self, is_dark: bool) -> ft.Container:
        """Build the selection action bar shown in place of the filter chips."""
        accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)

        self._selection_count_text = ft.Text(
            "0 selected",
            size=12,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_on_surface(is_dark),
        )

        # Same pill language as "Update all (N)" beside it — this is the same
        # action, narrowed to a hand-picked set.
        fg = _on_accent(is_dark)
        update_button = build_pill(
            "Update selected",
            icon=ft.Icons.DOWNLOAD,
            bgcolor=MD3Colors.get_warning(is_dark),
            text_color=fg,
            icon_color=fg,
            text_size=12,
            icon_size=16,
        )
        row = update_button.content
        self._selection_update_icon: ft.Icon = row.controls[0]
        self._selection_update_text: ft.Text = row.controls[1]
        update_button.height = 32
        update_button.padding = ft.Padding.symmetric(horizontal=12, vertical=6)
        update_button.on_click = self._on_update_selected_clicked
        update_button.ink = True
        update_button.tooltip = "Update the DLLs of every selected game"
        self._selection_update_button = update_button

        self._select_all_button = ft.TextButton(
            "Select all",
            icon=ft.Icons.SELECT_ALL,
            on_click=self._on_select_all_visible,
            tooltip="Select every game visible in this launcher tab",
        )
        self._clear_selection_button = ft.TextButton(
            "Clear",
            icon=ft.Icons.CLOSE,
            on_click=self._on_clear_selection,
            tooltip="Clear the selection",
        )

        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CHECK_BOX, size=16, color=accent),
                    self._selection_count_text,
                    update_button,
                    self._select_all_button,
                    self._clear_selection_button,
                ],
                spacing=8,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            # The AnimatedSwitcher (self._chips_switcher) owns which of the
            # chips row / selection bar is shown; both mounted controls stay
            # visible=True, since it is the switcher's `content` that decides
            # what is actually attached and rendered.
            visible=True,
        )

    def _refresh_selection_bar(self, is_dark: bool) -> None:
        """Repaint the selection bar for the active theme.

        Mirrors the guard in `_apply_filter_chip_theme`: the selection bar is
        the other of the two mutually exclusive occupants of
        `self._chips_switcher` (see `_sync_selection_ui`). When the chips row
        is showing instead, the selection bar is DETACHED from the control
        tree, and the Flet desktop client silently drops property patches
        targeting detached subtrees (CLAUDE.md "Flet desktop client
        rendering pitfalls" #2) — painting now would be dropped and never
        recovered by a later identical-value repaint. Defer instead: mark
        stale and let `_sync_selection_ui` rebuild a fresh, correctly-themed
        bar the next time it is swapped back in.
        """
        if self.selection_bar is None:
            return
        if getattr(self, "_chips_switcher", None) is not None and self._chips_switcher.content is not self.selection_bar:
            self._selection_theme_stale = True
            return

        accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)
        fg = _on_accent(is_dark)

        leading_icon = self.selection_bar.content.controls[0]
        leading_icon.color = accent
        if self._selection_count_text is not None:
            self._selection_count_text.color = MD3Colors.get_on_surface(is_dark)
        if self._selection_update_button is not None:
            self._selection_update_button.bgcolor = MD3Colors.get_warning(is_dark)
            self._selection_update_icon.color = fg
            self._selection_update_text.color = fg

    def _sync_selection_ui(self) -> None:
        """Reconcile every selection-dependent surface with the selected set.

        One place so the bar, the chips row and each card's checkbox can never
        disagree. Does NOT call update() — callers own the flush, since they
        usually have other changes to batch with it.
        """
        count = len(self._selected_game_ids)
        active = count > 0

        # Mutually exclusive occupants of the same slot — swap the switcher's
        # content rather than toggling visibility, so entering and leaving
        # selection mode reads as one surface changing rather than two
        # blinking. The chips restate counts that the selection bar's own
        # count supersedes while picking games.
        if getattr(self, "_chips_switcher", None) is not None:
            if active:
                if self._selection_theme_stale:
                    # The selection bar was detached (chips row showing) the
                    # last time the theme changed, so its repaint was dropped
                    # (see CLAUDE.md "Flet desktop client rendering
                    # pitfalls" #2, and `_refresh_selection_bar`). Heal by
                    # rebuilding a fresh, correctly-themed bar instead of
                    # reattaching the stale one. `_build_selection_bar`
                    # re-points self._selection_count_text /
                    # self._selection_update_text (and the other selection
                    # widgets) at the fresh instance's controls, so the
                    # count-writing lines below always agree with what's live.
                    is_dark = self._get_is_dark()
                    self.selection_bar = self._build_selection_bar(is_dark)
                    self._selection_theme_stale = False
                self._chips_switcher.content = self.selection_bar
            elif self._chips_theme_stale:
                # The chips row was detached (selection bar showing) the last
                # time the theme changed, so its repaint was dropped (see
                # CLAUDE.md "Flet desktop client rendering pitfalls" #2, and
                # `_apply_filter_chip_theme`). Heal by rebuilding a fresh,
                # correctly-themed row instead of reattaching the stale one.
                # `_build_filter_chips_row` rebuilds fresh Chip instances that
                # default to `selected=False` and re-points
                # self._needs_update_chip / _up_to_date_chip / _has_backups_chip
                # at them, so the rebuild alone would silently drop the active
                # filter and its counts (mirrors the count-writing lines that
                # follow the `active` branch above). Restore both axes: the
                # `selected` state from the authoritative
                # _filter_needs_update / _filter_up_to_date / _filter_has_backups
                # flags, and the "(N)" counts via _update_filter_chip_counts().
                is_dark = self._get_is_dark()
                self.filter_chips_row = self._build_filter_chips_row(is_dark)
                self._chips_theme_stale = False
                self._chips_switcher.content = self.filter_chips_row
                self._apply_filter_chip_theme(is_dark)
                self._needs_update_chip.selected = self._filter_needs_update
                self._up_to_date_chip.selected = self._filter_up_to_date
                self._has_backups_chip.selected = self._filter_has_backups
                self._update_filter_chip_counts()
            else:
                self._chips_switcher.content = self.filter_chips_row
        if self._selection_count_text is not None:
            self._selection_count_text.value = f"{count} selected"
        if getattr(self, "_selection_update_text", None) is not None:
            self._selection_update_text.value = f"Update selected ({count})"

        # "Update all" yields to the selection and returns when it is cleared.
        self._set_update_all_state(self._needs_update_count)

        for game_id, card in self.game_cards.items():
            card.set_selection_active(active)
            card.set_selected(game_id in self._selected_game_ids)

    def _on_card_select_toggle(self, game_id: int, selected: bool) -> None:
        """A card's checkbox was clicked."""
        if selected:
            self._selected_game_ids.add(game_id)
        else:
            self._selected_game_ids.discard(game_id)

        self._sync_selection_ui()
        try:
            self.update()
        except Exception:
            pass

    def _on_select_all_visible(self, e) -> None:
        """Select every card currently visible in the active launcher tab.

        Visible, not merely present: a card filtered out by the search box or a
        status chip is not something the user can see, so including it would
        make the count lie. This is also the "update all in this launcher"
        affordance — Select all followed by Update selected — rather than a
        second bulk code path that could drift from this one.
        """
        # Mirrors _apply_visibility()'s own launcher + filter logic, and reads
        # the authoritative card registry rather than grid.controls (background
        # batches are still being appended to the grids during progressive
        # loading, but every card is in game_cards the moment it is created).
        launcher = self._get_current_launcher()
        for card in self.game_cards.values():
            if launcher and card.game.launcher != launcher:
                continue
            # Ignored games are dropped by the coordinator anyway; selecting
            # them would only inflate the count against what actually runs.
            if card.visible and not card.is_ignored:
                self._selected_game_ids.add(card.game.id)

        self._sync_selection_ui()
        try:
            self.update()
        except Exception:
            pass

    def _on_clear_selection(self, e=None) -> None:
        """Empty the selection, which also leaves selection mode."""
        self._selected_game_ids.clear()
        self._sync_selection_ui()
        try:
            self.update()
        except Exception:
            pass

    def _selected_cards(self) -> list[GameCard]:
        """The selected cards, in grid order, skipping ids with no live card."""
        return [
            card
            for game_id, card in self.game_cards.items()
            if game_id in self._selected_game_ids
        ]

    async def _on_update_selected_clicked(self, e) -> None:
        """Run a bulk update over just the selected games."""
        cards = self._selected_cards()
        if not cards:
            return
        if self._on_update_selected is None:
            self.logger.warning("Update selected clicked but no handler is wired")
            return

        dll_dict = build_dll_dict_for_selection(cards)
        if not dll_dict:
            await self._show_error_dialog(
                "Nothing to update",
                "The selected games have no DLLs on record. Try a rescan first.",
                ft.Colors.ORANGE,
            )
            return

        # Union of the selection's DLL filenames — the rollback check is per
        # DLL version, not per game (see collect_flagged_dlls).
        target_filenames = {
            dll.dll_filename.lower()
            for card in cards
            for dll in card.dlls
            if dll.dll_filename
        }

        await self._on_update_selected(dll_dict, target_filenames, len(cards))

        # The selection has been acted on; leaving it set would invite a
        # double-run on a second click of the same button.
        self._on_clear_selection()

    def _set_update_all_state(self, needs_update: int) -> None:
        """Show/label the bulk-update CTA from the live outdated count.

        Hidden entirely when nothing is outdated (or no callback was wired) -
        a greyed-out button next to "all up to date" would just be noise.

        Also hidden while a selection exists: "Update all (11)" sitting beside
        "Update selected (2)" offers to update the whole library instead, one
        misclick from doing something far larger than the user asked for. The
        count is remembered so _sync_selection_ui() can bring the button back
        unchanged when the selection is cleared.
        """
        button = getattr(self, "update_all_button", None)
        if button is None:
            return

        self._needs_update_count = needs_update

        if needs_update > 0 and self._on_update_all is not None and not self._selected_game_ids:
            self._update_all_text.value = f"Update all ({needs_update})"
            button.visible = True
            menu = getattr(self, "_scope_menu", None)
            if menu is not None:
                menu.refresh_ticks()  # re-label the run row for the new count
        else:
            button.visible = False

    def _refresh_update_all_button(self, is_dark: bool) -> None:
        """Repaint the CTA for the active theme (fill AND foreground invert)."""
        button = getattr(self, "update_all_button", None)
        if button is None:
            return
        accent = MD3Colors.get_warning(is_dark)
        fg = _on_accent(is_dark)
        # `button` may be the scope menu (a transparent shell) rather than the
        # pill, so paint the pill itself - filling the shell would draw a
        # second box behind it.
        pill = getattr(self, "_update_all_pill", None) or button
        pill.bgcolor = accent
        self._update_all_icon.color = fg
        self._update_all_text.color = fg
        menu = getattr(self, "_scope_menu", None)
        if menu is not None:
            menu.set_accent(accent)

    async def _on_update_all_clicked(self, e) -> None:
        """Run the library-wide update through MainView's existing pipeline."""
        if not self._on_update_all:
            return
        self.logger.info("Bulk update requested from the Games header")
        await self._on_update_all()

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

    def _build_filter_chips_row(self, is_dark: bool) -> ft.Row:
        """Construct the three status filter chips and their row.

        Extracted so `_sync_selection_ui` can rebuild a fresh instance when
        the live row went theme-stale while detached inside the
        AnimatedSwitcher (see `_apply_filter_chip_theme` and
        `_chips_theme_stale`) — the client silently drops property patches
        targeting detached subtrees, so re-theming the same instance in place
        would be a no-op. Callers are responsible for calling
        `_apply_filter_chip_theme(is_dark)` afterwards to paint it.
        """
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
        return ft.Row(
            controls=[
                self._needs_update_chip,
                self._up_to_date_chip,
                self._has_backups_chip,
            ],
            spacing=8,
            wrap=True,
        )

    def _apply_filter_chip_theme(self, is_dark: bool) -> None:
        """Tint the three status filter chips with semantic accents.

        Needs update -> WARNING amber, Up to date -> SUCCESS green,
        Has backups -> BACKUPS orange (themed_accent picks the _LIGHT variant
        in light mode). Unselected chips stay on the neutral surface with a
        subtle colored outline; selecting fills with a translucent accent tint.

        The chips row is one of two mutually exclusive occupants of
        `self._chips_switcher` (see `_sync_selection_ui`); when the selection
        bar is showing instead, the chips row is DETACHED from the control
        tree. The Flet desktop client silently drops property patches
        targeting detached subtrees while the server marks them delivered, so
        painting now would be dropped and never recovered by a later
        identical-value repaint. Defer instead: mark stale and let
        `_sync_selection_ui` rebuild a fresh, correctly-themed row the next
        time the chips are swapped back in.
        """
        if getattr(self, "_chips_switcher", None) is not None and self._chips_switcher.content is not self.filter_chips_row:
            self._chips_theme_stale = True
            return

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

        # Header subtitle shares this recount (avoids a second pass over the cards).
        self._update_games_subtitle(needs_update)

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware system"""
        return {
            "header.bgcolor": (MD3Colors.SURFACE_VARIANT, MD3Colors.SURFACE_VARIANT_LIGHT),
            "header_title.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "loading_text.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "divider.color": (MD3Colors.get_outline(True), MD3Colors.get_outline(False)),
            "games_subtitle_text.color": (MD3Colors.get_on_surface_variant(True), MD3Colors.get_on_surface_variant(False)),
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

        # Same for the sort menu (its check-mark uses the GAMES accent).
        if self.sort_menu:
            self.sort_menu.items = self._build_sort_menu_items()
            try:
                self.sort_menu.update()
            except Exception:
                pass

        # Bulk-update CTA: both the WARNING fill and its foreground invert.
        self._refresh_update_all_button(is_dark)

        # Header brand wash — rebuild the diagonal GAMES-blue gradient at the
        # new theme's opacity/accent.
        if getattr(self, "_header_wash", None):
            header_accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)
            self._header_wash.gradient = build_brand_wash(header_accent, is_dark)

        # Status filter chip semantic tints (WARNING/SUCCESS/BACKUPS accents
        # differ between light and dark mode).
        self._apply_filter_chip_theme(is_dark)

        # Selection bar: same WARNING fill + inverted foreground as the CTA it
        # mirrors, plus the themed count label and leading accent glyph.
        self._refresh_selection_bar(is_dark)

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
                # No self.update() here: the finally block issues the single
                # terminal update for this early-return path (was double-updating).
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
        self._grids_by_launcher = {}

        # Drop the bulk selection with the cards it referred to: a rebuild
        # follows a rescan or a database delete, after which the retained ids
        # may name games that no longer exist.
        self._selected_game_ids.clear()
        self._sync_selection_ui()

        # Launcher icons mapping
        launcher_icons = {
            "Steam": ft.Icons.VIDEOGAME_ASSET,
            "EA Launcher": ft.Icons.SPORTS_ESPORTS,
            "Epic Games Launcher": ft.Icons.GAMES,
            "Ubisoft Launcher": ft.Icons.GAMEPAD,
            "GOG Launcher": ft.Icons.VIDEOGAME_ASSET_OUTLINED,
            "Battle.net Launcher": ft.Icons.MILITARY_TECH,
            "Xbox Launcher": ft.Icons.SPORTS_ESPORTS_OUTLINED,
            "Playnite": ft.Icons.LIBRARY_BOOKS,
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
            LoadTask("dlss_presets", lambda: db_manager.batch_get_game_dlss_presets_sync(all_game_ids)),
            LoadTask("dll_manifest", lambda: dll_repository.get_cached_manifest()),
        ])

        dlls_by_game: dict[int, list[GameDLL]] = results.get("dlls", {})
        backups_by_game: dict[int, dict[str, list[DLLBackup]]] = results.get("backups", {})
        cached_image_paths: dict[int, str] = results.get("images", {})
        self._ignored_game_ids = results.get("ignored", set())
        # Saved per-game SR/RR preset overrides (Windows-only feature — empty
        # dict everywhere else). Looked up by id in create_card() below rather
        # than threaded through the (mg, dlls, backup_groups) tuples used by
        # progressive/background card loading.
        presets_by_game: dict[int, GameDLSSPresets] = results.get("dlss_presets", {})
        # Cached DLL manifest — source of tech-version chip labels/bucketing
        # (see version_labels.py). Same dict handed to every card; None
        # before the DLL cache has ever been initialized.
        dll_manifest: dict | None = results.get("dll_manifest")

        # Headline game count for the subtitle — the true merged total, shown
        # immediately even while later cards are still loading progressively.
        self._total_games = len(all_merged_games)

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
            dlss_presets = next(
                (presets_by_game[gid] for gid in merged.all_game_ids if gid in presets_by_game),
                None,
            )
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
                dlss_presets=dlss_presets,
                dll_manifest=dll_manifest,
                banner_height=resolve_density(self._density)[2],
                on_select_toggle=self._on_card_select_toggle,
            )
            # A card created by progressive loading (or after a refresh) while a
            # selection is live must join it already showing its checkbox.
            if self._selected_game_ids:
                card.set_selection_active(True)
                card.set_selected(card.game.id in self._selected_game_ids)
            card.opacity = 0 if not is_ignored else 0.5
            card.animate_opacity = ft.Animation(400, ft.AnimationCurve.EASE_OUT)
            # Stable key so Flet's list differ reconciles a re-ordered grid by
            # identity (a keyed move) rather than positionally merging one
            # card's subtree onto another's - see _apply_sort_to_grids().
            card.key = f"gc-{merged.primary_game.id}"
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

            # Order BEFORE the progressive split so the first (immediately
            # visible) batch really is the top of the sort and the background
            # batches append in order - sorting after the fact would make the
            # initial paint show an arbitrary 16 games.
            merged_data = self._sort_entries(
                games_by_launcher_merged[launcher], self._entry_sort_fields
            )
            game_count = len(self.games_by_launcher[launcher])

            # Create GridView first (will be populated progressively)
            max_extent, aspect_ratio, _ = resolve_density(self._density)

            game_grid = ft.GridView(
                controls=[],
                max_extent=max_extent,
                child_aspect_ratio=aspect_ratio,
                # Bottom padding lets the last row scroll clear of the floating pill
                padding=ft.Padding.only(left=16, right=16, top=16, bottom=PILL_CLEARANCE),
                spacing=12,
                run_spacing=12,
                expand=True,
                scroll=ft.Scrollbar(
                    thumb_visibility=False,   # appears on scroll, not at rest
                    thickness=8,
                    radius=4,
                    interactive=True,
                ),
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
                    card.set_image(cached_image_paths[eff_id])
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

        # Keep the grids reachable so a later sort change can reorder them
        # in place instead of forcing a full reload.
        self._grids_by_launcher = grids_by_launcher
        self._sort_applied_at_build = self._sort_preference

        cards_ms = (time.perf_counter() - start_cards) * 1000
        self.logger.debug(f"[PERF] Initial card creation ({initial_card_count} cards): {cards_ms:.1f}ms")

        # ========== PHASE 5: Create tabs control and show UI ==========
        # Indicator/label accent = GAMES blue, matching the header wash.
        is_dark = self._get_is_dark()
        tab_accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)
        self._tab_bar_ref = ft.TabBar(
            tabs=tabs,
            indicator_color=tab_accent,
            label_color=tab_accent,
            indicator_animation=ft.TabIndicatorAnimation.ELASTIC,
        )
        # A lone launcher tab (e.g. "Steam (14)") just wastes a vertical band — the
        # grid alone is unambiguous. Hide the bar row entirely when there's only one
        # launcher; the TabBarView still renders index 0. Detachment (visible=False)
        # keeps the Tabs(length=N)/Column([TabBar, TabBarView]) shape intact.
        self._tab_bar_ref.visible = len(tabs) > 1
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
                        card.set_image(str(path))
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
                        card.set_image(cached_image_paths[eff_id])
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

        # A sort chosen WHILE these batches were landing reordered only the
        # cards that existed at that moment; the rest appended in the old
        # order. Re-apply once, now that the dataset is complete.
        if self._sort_preference != self._sort_applied_at_build:
            self._apply_sort_to_grids()

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

    def _scan_age_str(self) -> str | None:
        """Compact "scanned Xd ago" derived from the most recent Game.last_scanned.

        Uses only already-loaded game data (no new query). Mirrors the hub's format
        (m/h/d). Returns None if no games are loaded or the timestamps are unusable.
        """
        try:
            from datetime import datetime

            latest = None
            for games in self.games_by_launcher.values():
                for g in games:
                    ts = getattr(g, "last_scanned", None)
                    if ts is not None and (latest is None or ts > latest):
                        latest = ts
            if latest is None:
                return None
            age = datetime.now() - latest
            hours = age.total_seconds() / 3600
            if hours < 1:
                return f"scanned {int(age.total_seconds() / 60)}m ago"
            if hours < 24:
                return f"scanned {int(hours)}h ago"
            return f"scanned {int(hours / 24)}d ago"
        except Exception:
            return None

    def _update_games_subtitle(self, needs_update: int) -> None:
        """Set the header subtitle to game-centric stats (no new queries).

        e.g. "14 games · 2 need updates · scanned 3d ago". The total is the true
        merged count; needs_update is passed in from the shared card recount so we
        don't iterate the cards twice.
        """
        total = self._total_games or len(self.game_cards)
        if total == 0:
            self.games_subtitle_text.value = ""
            self._set_update_all_state(0)
            return

        parts = [f"{total} game{'s' if total != 1 else ''}"]
        if needs_update > 0:
            verb = "needs" if needs_update == 1 else "need"
            noun = "update" if needs_update == 1 else "updates"
            parts.append(f"{needs_update} {verb} {noun}")
        else:
            parts.append("all up to date")

        age = self._scan_age_str()
        if age:
            parts.append(age)

        self.games_subtitle_text.value = " · ".join(parts)

        # The bulk-update CTA shows the same count this line just reported.
        self._set_update_all_state(needs_update)

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
                scroll=ft.Scrollbar(
                    thumb_visibility=False,   # appears on scroll, not at rest
                    thickness=8,
                    radius=4,
                    interactive=True,
                ),
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
                scroll=ft.Scrollbar(
                    thumb_visibility=False,   # appears on scroll, not at rest
                    thickness=8,
                    radius=4,
                    interactive=True,
                ),
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
        self._grids_by_launcher = {}
        self._selected_game_ids.clear()

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
