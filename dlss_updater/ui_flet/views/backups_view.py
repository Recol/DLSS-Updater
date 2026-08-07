"""
Backups View - Browse and restore DLL backups grouped by game

PERFORMANCE NOTES:
- Uses ListView with BackupGroup components (ExpansionTile per game)
- Progressive loading: first batch shown immediately, rest created in background
- Data preparation runs in thread pool for parallel processing (HyperParallelLoader)
- Batch UI updates minimize page.update() calls
- BackupGroup uses native ExpansionTile for GPU-accelerated expand/collapse
- Collapsed groups show ~6 controls, expanded shows ~8 per backup row
- Header summary tiles are aggregated from the already-loaded backup rows,
  so they add zero queries (the old summary-stats LoadTask was dropped)
"""

import asyncio
import itertools
import math
import time
import anyio
import flet as ft

from dlss_updater.concurrency_limiters import thread_io
from dlss_updater.database import db_manager, DLLBackup
from dlss_updater.models import GameWithBackupCount, GameDLLBackup
from dlss_updater.name_normalize import normalize_search_name_spaceless
from dlss_updater.backup_manager import restore_dll_from_backup, delete_backup
from dlss_updater.services.backup_service import restore_orphaned_dll_from_backup
from dlss_updater.ui_flet.components.backup_group import BackupGroup
from dlss_updater.ui_flet.components.floating_pill import PILL_CLEARANCE
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.hyper_parallel_loader import HyperParallelLoader, LoadTask
from dlss_updater.task_registry import register_task

# Number of groups to create in first batch (shown immediately)
INITIAL_BATCH_SIZE = 8
# Number of groups per background batch
BACKGROUND_BATCH_SIZE = 12


def _resolve_orphan_app_ids_sync(labels_by_group_id: dict[int, str]) -> dict[int, int]:
    """Map orphan group id -> Steam app id by NAME, in a single thread hop.

    Unlinked ("orphaned") groups have no ``games`` row, so the linked path's
    ``game_id -> steam_app_id`` query cannot reach them — their only surviving
    identity is the display label recovered from the backup path. That label is
    normalized the same way the Steam app index is built (single source of
    truth: ``name_normalize``) and looked up against the indexed
    ``steam_apps.name_normalized`` column, which is an O(log n) point lookup.

    Called once per load with a handful of groups, so the loop runs inside ONE
    ``anyio.to_thread.run_sync`` hop reusing the thread-local connection rather
    than paying a hop per group. Misses simply produce no entry (the caller then
    falls back to the folder icon); this NEVER hits the network.

    Args:
        labels_by_group_id: Synthetic (negative) orphan group id -> game label.

    Returns:
        Dict of orphan group id -> steam app id for the labels that matched.
    """
    resolved: dict[int, int] = {}
    for group_id, label in labels_by_group_id.items():
        normalized = normalize_search_name_spaceless(label)
        if not normalized:
            continue
        app_id = db_manager._get_steam_app_by_name(normalized)
        if app_id:
            resolved[group_id] = app_id
    return resolved


