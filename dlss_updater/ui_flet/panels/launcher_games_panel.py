"""
LauncherGamesPanel - Detected games list for a single launcher
Slide panel content shown when a LauncherCard's banner is tapped (Option B
banner-card grid design — see launcher_card.py). Migrated from the old
inline ExpansionTile game list (Option A) so the launcher grid stays a
compact, uniform-height set of banner cards instead of expanding rows.
"""

import logging

import flet as ft

from dlss_updater.models import GameCardData
from dlss_updater.ui_flet.components.slide_panel import PanelContentBase
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


class LauncherGamesPanel(ThemeAwareMixin, PanelContentBase):
    """
    Read-only panel listing the games detected for one launcher.

    Data flows in at construction time (``games_data``) — the caller
    (LauncherCard._open_games_panel()) already holds the list handed to it
    by main_view's ``card.set_games(...)`` call, so no extra query is made
    here; this panel is purely presentational, matching ReleaseNotesPanel's
    "read-only, Save just closes" convention.
    """

    # Virtualization threshold - ListView kicks in above this count (migrated
    # from the old LauncherCard._create_game_tile()/set_games() logic).
    VIRTUALIZATION_THRESHOLD = 20
    # Fixed tile height for virtualization (enables ListView optimization)
    GAME_TILE_HEIGHT = 44
    # Cap on the virtualized ListView's own height — the panel's outer content
    # area already scrolls (see SlidePanel._build_content_area()), so this
    # just keeps a single very long list from making the ListView itself
    # unreasonably tall; ~13 rows visible before it scrolls internally.
    _LIST_MAX_HEIGHT = 560

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        launcher_name: str,
        accent: str,
        games_data: list[GameCardData] | None,
    ):
        """
        Args:
            page: Flet Page instance
            logger: Logger instance
            launcher_name: Display name of the launcher (e.g. "Steam Games")
            accent: The launcher's brand color, for the panel header wash
            games_data: Detected games for this launcher (may be empty)
        """
        super().__init__(page, logger)

        self._registry = get_theme_registry()
        self._theme_priority = 60  # Panels animate later in cascade

        self._launcher_name = launcher_name
        self._accent = accent
        self._games_data: list[GameCardData] = games_data or []

        self._register_theme_aware()

    @property
    def title(self) -> str:
        count = len(self._games_data)
        return f"{self._launcher_name} — {count} game{'s' if count != 1 else ''}"

    @property
    def subtitle(self) -> str | None:
        return None

    @property
    def accent(self) -> str | None:
        """Brand accent for the header wash — the launcher's own brand color."""
        return self._accent

    @property
    def icon(self) -> str | None:
        """
        Generic gamepad glyph for the header watermark. Brand identity comes
        from the accent wash (see PanelContentBase.icon docstring), not this
        icon, so it stays a neutral Material glyph rather than the brand PNG.
        """
        return ft.Icons.SPORTS_ESPORTS

    @property
    def width(self) -> int:
        return 480

    def build(self) -> ft.Control:
        """Build the games list (or an empty state) for the panel body."""
        is_dark = self._registry.is_dark

        if not self._games_data:
            return self._build_empty_state(is_dark)

        game_tiles = [self._create_game_tile(game) for game in self._games_data]

        if len(self._games_data) > self.VIRTUALIZATION_THRESHOLD:
            # ListView with item_extent enables virtualization - only visible
            # items rendered. Bounded height keeps the panel's own outer
            # scroll area well-behaved (see _LIST_MAX_HEIGHT above).
            list_control: ft.Control = ft.ListView(
                controls=game_tiles,
                spacing=4,
                item_extent=self.GAME_TILE_HEIGHT,
                height=min(self._LIST_MAX_HEIGHT, len(game_tiles) * (self.GAME_TILE_HEIGHT + 4)),
            )
        else:
            # Small lists: direct controls, no virtualization overhead. Sized
            # naturally to content — the panel's outer content area scrolls.
            list_control = ft.Column(controls=game_tiles, spacing=4)

        return list_control

    def _build_empty_state(self, is_dark: bool) -> ft.Control:
        """
        Empty state shown for configured-but-empty launchers (paths are set
        but no games have been detected yet) — distinct from the "no paths"
        case, which never opens this panel (see LauncherCard._on_banner_clicked()).
        """
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(
                        ft.Icons.GAMEPAD_OUTLINED,
                        size=48,
                        color=MD3Colors.get_text_secondary(is_dark),
                    ),
                    ft.Text(
                        "No games detected yet",
                        size=15,
                        weight=ft.FontWeight.W_500,
                        color=MD3Colors.get_text_primary(is_dark),
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        "Run a scan to detect games in this launcher's configured folder(s).",
                        size=12,
                        color=MD3Colors.get_text_secondary(is_dark),
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.symmetric(horizontal=24, vertical=48),
        )

    def _create_dll_badge(
        self,
        dll_type: str,
        current_ver: str,
        latest_ver: str | None,
        update_available: bool,
    ) -> ft.Container:
        """
        Create a lightweight Container-based DLL badge (replaces heavy ft.Chip).

        Migrated verbatim from the old LauncherCard._create_dll_badge() —
        performance rationale (Container+Row+Icon+Text = 4 controls vs
        Chip's internal 5+) still applies here.

        Args:
            dll_type: Type of DLL (DLSS, XESS, FSR, etc.)
            current_ver: Current version string
            latest_ver: Latest available version (if update available)
            update_available: Whether an update is available

        Returns:
            ft.Container styled as a badge with icon and text
        """
        dll_type_upper = dll_type.upper()
        if dll_type_upper == "DLSS":
            badge_bgcolor = "#76B900"  # NVIDIA green
        elif dll_type_upper == "XESS":
            badge_bgcolor = "#0071C5"  # Intel blue
        elif dll_type_upper == "FSR":
            badge_bgcolor = "#ED1C24"  # AMD red
        else:
            badge_bgcolor = MD3Colors.PRIMARY  # Default

        if update_available and latest_ver:
            label_text = f"{dll_type}: {current_ver} -> {latest_ver}"
            icon_name = ft.Icons.ARROW_UPWARD
        else:
            label_text = f"{dll_type}: {current_ver}"
            icon_name = ft.Icons.CHECK

        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(icon_name, size=14, color=ft.Colors.WHITE),
                    ft.Text(
                        label_text,
                        size=11,
                        color=ft.Colors.WHITE,
                        weight=ft.FontWeight.W_500,
                    ),
                ],
                spacing=4,
                tight=True,
            ),
            bgcolor=badge_bgcolor,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=16,
            height=26,
        )

    def _create_game_tile(self, game: GameCardData) -> ft.Container:
        """
        Create a flat Container-based game tile showing DLL info as badges.

        Migrated verbatim from the old LauncherCard._create_game_tile() —
        flat Container+Row layout (vs a heavier ListTile) still applies.

        Args:
            game: GameCardData with name, path, and dlls

        Returns:
            ft.Container with game name and DLL badges in a flat layout
        """
        dll_badges = []
        has_updates = False

        for dll in game.dlls:
            if dll.update_available:
                has_updates = True

            dll_badges.append(
                self._create_dll_badge(
                    dll_type=dll.dll_type,
                    current_ver=dll.current_version,
                    latest_ver=dll.latest_version,
                    update_available=dll.update_available,
                )
            )

        if dll_badges:
            badges_content: ft.Control = ft.Row(
                controls=dll_badges,
                spacing=6,
                wrap=True,
            )
        else:
            badges_content = ft.Text(
                "No DLLs detected",
                size=11,
                color=ft.Colors.GREY,
            )

        icon_color = ft.Colors.ORANGE if has_updates else MD3Colors.PRIMARY

        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=20, color=icon_color),
                    ft.Column(
                        controls=[
                            ft.Text(
                                game.name,
                                size=13,
                                weight=ft.FontWeight.W_500,
                            ),
                            badges_content,
                        ],
                        spacing=2,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=ft.Padding.symmetric(horizontal=0, vertical=6),
        )

    async def on_save(self) -> bool:
        """
        Read-only panel — Save just closes it (mirrors ReleaseNotesPanel).

        Returns:
            True (always succeeds since there's nothing to persist)
        """
        return True
