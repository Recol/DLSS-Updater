"""
Launcher Card Component
Banner-card grid tile showing launcher configuration status. Detected games
have moved to the slide panel (LauncherGamesPanel) — see hero_surface.py
for the shared banner/pill/wash primitives this design reuses.

Hero design (Option B — "banner card"): the card is a Column of
[fixed-height banner Stack, fixed-height footer]. The banner carries a
strong brand-color wash + a large circular brand-glyph badge + the
launcher's name/status; the footer holds path chips (horizontally
scrollable), the compact "Add Sub-Folder" button, and the kebab menu.
Tapping the banner opens LauncherGamesPanel (or the browse dialog when
unconfigured) — see game_card.py's banner+footer anatomy for the idiom
this mirrors.
"""

import logging
import subprocess
from pathlib import Path
from typing import Callable

import anyio
import flet as ft

from dlss_updater.concurrency_limiters import thread_io
from dlss_updater.config import LauncherPathName, config_manager
from dlss_updater.platform_utils import IS_WINDOWS, IS_LINUX
from dlss_updater.models import GameCardData, MAX_PATHS_PER_LAUNCHER
from dlss_updater.ui_flet.theme.colors import MD3Colors, LauncherColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.components.hero_surface import build_brand_wash, build_pill
from dlss_updater.ui_flet.components.slide_panel import PanelManager

# ==================== BANNER GEOMETRY ====================
# Fixed banner height (top identity zone) — big enough for the large brand
# badge + two lines of identity text without cramping either.
BANNER_HEIGHT = 132

# Large circular brand badge diameter. Full-bleed (BoxFit.COVER, no inset) —
# see _build_brand_badge() for why this fixes the square-tile-in-circle clash
# the old inset avatar had (GOG / Battle.net square tiles fighting an 8px
# inner radius inside a circular frame).
_BADGE_SIZE = 84

# Banner wash opacity — noticeably stronger than the old row wash (0.15/0.10)
# since the banner IS the card's brand identity now, not a subtle tint behind
# an ExpansionTile row. Tuned to sit between hero_surface's row-wash defaults
# and hub_card's full-strength hero wash.
_BANNER_WASH_OPACITY_DARK = 0.42
_BANNER_WASH_OPACITY_LIGHT = 0.26

# Opacity applied to the whole shell when the launcher has no path configured.
_UNCONFIGURED_OPACITY = 0.6

# ==================== FOOTER GEOMETRY ====================
# Fixed footer height, matching game_card.py's 52px footer idiom (there:
# FOOTER_CONTENT_HEIGHT=36 + FOOTER_V_PADDING=8*2=52). Launcher chips carry
# more visual weight (text + remove-X) than game_card's icon buttons, so the
# content band is a touch taller; the footer NEVER grows regardless of chip
# count — see the horizontally-scrollable chip strip in _build_footer().
FOOTER_CONTENT_HEIGHT = 40
FOOTER_V_PADDING = 8
FOOTER_HEIGHT = FOOTER_CONTENT_HEIGHT + FOOTER_V_PADDING * 2  # 56px

# ==================== BRAND ICON RESOLUTION ====================
# Real brand PNGs live in `dlss_updater/icons/`. Resolved relative to this
# package file (not cwd) so it survives a frozen PyInstaller build: the specs
# bundle the whole `dlss_updater` package as a data dir (`('dlss_updater',
# 'dlss_updater')` in DLSS_Updater.spec / DLSS_Updater_MSI.spec, equivalent
# `dlss_updater/icons/*.png -> icons/` copy on Linux), so `dlss_updater/icons`
# ships intact under `_internal/dlss_updater/icons` next to this very file.
# No existing resource_path()/_MEIPASS helper was found elsewhere in the
# codebase (checked main.py, utils.py, exe_resolver.py) — this is the first
# bundled-asset lookup of its kind, so it establishes the pattern locally.
_ICONS_DIR = Path(__file__).resolve().parent.parent.parent / "icons"