class _StatTile(ft.Container):
    """Compact summary stat tile for the backups header band.

    Container-based rather than a Card (see CLAUDE.md "Container-based Badge
    Pattern"): ~7 controls each and no elevation, so the three tiles add ~21
    controls to the header. Colors are re-applied IN PLACE by
    ``apply_colors()`` during a theme cascade — tiles are never rebuilt, so no
    same-class child swap is involved.
    """

    def __init__(self, icon: ft.IconData, label: str, accent_key: str, is_dark: bool):
        # Accent key indexes MD3Colors.THEMED so the tile re-resolves its own
        # tint on a theme change without the view knowing the palette.
        self._accent_key = accent_key
        accent = MD3Colors.get_themed(accent_key, is_dark)

        self._icon = ft.Icon(icon, size=16, color=accent)
        # Tinted icon well. Alpha on a plain Container bgcolor is safe — the
        # black-through-gradient pitfall only applies to gradients on shadowed
        # containers, and this tile has neither.
        self._icon_well = ft.Container(
            content=self._icon,
            width=30,
            height=30,
            border_radius=15,
            bgcolor=ft.Colors.with_opacity(0.14, accent),
            alignment=ft.Alignment.CENTER,
        )
        self._value_text = ft.Text(
            "—",
            size=16,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_text_primary(is_dark),
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._label_text = ft.Text(
            label,
            size=11,
            color=MD3Colors.get_on_surface_variant(is_dark),
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        super().__init__(
            content=ft.Row(
                controls=[
                    self._icon_well,
                    # The text column is the flexible element so long values
                    # ellipsize instead of clipping the tile.
                    ft.Column(
                        controls=[self._value_text, self._label_text],
                        spacing=0,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=10,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=MD3Colors.get_surface(is_dark),
            border=ft.Border.all(1, MD3Colors.get_themed("outline_variant", is_dark)),
            border_radius=12,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            expand=True,
        )

    def set_value(self, value: str) -> None:
        """Set the tile's headline value (no update() — the view batches)."""
        self._value_text.value = value

    def apply_colors(self, is_dark: bool) -> None:
        """Re-resolve every themed color in place (no rebuild, no update())."""
        accent = MD3Colors.get_themed(self._accent_key, is_dark)
        self._icon.color = accent
        self._icon_well.bgcolor = ft.Colors.with_opacity(0.14, accent)
        self._value_text.color = MD3Colors.get_text_primary(is_dark)
        self._label_text.color = MD3Colors.get_on_surface_variant(is_dark)
        self.bgcolor = MD3Colors.get_surface(is_dark)
        self.border = ft.Border.all(1, MD3Colors.get_themed("outline_variant", is_dark))


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
        self.clear_all_button: ft.OutlinedButton | None = None

        # Summary stat tiles (total backups / total size / most recent). Built
        # once in _build_ui and updated in place; the row is only revealed when
        # there is at least one backup so the empty state stays uncluttered.
        self._stat_tiles: list[_StatTile] = []
        self.stats_row: ft.Row | None = None

        # Game filter state
        self.selected_game_id: int | None = None
        self.game_filter_dropdown: ft.Dropdown | None = None
        self.games_with_backups: list[GameWithBackupCount] = []

        # PERFORMANCE: Track if backups are already loaded to prevent redundant rebuilds
        self._backups_loaded = False

        # Batch-resolved game_id -> cached local artwork path (header thumbnails).
        # Populated once per load_backups() call; never queried per-group.
        self._art_paths_by_game_id: dict[int, str] = {}

        # "Unlinked games" (orphaned backups) section header controls. Recreated
        # per orphan render; referenced by get_themed_properties for live
        # re-theming (missing paths are skipped when no orphan section exists).
        self._orphan_divider: ft.Divider | None = None
        self._orphan_icon: ft.Icon | None = None
        self._orphan_label: ft.Text | None = None
        self._orphan_hint: ft.Text | None = None

        # Initialize theme system reference before building UI
        self._registry = get_theme_registry()
        self._theme_priority = 10  # Views are high priority (animate early)

        # Build initial UI
        self._build_ui()

        # Register with theme system after UI is built
        self._register_theme_aware()

    def _clear_all_button_style(self, is_dark: bool) -> ft.ButtonStyle:
        """Outlined/tonal treatment for the destructive Clear All action.

        The page's most destructive control must not be its most dominant one:
        at rest it is red TEXT + red BORDER on a transparent surface, and it
        fills solid red only on hover. Hover / disabled are resolved natively by
        the client from these ControlState maps, so there is no on_hover round
        trip (and no exposure to the ``e.data == "true"`` trap — hover data is a
        boolean in 0.86).

        Hover foreground follows MD3's on-error convention: dark text on the
        light-red dark-theme error color, white on the deep light-theme red —
        both clear AA at the button's 14px label.
        """
        danger = MD3Colors.get_error(is_dark)
        muted = MD3Colors.get_themed("text_disabled", is_dark)
        on_danger = ft.Colors.BLACK if is_dark else ft.Colors.WHITE
        foreground = {
            ft.ControlState.DEFAULT: danger,
            ft.ControlState.HOVERED: on_danger,
            ft.ControlState.PRESSED: on_danger,
            ft.ControlState.DISABLED: muted,
        }
        return ft.ButtonStyle(
            color=foreground,
            icon_color=foreground,
            bgcolor={
                ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
                ft.ControlState.HOVERED: danger,
                ft.ControlState.PRESSED: danger,
                ft.ControlState.DISABLED: ft.Colors.TRANSPARENT,
            },
            # The default outlined-button ink overlay would tint the hover fill;
            # let the bgcolor map own it outright.
            overlay_color={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
            side={
                ft.ControlState.DEFAULT: ft.BorderSide(1, danger),
                ft.ControlState.HOVERED: ft.BorderSide(1, danger),
                ft.ControlState.DISABLED: ft.BorderSide(1, muted),
            },
            shape=ft.RoundedRectangleBorder(radius=20),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
        )

    def _create_clear_all_button(self) -> ft.OutlinedButton:
        """Create and store reference to Clear All Backups button.

        OutlinedButton (not ElevatedButton): no elevation/shadow to compete
        with the primary actions, and its border is driven by the ButtonStyle
        ``side`` map. The confirmation dialog wiring is unchanged.
        """
        self.clear_all_button = ft.OutlinedButton(
            "Clear All Backups",
            icon=ft.Icons.DELETE_SWEEP,
            on_click=self._on_clear_all_clicked,
            disabled=True,  # Initially disabled until backups are loaded
            style=self._clear_all_button_style(self._get_is_dark()),
        )
        return self.clear_all_button

    def _update_clear_button_state(self, has_backups: bool):
        """Update clear button enabled/disabled state"""
        if self.clear_all_button:
            self.clear_all_button.disabled = not has_backups

    def _create_game_filter_dropdown(self) -> ft.Dropdown:
        """Create the game filter as a compact, filled pill.

        Restyled away from the outlined/floating-label form-field look toward
        the app's chip/pill language (see games_view status chips): filled
        surface-container background, borderless, rounded, no floating label
        (the selected value / "All Games" hint carries the meaning). A leading
        filter glyph reinforces the affordance.

        Starts hidden; ``load_backups`` reveals it only when >= 2 backup groups
        exist (linked + orphaned), since a filter is pointless with fewer.
        """
        is_dark = self._get_is_dark()
        surface = MD3Colors.get_surface_container(is_dark)
        on_surface = MD3Colors.get_on_surface(is_dark)
        self.game_filter_dropdown = ft.Dropdown(
            hint_text="All Games",
            options=[ft.dropdown.Option(key="all", text="All Games")],
            value="all",
            on_select=self._on_game_filter_changed,
            leading_icon=ft.Icons.FILTER_LIST,
            width=210,
            dense=True,
            text_size=13,
            filled=True,
            fill_color=surface,
            bgcolor=surface,
            color=on_surface,
            border_width=0,
            border_color=ft.Colors.TRANSPARENT,
            border_radius=20,
            content_padding=ft.Padding.symmetric(horizontal=14, vertical=6),
            text_style=ft.TextStyle(color=on_surface),
            hint_style=ft.TextStyle(color=MD3Colors.get_on_surface_variant(is_dark)),
            visible=False,
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

        # One-line context under the title. The count/size/date it used to
        # carry inline now live in the summary tiles below, so this stays a
        # static description instead of duplicating them.
        self.header_subtitle = ft.Text(
            "Saved copies of every DLL replaced by an update — restore any of them at any time.",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        self.loading_text = ft.Text(
            "Loading backups...",
            color=MD3Colors.get_text_primary(is_dark),
        )

        self.divider = ft.Divider(height=1, color=MD3Colors.get_outline(is_dark))

        # Summary stat tiles — the numbers are aggregated from the SAME backup
        # data the groups are built from (see _update_summary), so they cost no
        # extra query. Hidden until a load reports at least one backup.
        self._stat_tiles = [
            _StatTile(ft.Icons.INVENTORY_2, "Total backups", "primary", is_dark),
            _StatTile(ft.Icons.SD_STORAGE, "Disk space used", "info", is_dark),
            _StatTile(ft.Icons.SCHEDULE, "Most recent", "success", is_dark),
        ]
        self.stats_row = ft.Row(
            controls=list(self._stat_tiles),
            spacing=12,
            visible=False,
        )

        # Header with game filter. The title/subtitle column is the FLEXIBLE
        # element (expand + ellipsis) rather than a rigid Container spacer, so
        # the trailing controls compress instead of being clipped off-screen at
        # narrow widths (see CLAUDE.md "Rigid spacers can't shrink").
        self.header = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[self.header_title, self.header_subtitle],
                                spacing=2,
                                tight=True,
                                expand=True,
                            ),
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
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                    ),
                    self.stats_row,
                ],
                spacing=12,
                tight=True,
            ),
            padding=16,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
        )

        # Empty state — themed (the old flat greys read as disabled chrome in
        # light mode) and centered: tinted icon well, headline, one-line reason.
        self._empty_icon = ft.Icon(
            ft.Icons.RESTORE_FROM_TRASH,
            size=40,
            color=MD3Colors.get_primary(is_dark),
        )
        self._empty_icon_well = ft.Container(
            content=self._empty_icon,
            width=88,
            height=88,
            border_radius=44,
            bgcolor=ft.Colors.with_opacity(0.12, MD3Colors.get_primary(is_dark)),
            alignment=ft.Alignment.CENTER,
        )
        self._empty_title = ft.Text(
            "No backups yet",
            size=18,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_text_primary(is_dark),
            text_align=ft.TextAlign.CENTER,
        )
        self._empty_hint = ft.Text(
            "A copy of each DLL is saved here automatically the first time an update replaces it.",
            size=13,
            color=MD3Colors.get_on_surface_variant(is_dark),
            text_align=ft.TextAlign.CENTER,
        )
        self.empty_state = ft.Container(
            content=ft.Column(
                controls=[
                    self._empty_icon_well,
                    self._empty_title,
                    self._empty_hint,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=12,
                tight=True,
            ),
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.symmetric(horizontal=40, vertical=24),
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

        # Backups list - Using ListView for BackupGroup components (expandable tiles)
        # Each BackupGroup expands vertically, so ListView is more appropriate than GridView
        # ListView provides virtualization for performance with many groups
        self.backups_list = ft.ListView(
            controls=[],
            # Bottom padding lets the last group scroll clear of the floating
            # pill, which sits bottom-left over this list (games/settings
            # already reserve the same clearance).
            padding=ft.Padding.only(left=16, right=16, top=16, bottom=PILL_CLEARANCE),
            spacing=8,
            expand=True,
            auto_scroll=False,  # Maintain scroll position
        )
        self.backups_list_container = ft.Container(
            content=self.backups_list,
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
                    self.backups_list_container,
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
            # Filter pill: filled surface-container, borderless (see
            # _create_game_filter_dropdown). No border/focused-border entries
            # since the pill has no visible border.
            "game_filter_dropdown.bgcolor": (MD3Colors.get_surface_container(True), MD3Colors.get_surface_container(False)),
            "game_filter_dropdown.fill_color": (MD3Colors.get_surface_container(True), MD3Colors.get_surface_container(False)),
            "game_filter_dropdown.color": (MD3Colors.get_on_surface(True), MD3Colors.get_on_surface(False)),
            "header_subtitle.color": (MD3Colors.get_on_surface_variant(True), MD3Colors.get_on_surface_variant(False)),
            # Empty state (the tinted icon well's alpha bgcolor is computed, so
            # it is refreshed in apply_theme rather than mapped here).
            "_empty_icon.color": MD3Colors.get_themed_pair("primary"),
            "_empty_title.color": (MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)),
            "_empty_hint.color": (MD3Colors.get_on_surface_variant(True), MD3Colors.get_on_surface_variant(False)),
            # Orphan ("Unlinked games") section header — controls only exist
            # while an orphan section is rendered; _set_nested_property skips
            # missing paths, so these are safe no-ops otherwise.
            "_orphan_divider.color": (MD3Colors.get_outline(True), MD3Colors.get_outline(False)),
            "_orphan_icon.color": (MD3Colors.get_text_secondary(True), MD3Colors.get_text_secondary(False)),
            "_orphan_label.color": (MD3Colors.get_on_surface_variant(True), MD3Colors.get_on_surface_variant(False)),
            "_orphan_hint.color": (MD3Colors.get_text_secondary(True), MD3Colors.get_text_secondary(False)),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Re-theme the composed pieces the declarative map cannot express.

        ``get_themed_properties`` only carries flat color assignments; the stat
        tiles' tinted wells, the Clear All ControlState style and the empty
        state's tinted well are COMPUTED values. They are refreshed here before
        delegating, so the base implementation's cascade delay and its single
        self.update() still flush everything in one pass.
        """
        try:
            for tile in self._stat_tiles:
                tile.apply_colors(is_dark)

            if self.clear_all_button is not None:
                self.clear_all_button.style = self._clear_all_button_style(is_dark)

            if getattr(self, "_empty_icon_well", None) is not None:
                self._empty_icon_well.bgcolor = ft.Colors.with_opacity(
                    0.12, MD3Colors.get_primary(is_dark)
                )
        except Exception:
            pass  # Never let decorative re-theming abort the cascade

        await super().apply_theme(is_dark, delay_ms)

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human-readable string"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _update_summary(self, backups: list[GameDLLBackup]):
        """Populate the summary stat tiles from the backups being displayed.

        Aggregated IN PLACE from the same list the groups are built from — no
        extra query. With no game filter that list is every active backup
        (linked groups plus the unlinked section are the exact complement of one
        another), so the totals match the whole library; under an active filter
        the tiles describe what is actually on screen, which is what the user is
        looking at.

        The row is hidden at zero so the empty state reads as a clean, centered
        message rather than a wall of zeroes.
        """
        count = len(backups)
        if self.stats_row is not None:
            self.stats_row.visible = count > 0
        if count == 0:
            return

        total_size = sum(b.backup_size or 0 for b in backups)
        most_recent = max((b.backup_created_at for b in backups), default=None)

        self._stat_tiles[0].set_value(f"{count}")
        self._stat_tiles[1].set_value(self._format_size(total_size))
        # Same YYYY-MM-DD form as BackupRow / BackupGroup subtitles.
        self._stat_tiles[2].set_value(
            most_recent.strftime("%Y-%m-%d") if most_recent else "—"
        )

    async def load_backups(self, force: bool = False):
        """Load backups from database with optional game filter.

        PERFORMANCE: Skips full reload if backups are already loaded (tab switching).
        Use force=True to trigger a full refresh (explicit refresh button).

        Uses BackupGroup components which group backups by game for efficient display.
        Each group is an ExpansionTile that can be collapsed/expanded.

        Args:
            force: If True, forces a full reload even if backups are already loaded.
        """
        if self.is_loading:
            return

        # PERFORMANCE: Skip full reload if already loaded (fast tab switching)
        if self._backups_loaded and not force:
            self.logger.debug("Backups already loaded - animating groups on tab switch")
            # Ensure the view is visible
            if self.backups:
                self.backups_list_container.visible = True
                self.empty_state.visible = False

                # Animate groups progressively on tab switch for better UX
                visible_groups = self.backups_list.controls[:INITIAL_BATCH_SIZE]
                if visible_groups:
                    # Reset opacity for animation
                    for group in visible_groups:
                        group.opacity = 0
                        group.animate_opacity = ft.Animation(400, ft.AnimationCurve.EASE_OUT)
                    # View-scoped update (serializes only the BackupsView subtree,
                    # not the whole page) — matches GamesView's self.update() usage.
                    self.update()
                    # Trigger staggered fade-in
                    anim_task = asyncio.create_task(self._animate_groups_in(visible_groups))
                    register_task(anim_task, "animate_backups_tab_switch")
            else:
                self.empty_state.visible = True
                self.backups_list_container.visible = False
                self.update()
            self.loading_indicator.visible = False
            return

        self.is_loading = True
        self.loading_indicator.visible = True
        self.empty_state.visible = False
        self.backups_list_container.visible = False
        # Clear existing groups for fresh reload
        self.backups_list.controls.clear()
        self.update()

        try:
            start_total = time.perf_counter()

            # Ensure database pool is ready
            await db_manager.ensure_pool()

            self.logger.info("Loading backups from database (grouped by game)...")

            # PERFORMANCE: Run all database queries in parallel via HyperParallelLoader
            # (anyio task group + shared thread_io limiter for true parallelism)
            start_db = time.perf_counter()
            loader = HyperParallelLoader()
            game_id = self.selected_game_id  # Capture for lambda

            results = await loader.load_all([
                LoadTask("games", lambda: db_manager.get_games_with_backups_sync()),
                LoadTask("grouped", lambda gid=game_id: db_manager.get_backups_grouped_by_game_sync(gid)),
                LoadTask("orphaned", lambda: db_manager.get_orphaned_backups_grouped_sync()),
            ])

            self.games_with_backups = results.get("games", [])
            grouped_backups: dict[int, list[GameDLLBackup]] = results.get("grouped", {})
            # Orphaned backups: active rows whose owning game left the library.
            # Loaded UNFILTERED (independent of the game filter) so (a) the
            # header total reconciles with what's displayed and (b) the filter
            # pill's visibility reflects the true group count. Only RENDERED
            # under the "All Games" view — a specific game filter excludes them
            # cleanly (they are never filter options; see below).
            orphaned_grouped: dict[int, list[GameDLLBackup]] = results.get("orphaned", {})
            self._update_game_filter_options()

            # Reveal the filter pill only when there are >= 2 backup groups
            # (linked games with backups + orphan groups), counted UNFILTERED so
            # an active filter can always be reset back to "All Games".
            if self.game_filter_dropdown:
                total_group_count = len(self.games_with_backups) + len(orphaned_grouped)
                self.game_filter_dropdown.visible = total_group_count >= 2

            db_ms = (time.perf_counter() - start_db) * 1000
            self.logger.debug(f"[PERF] Database queries (hyper-parallel): {db_ms:.1f}ms")

            # Orphan groups are only RENDERED under "All Games" (no active game
            # filter). A specific game filter shows just that linked game and
            # excludes orphans — they are never filter options, so this is the
            # clean exclusion path and the view cannot crash on a stale filter.
            orphan_items: list[tuple[int, list[GameDLLBackup]]] = (
                list(orphaned_grouped.items()) if self.selected_game_id is None else []
            )

            # Flatten for total count and clear-all state. Include orphan backups
            # when they are being displayed so the Clear All enablement and its
            # confirmation count reflect everything on screen.
            self.backups = list(itertools.chain.from_iterable(grouped_backups.values()))
            for _gid, _obs in orphan_items:
                self.backups.extend(_obs)
            total_count = len(self.backups)

            # Summary tiles are aggregated from that same flattened list — the
            # backup rows already in memory — so the header costs no query of
            # its own (the old get_backup_summary_stats_sync LoadTask is gone).
            self._update_summary(self.backups)

            if not grouped_backups and not orphan_items:
                self.logger.info("No backups found")
                self.empty_state.visible = True
                self.loading_indicator.visible = False
                self._update_clear_button_state(False)
                self._backups_loaded = True
                self.update()
                return

            # PERFORMANCE: Resolve header artwork thumbnails for ONLY the games
            # actually being displayed — a targeted game_id -> effective Steam
            # app_id query (batch_get_app_ids_for_games_sync) instead of
            # scanning the entire games table. A single batch query then
            # resolves cached local image paths for exactly those app_ids.
            #
            # UNLINKED groups are resolved the same way, just with a different
            # first leg: they have no games row to read an app_id from, so the
            # id comes from an exact normalized-NAME lookup against the cached
            # Steam app index (_resolve_orphan_app_ids_sync). Both legs then
            # share ONE cached-image-path query, so an unlinked game whose art
            # was cached while it was still in the library shows the same
            # thumbnail as a linked one; a miss falls back to the folder icon.
            self._art_paths_by_game_id: dict[int, str] = {}
            displayed_game_ids = list(grouped_backups.keys())
            orphan_labels: dict[int, str] = {
                gid: (obs[0].game_name if obs else "")
                for gid, obs in orphan_items
            }
            try:
                app_id_by_game_id = await anyio.to_thread.run_sync(
                    db_manager.batch_get_app_ids_for_games_sync, displayed_game_ids, limiter=thread_io
                )
                if orphan_labels:
                    # Synthetic orphan ids are negative, so they can never
                    # collide with the linked game ids merged in here. Kept as a
                    # second sequential hop rather than a HyperParallelLoader
                    # fan-out: it is a handful of indexed point lookups, and the
                    # loader returns the Exception AS the result on failure,
                    # which this dict merge would then choke on.
                    app_id_by_game_id.update(
                        await anyio.to_thread.run_sync(
                            _resolve_orphan_app_ids_sync, orphan_labels, limiter=thread_io
                        )
                    )
                needed_app_ids = list(set(app_id_by_game_id.values()))
                if needed_app_ids:
                    cached_art_paths = await anyio.to_thread.run_sync(
                        db_manager._batch_get_cached_image_paths, needed_app_ids, limiter=thread_io
                    )
                    self._art_paths_by_game_id = {
                        gid: cached_art_paths[app_id]
                        for gid, app_id in app_id_by_game_id.items()
                        if app_id in cached_art_paths
                    }
            except Exception as art_err:
                # Header artwork is decorative — a failure here must not abort
                # the backup load (anyio.to_thread propagates, unlike the
                # HyperParallelLoader which caught the old art_games task).
                self.logger.debug(f"Header artwork resolution failed: {art_err}")

            # PERFORMANCE: Progressive loading with BackupGroup components
            # 1. Create first batch of groups immediately (visible groups)
            # 2. Show UI immediately
            # 3. Create remaining groups in background batches

            start_groups = time.perf_counter()

            # Convert to list of (game_id, backups) for ordering
            game_items = list(grouped_backups.items())

            # Step 1: Create first batch of BackupGroup components
            first_batch_items = game_items[:INITIAL_BATCH_SIZE]
            groups = []
            for gid, backups in first_batch_items:
                game_name = backups[0].game_name if backups else "Unknown"
                group = BackupGroup(
                    game_name=game_name,
                    game_id=gid,
                    backups=backups,
                    page=self._page_ref,
                    logger=self.logger,
                    on_restore=self._on_restore_backup_from_group,
                    on_delete=self._on_delete_backup_from_group,
                    on_restore_all=self._on_restore_all_for_game,
                    art_path=self._art_paths_by_game_id.get(gid),
                )
                groups.append(group)

            first_batch_ms = (time.perf_counter() - start_groups) * 1000
            self.logger.debug(f"[PERF] First batch ({len(first_batch_items)} groups): {first_batch_ms:.1f}ms")

            # Step 2: Show UI immediately with first batch
            self.backups_list.controls = groups
            self.backups_list_container.visible = True
            self.empty_state.visible = False
            self.loading_indicator.visible = False
            self._update_clear_button_state(True)
            self._backups_loaded = True

            self.update()

            # Step 3: Create remaining linked groups in background batches, then
            # the orphan section LAST so it always sits below the linked groups.
            # If there are no remaining linked groups, append orphans inline now.
            remaining_items = game_items[INITIAL_BATCH_SIZE:]
            if remaining_items:
                task = asyncio.create_task(
                    self._load_remaining_groups(remaining_items, orphan_items)
                )
                register_task(task, "load_remaining_backup_groups")
            elif orphan_items:
                self._append_orphan_section(orphan_items)
                self.update()

            total_ms = (time.perf_counter() - start_total) * 1000
            self.logger.info(
                f"Loaded {len(first_batch_items)} game groups ({total_count} backups total) instantly, "
                f"{len(remaining_items)} groups loading in background, "
                f"{len(orphan_items)} orphan group(s) ({total_ms:.1f}ms)"
            )

            # NOTE: the success path already issued its single self.update()
            # above (first-batch reveal); the finally block no longer re-updates.

        except Exception as e:
            self.logger.error(f"Error loading backups: {e}", exc_info=True)
            self.empty_state.visible = True
            self.loading_indicator.visible = False
            self._update_clear_button_state(False)
            # Hide the summary rather than leaving stale totals over an empty
            # list (the load may have failed after a previous successful one).
            self._update_summary([])
            self._backups_loaded = False  # Allow retry on next tab switch
            self.update()  # Single terminal update for the error path

        finally:
            self.is_loading = False

    async def _load_remaining_groups(
        self,
        remaining_items: list[tuple[int, list[GameDLLBackup]]],
        orphan_items: list[tuple[int, list[GameDLLBackup]]] | None = None,
    ):
        """Load remaining BackupGroup components in background batches.

        PERFORMANCE: Creates groups in batches with yields to keep UI responsive.
        Each batch adds groups to the ListView which provides virtualization.

        Args:
            remaining_items: List of (game_id, backups) tuples to create groups for
            orphan_items: Optional orphan (game_id, backups) tuples to render in
                the "Unlinked games" section AFTER all linked groups.
        """
        try:
            total_remaining = len(remaining_items)
            loaded = 0

            for i in range(0, total_remaining, BACKGROUND_BATCH_SIZE):
                batch = remaining_items[i:i + BACKGROUND_BATCH_SIZE]

                # Create groups for this batch
                new_groups = []
                for gid, backups in batch:
                    game_name = backups[0].game_name if backups else "Unknown"
                    group = BackupGroup(
                        game_name=game_name,
                        game_id=gid,
                        backups=backups,
                        page=self._page_ref,
                        logger=self.logger,
                        on_restore=self._on_restore_backup_from_group,
                        on_delete=self._on_delete_backup_from_group,
                        on_restore_all=self._on_restore_all_for_game,
                        art_path=self._art_paths_by_game_id.get(gid),
                    )
                    new_groups.append(group)

                # Add to list (virtualized - only visible groups render)
                self.backups_list.controls.extend(new_groups)
                loaded += len(new_groups)

                # Single view-scoped update per batch. Guard against the user
                # navigating away mid-load (view detached -> self.update() raises).
                try:
                    self.update()
                except Exception:
                    pass

                # Yield to event loop to keep UI responsive
                await anyio.sleep(0.01)

            self.logger.debug(f"[PERF] Background loaded {loaded} additional backup groups")

            # Orphan section renders last, below every linked group.
            if orphan_items:
                self._append_orphan_section(orphan_items)
                try:
                    self.update()
                except Exception:
                    pass

        except Exception as e:
            self.logger.error(f"Error loading remaining backup groups: {e}", exc_info=True)

    def _build_orphan_section_header(self, is_dark: bool) -> ft.Container:
        """Build the "Unlinked games" section header (divider + label + hint).

        Stores its themed sub-controls on self so get_themed_properties can
        re-theme them live. Matches the view's secondary typography.
        """
        self._orphan_divider = ft.Divider(height=1, color=MD3Colors.get_outline(is_dark))
        self._orphan_icon = ft.Icon(
            ft.Icons.LINK_OFF,
            size=16,
            color=MD3Colors.get_text_secondary(is_dark),
        )
        self._orphan_label = ft.Text(
            "Unlinked games",
            size=13,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )
        self._orphan_hint = ft.Text(
            "No longer in your library — restore reinstalls the saved DLL if the game is still installed.",
            size=11,
            italic=True,
            color=MD3Colors.get_text_secondary(is_dark),
        )
        return ft.Container(
            content=ft.Column(
                controls=[
                    self._orphan_divider,
                    ft.Row(
                        controls=[self._orphan_icon, self._orphan_label],
                        spacing=6,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._orphan_hint,
                ],
                spacing=4,
                tight=True,
            ),
            padding=ft.Padding.only(left=4, right=4, top=12, bottom=4),
        )

    def _append_orphan_section(self, orphan_items: list[tuple[int, list[GameDLLBackup]]]):
        """Append the "Unlinked games" header and one delete-only BackupGroup
        per orphan group to the end of the backups list.

        Orphan groups wire a per-DLL restore handler (restore-by-path via
        restore_orphaned_dll_from_backup) but pass on_restore_all=None, so
        BackupGroup shows per-row Restore but no group-level Restore All. Delete
        still works. is_orphan=True is informational only (see BackupGroup).

        Artwork comes from the same batch-resolved map as the linked groups —
        orphan ids are resolved by name in load_backups — so unlinked games with
        cached Steam art get a real thumbnail instead of the folder icon.
        """
        if not orphan_items:
            return

        is_dark = self._get_is_dark()
        self.backups_list.controls.append(self._build_orphan_section_header(is_dark))

        for gid, backups in orphan_items:
            game_name = backups[0].game_name if backups else "Unknown"
            group = BackupGroup(
                game_name=game_name,
                game_id=gid,
                backups=backups,
                page=self._page_ref,
                logger=self.logger,
                on_restore=self._on_restore_orphan_from_group,  # restore-by-path
                on_delete=self._on_delete_backup_from_group,
                on_restore_all=None,      # no Restore All for orphans
                # Name-resolved cached art when we have it; None (folder icon)
                # when the game was never cached or the name didn't match.
                art_path=self._art_paths_by_game_id.get(gid),
                is_orphan=True,
            )
            self.backups_list.controls.append(group)

    async def _animate_groups_in(self, groups: list):
        """Animate backup groups with staggered fade-in for better UX"""
        # Small initial delay
        await anyio.sleep(0.05)

        # Animate groups in batches of 3 for smooth effect (fewer groups than cards)
        batch_size = 3
        for batch_start in range(0, len(groups), batch_size):
            batch_end = min(batch_start + batch_size, len(groups))
            # Set opacity for entire batch
            for group in groups[batch_start:batch_end]:
                group.opacity = 1
            # Single view-scoped update per batch. Guard against the view being
            # detached mid-animation (user navigated away).
            try:
                self.update()
            except Exception:
                pass
            await anyio.sleep(0.08)  # 80ms delay per batch (slightly longer for groups)

    async def _on_refresh_clicked(self, e):
        """Handle refresh button click with rotation animation"""
        # Rotate refresh button (view-scoped — the button lives in this subtree)
        if self.refresh_button_ref.current:
            self.refresh_button_ref.current.rotate += math.pi * 2  # 360 degrees
            self.update()

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

    def _on_restore_backup(self, backup: DLLBackup, orphan: bool = False):
        """Handle restore backup button click.

        When ``orphan`` is True the backup's owning game has left the library, so
        restore is performed by path (``restore_orphaned_dll_from_backup``) and
        the dialog adds a "game must still be installed" note.
        """
        # Create dialog first without actions
        content_controls = [
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
        ]
        if orphan:
            content_controls.append(
                ft.Text(
                    "This game is no longer in your library — it must still be "
                    "installed at its original location for the restore to succeed.",
                    color=ft.Colors.ORANGE,
                    size=12,
                )
            )
        dialog = ft.AlertDialog(
            title=ft.Text("Restore DLL Backup?"),
            content=ft.Column(
                controls=content_controls,
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
                on_click=self._create_restore_handler(backup, dialog, orphan),
                style=ft.ButtonStyle(
                    bgcolor="#2D6E88",
                    color=ft.Colors.WHITE,
                ),
            ),
        ]

        self._page_ref.show_dialog(dialog)

    def _create_restore_handler(self, backup: DLLBackup, dialog: ft.AlertDialog, orphan: bool = False):
        """Create async restore handler for specific backup"""
        async def handler(e):
            await self._perform_restore(backup, dialog, orphan)
        return handler

    async def _perform_restore(self, backup: DLLBackup, dialog: ft.AlertDialog, orphan: bool = False):
        """Perform the restore operation.

        ``orphan`` selects the restore-by-path service call for backups whose
        owning game left the library; both paths share identical progress/result
        feedback and the same force-refresh on success.
        """
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
            # Perform restore (orphans go through the restore-by-path service)
            if orphan:
                success, message = await restore_orphaned_dll_from_backup(backup.id)
            else:
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

            # Refresh backups list if successful (force: the restored row must
            # actually disappear, not hit the already-loaded animate path)
            if success:
                await self.load_backups(force=True)

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

            # Refresh backups list if successful (force: see _perform_restore)
            if success:
                await self.load_backups(force=True)

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

    # -------------------------------------------------------------------------
    # BackupGroup callback methods (for grouped backup display)
    # -------------------------------------------------------------------------

    def _on_restore_backup_from_group(self, backup: GameDLLBackup):
        """Handle restore callback from BackupGroup component.

        Converts GameDLLBackup to DLLBackup and delegates to existing restore logic.
        """
        # Convert GameDLLBackup to DLLBackup for compatibility with existing restore logic
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
        self._on_restore_backup(dll_backup)

    def _on_restore_orphan_from_group(self, backup: GameDLLBackup):
        """Handle restore callback for an ORPHANED backup from BackupGroup.

        Same conversion as the linked path, but routes through the orphan
        restore-by-path flow (the owning game is no longer in the DB, so the
        target DLL path is derived from the backup's stored .dlsss sidecar).
        """
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
        self._on_restore_backup(dll_backup, orphan=True)

    def _on_delete_backup_from_group(self, backup: GameDLLBackup):
        """Handle delete callback from BackupGroup component.

        Converts GameDLLBackup to DLLBackup and delegates to existing delete logic.
        """
        # Convert GameDLLBackup to DLLBackup for compatibility with existing delete logic
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
        self._on_delete_backup(dll_backup)

    def _on_restore_all_for_game(self, game_id: int, game_name: str):
        """Handle restore all backups for a specific game from BackupGroup.

        Shows a confirmation dialog and restores all backups for the given game.
        """
        # Create dialog first without actions
        dialog = ft.AlertDialog(
            title=ft.Text("Restore All Backups?"),
            content=ft.Column(
                controls=[
                    ft.Text(f"This will restore all backup DLLs for:"),
                    ft.Text(game_name, weight=ft.FontWeight.BOLD),
                    ft.Divider(),
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

        # Add actions after dialog variable exists
        dialog.actions = [
            ft.TextButton(
                "Cancel",
                on_click=lambda e: self._page_ref.pop_dialog(),
            ),
            ft.ElevatedButton(
                "Restore All",
                on_click=self._create_restore_all_handler(game_id, game_name, dialog),
                style=ft.ButtonStyle(
                    bgcolor="#2D6E88",
                    color=ft.Colors.WHITE,
                ),
            ),
        ]

        self._page_ref.show_dialog(dialog)

    def _create_restore_all_handler(self, game_id: int, game_name: str, dialog: ft.AlertDialog):
        """Create async restore all handler for specific game"""
        async def handler(e):
            await self._perform_restore_all(game_id, game_name, dialog)
        return handler

    async def _perform_restore_all(self, game_id: int, game_name: str, dialog: ft.AlertDialog):
        """Perform restore all operation for a game.

        Restores all backups for the specified game sequentially and shows results.
        """
        self._page_ref.pop_dialog()

        # Show progress indicator
        progress_dialog = ft.AlertDialog(
            title=ft.Text("Restoring..."),
            content=ft.Column(
                controls=[
                    ft.ProgressRing(),
                    ft.Text(f"Restoring all backups for {game_name}...", size=12),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
                tight=True,
            ),
        )
        self._page_ref.show_dialog(progress_dialog)
        self._page_ref.update()

        try:
            # Get all backups for this game using sync method in thread
            grouped = await anyio.to_thread.run_sync(
                db_manager.get_backups_grouped_by_game_sync, game_id, limiter=thread_io
            )
            backups = grouped.get(game_id, [])

            success_count = 0
            error_count = 0

            for backup in backups:
                try:
                    success, _ = await restore_dll_from_backup(backup.id)
                    if success:
                        success_count += 1
                    else:
                        error_count += 1
                except Exception as ex:
                    self.logger.error(f"Error restoring backup {backup.id}: {ex}")
                    error_count += 1

            # Close progress dialog
            self._page_ref.pop_dialog()

            # Show result summary
            total = len(backups)
            if error_count == 0:
                result_title = "Restore Complete"
                result_message = f"Successfully restored all {success_count} backup(s) for {game_name}."
            else:
                result_title = "Restore Partially Complete"
                result_message = f"Restored {success_count} of {total} backup(s) for {game_name}.\n{error_count} backup(s) failed."

            result_dialog = ft.AlertDialog(
                title=ft.Text(result_title),
                content=ft.Text(result_message),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self._page_ref.pop_dialog(),
                    ),
                ],
            )
            self._page_ref.show_dialog(result_dialog)

            # Refresh backups list
            await self.load_backups(force=True)

        except Exception as e:
            self.logger.error(f"Error restoring all backups for game {game_id}: {e}", exc_info=True)
            self._page_ref.pop_dialog()

            error_dialog = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(f"Failed to restore backups: {str(e)}"),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda e: self._page_ref.pop_dialog(),
                    ),
                ],
            )
            self._page_ref.show_dialog(error_dialog)
