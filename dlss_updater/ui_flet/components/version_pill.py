"""
Version pill for the app bar — doubles as the application update affordance.

Three visual states in one slot next to the app title:

* **Idle** — ``v<version>`` on a hairline-bordered surface chip.
* **Update available** — an accent-tinted action chip, ``v<current> -> <latest>`` with a
  download glyph and a dismiss affordance.
* **Working** — a progress ring plus percentage while downloading/verifying,
  resolving to a terminal action ("Restart & install" on Windows, "Show
  download" on Linux, where the Flatpak sandbox cannot install for the user).

**State deliberately does not live in this control.** The app bar is plain
Containers/Text rather than theme-aware controls, so a theme toggle rebuilds it
wholesale (``MainView._rebuild_app_bar_for_theme`` -> ``_create_app_bar`` ->
``_build_version_pill``) and constructs a brand-new pill. Anything held on the
widget would be lost - flipping the theme mid-download would silently reset the
badge to idle. ``UpdateBadgeState`` is therefore owned by MainView and shared
into each pill instance by reference, so a rebuilt pill renders whatever the
update flow had reached.

Animation is opacity/scale/offset only. Width and height are never animated:
per CLAUDE.md that forces a full layout recalculation, whereas these three are
GPU-composited.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import flet as ft
import msgspec

from dlss_updater.self_update import SelfUpdateStage, UpdateInfo
from dlss_updater.ui_flet.components.hero_surface import PILL_HEIGHT
from dlss_updater.ui_flet.theme.colors import Animations, MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin
from dlss_updater.version import __version__

# One shimmer sweep on first appearance draws the eye without the CPU cost and
# nagging quality of a permanent pulse.
_SHIMMER_PERIOD_MS = 1200
_SHIMMER_SWEEPS = 1

# Delay before the pop-in animation's target values are applied. The initial
# (transparent, slightly scaled and offset) values have to reach the client in
# their own flush first, otherwise there is nothing to animate from.
_POP_IN_DELAY_S = 0.05


class UpdateBadgeState(msgspec.Struct):
    """Update flow state, owned by MainView and shared into each pill instance.

    Held outside the widget so it survives the app bar being rebuilt on a theme
    toggle - see the module docstring.
    """

    stage: SelfUpdateStage = SelfUpdateStage.IDLE
    info: UpdateInfo | None = None
    fraction: float = 0.0
    message: str = ""
    downloaded_path: Path | None = None
    # Whether the pop-in animation has already played, so a rebuild (theme
    # toggle, navigation heal) doesn't replay it and draw attention twice.
    announced: bool = False

    @property
    def shows_update(self) -> bool:
        """Whether the pill should render its update-related appearance."""
        return self.stage in (
            SelfUpdateStage.AVAILABLE,
            SelfUpdateStage.DOWNLOADING,
            SelfUpdateStage.VERIFYING,
            SelfUpdateStage.READY,
            SelfUpdateStage.INSTALLING,
            SelfUpdateStage.FAILED,
        )


class VersionPill(ThemeAwareMixin, ft.Container):
    """The app bar's version chip, which becomes the update affordance."""

    # Chrome animates late in the cascade - it is peripheral to the content the
    # user is actually looking at.
    _theme_priority = 65

    def __init__(
        self,
        state: UpdateBadgeState,
        is_dark: bool,
        *,
        on_action: Callable[[], Awaitable[None]] | None = None,
        on_dismiss: Callable[[], Awaitable[None]] | None = None,
        on_show_release_notes: Callable[[], Awaitable[None]] | None = None,
        supported: bool = True,
        applies_in_place: bool = True,
    ) -> None:
        super().__init__()
        self.state = state
        self._is_dark = is_dark
        self._on_action = on_action
        self._on_dismiss = on_dismiss
        self._on_show_release_notes = on_show_release_notes
        self._supported = supported
        self._applies_in_place = applies_in_place

        self.height = PILL_HEIGHT
        self.border_radius = 16
        self.padding = ft.Padding.symmetric(horizontal=10, vertical=4)
        self.alignment = ft.Alignment.CENTER

        # Colour/appearance transitions ride the same curve as the rest of the
        # chrome; the pop-in below reuses these.
        self.animate_opacity = Animations.FADE
        self.animate_scale = Animations.SCALE
        self.animate_offset = Animations.NORMAL
        self.animate = Animations.HOVER
        self.on_click = self._handle_click

        self._render()
        self._register_theme_aware()

    def is_isolated(self) -> bool:
        """Exclude this subtree from the parent's ``page.update()`` digest.

        Download progress ticks update only the pill; without isolation each
        tick would serialise the entire control tree (CLAUDE.md's documented
        "high-frequency update component" case for isolation).
        """
        return True

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """No declarative mapping — colours are derived per state in ``_render``."""
        return {}

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Re-derive the whole pill for the new theme, then cascade normally."""
        self._is_dark = is_dark
        self._render()
        await super().apply_theme(is_dark, delay_ms)

    # -------------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------------

    def _render(self) -> None:
        """Rebuild content, colours and tooltip from the current state."""
        is_dark = self._is_dark
        stage = self.state.stage

        if not self._supported or not self.state.shows_update:
            self._render_idle(is_dark)
        elif stage in (SelfUpdateStage.DOWNLOADING, SelfUpdateStage.VERIFYING,
                       SelfUpdateStage.INSTALLING):
            self._render_working(is_dark)
        elif stage is SelfUpdateStage.READY:
            self._render_ready(is_dark)
        elif stage is SelfUpdateStage.FAILED:
            self._render_failed(is_dark)
        else:
            self._render_available(is_dark)

    def _render_idle(self, is_dark: bool) -> None:
        """``v<version>`` — a quiet, hairline-bordered surface chip."""
        self.bgcolor = MD3Colors.get_surface_container(is_dark)
        self.border = ft.Border.all(1, MD3Colors.get_outline(is_dark))
        self.gradient = None
        self.tooltip = (
            f"DLSS Updater {__version__}"
            if not self._supported
            else f"DLSS Updater {__version__} - click for release notes"
        )
        self.scale = 1.0
        self.opacity = 1.0
        self.offset = ft.Offset(0, 0)
        self.content = ft.Text(
            f"v{__version__}",
            size=11,
            weight=ft.FontWeight.W_500,
            color=MD3Colors.get_on_surface_variant(is_dark),
            no_wrap=True,
        )

    def _render_available(self, is_dark: bool) -> None:
        """``v<current> -> <latest>`` with a download glyph and a dismiss affordance."""
        accent = MD3Colors.get_primary(is_dark)
        latest = self.state.info.latest_version if self.state.info else ""

        self.bgcolor = accent
        self.border = None
        self.gradient = None
        self.tooltip = f"Version {latest} is available - click to update"
        self.content = ft.Row(
            controls=[
                ft.Icon(ft.Icons.FILE_DOWNLOAD, size=14, color=ft.Colors.WHITE),
                ft.Text(
                    f"v{__version__} → {latest}",
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=ft.Colors.WHITE,
                    no_wrap=True,
                ),
                self._build_dismiss(),
            ],
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _render_working(self, is_dark: bool) -> None:
        """A progress ring plus percentage while fetching or verifying."""
        accent = MD3Colors.get_primary(is_dark)
        fraction = self.state.fraction
        stage = self.state.stage

        if stage is SelfUpdateStage.VERIFYING:
            label = "Verifying"
        elif stage is SelfUpdateStage.INSTALLING:
            label = "Installing"
        elif fraction > 0:
            label = f"{int(fraction * 100)}%"
        else:
            label = "Starting"

        self.bgcolor = accent
        self.border = None
        self.gradient = None
        self.tooltip = self.state.message or label
        self.content = ft.Row(
            controls=[
                ft.ProgressRing(
                    width=12,
                    height=12,
                    stroke_width=2,
                    color=ft.Colors.WHITE,
                    # Determinate while a fraction is known, indeterminate for
                    # the phases that have no measurable progress.
                    value=fraction if (fraction > 0 and stage is SelfUpdateStage.DOWNLOADING) else None,
                ),
                ft.Text(
                    label,
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=ft.Colors.WHITE,
                    no_wrap=True,
                ),
            ],
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _render_ready(self, is_dark: bool) -> None:
        """Terminal action — install on Windows, reveal the bundle on Linux."""
        success = MD3Colors.get_success(is_dark)
        latest = self.state.info.latest_version if self.state.info else ""

        if self._applies_in_place:
            label = "Restart & install"
            icon = ft.Icons.RESTART_ALT
            tooltip = f"Restart to install {latest}"
        else:
            label = "Show download"
            icon = ft.Icons.FOLDER_OPEN
            tooltip = f"{latest} downloaded - click to open its folder"

        self.bgcolor = success
        self.border = None
        self.gradient = None
        self.tooltip = tooltip
        self.content = ft.Row(
            controls=[
                ft.Icon(icon, size=14, color=ft.Colors.WHITE),
                ft.Text(
                    label,
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=ft.Colors.WHITE,
                    no_wrap=True,
                ),
                self._build_dismiss(),
            ],
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _render_failed(self, is_dark: bool) -> None:
        """Error tint with a retry action."""
        self.bgcolor = MD3Colors.get_error(is_dark)
        self.border = None
        self.gradient = None
        self.tooltip = self.state.message or "Update failed - click to retry"
        self.content = ft.Row(
            controls=[
                ft.Icon(ft.Icons.REFRESH, size=14, color=ft.Colors.WHITE),
                ft.Text(
                    "Retry update",
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=ft.Colors.WHITE,
                    no_wrap=True,
                ),
                self._build_dismiss(),
            ],
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_dismiss(self) -> ft.Control:
        """Small inline dismiss affordance.

        A bare Container rather than an IconButton: IconButton brings its own
        padding, ink and constraints and would not fit a 26px pill, and this
        keeps the control count down (CLAUDE.md's badge pattern).
        """
        return ft.Container(
            content=ft.Icon(ft.Icons.CLOSE, size=12, color=ft.Colors.WHITE70),
            on_click=self._handle_dismiss,
            tooltip="Dismiss until the next release",
            border_radius=10,
            padding=ft.Padding.all(1),
        )

    # -------------------------------------------------------------------------
    # State transitions
    # -------------------------------------------------------------------------

    async def refresh(self, *, animate_in: bool = False) -> None:
        """Re-render from the shared state and push it to the client.

        ``self.update()`` rather than a page update: the pill is isolated, so
        this serialises only its own subtree.
        """
        self._render()

        if animate_in and not self.state.announced:
            self.state.announced = True
            await self._pop_in()
            return

        self._safe_update()

    async def _pop_in(self) -> None:
        """Fade, scale and slide the pill in, with a single shimmer sweep."""
        # Phase 1: pre-animation values must land in their own flush, otherwise
        # the client has nothing to interpolate from.
        self.opacity = 0.0
        self.scale = 0.85
        self.offset = ft.Offset(-0.15, 0)
        # `loop` bounds the sweep count natively, so the wrapper never has to be
        # swapped back out - which also avoids a same-class child swap that the
        # client can drop after a theme cascade (CLAUDE.md pitfall 3).
        self.content = ft.Shimmer(
            base_color=MD3Colors.get_primary(self._is_dark),
            highlight_color=ft.Colors.WHITE24,
            period=_SHIMMER_PERIOD_MS,
            direction=ft.ShimmerDirection.LTR,
            loop=_SHIMMER_SWEEPS,
            content=self.content,
        )
        self._safe_update()

        await asyncio.sleep(_POP_IN_DELAY_S)

        # Phase 2: target values — animate_* on the container does the rest.
        self.opacity = 1.0
        self.scale = 1.0
        self.offset = ft.Offset(0, 0)
        self._safe_update()

    def _safe_update(self) -> None:
        """Update this control, tolerating not being attached yet.

        A pill built during ``_create_app_bar`` has no page until the bar is
        swapped in, and an update before then raises rather than no-opping.
        """
        try:
            self.update()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------------

    async def _handle_click(self, e) -> None:
        if not self.state.shows_update or not self._supported:
            if self._on_show_release_notes is not None:
                await self._on_show_release_notes()
            return

        # Ignore clicks while work is already in flight, so a double click
        # cannot start two downloads.
        if self.state.stage in (
            SelfUpdateStage.DOWNLOADING,
            SelfUpdateStage.VERIFYING,
            SelfUpdateStage.INSTALLING,
        ):
            return

        if self._on_action is not None:
            await self._on_action()

    async def _handle_dismiss(self, e) -> None:
        # No propagation guard needed: a tap is claimed by the innermost
        # hit-tested gesture handler, so the outer pill's on_click does not also
        # fire for a click landing on this control.
        if self._on_dismiss is not None:
            await self._on_dismiss()