# Launcher key (LauncherPathName.name, e.g. "STEAM") -> brand PNG filename.
# Mirrors LauncherColors._MAPPING's key convention for consistency. Custom
# folders (CUSTOM1-4) are intentionally absent — they have no brand and keep
# the Material "folder" glyph.
_BRAND_ICON_FILES: dict[str, str] = {
    "STEAM": "steam.png",
    "EA": "ea.png",
    "EPIC": "epic.png",
    "UBISOFT": "ubisoft.png",
    "GOG": "gog.png",
    "BATTLENET": "battlenet.png",
    "XBOX": "xbox.png",
}

# Defensive fallback for the (unexpected) case where launcher_key doesn't hit
# _BRAND_ICON_FILES directly — substring-matched against the display name,
# case-insensitively. Phrases are chosen to avoid false positives (e.g. bare
# "ea" would match inside unrelated words).
_BRAND_NAME_HINTS: list[tuple[str, str]] = [
    ("steam", "steam.png"),
    ("epic", "epic.png"),
    ("gog", "gog.png"),
    ("ubisoft", "ubisoft.png"),
    ("xbox", "xbox.png"),
    ("battle.net", "battlenet.png"),
    ("battlenet", "battlenet.png"),
    ("ea games", "ea.png"),
    ("ea launcher", "ea.png"),
]


def _resolve_brand_icon_path(launcher_key: str, display_name: str) -> str | None:
    """
    Resolve the absolute path to a launcher's brand PNG, or ``None`` when
    there isn't one (custom folders, or an unrecognized launcher — both fall
    back to the existing Material icon rendering, unchanged).
    """
    filename = _BRAND_ICON_FILES.get(launcher_key.upper())
    if filename is None:
        lowered = display_name.lower()
        for hint, hinted_filename in _BRAND_NAME_HINTS:
            if hint in lowered:
                filename = hinted_filename
                break
    if filename is None:
        return None
    path = _ICONS_DIR / filename
    return str(path) if path.exists() else None


