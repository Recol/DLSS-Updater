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
from dlss_updater.ui_flet.components.hub_card import HubCard
from dlss_updater.ui_flet.hyper_parallel_loader import HyperParallelLoader, LoadTask


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

        is_dark = page.theme_mode == ft.ThemeMode.DARK

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
        )

        self._games_card = HubCard(
            title="Games",
            subtitle="Browse & manage your\ncomplete game library",
            icon=ft.Icons.SPORTS_ESPORTS,
            accent_color_dark=TabColors.GAMES,
            accent_color_light=TabColors.GAMES_LIGHT,
            icon_size=64,
            title_size=24,
            on_click=lambda e: self._navigate("games"),
            border_radius_val=20,
            page=page,
        )
        self._games_card._title_text.weight = ft.FontWeight.W_700

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
        if FEATURES.dlss_windows_presets and on_open_dlss_settings:
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
        self.controls = [
            ft.Container(
                content=hub_layout,
                expand=True,
                padding=ft.Padding.all(24),
                alignment=ft.Alignment.CENTER,
            ),
        ]

        self._register_theme_aware()

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
            ])

            game_count = results.get("game_count", 0)
            launcher_count = results.get("launcher_count", 0)
            backup_stats = results.get("backup_stats", (0, 0))
            scan_age = results.get("scan_age", None)

            # Handle exceptions from failed tasks
            if isinstance(game_count, Exception):
                game_count = 0
            if isinstance(launcher_count, Exception):
                launcher_count = 0
            if isinstance(backup_stats, Exception) or not backup_stats:
                backup_stats = (0, 0)
            if isinstance(scan_age, Exception):
                scan_age = None

            # Update card stats
            if game_count > 0:
                self._games_card.set_stats(f"{game_count} games found")
            if launcher_count > 0:
                self._launchers_card.set_stats(f"{launcher_count} configured")

            # Secondary stats line on the Games card: backups + last scan age
            detail_parts = []
            backup_count, backup_size = backup_stats
            if backup_count > 0:
                detail_parts.append(
                    f"{backup_count} DLL backup{'s' if backup_count != 1 else ''}"
                    f" · {self._format_size(backup_size)}"
                )
            if scan_age:
                detail_parts.append(scan_age)
            if detail_parts:
                self._games_card.set_stats_detail("  ·  ".join(detail_parts))

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
