"""
Hub View - Staggered/Asymmetric Home Screen
Left column: Launchers + Settings cards stacked
Right side: Large Games card spanning full height
"""

import anyio
import logging

import flet as ft

from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin
from dlss_updater.ui_flet.components.hub_card import HubCard, GamesHeroCard
from dlss_updater.ui_flet.hyper_parallel_loader import HyperParallelLoader, LoadTask


# The Launchers accent (TabColors.LAUNCHERS teal) is markedly lower-chroma than
# the other three side-tile accents (NVIDIA green / rust / purple). A brand wash
# preserves accent chroma scaled by its alpha, so at the shared hero-wash
# default alpha (WASH_OPACITY_DARK/LIGHT) the teal reads as barely tinted —
# near-neutral in dark, near-white in light. Boost ONLY this tile's wash alpha
# so its resulting tint matches the other tiles' (roughly their average
# resulting chroma). Tune these two numbers if a visual pass wants more/less;
# every other tile keeps hero_surface's defaults untouched.
LAUNCHERS_WASH_OPACITY_DARK = 0.34
LAUNCHERS_WASH_OPACITY_LIGHT = 0.24


class HubView(ThemeAwareMixin, ft.Column):
    """
    Staggered hub home screen with 3 navigation cards.

    Layout:
        Left column (280px): Launchers card + Settings card (stacked)
        Right (expand): Games card (full height)
    """

    _theme_priority = 10

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        on_navigate=None,
        on_open_dlss_settings=None,
    ):
        super().__init__()
        self._page_ref = page
        self.logger = logger
        self._on_navigate = on_navigate
        self._on_open_dlss_settings = on_open_dlss_settings
        self.expand = True
        self.alignment = ft.MainAxisAlignment.CENTER
        self.horizontal_alignment = ft.CrossAxisAlignment.CENTER

        # References to the current card instances - kept as attributes so
        # load_stats() and rebuild_for_theme() can find them. Populated by
        # _build_layout() below (also called by rebuild_for_theme()).
        self._launchers_card: HubCard | None = None
        self._games_card: GamesHeroCard | None = None
        self._settings_card: HubCard | None = None
        self._dlss_settings_card: HubCard | None = None
        self._backups_card: HubCard | None = None

        self.controls = [self._build_layout()]

        self._register_theme_aware()

    def _build_layout(self) -> ft.Container:
        """Build the hub cards + staggered layout, and assign the fresh card
        instances onto self._launchers_card / self._games_card / etc.

        Factored out of __init__ so rebuild_for_theme() can call it again
        to produce brand-new card instances after a theme toggle fired
        while this view was detached (see rebuild_for_theme()'s docstring).
        """
        page = self._page_ref

        # Create hub cards
        self._launchers_card = HubCard(
            title="Launchers",
            subtitle="Configure launcher paths",
            icon=ft.Icons.ROCKET_LAUNCH,
            accent_color_dark=TabColors.LAUNCHERS,
            accent_color_light=TabColors.LAUNCHERS_LIGHT,
            icon_size=40,
            title_size=18,
            on_click=lambda e: self._navigate("launchers"),
            border_radius_val=16,
            page=page,
            wash_opacity_dark=LAUNCHERS_WASH_OPACITY_DARK,
            wash_opacity_light=LAUNCHERS_WASH_OPACITY_LIGHT,
        )

        self._games_card = GamesHeroCard(
            title="Games",
            subtitle="Browse & manage your\ncomplete game library",
            icon=ft.Icons.SPORTS_ESPORTS,
            accent_color_dark=TabColors.GAMES,
            accent_color_light=TabColors.GAMES_LIGHT,
            on_click=lambda e: self._navigate("games"),
            border_radius_val=20,
            page=page,
        )

        self._settings_card = HubCard(
            title="Settings",
            subtitle="Preferences &\nconfiguration",
            icon=ft.Icons.SETTINGS,
            accent_color_dark=TabColors.SETTINGS,
            accent_color_light=TabColors._TAB_COLORS_LIGHT.get("Settings", "#6A1B9A"),
            icon_size=40,
            title_size=18,
            on_click=lambda e: self._navigate("settings"),
            border_radius_val=16,
            page=page,
        )

        # DLSS Settings card (Windows + NVIDIA only) - global SR preset override
        from dlss_updater.platform_utils import FEATURES
        left_cards = [self._launchers_card]
        self._dlss_settings_card = None
        if FEATURES.dlss_windows_presets and self._on_open_dlss_settings:
            self._dlss_settings_card = HubCard(
                title="DLSS Settings",
                subtitle="Global DLSS preset\noverrides (SR/RR/FG)",
                icon=ft.Icons.AUTO_AWESOME,
                accent_color_dark="#76B900",   # NVIDIA green
                accent_color_light="#558B00",
                icon_size=40,
                title_size=18,
                on_click=lambda e: self._open_dlss_settings(),
                border_radius_val=16,
                page=page,
            )
            left_cards.append(self._dlss_settings_card)

        # Backups card (always shown, not feature-gated) - sits between
        # DLSS Settings (when present) and Settings.
        self._backups_card = HubCard(
            title="Backups",
            subtitle="Restore & manage\nDLL backups",
            icon=ft.Icons.SETTINGS_BACKUP_RESTORE,
            accent_color_dark=TabColors.BACKUPS,
            accent_color_light=TabColors.BACKUPS_LIGHT,
            icon_size=40,
            title_size=18,
            on_click=lambda e: self._navigate("backups"),
            border_radius_val=16,
            page=page,
        )
        left_cards.append(self._backups_card)
        left_cards.append(self._settings_card)

        # Left column: Launchers (+ DLSS Settings) + Settings stacked
        left_column = ft.Column(
            controls=left_cards,
            spacing=16,
            expand=True,
            width=280,
        )

        # Right: Games card (full height)
        right_column = ft.Column(
            controls=[self._games_card],
            expand=True,
        )

        # Main staggered layout
        hub_layout = ft.Row(
            controls=[
                ft.Container(content=left_column, width=280),
                ft.Container(content=right_column, expand=True),
            ],
            spacing=16,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        # Wrap in centered container with padding
        return ft.Container(
            content=hub_layout,
            expand=True,
            padding=ft.Padding.all(24),
            alignment=ft.Alignment.CENTER,
        )

    def rebuild_for_theme(self) -> None:
        """Rebuild all hub cards as fresh instances reflecting the current
        theme.

        Flet 0.86 silently drops property-only patches sent to controls in
        a detached subtree (see CLAUDE.md); this view sits behind the nav
        controller's content-detachment pattern, so a theme toggle that
        fires while the hub is detached leaves the existing card instances
        stale even though their Python state (and ThemeAwareMixin's
        apply_theme) is correct. Only freshly constructed/replaced controls
        are guaranteed to render correctly, so this rebuilds the whole card
        set instead of trying to heal it in place.

        Called by MainView on hub attach when the theme changed while this
        view was detached (see MainView._theme_stale_views). Stats/mosaic
        are repopulated by the caller's subsequent load_stats() call - a
        fresh card starts with its stat pill hidden and mosaic inactive.
        """
        old_cards = [
            c for c in (
                self._launchers_card, self._dlss_settings_card,
                self._backups_card, self._settings_card, self._games_card,
            ) if c is not None
        ]
        for card in old_cards:
            card._unregister_theme_aware()

        self.controls = [self._build_layout()]

        try:
            self.update()
        except Exception:
            pass

    def _navigate(self, view_name: str):
        """Handle card click navigation."""
        if self._on_navigate:
            if self._page_ref:
                self._page_ref.run_task(self._on_navigate, view_name)

    def _open_dlss_settings(self):
        """Open the DLSS Settings panel (global SR preset override)."""
        if self._on_open_dlss_settings and self._page_ref:
            self._page_ref.run_task(self._on_open_dlss_settings)

    @staticmethod
    def _format_size(total_size: int) -> str:
        """Format a byte count for the stats line."""
        if total_size < 1024 * 1024:
            return f"{total_size / 1024:.0f} KB"
        if total_size < 1024 * 1024 * 1024:
            return f"{total_size / (1024 * 1024):.1f} MB"
        return f"{total_size / (1024 * 1024 * 1024):.1f} GB"

    @staticmethod
    def _read_scan_age_str() -> str | None:
        """Read the scan cache timestamp and return a compact age string.

        Runs inside the HyperParallelLoader thread pool (blocking file I/O).
        """
        try:
            from datetime import datetime
            from pathlib import Path
            from dlss_updater.config import get_config_path
            from dlss_updater.models import ScanCacheData, decode_json

            cache_path = Path(get_config_path()).parent / "scan_cache.json"
            if not cache_path.exists():
                return None
            cache = decode_json(cache_path.read_bytes(), type=ScanCacheData)
            if not cache.timestamp:
                return None
            age = datetime.now() - datetime.fromisoformat(cache.timestamp)
            hours = age.total_seconds() / 3600
            if hours < 1:
                return f"scanned {int(age.total_seconds() / 60)}m ago"
            if hours < 24:
                return f"scanned {int(hours)}h ago"
            return f"scanned {int(hours / 24)}d ago"
        except Exception:
            return None

    @staticmethod
    def _load_mosaic_art_paths() -> list[str]:
        """Collect up to 6 locally cached game artwork paths for the Games
        hero mosaic. Runs inside the HyperParallelLoader thread pool
        (blocking DB I/O).

        Uses the targeted ``get_mosaic_app_ids_sync`` query (a capped candidate
        pool of resolved Steam app IDs) instead of loading the entire games
        table just to pick 6 images, then resolves cached local image paths for
        that pool in a single batch.
        """
        from dlss_updater.database import db_manager

        try:
            app_ids = db_manager.get_mosaic_app_ids_sync(limit=60)
        except Exception:
            return []

        if not app_ids:
            return []

        try:
            cached = db_manager._batch_get_cached_image_paths(app_ids)
        except Exception:
            return []

        return list(cached.values())[:6]

    async def load_stats(self):
        """Load hub card stats via HyperParallelLoader."""
        try:
            from dlss_updater.database import db_manager

            loader = HyperParallelLoader()
            results = await loader.load_all([
                LoadTask("game_count", lambda: db_manager.get_game_count_sync()),
                LoadTask("launcher_count", lambda: db_manager.get_configured_launchers_count_sync()),
                LoadTask("backup_stats", lambda: db_manager.get_backup_summary_stats_sync()),
                LoadTask("scan_age", self._read_scan_age_str),
                LoadTask("mosaic_paths", self._load_mosaic_art_paths),
            ])

            game_count = results.get("game_count", 0)
            launcher_count = results.get("launcher_count", 0)
            backup_stats = results.get("backup_stats", (0, 0))
            scan_age = results.get("scan_age", None)
            mosaic_paths = results.get("mosaic_paths", [])

            # Handle exceptions from failed tasks
            if isinstance(game_count, Exception):
                game_count = 0
            if isinstance(launcher_count, Exception):
                launcher_count = 0
            if isinstance(backup_stats, Exception) or not backup_stats:
                backup_stats = (0, 0)
            if isinstance(scan_age, Exception):
                scan_age = None
            if isinstance(mosaic_paths, Exception):
                mosaic_paths = []

            # Populate the Games hero mosaic (no-ops -> brand-wash fallback
            # when fewer than 2 cached art paths are available).
            if mosaic_paths:
                self._games_card.set_mosaic(mosaic_paths)

            # Update side-card stat pills
            if launcher_count > 0:
                self._launchers_card.set_stats(f"{launcher_count} configured")

            # Games hero stat pills: game count, backups, last scan age
            backup_count, backup_size = backup_stats

            # Backups side-card stat pill - reuses the same already-loaded
            # backup_stats result (no new query).
            if backup_count > 0:
                self._backups_card.set_stats(
                    f"{backup_count} backup{'s' if backup_count != 1 else ''}"
                    f" · {self._format_size(backup_size)}"
                )
            pills: list[tuple[str, str | None]] = []
            if game_count > 0:
                pills.append((f"{game_count} games found", ft.Icons.SPORTS_ESPORTS))
            if backup_count > 0:
                pills.append((
                    f"{backup_count} backup{'s' if backup_count != 1 else ''}"
                    f" · {self._format_size(backup_size)}",
                    ft.Icons.FOLDER_ZIP,
                ))
            if scan_age:
                pills.append((scan_age, ft.Icons.SCHEDULE))
            if pills:
                self._games_card.set_pills(pills)

            if self._page_ref:
                self._page_ref.update()

        except Exception as e:
            self.logger.warning(f"Failed to load hub stats: {e}")

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme to hub view and all child cards."""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        # Cards handle their own theming via ThemeAwareMixin
        # Nothing extra needed at hub level

        try:
            self.update()
        except Exception:
            pass