class LauncherCard(ThemeAwareMixin, ft.Container):
    """
    Banner-card grid tile for launcher configuration.

    Card shell: `self` (a Container) provides the rounded, clipped card body
    — a Column of [banner, footer]. The banner (`self._banner`) carries the
    brand wash + large circular badge + identity text and is itself the tap
    target that opens the detected-games slide panel (LauncherGamesPanel).
    The footer (`self._footer`) holds path chips, Add Sub-Folder, and the
    kebab menu.

    Performance: Uses is_isolated=True to prevent parent update() from including
    this control's changes. Must call self.update() manually for changes.
    """

    def is_isolated(self):
        """Isolated controls are excluded from parent's update digest."""
        return True

    def __init__(
        self,
        name: str,
        launcher_enum: LauncherPathName,
        icon: str,
        is_custom: bool,
        on_reset: Callable,
        on_add_subfolder: Callable,  # New: callback for adding sub-folder
        page: ft.Page,
        logger: logging.Logger,
        on_path_removed: Callable | None = None,
    ):
        # Store instance variables
        self.name_str = name
        self.launcher_enum = launcher_enum
        self.icon_name = icon
        self.is_custom = is_custom

        # Real brand PNG for this launcher (None for custom folders / unknown
        # launchers, which keep the Material `icon_name` glyph throughout).
        self._brand_icon_path: str | None = _resolve_brand_icon_path(
            launcher_enum.name, name
        )
        self.on_reset_callback = on_reset
        self.on_add_subfolder_callback = on_add_subfolder
        self._page_ref = page
        self.logger = logger
        self.on_path_removed_callback = on_path_removed

        # Track whether control has been added to the page
        self._attached = False

        # State - multi-path support
        self.current_paths: list[str] = []  # List of configured paths
        self.games_count: int = 0
        self.games_data: list[GameCardData] = []  # Detected games, handed to
        # LauncherGamesPanel when the banner is tapped (see _open_games_panel).

        # Get theme registry and state
        self._registry = get_theme_registry()
        self._theme_priority = 25  # Cards are mid-priority
        is_dark = self._registry.is_dark

        # Brand color (used by banner wash, badge fill, and the panel accent)
        self.brand_color = LauncherColors.get_color(self.launcher_enum.name)

        # Game-count pill ref (populated/toggled by _update_header_pill();
        # None both before the first set_paths()/set_games() call and
        # whenever unconfigured).
        self.game_count_pill: ft.Container | None = None

        # Build banner + footer
        self._banner = self._build_banner(is_dark)
        self._footer = self._build_footer(is_dark)

        card_body = ft.Column(
            controls=[self._banner, self._footer],
            spacing=0,
        )

        # Outer card shell: rounded, clipped Container wrapping banner+footer.
        super().__init__(
            content=card_body,
            border_radius=12,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            bgcolor=MD3Colors.get_surface(is_dark),
            animate_opacity=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

        # Dim the shell if unconfigured (current_paths is empty at this point)
        self._apply_configured_state(is_dark)

        # Apply custom styling for custom launchers
        if self.is_custom:
            self.border = ft.Border.all(1, MD3Colors.get_primary(is_dark))

        # Register for theme updates
        self._register_theme_aware()

    def did_mount(self):
        """Called when control is added to the page."""
        self._attached = True

    @property
    def name(self) -> str:
        """Property for backward compatibility - returns the launcher name"""
        return self.name_str

    # ==================== CONFIGURED / DIMMED STATE ====================

    def _current_banner_wash_opacity(self, is_dark: bool) -> float:
        """Banner wash opacity for the current configured state (halved when unconfigured)."""
        base = _BANNER_WASH_OPACITY_DARK if is_dark else _BANNER_WASH_OPACITY_LIGHT
        return base if self.current_paths else base / 2

    def _apply_configured_state(self, is_dark: bool) -> None:
        """
        Dim the whole shell when no path is configured; restore full strength
        once one is. Hooked from set_paths() — the single call site main_view
        already uses for every path add/remove/reset/auto-detect, so runtime
        (re)configuration is covered without extra plumbing.
        """
        configured = bool(self.current_paths)
        self._banner.gradient = build_brand_wash(
            self.brand_color, is_dark, opacity=self._current_banner_wash_opacity(is_dark)
        )
        self.opacity = 1.0 if configured else _UNCONFIGURED_OPACITY

    # ==================== BANNER ====================

    def _build_brand_badge(self, is_dark: bool, size: int = _BADGE_SIZE) -> ft.Container:
        """
        Large circular brand badge for the banner.

        ICON FIX: the brand PNGs are square tiles. Rendering them full-bleed
        (BoxFit.COVER, no inset padding, no inner radius) inside a
        border_radius=size/2 clipped Container makes the tile's own
        background become the circle fill — no square corners poking out,
        no colored circle showing behind an inset image (the old avatar's
        GOG/Battle.net clash). Custom folders keep a Material glyph in a
        brand-colored filled circle, unchanged in spirit from before.
        """
        if self._brand_icon_path:
            content: ft.Control = ft.Image(
                src=self._brand_icon_path,
                width=size,
                height=size,
                fit=ft.BoxFit.COVER,
            )
            bgcolor = None  # tile's own background fills the circle
        else:
            content = ft.Icon(self.icon_name, size=round(size * 0.45), color=ft.Colors.WHITE)
            bgcolor = self.brand_color

        return ft.Container(
            content=content,
            width=size,
            height=size,
            bgcolor=bgcolor,
            border_radius=size / 2,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            alignment=ft.Alignment.CENTER,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                offset=ft.Offset(0, 3),
                color=ft.Colors.with_opacity(0.35, ft.Colors.BLACK),
            ),
        )

    def _build_banner(self, is_dark: bool) -> ft.Container:
        """
        Build the fixed-height banner: brand wash + large circular badge
        (center-right) + game-count pill (top-left) + name/status identity
        (bottom-left). The whole banner is the tap target that opens
        LauncherGamesPanel (or the browse dialog when unconfigured) — see
        _on_banner_clicked().
        """
        self._badge = self._build_brand_badge(is_dark)
        badge_layer = ft.Container(
            content=self._badge,
            alignment=ft.Alignment.CENTER_RIGHT,
            padding=ft.Padding.only(right=18),
            expand=True,
        )

        # Game-count pill — top-left, refreshed by _update_header_pill().
        # Empty (no controls) when unconfigured, matching the old "hidden
        # entirely" behavior.
        self._pill_row = ft.Row(controls=[], vertical_alignment=ft.CrossAxisAlignment.CENTER)
        pill_layer = ft.Container(
            content=self._pill_row,
            left=0,
            top=0,
            right=0,
            padding=ft.Padding.only(left=16, top=14),
        )

        # Identity — bottom-left. `right` reserves room so the name/status
        # text ellipsizes before it would ever overlap the badge, rather
        # than the two visually colliding.
        self.title_text = ft.Text(
            self.name_str,
            size=17,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_on_surface(is_dark),
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.status_icon = ft.Icon(
            ft.Icons.INFO_OUTLINE,
            color=ft.Colors.GREY,
            size=14,
        )
        self.path_text = ft.Text(
            "No paths configured",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        status_row = ft.Row(
            controls=[self.status_icon, self.path_text],
            spacing=4,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        identity_col = ft.Column(
            controls=[self.title_text, status_row],
            spacing=3,
            tight=True,
        )
        identity_layer = ft.Container(
            content=identity_col,
            left=0,
            bottom=0,
            right=_BADGE_SIZE + 24,  # keep clear of the badge
            padding=ft.Padding.only(left=16, right=8, bottom=14),
        )

        banner_stack = ft.Stack(
            controls=[badge_layer, pill_layer, identity_layer],
            expand=True,
        )

        return ft.Container(
            content=banner_stack,
            height=BANNER_HEIGHT,
            bgcolor=MD3Colors.get_surface(is_dark),
            gradient=build_brand_wash(
                self.brand_color, is_dark, opacity=self._current_banner_wash_opacity(is_dark)
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            ink=True,
            on_click=self._on_banner_clicked,
            tooltip="View detected games" if self.current_paths else "Add a game folder",
        )

    async def _on_banner_clicked(self, e):
        """
        Banner tap: unconfigured launchers open the same browse flow as the
        old empty-state "add folder" zone (no dead-feeling click); configured
        launchers open LauncherGamesPanel (empty-state message inside the
        panel itself when paths are set but no games have been detected yet).
        """
        if not self.current_paths:
            await self.on_add_subfolder_callback(e)
            return
        await self._open_games_panel()

    async def _open_games_panel(self):
        """Open the slide panel showing this launcher's detected games."""
        from dlss_updater.ui_flet.panels.launcher_games_panel import LauncherGamesPanel

        panel = LauncherGamesPanel(
            page=self._page_ref,
            logger=self.logger,
            launcher_name=self.name_str,
            accent=self.brand_color,
            games_data=self.games_data,
        )
        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        await panel_manager.show_content(panel)

    def _update_header_pill(self) -> None:
        """
        Refresh the banner's top-left game-count pill.

        Hidden entirely when unconfigured (no paths) — the dimmed shell
        already communicates "not set up" without a redundant badge. Uses a
        translucent black scrim (not a theme surface color) for the neutral
        "0 games" state so it stays legible against the banner's brand wash
        at any strength/theme — mirrors game_card.py's overlay-cluster
        buttons (ignore/resolve/kebab), which use the same idiom for
        controls that sit on top of variable-color art.
        """
        if not self.current_paths:
            self._pill_row.controls = []
            self.game_count_pill = None
            return
        label = f"{self.games_count} game{'s' if self.games_count != 1 else ''}"
        if self.games_count == 0:
            self.game_count_pill = build_pill(
                label,
                bgcolor=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
                text_color=ft.Colors.WHITE,
            )
        else:
            self.game_count_pill = build_pill(label, bgcolor=self.brand_color)
        self._pill_row.controls = [self.game_count_pill]

    # ==================== FOOTER ====================

    def _build_footer(self, is_dark: bool) -> ft.Container:
        """
        Build the fixed-height footer: horizontally-scrollable path chips
        (never grows the footer regardless of chip count) + compact Add
        Sub-Folder + kebab menu. Mirrors game_card.py's fixed-footer idiom
        (wrap=False row, fixed-height wrappers).
        """
        # Path chips row — scroll=AUTO instead of wrap=True (the old
        # ExpansionTile subtitle behavior) so an arbitrary number of chips
        # never grows the footer's fixed height; it scrolls sideways instead.
        self.paths_row = ft.Row(
            controls=[],
            spacing=6,
            wrap=False,
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        chips_area = ft.Container(
            content=self.paths_row,
            expand=True,
            height=FOOTER_CONTENT_HEIGHT,
            alignment=ft.Alignment.CENTER_LEFT,
        )

        # Compact icon-only Add Sub-Folder (tooltip carries the label — the
        # footer has no room for a labelled TextButton alongside chips+kebab).
        self.add_subfolder_btn = ft.IconButton(
            icon=ft.Icons.CREATE_NEW_FOLDER,
            icon_size=18,
            icon_color=MD3Colors.PRIMARY,
            tooltip="Add Sub-Folder",
            on_click=self.on_add_subfolder_callback,
            style=ft.ButtonStyle(padding=ft.Padding.all(6)),
            width=32,
            height=32,
        )

        # Kebab menu — every action from Option A preserved. The standalone
        # "Reset" TextButton is dropped (it duplicated "Clear All" below;
        # see report) to make room in the tight fixed-height footer.
        open_folder_text = "Open in Explorer" if IS_WINDOWS else "Open in File Manager"
        self.config_menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_color=MD3Colors.get_on_surface_variant(is_dark),
            tooltip="More options",
            items=[
                ft.PopupMenuItem(content="Copy Path(s)", icon=ft.Icons.CONTENT_COPY, on_click=self._on_copy_paths),
                ft.PopupMenuItem(content=open_folder_text, icon=ft.Icons.FOLDER_OPEN, on_click=self._on_open_explorer),
                ft.PopupMenuItem(content="Auto-Detect", icon=ft.Icons.AUTO_FIX_HIGH, on_click=self._on_auto_detect),
                ft.PopupMenuItem(),  # Divider
                ft.PopupMenuItem(content="Clear All", icon=ft.Icons.DELETE_OUTLINE, on_click=self.on_reset_callback),
            ],
        )

        footer_row = ft.Row(
            controls=[chips_area, self.add_subfolder_btn, self.config_menu],
            spacing=2,
            wrap=False,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        return ft.Container(
            content=footer_row,
            height=FOOTER_HEIGHT,
            padding=ft.Padding.symmetric(horizontal=10, vertical=FOOTER_V_PADDING),
            border=ft.Border.only(top=ft.BorderSide(1, MD3Colors.get_divider(is_dark))),
        )

    def _create_path_chip(self, path: str, is_dark: bool) -> ft.Container:
        """
        Create a removable chip for a path.

        Args:
            path: The path to display
            is_dark: Whether dark theme is active

        Returns:
            Container with path chip and remove button
        """
        # Truncate long paths for display
        display_path = path
        if len(path) > 35:
            display_path = f"{path[:12]}...{path[-18:]}"

        # Create handler that properly schedules the async coroutine
        def on_remove_click(e, p=path):
            self._page_ref.run_task(self._on_remove_path, p)

        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(
                        display_path,
                        size=11,
                        color=MD3Colors.get_on_surface(is_dark),
                        tooltip=path,  # Show full path on hover
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=14,
                        icon_color=MD3Colors.get_on_surface_variant(is_dark),
                        on_click=on_remove_click,
                        tooltip="Remove path",
                        style=ft.ButtonStyle(
                            padding=ft.Padding.all(2),
                        ),
                        width=20,
                        height=20,
                    ),
                ],
                spacing=2,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            border_radius=16,
            padding=ft.Padding.only(left=10, right=4, top=4, bottom=4),
        )

    async def _on_remove_path(self, path: str):
        """
        Handle removing a path from this launcher.

        Args:
            path: The path to remove
        """
        self.logger.info(f"Removing path from {self.name_str}: {path}")

        # Remove from config
        config_manager.remove_launcher_path(self.launcher_enum, path)

        # Update local state
        if path in self.current_paths:
            self.current_paths.remove(path)

        # Update UI
        await self._update_paths_display()
        self._apply_configured_state(self._registry.is_dark)
        if self._attached:
            self.update()

        # Notify parent to show rescan prompt (with undo context)
        if self.on_path_removed_callback:
            await self.on_path_removed_callback(self.name_str, self.launcher_enum, path)

    async def _update_paths_display(self):
        """Update the path chips + status line + banner tooltip."""
        is_dark = self._registry.is_dark

        # Create path chips (footer strip — no add button mixed in anymore,
        # it's a fixed sibling in footer_row; see _build_footer()).
        path_chips = [self._create_path_chip(p, is_dark) for p in self.current_paths]

        # Enable/disable add button based on path limit
        at_limit = len(self.current_paths) >= MAX_PATHS_PER_LAUNCHER
        self.add_subfolder_btn.disabled = at_limit
        self.add_subfolder_btn.tooltip = (
            f"Maximum {MAX_PATHS_PER_LAUNCHER} paths reached" if at_limit else "Add Sub-Folder"
        )

        self.paths_row.controls = path_chips

        # Update path status text + combined status/validity icon (folds the
        # old separate status_icon + path_health_icon into one — see
        # _build_banner()).
        path_count = len(self.current_paths)
        if path_count == 0:
            self.path_text.value = "No paths configured"
            self.path_text.color = MD3Colors.get_on_surface_variant(is_dark)
            self.status_icon.name = ft.Icons.INFO_OUTLINE
            self.status_icon.color = ft.Colors.GREY
            self.status_icon.tooltip = None
        else:
            all_valid = await self._validate_paths_async()
            self.path_text.value = f"{path_count} path{'s' if path_count != 1 else ''} configured"
            self.path_text.color = MD3Colors.get_on_surface(is_dark)
            if all_valid:
                self.status_icon.name = ft.Icons.CHECK_CIRCLE
                self.status_icon.color = MD3Colors.get_success(is_dark)
                self.status_icon.tooltip = "All paths valid"
            else:
                self.status_icon.name = ft.Icons.WARNING_AMBER
                self.status_icon.color = MD3Colors.get_warning(is_dark)
                self.status_icon.tooltip = "Some paths inaccessible"

        # Refresh the header pill and banner tooltip to match the (possibly
        # changed) path count
        self._update_header_pill()
        self._banner.tooltip = "View detected games" if self.current_paths else "Add a game folder"

        if self._attached:
            self.update()

    async def set_paths(self, paths: list[str]):
        """
        Update the launcher paths (multi-path support).

        Args:
            paths: List of paths configured for this launcher
        """
        self.current_paths = paths if paths else []

        # Get theme state
        is_dark = self._registry.is_dark

        # Update paths display (also refreshes the header pill + tooltip)
        await self._update_paths_display()

        # Dim/restore the hero shell for the (possibly new) configured state
        self._apply_configured_state(is_dark)

        if self._attached:
            self.update()

    # Keep set_path for backward compatibility
    async def set_path(self, path: str):
        """
        Update the launcher path (legacy single-path method).

        For backward compatibility. Converts single path to list.

        Args:
            path: The path to set
        """
        if path:
            await self.set_paths([path])
        else:
            await self.set_paths([])

    async def set_games(self, games_data: list[GameCardData]):
        """
        Store the detected game list for this launcher and refresh the
        game-count pill.

        Tile-building has moved to LauncherGamesPanel (opened by tapping the
        banner — see _open_games_panel()); this method only keeps the data
        for that panel to consume and updates the lightweight count pill,
        matching CLAUDE.md's "content detachment" spirit — no offscreen
        control tree is built until the user actually asks to see it.

        Args:
            games_data: List of GameCardData (name, path, dlls) — see
                main_view._populate_launcher_cards() for how this is built.
        """
        self.games_data = games_data or []
        self.games_count = len(self.games_data)

        self._update_header_pill()

        if self._attached:
            self.update()

    def _validate_paths_sync(self) -> bool:
        """Check if all paths exist (sync version for filesystem I/O)"""
        if not self.current_paths:
            return True
        return all(Path(p).exists() for p in self.current_paths)

    async def _validate_paths_async(self) -> bool:
        """Check if all paths exist (async version - offloads blocking I/O to thread pool)"""
        if not self.current_paths:
            return True
        return await anyio.to_thread.run_sync(self._validate_paths_sync, limiter=thread_io)

    async def _on_copy_paths(self, e):
        """Copy all configured paths to clipboard"""
        if self.current_paths:
            paths_text = "\n".join(self.current_paths)
            try:
                await ft.Clipboard().set(paths_text)
                self._page_ref.snack_bar = ft.SnackBar(ft.Text("Paths copied to clipboard"))
                self._page_ref.snack_bar.open = True
                self._page_ref.update()
            except Exception as ex:
                self.logger.warning(f"Clipboard operation failed: {ex}")
                self._page_ref.snack_bar = ft.SnackBar(
                    ft.Text("Failed to copy to clipboard"),
                    bgcolor=ft.Colors.ERROR,
                )
                self._page_ref.snack_bar.open = True
                self._page_ref.update()

    async def _on_open_explorer(self, e):
        """Open first path in file manager (cross-platform, non-blocking)"""
        if self.current_paths:
            path = self.current_paths[0]
            try:
                if IS_WINDOWS:
                    import os
                    # Wrap blocking os.startfile in thread pool
                    await anyio.to_thread.run_sync(os.startfile, path, limiter=thread_io)
                elif IS_LINUX:
                    # Non-blocking subprocess; xdg-open exits as soon as it has
                    # handed the path to the file manager
                    await anyio.run_process(
                        ['xdg-open', path],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            except Exception as ex:
                self.logger.error(f"Failed to open path in file manager: {ex}")
                self._page_ref.snack_bar = ft.SnackBar(ft.Text(f"Could not open: {path}"))
                self._page_ref.snack_bar.open = True
                self._page_ref.update()

    async def _on_auto_detect(self, e):
        """Attempt to auto-detect launcher path (offloads blocking scan to thread pool)"""
        from dlss_updater.scanner import auto_detect_launcher_path, auto_detect_steam_library_paths

        if self.launcher_enum == LauncherPathName.STEAM:
            # Steam: detect all library folders via libraryfolders.vdf
            detected_paths = await anyio.to_thread.run_sync(
                auto_detect_steam_library_paths, limiter=thread_io
            )
            if detected_paths:
                added_count = 0
                for path in detected_paths:
                    if config_manager.add_launcher_path(self.launcher_enum, path):
                        self.current_paths.append(path)
                        added_count += 1
                if added_count > 0:
                    await self._update_paths_display()
                    self._apply_configured_state(self._registry.is_dark)
                    msg = f"Detected {added_count} Steam library path(s)"
                else:
                    msg = "All detected paths already configured"
                self._page_ref.snack_bar = ft.SnackBar(ft.Text(msg))
            else:
                self._page_ref.snack_bar = ft.SnackBar(ft.Text("Could not auto-detect Steam paths"))
        else:
            # Other launchers: single registry-based detection
            detected = await anyio.to_thread.run_sync(
                auto_detect_launcher_path, self.launcher_enum, limiter=thread_io
            )
            if detected:
                added = config_manager.add_launcher_path(self.launcher_enum, detected)
                if added:
                    self.current_paths.append(detected)
                    await self._update_paths_display()
                    self._apply_configured_state(self._registry.is_dark)
                    self._page_ref.snack_bar = ft.SnackBar(ft.Text(f"Detected: {detected}"))
                else:
                    self._page_ref.snack_bar = ft.SnackBar(ft.Text("Path already configured or at limit"))
            else:
                self._page_ref.snack_bar = ft.SnackBar(ft.Text("Could not auto-detect path"))
        self._page_ref.snack_bar.open = True
        self._page_ref.update()
        if self._attached:
            self.update()

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay - rebuilds banner wash/badge, footer border, and text colors."""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        try:
            self.bgcolor = MD3Colors.get_surface(is_dark)

            # Rebuild the banner wash + badge for the new theme, respecting
            # the current configured/unconfigured dimming state.
            self._apply_configured_state(is_dark)
            self._banner.bgcolor = MD3Colors.get_surface(is_dark)
            self._badge = self._build_brand_badge(is_dark)
            # badge_layer is the first control in the banner's Stack
            self._banner.content.controls[0].content = self._badge

            # Identity text colors
            self.title_text.color = MD3Colors.get_on_surface(is_dark)
            self.path_text.color = MD3Colors.get_on_surface_variant(is_dark)

            # Footer border + kebab icon color
            self._footer.border = ft.Border.only(top=ft.BorderSide(1, MD3Colors.get_divider(is_dark)))
            if hasattr(self, 'config_menu'):
                self.config_menu.icon_color = MD3Colors.get_on_surface_variant(is_dark)

            # Custom launchers get a themed border on the outer shell
            if self.is_custom:
                self.border = ft.Border.all(1, MD3Colors.get_primary(is_dark))

            # Update path chips + status icon/text by rebuilding the display
            # (also refreshes the header pill)
            await self._update_paths_display()

            if hasattr(self, 'update'):
                self.update()
        except Exception:
            pass  # Silent fail - component may have been garbage collected
