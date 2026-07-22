"""
Game Card Component
Individual game card with Steam image, DLL badges, and action buttons
"""

import asyncio
import anyio
from pathlib import Path
from typing import Callable, Any
import flet as ft

from dlss_updater.database import GameDLL
from dlss_updater.models import Game, MergedGame
from dlss_updater.name_normalize import prettify_display_name
from dlss_updater.steam_integration import fetch_steam_image
from dlss_updater.ui_flet.theme.colors import MD3Colors, TechnologyColors
from dlss_updater.ui_flet.theme.md3_system import MD3Spacing
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.constants import DLL_GROUPS


# Full-bleed "hero" card dimensions (Option C).
# The card is a Column of [flexible banner Stack, fixed-height footer]. The banner
# EXPANDS to fill whatever vertical space the GridView cell gives (minus the fixed
# footer), so the footer can never be clipped regardless of column count / width —
# the artwork (BoxFit.COVER) simply crops taller/shorter. HERO_HEIGHT is the banner's
# *target* height at the typical cell width (used to derive the grid aspect ratio and
# as the skeleton placeholder height); the banner flexes around it, it is not a hard cap.
HERO_HEIGHT = 204  # Target banner height at the typical cell width (~320 px wide).
BANNER_HEIGHT = HERO_HEIGHT  # Skeleton placeholder height (matches the hero target)

# ---- Footer geometry (FIXED height, never wraps) ----
# The footer holds the DLL badge + compact icon-only Update/Restore buttons that
# expand to labelled buttons on hover via a WIDTH animation (height stays constant —
# height animation is a documented anti-pattern here). Because the footer height is a
# constant and the banner flexes, the total card height maps cleanly onto the grid cell.
FOOTER_CONTENT_HEIGHT = 36  # Height of the footer Row (icon buttons drive this)
FOOTER_V_PADDING = 8  # Top/bottom padding inside the footer container
FOOTER_HEIGHT = FOOTER_CONTENT_HEIGHT + FOOTER_V_PADDING * 2  # 52 px — the fixed footer band

# Compact (resting) vs expanded (hover) widths for the footer controls.
BTN_COMPACT_WIDTH = 40  # Icon-only Update/Restore button (resting)
UPDATE_EXPANDED_WIDTH = 108  # icon + "Update" + ▾ (hover)
RESTORE_EXPANDED_WIDTH = 116  # icon + "Restore" + ▾ (hover)
BADGE_FULL_WIDTH = 140  # DLL status badge with text (resting) — headroom for
# double-digit counts like "14/14 outdated" (games bundling DLSS+Streamline+
# XeSS+FSR+DirectStorage can realistically hit 10+ tracked DLLs).
BADGE_COMPACT_WIDTH = 40  # DLL status badge shrunk to icon-only (hover, frees room for labels)
FOOTER_ANIM_MS = 180  # Width/opacity animation duration for the hover expand/collapse

# ---- Hero art zoom bias ----
# Steam's library_hero.jpg banners are composed for their OWN native ~3.1:1 aspect
# ratio, with a plain/gradient zone in the top third (designed to sit behind Steam's
# own UI chrome) and the actual character art concentrated lower-center. Our card box
# is far closer to square (~1.3-1.5:1), so BoxFit.COVER scales by height and shows the
# ENTIRE source height uncropped — including that plain top zone, which reads as an
# ugly "black bar" above the art. Applying a modest zoom (scale) + upward shift
# (offset, fractional units of the image's own rendered size) crops that dead zone
# away and biases the visible window toward the art-dense lower region, without
# needing to know the actual box's pixel size (both are resolution-independent).
HERO_ART_ZOOM = 1.3  # Post-cover zoom-in; overflow is clipped by image_container.
HERO_ART_OFFSET_Y = -0.12  # Shift up 12% of rendered height (reveals more bottom art).


class GameCard(ThemeAwareMixin, ft.Card):
    """Individual game card with image, DLL info, and actions

    Note: Cannot use is_isolated=True because cards need batch updates via
    ImageLoadCoordinator which uses page.update(). Isolated controls would
    not be included in page.update() and would require individual card.update() calls.
    """

    def __init__(self, game: Game | MergedGame, dlls: list[GameDLL], page: ft.Page, logger, on_update=None, on_view_backups=None, on_restore=None, backup_groups: dict[str, list] | None = None, is_ignored: bool = False, on_ignore_toggle=None, on_resolve=None, db_manager=None):
        super().__init__()

        # Handle both Game and MergedGame
        if isinstance(game, MergedGame):
            self.merged_game = game
            self.game = game.primary_game
            self.all_paths = game.all_paths
        else:
            self.merged_game = None
            self.game = game
            self.all_paths = [game.path]

        self.dlls = dlls
        # Cache for _get_update_counts(): parse_version() over every DLL is
        # otherwise re-run on each status-row / badge / context-menu build and
        # on every per-card filter recount in GamesView. Invalidated whenever
        # self.dlls changes (see _invalidate_update_counts()).
        self._update_counts_cache: tuple[int, int, int] | None = None
        self._page_ref = page
        self.logger = logger
        self.on_update_callback = on_update
        self.on_view_backups_callback = on_view_backups
        self.on_restore_callback = on_restore
        self.on_ignore_toggle_callback = on_ignore_toggle
        self.on_resolve_callback = on_resolve
        # DB manager used by the per-game DLSS panel. Defaults to the shared
        # module-level instance the card already uses elsewhere (see _show_dll_dialog,
        # load_image). An explicit instance can be wired in from GamesView.
        if db_manager is None:
            from dlss_updater.database import db_manager as _shared_db_manager
            db_manager = _shared_db_manager
        self.db_manager = db_manager
        self.backup_groups = backup_groups or {}
        self.has_backups = bool(backup_groups)
        self.is_ignored = is_ignored

        # Button references for loading state
        self.update_button: ft.PopupMenuButton | None = None
        self.restore_button: ft.PopupMenuButton | None = None
        self.ignore_button: ft.IconButton | None = None
        self.is_updating = False

        # Hover-expand wrappers (Container with animated width) + notification dot.
        # These let the footer controls grow from icon-only (resting) to labelled
        # (hover) via a width animation — see _on_hover().
        self.update_button_wrapper: ft.Container | None = None
        self.restore_button_wrapper: ft.Container | None = None
        self.dll_badge_wrapper: ft.Container | None = None
        self.update_notification_dot: ft.Container | None = None

        # Reference to dll_badges + footer row for in-place refresh
        self.dll_badges_container: ft.Container | None = None
        self._footer_row: ft.Row | None = None

        # Async lock for UI updates to prevent race conditions
        self._ui_lock = anyio.Lock()
        self._image_loaded = False  # Prevent duplicate image loads

        # Get theme state and register
        self._registry = get_theme_registry()
        self._theme_priority = 25  # Cards are mid-priority
        is_dark = self._registry.is_dark

        # Card styling optimized for grid layout
        self.elevation = 2
        self.surface_tint_color = MD3Colors.get_primary(is_dark)
        self.margin = ft.Margin.all(0)  # ResponsiveRow handles spacing
        self.width = None  # Let ResponsiveRow control width
        self.expand = True  # Fill available space in grid cell

        # Animation (ft.Card has no shadow/animate fields — elevation drives
        # the Material shadow and animates natively; scale animates below)
        self.animate_scale = ft.Animation(200, ft.AnimationCurve.EASE_OUT)

        # Hover callback: NOT wired here — ft.Card has no on_hover field in
        # Flet 0.86 (a dataclass attribute write is silently dead), so the
        # handler is attached to the full-bleed card_body Container in
        # _build_card_content() instead.

        # Build content
        self._build_card_content()

        # Register with theme system after building UI
        self._register_theme_aware()

    def _create_skeleton_loader(self):
        """Create GPU-accelerated animated shimmer skeleton loader for image placeholder.

        Uses Flet 0.80.4 ft.Shimmer control for smooth, GPU-accelerated animation.
        This is lighter weight than custom gradient animations and provides
        consistent visual feedback while images load.
        """
        is_dark = self._registry.is_dark

        # Inner placeholder container with game icon — wide banner aspect.
        placeholder_content = ft.Container(
            expand=True,  # Fill the full-width banner
            height=BANNER_HEIGHT,  # Min height while the card lays out
            bgcolor=MD3Colors.get_themed("skeleton_base", is_dark),
            content=ft.Icon(
                ft.Icons.VIDEOGAME_ASSET,
                size=48,
                color=ft.Colors.with_opacity(0.3, ft.Colors.GREY),
            ),
            alignment=ft.Alignment.CENTER,
        )

        # Wrap in Shimmer for GPU-accelerated animation
        return ft.Shimmer(
            base_color=MD3Colors.get_themed("skeleton_start", is_dark),
            highlight_color=MD3Colors.get_themed("skeleton_highlight", is_dark),
            period=1200,  # 1.2 second animation cycle
            direction=ft.ShimmerDirection.LTR,  # Left-to-right sweep
            content=placeholder_content,
        )

    def _get_breakpoint(self) -> str:
        """Determine current breakpoint from page width"""
        if not self._page_ref or not self._page_ref.width:
            return "lg"
        width = self._page_ref.width
        if width < 576:
            return "xs"
        elif width < 768:
            return "sm"
        elif width < 992:
            return "md"
        return "lg"

    def _check_for_updates(self) -> bool:
        """Check if any DLLs have updates available"""
        outdated, _, _ = self._get_update_counts()
        return outdated > 0

    def _get_update_counts(self) -> tuple[int, int, int]:
        """Count outdated, current, and unknown DLLs.

        Result is cached on the instance and reused until self.dlls changes
        (see _invalidate_update_counts()). This method is called from the
        status row, DLL badges, the update button, the context menu and the
        GamesView filter recount — each of which would otherwise re-run
        parse_version() over every DLL on every call.

        Returns:
            Tuple of (outdated_count, current_count, unknown_count)
        """
        if self._update_counts_cache is not None:
            return self._update_counts_cache

        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        outdated = 0
        current = 0
        unknown = 0

        for dll in self.dlls:
            if not dll.current_version or not dll.dll_filename:
                unknown += 1
                continue
            latest_version = LATEST_DLL_VERSIONS.get(dll.dll_filename.lower())
            if not latest_version:
                unknown += 1
                continue
            try:
                current_parsed = parse_version(dll.current_version)
                latest_parsed = parse_version(latest_version)
                if current_parsed < latest_parsed:
                    outdated += 1
                else:
                    current += 1
            except Exception:
                unknown += 1

        self._update_counts_cache = (outdated, current, unknown)
        return self._update_counts_cache

    def _invalidate_update_counts(self) -> None:
        """Clear the cached _get_update_counts() result.

        Must be called after any mutation of self.dlls (refresh_dlls, the
        _show_dll_dialog DB refresh) so the next count reflects the new
        versions instead of the stale cache.
        """
        self._update_counts_cache = None

    def _build_dll_popover_items(self) -> list[ft.PopupMenuItem]:
        """Build popup menu items for all DLLs with color coding and update status"""
        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        is_dark = self._registry.is_dark
        dll_colors = {
            "DLSS": "#76B900", "XeSS": "#0071C5", "FSR": "#ED1C24",
            "DLSS-G": "#76B900", "DLSS-D": "#76B900",
            "Streamline": "#76B900", "DirectStorage": "#FFB900",
        }

        items = []
        for dll in self.dlls:
            color = dll_colors.get(dll.dll_type, MD3Colors.get_text_secondary(is_dark))
            version_text = dll.current_version[:12] if dll.current_version else "N/A"

            # Check for update
            update_available = False
            if dll.current_version and dll.dll_filename:
                latest = LATEST_DLL_VERSIONS.get(dll.dll_filename.lower())
                if latest:
                    try:
                        update_available = parse_version(dll.current_version) < parse_version(latest)
                    except Exception:
                        pass

            status_icon = ft.Icon(
                ft.Icons.ARROW_UPWARD if update_available else ft.Icons.CHECK_CIRCLE,
                size=14,
                color=MD3Colors.get_warning(is_dark) if update_available else MD3Colors.get_success(is_dark),
            )

            items.append(ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Container(width=10, height=10, bgcolor=color, border_radius=5),
                        ft.Text(dll.dll_type, size=12, weight=ft.FontWeight.BOLD, width=90),
                        ft.Text(version_text, size=11, color=MD3Colors.get_on_surface_variant(is_dark), width=80),
                        status_icon,
                    ],
                    spacing=8,
                    tight=True,
                ),
            ))
        return items

    def _build_scrim_gradient(self, is_dark: bool) -> ft.LinearGradient:
        """Bottom-weighted gradient scrim (legibility on ANY box art).

        Delegates to hero_surface.build_scrim_gradient so grid cards and the
        hub mosaic share one per-theme ramp — light mode confines the scrim
        to the caption band instead of veiling the artwork.
        """
        from dlss_updater.ui_flet.components.hero_surface import build_scrim_gradient

        return build_scrim_gradient(is_dark)

    def _build_card_content(self):
        """Build the full-bleed "hero" card layout (Option C).

        The entire card is a single fixed-height ``ft.Stack``:

          - Bottom layer: the artwork, positioned to FILL the whole stack
            (left/top/right/bottom=0) so it covers the full card — no grey footer.
          - Scrim layer: a bottom-weighted transparent→black gradient (also
            filling the stack) so overlaid text/buttons are legible on any art.
          - Bottom overlay (positioned bottom/left/right=0): a Column of the
            Hidden chip, title, launcher·status row, then the action Row
            (DLL badges + Update ▾ / Restore ▾).
          - Top-right overlay cluster: eye (hide/unhide), pencil (edit), kebab (⋮).

        The Play/Launch button is intentionally removed — "Launch via Steam"
        lives in the kebab / right-click menu.
        """
        is_dark = self._registry.is_dark

        # ---- Hero artwork (bottom layer, fills the whole stack) ----
        # expand=True + COVER crops the art to fill its positioned box. The box is
        # made full-size by positioning image_container left/top/right/bottom=0 in
        # the FIXED-height stack below, so the image reliably fills the card.
        self.image_widget = ft.Image(
            src="/assets/placeholder_game.png",
            expand=True,  # Fill the positioned fill-box; COVER crops to fit
            fit=ft.BoxFit.COVER,
            # Zoom in + shift up post-cover to crop out the source art's plain top
            # zone (see HERO_ART_ZOOM/HERO_ART_OFFSET_Y above). image_container's
            # clip_behavior=ANTI_ALIAS clips the resulting overflow.
            scale=HERO_ART_ZOOM,
            offset=ft.Offset(0, HERO_ART_OFFSET_Y),
            error_content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=48, color=ft.Colors.GREY),
        )

        # image_container is the swappable host (skeleton -> image) used by
        # load_image()/ImageLoadCoordinator. It expands to fill its positioned
        # fill-box in the stack.
        self.image_container = ft.Container(
            content=self._create_skeleton_loader(),  # Start with skeleton
            expand=True,
            bgcolor=MD3Colors.get_surface(is_dark),
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        # ---- Bottom-weighted gradient scrim (legibility on ANY box art) ----
        # expand=True so it fills the flexible banner stack at any height. Themed
        # (fades to the card's surface color, not hardcoded black) so it matches
        # the footer below it in both themes — see _build_scrim_gradient().
        self._scrim = ft.Container(
            expand=True,
            gradient=self._build_scrim_gradient(is_dark),
        )

        # ---- Title + launcher/status overlay (on scrim, bottom-left) ----
        # Themed: the scrim now fades to the card's own surface color (not a fixed
        # black), so the text needs to flip dark/light with it to stay legible.
        self.game_name_text = ft.Text(
            prettify_display_name(self.game.display_name),
            size=16,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=self._title_tooltip(),  # Full name + path(s) on hover
        )

        self.launcher_text = ft.Text(
            self.game.launcher,
            size=11,
            color=MD3Colors.get_on_surface_variant(is_dark),
            no_wrap=True,
        )

        self.status_separator_text = ft.Text(
            "·", size=11, color=MD3Colors.get_on_surface_variant(is_dark)
        )

        # Status dot + label (e.g. "● Needs update" / "● Up to date").
        self.status_row = self._build_status_row()

        # "Hidden" chip (only visible when ignored).
        self.hidden_chip = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.VISIBILITY_OFF, size=12, color=ft.Colors.WHITE),
                    ft.Text("Hidden", size=10, weight=ft.FontWeight.W_500, color=ft.Colors.WHITE),
                ],
                spacing=4,
                tight=True,
            ),
            bgcolor=ft.Colors.with_opacity(0.85, "#FF9800"),
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            border_radius=10,
            visible=self.is_ignored,
        )

        title_overlay = ft.Container(
            content=ft.Column(
                controls=[
                    self.hidden_chip,
                    self.game_name_text,
                    ft.Row(
                        controls=[self.launcher_text, self.status_separator_text, self.status_row],
                        spacing=6,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=2,
                tight=True,
            ),
            padding=ft.Padding.only(left=12, right=12, bottom=10),
        )

        # ---- Top-right overlay cluster: eye + pencil + kebab ----
        self.ignore_button = ft.IconButton(
            icon=ft.Icons.VISIBILITY if self.is_ignored else ft.Icons.VISIBILITY_OFF,
            icon_size=16,
            tooltip="Unignore this game" if self.is_ignored else "Ignore this game",
            on_click=self._on_ignore_clicked,
            width=28,
            height=28,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.DEFAULT: "#FF9800" if self.is_ignored else ft.Colors.WHITE,
                },
                bgcolor={
                    ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.7, "#FF9800") if self.is_ignored else ft.Colors.with_opacity(0.5, ft.Colors.BLACK),
                },
                padding=ft.Padding.all(4),
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        self.resolve_button = ft.IconButton(
            icon=ft.Icons.EDIT,
            icon_size=14,
            tooltip="Edit display" if self.game.is_manually_resolved else "Edit display (image & name)",
            on_click=self._on_resolve_clicked,
            width=28,
            height=28,
            style=ft.ButtonStyle(
                color={ft.ControlState.DEFAULT: ft.Colors.WHITE},
                bgcolor={ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.5, ft.Colors.BLACK)},
                padding=ft.Padding.all(4),
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        # Kebab (⋮) reuses the SAME item builder as right-click (single source).
        self.kebab_button = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Icon(ft.Icons.MORE_VERT, size=18, color=ft.Colors.WHITE),
                bgcolor=ft.Colors.with_opacity(0.5, ft.Colors.BLACK),
                padding=ft.Padding.all(5),
                border_radius=8,
            ),
            tooltip="More actions",
            items=self._build_context_menu_items(),
        )

        # Corner action cluster is hidden at rest and fades in on card hover (see
        # _on_hover). opacity (GPU, no layout recalc) is the sanctioned show/hide
        # animation here — height animation is a documented anti-pattern. The buttons
        # sit INSIDE the card bounds, so the mouse can only reach them while the card
        # is hovered (opacity already 1) — no stray tooltip fires over an invisible one.
        self._overlay_cluster = ft.Container(
            content=ft.Row(
                controls=[self.ignore_button, self.resolve_button, self.kebab_button],
                spacing=2,
                tight=True,
            ),
            right=4,
            top=4,
            opacity=0,
            animate_opacity=ft.Animation(FOOTER_ANIM_MS, ft.AnimationCurve.EASE_OUT),
        )

        # ---- Banner stack: image | scrim | title overlay | overlay cluster ----
        # expand=True: the banner absorbs ALL of the card's height minus the fixed
        # footer, so it grows/shrinks with the GridView cell. The image (COVER) and
        # scrim fill the stack; the title overlay anchors to the bottom edge and the
        # action cluster to the top-right, so both stay pinned as the banner flexes.
        self._banner_stack = ft.Stack(
            controls=[
                self.image_container,
                self._scrim,
                ft.Container(content=title_overlay, bottom=0, left=0, right=0),
                self._overlay_cluster,
            ],
            expand=True,
        )

        # ---- Footer: DLL badge + compact Update / Restore (expand on hover) ----
        self.dll_badges_container = self._create_dll_badges()
        self.update_button = self._create_update_popup_menu()
        self.restore_button = self._create_restore_popup_menu()

        # Apply ignored visual state
        if self.is_ignored:
            self.opacity = 0.5
            if self.update_button:
                self.update_button.disabled = True
            # Suppress the "needs update" dot while the game is ignored.
            if self.update_notification_dot is not None:
                self.update_notification_dot.visible = False

        # Each footer control lives in a fixed-HEIGHT, animated-WIDTH Container so the
        # footer band never changes height (no layout recalc / GridView reflow). On
        # hover (_on_hover) the badge shrinks to icon-only and the buttons grow to
        # reveal their labels; clip_behavior hides the overflow while collapsed.
        _anim = ft.Animation(FOOTER_ANIM_MS, ft.AnimationCurve.EASE_OUT)
        self.dll_badge_wrapper = ft.Container(
            content=self.dll_badges_container,
            width=BADGE_FULL_WIDTH,
            height=FOOTER_CONTENT_HEIGHT,
            alignment=ft.Alignment.CENTER_LEFT,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            border_radius=8,
            animate=_anim,
        )
        self.update_button_wrapper = ft.Container(
            content=self.update_button,
            width=BTN_COMPACT_WIDTH,
            height=FOOTER_CONTENT_HEIGHT,
            alignment=ft.Alignment.CENTER_LEFT,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            border_radius=8,
            animate=_anim,
        )
        self.restore_button_wrapper = ft.Container(
            content=self.restore_button,
            width=BTN_COMPACT_WIDTH,
            height=FOOTER_CONTENT_HEIGHT,
            alignment=ft.Alignment.CENTER_LEFT,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            border_radius=8,
            animate=_anim,
        )

        # Footer row controls: [badge, spacer, update, restore]. wrap=False + fixed
        # child heights guarantee a single-line, constant-height footer at any width.
        self._footer_row = ft.Row(
            controls=[
                self.dll_badge_wrapper,
                ft.Container(expand=True),  # Push the action buttons to the right
                self.update_button_wrapper,
                self.restore_button_wrapper,
            ],
            spacing=8,
            wrap=False,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        footer = ft.Container(
            content=self._footer_row,
            height=FOOTER_HEIGHT,
            padding=ft.Padding.symmetric(horizontal=12, vertical=FOOTER_V_PADDING),
        )

        # ---- Card body (vertical: flexible banner over fixed footer) ----
        # The Column FILLS the card (no tight=True): the fixed-height footer takes its
        # 52 px band and the expand=True banner absorbs everything else. GridView forces
        # the card to the cell height, so this maps the cell exactly onto banner+footer
        # with zero clipping and zero grey gap at ANY column count. expand chains through
        # ContextMenu (a LayoutControl) so the cell's height reaches the Column.
        card_body = ft.Container(
            content=ft.Column(
                controls=[
                    self._banner_stack,
                    footer,
                ],
                spacing=0,
            ),
            expand=True,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            # Hover lives here, not on the Card: ft.Card has no on_hover
            # field (assignment on it is silently dead), while Container
            # emits enter/exit with boolean e.data. card_body fills the
            # whole card, so the hover area matches the card bounds.
            on_hover=self._on_hover,
        )
        # Kept for _on_hover: the border glow must target a Container
        # (ft.Card has no border field).
        self._card_body = card_body

        # Right-click context menu wrapping the whole card body.
        # secondary_items = right mouse button; left-click children (badges,
        # overlay buttons) keep their own on_tap/on_click behaviour.
        self._context_menu = ft.ContextMenu(
            content=card_body,
            expand=True,
            secondary_items=self._build_context_menu_items(),
        )

        # Card content layout
        self.content = self._context_menu

    def _title_tooltip(self) -> str:
        """Tooltip for the title: full name plus the install path(s)."""
        name = prettify_display_name(self.game.display_name)
        if len(self.all_paths) == 1:
            return f"{name}\n{self.all_paths[0]}"
        paths = "\n".join(f"• {p}" for p in self.all_paths)
        return f"{name}\nInstallations:\n{paths}"

    def _build_status_row(self) -> ft.Row:
        """Build the '● Needs update' / '● Up to date' status indicator (on scrim).

        Amber/green are state colors (kept fixed - legible on both light and dark
        scrim tones); the neutral "No DLLs" dot and the label text are themed since
        they carry no state signal of their own.
        """
        is_dark = self._registry.is_dark
        outdated, current, unknown = self._get_update_counts()
        if not self.dlls:
            dot_color = MD3Colors.get_on_surface_variant(is_dark)
            label = "No DLLs"
        elif outdated > 0:
            dot_color = "#FFB300"  # Amber — needs update
            label = "Needs update"
        else:
            dot_color = "#69F0AE"  # Green — up to date
            label = "Up to date"

        self.status_dot = ft.Container(width=8, height=8, bgcolor=dot_color, border_radius=4)
        self.status_label = ft.Text(
            label, size=11, color=MD3Colors.get_on_surface_variant(is_dark)
        )
        return ft.Row(
            controls=[self.status_dot, self.status_label],
            spacing=4,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _refresh_status_row(self) -> None:
        """Update the scrim status dot/label after DLL versions change."""
        if not getattr(self, "status_dot", None) or not getattr(self, "status_label", None):
            return
        is_dark = self._registry.is_dark
        outdated, current, unknown = self._get_update_counts()
        if not self.dlls:
            self.status_dot.bgcolor = MD3Colors.get_on_surface_variant(is_dark)
            self.status_label.value = "No DLLs"
        elif outdated > 0:
            self.status_dot.bgcolor = "#FFB300"
            self.status_label.value = "Needs update"
        else:
            self.status_dot.bgcolor = "#69F0AE"
            self.status_label.value = "Up to date"

    def _build_context_menu_items(self) -> list[ft.PopupMenuItem]:
        """Build right-click context menu items reflecting current card state.

        Rebuilt whenever state changes (set_ignored / refresh_dlls /
        refresh_restore_button) so disabled/enabled status stays accurate.
        """
        is_dark = self._registry.is_dark
        primary = MD3Colors.get_primary(is_dark)
        success = MD3Colors.get_success(is_dark)
        warning = MD3Colors.get_warning(is_dark)

        has_outdated = self._check_for_updates()
        is_steam = self.game.launcher == "Steam" and self.game.effective_steam_app_id

        items: list[ft.PopupMenuItem] = [
            ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.UPDATE, size=16, color=primary if has_outdated and not self.is_ignored else None),
                        ft.Text("Update all DLLs", size=14),
                    ],
                    spacing=10,
                    tight=True,
                ),
                on_click=lambda e: self._on_update_group_selected("all"),
            ),
            ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.RESTORE, size=16, color=success if self.has_backups else None),
                        ft.Text("Restore from backup", size=14),
                    ],
                    spacing=10,
                    tight=True,
                ),
                on_click=lambda e: self._on_restore_group_selected("all"),
            ),
        ]

        items.append(ft.PopupMenuItem())  # Divider

        items.append(
            ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.FOLDER_OPEN, size=16),
                        ft.Text("Open folder", size=14),
                    ],
                    spacing=10,
                    tight=True,
                ),
                on_click=lambda e: self._on_open_folder(),
            )
        )
        items.append(
            ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.CONTENT_COPY, size=16),
                        ft.Text("Copy path(s)" if len(self.all_paths) > 1 else "Copy path", size=14),
                    ],
                    spacing=10,
                    tight=True,
                ),
                on_click=lambda e: self._page_ref.run_task(self._on_copy_path_clicked, None),
            )
        )

        if is_steam:
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=16, color=primary),
                            ft.Text("Launch via Steam", size=14),
                        ],
                        spacing=10,
                        tight=True,
                    ),
                    on_click=lambda e: self._launch_game(),
                )
            )

        items.append(ft.PopupMenuItem())  # Divider

        items.append(
            ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.VISIBILITY if self.is_ignored else ft.Icons.VISIBILITY_OFF,
                            size=16,
                            color=warning,
                        ),
                        ft.Text("Unignore game" if self.is_ignored else "Ignore game", size=14),
                    ],
                    spacing=10,
                    tight=True,
                ),
                on_click=lambda e: self._on_ignore_clicked(None),
            )
        )
        items.append(
            ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.EDIT, size=16),
                        ft.Text("Edit display (image & name)", size=14),
                    ],
                    spacing=10,
                    tight=True,
                ),
                on_click=lambda e: self._on_resolve_clicked(None),
            )
        )

        # Per-game DLSS preset override (Windows + NVIDIA only).
        try:
            from dlss_updater import nvapi_drs
            dlss_available = nvapi_drs.is_available()
        except Exception:
            dlss_available = False
        if dlss_available:
            items.append(
                ft.PopupMenuItem(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.AUTO_AWESOME, size=16, color=primary),
                            ft.Text("DLSS Settings", size=14),
                        ],
                        spacing=10,
                        tight=True,
                    ),
                    on_click=lambda e: self._on_dlss_settings_clicked(),
                )
            )

        return items

    def _refresh_context_menu(self) -> None:
        """Rebuild context menu + kebab items to reflect current card state.

        Both the right-click menu and the ⋮ kebab share the same item builder so
        they stay a single source of truth.
        """
        if getattr(self, "_context_menu", None):
            self._context_menu.secondary_items = self._build_context_menu_items()
        if getattr(self, "kebab_button", None):
            self.kebab_button.items = self._build_context_menu_items()

    def _on_open_folder(self):
        """Open the game's install folder in the OS file explorer."""
        import os
        import subprocess
        import sys

        if not self.all_paths:
            return
        path = self.all_paths[0]
        try:
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            if not folder or not os.path.isdir(folder):
                self.logger.warning(f"Folder does not exist: {folder}")
                return
            if sys.platform == "win32":
                os.startfile(folder)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as ex:
            self.logger.warning(f"Failed to open folder: {ex}")

    def _create_dll_badges(self) -> ft.Container:
        """Create condensed DLL badge that opens grouped dialog on click"""
        is_dark = self._registry.is_dark

        # Edge case: No DLLs
        if not self.dlls:
            return ft.Container(
                content=ft.Text("0 DLLs", size=10, color=MD3Colors.get_themed("text_tertiary", is_dark)),
                bgcolor=MD3Colors.get_themed("badge_default_bg", is_dark),
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=8,
                height=28,
            )

        dll_count = len(self.dlls)
        outdated, current, unknown = self._get_update_counts()
        has_updates = outdated > 0

        # Build badge text with update status detail
        if has_updates:
            badge_text = f"{outdated}/{dll_count} outdated"
            badge_color = MD3Colors.get_warning(is_dark)
            badge_icon = ft.Icons.ARROW_UPWARD
            status_tooltip = f"{outdated} outdated, {current} current"
            if unknown:
                status_tooltip += f", {unknown} unknown"
        else:
            badge_text = f"{dll_count} DLL{'s' if dll_count != 1 else ''} current"
            badge_color = MD3Colors.get_success(is_dark)
            badge_icon = ft.Icons.CHECK_CIRCLE_OUTLINE
            status_tooltip = f"All {current} DLLs are up to date"
            if unknown:
                status_tooltip += f" ({unknown} unknown)"

        # PERFORMANCE: Flattened badge structure (GestureDetector > Container > Row)
        # Reduced from Container > GestureDetector > Container > Row to GestureDetector > Container
        return ft.GestureDetector(
            content=ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(
                            badge_icon,
                            size=14,
                            color=ft.Colors.WHITE,
                        ),
                        ft.Text(
                            badge_text,
                            size=11,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.WHITE,
                            no_wrap=True,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=4,
                    tight=True,
                ),
                bgcolor=badge_color,
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=8,
                height=28,
                tooltip=f"{status_tooltip} - click for details",
            ),
            on_tap=lambda e: self._page_ref.run_task(self._show_dll_dialog),
            mouse_cursor=ft.MouseCursor.CLICK,
        )

    def _get_dll_groups_for_game(self) -> list[str]:
        """Get DLL groups with at least one outdated DLL (unknown versions count as updatable)."""
        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        groups_present = set()
        for dll in self.dlls:
            dll_filename = dll.dll_filename.lower() if dll.dll_filename else ""
            if not dll_filename:
                continue

            needs_update = True
            if dll.current_version:
                latest = LATEST_DLL_VERSIONS.get(dll_filename)
                if latest:
                    try:
                        if parse_version(dll.current_version) >= parse_version(latest):
                            needs_update = False
                    except Exception:
                        pass
            if not needs_update:
                continue

            for group_name, group_dlls in DLL_GROUPS.items():
                if dll_filename in [d.lower() for d in group_dlls]:
                    groups_present.add(group_name)
                    break
        return sorted(list(groups_present))

    def _create_update_popup_menu(self) -> ft.PopupMenuButton:
        """Create popup menu button for selective DLL updates.

        Menu items are populated upfront since Flet's on_open fires after rendering.
        """
        is_dark = self._registry.is_dark
        has_outdated = self._check_for_updates()
        disabled_color = MD3Colors.get_themed("text_tertiary", is_dark)
        active_color = MD3Colors.get_primary(is_dark)
        color = active_color if has_outdated else disabled_color

        # Store references for theming.
        self.update_button_icon = ft.Icon(ft.Icons.UPDATE, size=20, color=color)
        # Amber notification dot overlaid on the icon — carries the "needs update"
        # signal in the compact/icon-only state where the "Update" label is hidden.
        # Ringed with the surface colour so it reads clearly on the update icon.
        self.update_notification_dot = ft.Container(
            width=9,
            height=9,
            border_radius=5,
            bgcolor="#FFB300",  # Matches the scrim "Needs update" dot
            border=ft.Border.all(1.5, MD3Colors.get_surface(is_dark)),
            visible=has_outdated,
            right=0,
            top=1,
        )
        icon_cell = ft.Stack(
            controls=[
                ft.Container(
                    content=self.update_button_icon,
                    width=24,
                    height=24,
                    alignment=ft.Alignment.CENTER,
                ),
                self.update_notification_dot,
            ],
            width=24,
            height=24,
        )
        # opacity=0 while collapsed; _on_hover fades it in as the wrapper widens.
        self.update_button_text = ft.Text(
            "Update",
            size=13,
            color=color,
            no_wrap=True,
            opacity=0,
            animate_opacity=ft.Animation(FOOTER_ANIM_MS, ft.AnimationCurve.EASE_OUT),
        )
        self.update_button_arrow = ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color=color)

        return ft.PopupMenuButton(
            content=ft.Row(
                controls=[
                    icon_cell,
                    self.update_button_text,
                    self.update_button_arrow,
                ],
                spacing=2,
                tight=True,
            ),
            tooltip="Select DLLs to update" if has_outdated else "All DLLs are up to date",
            items=self._build_update_menu_items(),
            disabled=not has_outdated,
        )

    def _build_update_menu_items(self) -> list[ft.PopupMenuItem]:
        """Build update menu items — only groups with at least one outdated DLL appear."""
        is_dark = self._registry.is_dark
        groups = self._get_dll_groups_for_game()

        if not groups:
            return []

        menu_items = [
            ft.PopupMenuItem(
                content="Update All",
                icon=ft.Icons.UPDATE,
                on_click=lambda e: self._on_update_group_selected("all"),
            ),
            ft.PopupMenuItem(),  # Divider
        ]

        for group in groups:
            color = TechnologyColors.get_themed_color(group, is_dark)
            menu_items.append(
                ft.PopupMenuItem(
                    content=ft.Row(
                        controls=[
                            ft.Container(
                                width=8,
                                height=8,
                                bgcolor=color,
                                border_radius=4,
                            ),
                            ft.Text(f"Update {group}", size=14),
                        ],
                        spacing=8,
                    ),
                    on_click=lambda e, g=group: self._on_update_group_selected(g),
                )
            )

        return menu_items

    def _on_update_group_selected(self, group: str):
        """Handle DLL group selection from popup menu"""
        if self.is_updating:
            return
        if self.on_update_callback:
            # Pass both game and selected group
            self.on_update_callback(self.game, group)

    def _create_restore_popup_menu(self) -> ft.PopupMenuButton:
        """Create popup menu button for selective DLL restore from backups.

        Menu items are populated upfront since Flet's on_open fires after rendering.
        """
        is_dark = self._registry.is_dark
        disabled_color = MD3Colors.get_themed("text_tertiary", is_dark)
        success_color = MD3Colors.get_success(is_dark)

        # Colour of the restore icon is the at-a-glance state signal in the compact
        # (icon-only) footer: success/green when backups exist, muted when they don't.
        color = success_color if self.backup_groups else disabled_color

        self.restore_button_icon = ft.Icon(ft.Icons.RESTORE, size=20, color=color)
        # opacity=0 while collapsed; _on_hover fades it in as the wrapper widens.
        self.restore_button_text = ft.Text(
            "Restore",
            size=13,
            color=color,
            no_wrap=True,
            opacity=0,
            animate_opacity=ft.Animation(FOOTER_ANIM_MS, ft.AnimationCurve.EASE_OUT),
        )
        self.restore_button_arrow = ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=18, color=color)

        restore_content = ft.Row(
            controls=[
                ft.Container(
                    content=self.restore_button_icon,
                    width=24,
                    height=24,
                    alignment=ft.Alignment.CENTER,
                ),
                self.restore_button_text,
                self.restore_button_arrow,
            ],
            spacing=2,
            tight=True,
        )

        if not self.backup_groups:
            # Disabled button if no backups (icon stays muted).
            return ft.PopupMenuButton(
                content=restore_content,
                tooltip="No backups available",
                items=[],
                disabled=True,
            )

        return ft.PopupMenuButton(
            content=restore_content,
            tooltip="Restore DLLs from backup",
            items=self._build_restore_menu_items(),
        )

    def _build_restore_menu_items(self) -> list[ft.PopupMenuItem]:
        """Build the actual restore menu items (extracted from _create_restore_popup_menu)."""
        is_dark = self._registry.is_dark
        groups = sorted(self.backup_groups.keys())

        menu_items = [
            ft.PopupMenuItem(
                content="Restore All",
                icon=ft.Icons.RESTORE,
                on_click=lambda e: self._on_restore_group_selected("all"),
            ),
        ]

        if groups:
            menu_items.append(ft.PopupMenuItem())  # Divider

            for group in groups:
                color = TechnologyColors.get_themed_color(group, is_dark)
                backup_count = len(self.backup_groups[group])
                menu_items.append(
                    ft.PopupMenuItem(
                        content=ft.Row(
                            controls=[
                                ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
                                ft.Text(f"Restore {group} ({backup_count})", size=14),
                            ],
                            spacing=8,
                        ),
                        on_click=lambda e, g=group: self._on_restore_group_selected(g),
                    )
                )

        return menu_items

    def _on_restore_group_selected(self, group: str):
        """Handle DLL group selection from restore popup menu"""
        if self.on_restore_callback:
            self.on_restore_callback(self.game, group)

    def _launch_game(self):
        """Launch the game via Steam protocol."""
        import webbrowser
        import subprocess
        import sys

        if not self.game.effective_steam_app_id:
            return

        url = f"steam://rungameid/{self.game.effective_steam_app_id}"
        try:
            if sys.platform == "linux":
                subprocess.Popen(
                    ["xdg-open", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                webbrowser.open(url)
        except Exception as ex:
            self.logger.warning(f"Failed to launch game: {ex}")

    def _on_dlss_settings_clicked(self):
        """Open the per-game DLSS preset override panel."""
        if self._page_ref:
            self._page_ref.run_task(self._show_dlss_settings_panel)

    async def _show_dlss_settings_panel(self):
        from dlss_updater.ui_flet.components.slide_panel import PanelManager
        from dlss_updater.ui_flet.panels.per_game_dlss_panel import PerGameDLSSPanel

        panel_manager = PanelManager.get_instance(self._page_ref, self.logger)
        panel = PerGameDLSSPanel(self._page_ref, self.logger, self.game, self.db_manager)
        await panel_manager.show_content(panel)

    def _on_resolve_clicked(self, e):
        """Open the Steam resolve dialog."""
        if self._page_ref:
            self._page_ref.run_task(self._show_resolve_dialog)

    async def _show_resolve_dialog(self):
        from dlss_updater.ui_flet.dialogs.steam_resolve_dialog import SteamResolveDialog
        dialog = SteamResolveDialog(
            page=self._page_ref,
            logger=self.logger,
            game=self.game,
            on_resolved=self._on_resolved,
        )
        await dialog.show()

    def _on_resolved(self, override_steam_app_id: int, display_name_override: str):
        """Called by dialog after a successful save or clear."""
        if self._page_ref:
            self._page_ref.run_task(
                self.apply_resolution, override_steam_app_id, display_name_override
            )

    async def apply_resolution(self, override_steam_app_id: int, display_name_override: str):
        """Update card UI after the user links or clears a Steam override.

        Resets the image, updates the title, and refreshes the link button icon.
        Also fires on_resolve_callback so GamesView can reload the full card if needed.
        """
        async with self._ui_lock:
            # Update in-memory game fields (Game is a non-frozen msgspec.Struct)
            if override_steam_app_id:
                self.game.override_steam_app_id = override_steam_app_id
                self.game.display_name_override = display_name_override or None
            else:
                self.game.override_steam_app_id = None
                self.game.display_name_override = None

            # Refresh title text (prettified for display only — see prettify_display_name)
            self.game_name_text.value = prettify_display_name(self.game.display_name)
            self.game_name_text.tooltip = self._title_tooltip()

            # Refresh resolve button tooltip
            self.resolve_button.tooltip = "Edit display" if self.game.is_manually_resolved else "Edit display (image & name)"

            # Reset image so it reloads with the new (or cleared) app_id
            self._image_loaded = False
            self.image_container.content = self._create_skeleton_loader()
            self.image_container.opacity = 1.0

        self.update()

        # Reload image for new app_id (non-blocking)
        if self.game.effective_steam_app_id:
            task = asyncio.create_task(self.load_image())
            task_name = f"resolve_image_{self.game.name[:15]}"
            try:
                from dlss_updater.ui_flet.task_registry import register_task
                register_task(task, task_name)
            except Exception:
                pass

        # Notify GamesView so it can persist any coordinated state
        if self.on_resolve_callback:
            self.on_resolve_callback(
                self.merged_game if self.merged_game else self.game,
                override_steam_app_id,
                display_name_override,
            )

    async def _show_dll_dialog(self):
        """Show the grouped DLL dialog with technology categories"""
        from dlss_updater.ui_flet.dialogs.dll_group_dialog import DLLGroupDialog
        from dlss_updater.database import db_manager

        # Refresh DLL versions and backup groups from DB before showing dialog
        # so the dialog reflects any updates/restores that happened since last render.
        try:
            refreshed_dlls = await db_manager.refresh_dll_versions_for_game(self.game.id)
            if refreshed_dlls:
                self.dlls = refreshed_dlls
                self._invalidate_update_counts()
                self.logger.debug(f"Refreshed {len(refreshed_dlls)} DLL versions for {self.game.name}")
        except Exception as e:
            self.logger.warning(f"Failed to refresh DLL versions: {e}")

        try:
            self.backup_groups = await db_manager.get_backups_grouped_by_dll_type(self.game.id)
            self.has_backups = bool(self.backup_groups)
        except Exception as e:
            self.logger.warning(f"Failed to refresh backup groups: {e}")

        # Determine which game object to pass (MergedGame or Game)
        game_to_show = self.merged_game if self.merged_game else self.game

        dialog = DLLGroupDialog(
            page=self._page_ref,
            logger=self.logger,
            game=game_to_show,
            dlls=self.dlls,
            backup_groups=self.backup_groups,
            on_update=self._on_dialog_update,
            on_restore=self._on_dialog_restore,
        )
        await dialog.show()

    def _on_dialog_update(self, game, group: str):
        """Handle update request from grouped DLL dialog"""
        if self.is_updating:
            return
        if self.on_update_callback:
            self.on_update_callback(game, group)

    def _on_dialog_restore(self, game, group: str):
        """Handle restore request from grouped DLL dialog"""
        if self.on_restore_callback:
            self.on_restore_callback(game, group)

    async def load_image(self, prefetched_path: str | None = None, coordinator: Any | None = None):
        """Async load Steam image with fade-in animation.

        Args:
            prefetched_path: Optional pre-fetched cached path from batch query.
                            If provided, skips database lookup for better performance.
            coordinator: Optional ImageLoadCoordinator for batched UI updates.
                        If provided, updates are batched for ~5x better performance.
                        Falls back to direct _fade_in_image() if not provided.
        """
        # Prevent duplicate image loads
        if self._image_loaded:
            return

        effective_app_id = self.game.effective_steam_app_id
        if not effective_app_id:
            self.logger.debug(f"No Steam app ID for {self.game.name}, skipping image")
            return

        try:
            from dlss_updater.database import db_manager
            from pathlib import Path

            # Use prefetched path or check cache
            cached_path = prefetched_path
            if cached_path is None:
                cached_path = await db_manager.get_cached_image_path(effective_app_id)

            image_path = None

            if cached_path:
                # Validate file exists before using
                try:
                    if Path(cached_path).exists():
                        image_path = cached_path
                        self.logger.debug(f"Using cached image for {self.game.name}")
                except OSError:
                    pass  # File access error, will fetch fresh

            # Fetch from Steam CDN if no valid cached path
            if image_path is None:
                self.logger.info(f"Fetching Steam image for {self.game.name} (app_id: {effective_app_id})")
                fetched_path = await fetch_steam_image(effective_app_id)
                if fetched_path:
                    image_path = str(fetched_path)
                    self.logger.info(f"Successfully fetched image for {self.game.name}")

            if image_path:
                if coordinator:
                    # Batched update via coordinator (preferred for concurrent loading)
                    await coordinator.schedule_image_update(self, image_path)
                else:
                    # Fallback: direct update (for single card refresh operations)
                    await self._fade_in_image(image_path)
                    self._image_loaded = True
            else:
                self.logger.debug(f"No image available for {self.game.name}")

        except Exception as e:
            self.logger.error(f"Error loading image for {self.game.name}: {e}", exc_info=True)

    async def _fade_in_image(self, image_path: str):
        """Fade in image smoothly with minimal UI updates.

        Optimized for Flet 0.80.4 performance:
        - Single 100ms wait for control attachment (vs 5x50ms retry loop)
        - Maximum 2 update calls (vs 6-7 previously)
        - Uses page.update() which is more reliable for batch operations

        Note: Lock is released during sleep to avoid blocking other concurrent UI updates.
        This is important for Python 3.14 free-threading compatibility.
        """
        # Phase 1: Set up image and prepare for fade-in (under lock)
        async with self._ui_lock:
            # Check if page is still available (may be None if view was closed)
            if not self._page_ref:
                return

            # Update image source and prepare fade-in animation
            self.image_widget.src = image_path
            self.image_container.opacity = 0
            self.image_container.animate_opacity = ft.Animation(300, ft.AnimationCurve.EASE_IN)
            self.image_container.content = self.image_widget

        # Single wait for control to be added to page (Flet 0.80.4 is faster)
        await anyio.sleep(0.1)

        # Phase 2: Trigger fade-in with single card update
        async with self._ui_lock:
            if not self._page_ref:
                return
            # Set final opacity and trigger animation
            self.image_container.opacity = 1
            try:
                # Use self.update() — card-level update (isolated GamesView)
                self.update()
            except Exception:
                pass  # Page may be closing

    def _on_ignore_clicked(self, e):
        """Toggle ignore status and notify parent."""
        new_state = not self.is_ignored
        if self.on_ignore_toggle_callback:
            self.on_ignore_toggle_callback(
                self.merged_game if self.merged_game else self.game,
                new_state
            )

    def set_ignored(self, ignored: bool):
        """Update ignore visual state. Called by GamesView after DB confirms the change."""
        self.is_ignored = ignored
        self.opacity = 0.5 if ignored else 1.0
        if self.ignore_button:
            self.ignore_button.icon = ft.Icons.VISIBILITY if ignored else ft.Icons.VISIBILITY_OFF
            self.ignore_button.tooltip = "Unignore this game" if ignored else "Ignore this game"
            self.ignore_button.style = ft.ButtonStyle(
                color={
                    ft.ControlState.DEFAULT: "#FF9800" if ignored else ft.Colors.WHITE,
                },
                bgcolor={
                    ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.7, "#FF9800") if ignored else ft.Colors.with_opacity(0.5, ft.Colors.BLACK),
                },
                padding=ft.Padding.all(4),
                shape=ft.RoundedRectangleBorder(radius=8),
            )
        if self.update_button:
            self.update_button.disabled = ignored
        # Dot follows: shown only when there are updates AND the game isn't ignored.
        if self.update_notification_dot is not None:
            self.update_notification_dot.visible = self._check_for_updates() and not ignored
        if getattr(self, "hidden_chip", None):
            self.hidden_chip.visible = ignored
        self._refresh_context_menu()

    def _on_hover(self, e):
        """Handle hover effect with multi-layer shadow and border glow"""
        import time
        start = time.perf_counter()

        is_dark = self._registry.is_dark
        primary_color = MD3Colors.get_primary(is_dark)
        hovering = e.data is True or e.data == "true"
        # elevation/scale are real ft.Card fields; shadow/border are NOT
        # (dead dataclass attribute writes) — the border glow targets the
        # card_body Container instead.
        if hovering:
            self.elevation = 8
            self.scale = 1.015
            self._card_body.border = ft.Border.all(1, ft.Colors.with_opacity(0.3, primary_color))
        else:
            self.elevation = 2
            self.scale = 1.0
            self._card_body.border = None

        # Footer expand/collapse — WIDTH animation only (height stays constant, so no
        # layout recalc and no GridView reflow). On hover: shrink the badge to icon-only
        # and grow the buttons to reveal their labels + ▾; fade the labels in/out.
        if self.update_button_wrapper is not None:
            self.update_button_wrapper.width = UPDATE_EXPANDED_WIDTH if hovering else BTN_COMPACT_WIDTH
        if self.restore_button_wrapper is not None:
            self.restore_button_wrapper.width = RESTORE_EXPANDED_WIDTH if hovering else BTN_COMPACT_WIDTH
        if self.dll_badge_wrapper is not None:
            self.dll_badge_wrapper.width = BADGE_COMPACT_WIDTH if hovering else BADGE_FULL_WIDTH
        if self.update_button_text is not None:
            self.update_button_text.opacity = 1 if hovering else 0
        if self.restore_button_text is not None:
            self.restore_button_text.opacity = 1 if hovering else 0

        # Corner action cluster (eye / pencil / kebab) fades in on hover only.
        if getattr(self, "_overlay_cluster", None) is not None:
            self._overlay_cluster.opacity = 1 if hovering else 0

        start_update = time.perf_counter()
        self.update()
        update_ms = (time.perf_counter() - start_update) * 1000
        total_ms = (time.perf_counter() - start) * 1000

        # Only log if slow (>30ms)
        if total_ms > 30:
            from dlss_updater.ui_flet.perf_monitor import perf_logger
            perf_logger.warning(f"[SLOW] card_hover: update={update_ms:.1f}ms, total={total_ms:.1f}ms")

    def set_updating(self, is_updating: bool):
        """Set updating state - shows spinner and disables button.

        Uses the stored icon/text/arrow references (the button content is now an
        icon-Stack + label + arrow, so positional row indexing no longer applies).
        """
        is_dark = self._registry.is_dark
        self.is_updating = is_updating
        color = MD3Colors.get_text_secondary(is_dark) if is_updating else MD3Colors.get_primary(is_dark)
        if self.update_button_icon is not None:
            self.update_button_icon.name = ft.Icons.HOURGLASS_TOP if is_updating else ft.Icons.UPDATE
            self.update_button_icon.color = color
        if self.update_button_text is not None:
            self.update_button_text.color = color
        if self.update_button_arrow is not None:
            self.update_button_arrow.color = color
        # Hide the "needs update" dot while an update is in flight.
        if self.update_notification_dot is not None and is_updating:
            self.update_notification_dot.visible = False
        if self.update_button:
            self.update_button.disabled = is_updating
            self.update_button.update()

    async def refresh_dlls(self, new_dlls: list[GameDLL]):
        """Refresh DLL badges and update button with new data after update/restore."""
        async with self._ui_lock:
            self.dlls = new_dlls
            self._invalidate_update_counts()

            # Rebuild the DLL badges
            new_badges = self._create_dll_badges()

            # Swap the badge inside its animated wrapper (keeps the hover-collapse
            # wrapper — and its current width state — intact).
            if self.dll_badge_wrapper is not None:
                self.dll_badge_wrapper.content = new_badges
            self.dll_badges_container = new_badges

            # Refresh the scrim status dot/label (Needs update / Up to date)
            self._refresh_status_row()

            # Rebuild update button in-place so menu items and enabled state reflect
            # current DLL versions. Keep disabled if game is ignored.
            if self.update_button:
                has_outdated = self._check_for_updates()
                is_dark = self._registry.is_dark
                disabled_color = MD3Colors.get_themed("text_tertiary", is_dark)
                active_color = MD3Colors.get_primary(is_dark)
                color = active_color if has_outdated else disabled_color

                self.update_button.items = self._build_update_menu_items()
                self.update_button.disabled = self.is_ignored or not has_outdated
                self.update_button.tooltip = (
                    "Select DLLs to update" if has_outdated else "All DLLs are up to date"
                )
                if self.update_button_icon:
                    self.update_button_icon.color = color
                if self.update_button_text:
                    self.update_button_text.color = color
                if self.update_button_arrow:
                    self.update_button_arrow.color = color
                # Show/hide the compact-state "needs update" dot.
                if self.update_notification_dot is not None:
                    self.update_notification_dot.visible = has_outdated and not self.is_ignored

            self._refresh_context_menu()

            if self._footer_row:
                self._footer_row.update()

    async def refresh_restore_button(self, new_backup_groups: dict[str, list]):
        """Refresh restore button with new backup data after restore"""
        async with self._ui_lock:
            self.backup_groups = new_backup_groups
            self.has_backups = bool(new_backup_groups)

            # Rebuild restore button
            new_restore_button = self._create_restore_popup_menu()

            # Swap the button inside its animated wrapper (preserves the wrapper's
            # current hover-width state). _create_restore_popup_menu resets the label
            # opacity to 0 (collapsed) which is correct for the resting state.
            if self.restore_button_wrapper is not None:
                self.restore_button_wrapper.content = new_restore_button
                self.restore_button = new_restore_button
                self.restore_button_wrapper.update()
            self._refresh_context_menu()

    async def _on_copy_path_clicked(self, e):
        """Copy game path(s) to clipboard with snackbar confirmation"""
        is_dark = self._registry.is_dark

        # Copy all paths, one per line
        copy_content = "\n".join(self.all_paths)

        try:
            await ft.Clipboard().set(copy_content)

            if len(self.all_paths) > 1:
                message = f"{len(self.all_paths)} paths copied to clipboard"
            else:
                message = "Path copied to clipboard"

            self._page_ref.show_dialog(ft.SnackBar(
                content=ft.Text(message),
                bgcolor=MD3Colors.get_themed("snackbar_bg", is_dark),
            ))
        except Exception as ex:
            self.logger.warning(f"Clipboard operation failed: {ex}")
            self._page_ref.show_dialog(ft.SnackBar(
                content=ft.Text("Failed to copy to clipboard"),
                bgcolor=MD3Colors.get_error(is_dark),
            ))

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware updates.

        Returns:
            Dict mapping property paths to (dark_value, light_value) tuples.
        """
        return {
            # Card surface tint
            "surface_tint_color": MD3Colors.get_themed_pair("primary"),
            # Image container (visible behind the banner image while loading)
            "image_container.bgcolor": MD3Colors.get_themed_pair("surface"),
            # Scrim fades to the card's surface color (not hardcoded black), so it
            # blends into the footer below it in both themes.
            "_scrim.gradient": (
                self._build_scrim_gradient(True),
                self._build_scrim_gradient(False),
            ),
            # Text overlaid on the scrim — flips with it to stay legible.
            "game_name_text.color": (
                MD3Colors.get_text_primary(True), MD3Colors.get_text_primary(False)
            ),
            "launcher_text.color": (
                MD3Colors.get_on_surface_variant(True), MD3Colors.get_on_surface_variant(False)
            ),
            "status_separator_text.color": (
                MD3Colors.get_on_surface_variant(True), MD3Colors.get_on_surface_variant(False)
            ),
            "status_label.color": (
                MD3Colors.get_on_surface_variant(True), MD3Colors.get_on_surface_variant(False)
            ),
            # Update button colors
            "update_button_icon.color": MD3Colors.get_themed_pair("primary"),
            "update_button_text.color": MD3Colors.get_themed_pair("primary"),
            "update_button_arrow.color": MD3Colors.get_themed_pair("primary"),
            # Restore button colors (when enabled - success color)
            "restore_button_icon.color": MD3Colors.get_themed_pair("success"),
            "restore_button_text.color": MD3Colors.get_themed_pair("success"),
            "restore_button_arrow.color": MD3Colors.get_themed_pair("success"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay.

        Overrides ThemeAwareMixin.apply_theme to also rebuild menu items with
        correct theme colors.

        Args:
            is_dark: Whether dark mode is active
            delay_ms: Milliseconds to wait before applying (for cascade effect)
        """
        # Rebuild menu items with new theme colors
        if self.update_button:
            self.update_button.items = self._build_update_menu_items()
        if self.restore_button and not self.restore_button.disabled:
            self.restore_button.items = self._build_restore_menu_items()

        # Rebuild context menu items with new theme colors
        self._refresh_context_menu()

        # status_dot is state-colored (amber/green are fixed regardless of theme) —
        # only the neutral "No DLLs" case is themed, so it's handled here rather
        # than via the declarative get_themed_properties() pair, which would
        # otherwise clobber an amber/green dot with the neutral color on every
        # theme toggle.
        if getattr(self, "status_dot", None) is not None and not self.dlls:
            self.status_dot.bgcolor = MD3Colors.get_on_surface_variant(is_dark)

        # Call parent implementation for standard themed property updates
        await super().apply_theme(is_dark, delay_ms)
