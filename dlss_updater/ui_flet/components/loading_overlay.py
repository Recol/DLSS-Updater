"""
Loading Overlay Component
Semi-transparent overlay with progress indicator
"""

import asyncio
import time
from collections.abc import Callable, Hashable

import anyio
import flet as ft

from dlss_updater.ui_flet.theme.colors import Shadows, MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


# Minimum gap between two non-final progress flushes. Each Control.update() is
# sent immediately (Flet 1.0 does not coalesce), so a scan/update run that
# reports hundreds of ticks per second would otherwise send hundreds of patches.
PROGRESS_FLUSH_INTERVAL_S = 0.05


class ProgressFlushThrottle:
    """Decides when a progress tick is worth sending to the client.

    - A tick whose displayed state (``key``) equals what was last flushed is
      dropped: there is nothing new to draw.
    - The first tick after reset() and any ``final`` tick flush immediately.
    - Otherwise ticks closer than ``min_interval`` to the previous flush are
      deferred: the caller has already written the new values onto its
      controls, and a single trailing flush (scheduled on the running event
      loop) sends the latest state once the interval has elapsed, so a burst
      that ends on a throttled tick is never left stale on screen.
    """

    def __init__(
        self,
        min_interval: float = PROGRESS_FLUSH_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._min_interval = min_interval
        self._clock = clock
        self._last_key: Hashable | None = None
        self._last_time: float | None = None
        self._pending_key: Hashable | None = None
        self._pending_flush: Callable[[], None] | None = None
        self._handle: asyncio.TimerHandle | None = None

    def reset(self) -> None:
        """Forget flush history and drop any pending trailing flush."""
        self._cancel_trailing()
        self._last_key = None
        self._last_time = None

    def submit(self, key: Hashable, flush: Callable[[], None], final: bool = False) -> bool:
        """Offer a tick. Returns True when ``flush`` was called synchronously."""
        if key == self._last_key:
            # Back to what is already on screen - a pending trailing flush
            # would only resend it.
            self._cancel_trailing()
            return False
        now = self._clock()
        if (
            final
            or self._last_time is None
            or now - self._last_time >= self._min_interval
        ):
            self._cancel_trailing()
            self._last_key = key
            self._last_time = now
            flush()
            return True

        self._pending_key = key
        self._pending_flush = flush
        if self._handle is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return False  # No loop (sync caller): the next tick flushes it
            delay = max(0.0, self._min_interval - (now - self._last_time))
            self._handle = loop.call_later(delay, self._fire_trailing)
        return False

    def _fire_trailing(self) -> None:
        self._handle = None
        key, flush = self._pending_key, self._pending_flush
        self._pending_key = None
        self._pending_flush = None
        if flush is None or key == self._last_key:
            return
        self._last_key = key
        self._last_time = self._clock()
        try:
            flush()
        except Exception:
            pass  # Control torn down between the tick and the trailing flush

    def _cancel_trailing(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        self._pending_key = None
        self._pending_flush = None


class LoadingOverlay(ThemeAwareMixin, ft.Container):
    """
    Full-screen loading overlay with progress indicator
    Similar to PyQt6's LoadingOverlay but using Flet components
    Supports light/dark theme
    """

    def __init__(self, page: ft.Page = None):
        super().__init__()

        # State
        self._is_showing = False  # Track visibility state (don't shadow ft.Container.visible)
        self._progress_value = 0
        self._page_ref = page
        self._registry = get_theme_registry()
        self._theme_priority = 40  # Utility components are mid-low priority

        # Cancellation callback for the current run (set per show(); None hides
        # the Cancel button for runs that aren't cancellable).
        self._on_cancel_cb = None
        # Once the user clicks Cancel, the "Cancelling…" label is pinned so
        # in-flight progress messages don't overwrite it (the progress bar still
        # advances as the current atomic unit finishes).
        self._cancelling = False

        # Progress ticks update only this overlay's subtree, deduplicated and
        # throttled (see ProgressFlushThrottle).
        self._flush_throttle = ProgressFlushThrottle()

        # Get theme preference from registry
        is_dark = self._registry.is_dark

        # Progress ring with breathing animation (500ms for responsiveness per MD3 guidelines)
        self.progress_ring = ft.ProgressRing(
            width=60,
            height=60,
            stroke_width=4,
            color=MD3Colors.get_primary(is_dark),
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            animate_scale=ft.Animation(500, ft.AnimationCurve.EASE_IN_OUT),
        )

        # Status text
        self.status_text = ft.Text(
            "Processing...",
            size=16,
            color=MD3Colors.get_text_primary(is_dark),
            text_align=ft.TextAlign.CENTER,
            animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
        )

        # Progress percentage text
        self.progress_text = ft.Text(
            "0%",
            size=24,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
            text_align=ft.TextAlign.CENTER,
        )

        # Progress bar with gradient (for determinate progress)
        self.progress_bar = ft.ProgressBar(
            width=300,
            height=4,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
            color=MD3Colors.get_primary(is_dark),
            value=0,
        )

        # Cancel button — lets the user abort a long scan/update run. Hidden by
        # default; show() reveals it when the run passes an on_cancel callback.
        self.cancel_button = ft.OutlinedButton(
            "Cancel",
            icon=ft.Icons.CLOSE,
            on_click=self._on_cancel_click,
            visible=False,
            style=ft.ButtonStyle(color=MD3Colors.get_error(is_dark)),
        )

        # Content container with glassmorphism effect
        # In dark mode use dark bg, in light mode use light bg with subtle transparency
        content_bg = "rgba(46, 46, 46, 0.95)" if is_dark else "rgba(255, 255, 255, 0.95)"
        border_color = "rgba(255, 255, 255, 0.1)" if is_dark else "rgba(0, 0, 0, 0.1)"

        # PERF: Use Column spacing instead of spacer Containers (-3 controls)
        self.content_container = ft.Container(
            content=ft.Column(
                controls=[
                    self.progress_ring,
                    self.progress_text,
                    self.progress_bar,
                    self.status_text,
                    self.cancel_button,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=12,  # Replaces spacer Containers
            ),
            bgcolor=content_bg,
            border_radius=16,
            padding=ft.Padding.all(40),
            border=ft.Border.all(1, border_color),
            shadow=Shadows.LEVEL_5,
        )

        # Overlay styling
        self.content = ft.Container(
            content=self.content_container,
            alignment=ft.Alignment.CENTER,
            expand=True,
        )
        overlay_bg = ft.Colors.with_opacity(0.7, ft.Colors.BLACK) if is_dark else ft.Colors.with_opacity(0.5, ft.Colors.BLACK)
        self.bgcolor = overlay_bg
        self.expand = True
        # Note: Don't add to page.overlay here - add dynamically in show()

        # Register for theme updates
        self._register_theme_aware()

    def show(self, page: ft.Page, message: str = "Processing...", on_cancel=None):
        """Show the loading overlay by adding to page.overlay.

        Args:
            page: Flet page instance.
            message: Initial status message.
            on_cancel: Optional zero-arg callable invoked when the user clicks
                Cancel. When provided, the Cancel button is shown and reset to
                its enabled state; when None, the button stays hidden.
        """
        self._page_ref = page
        self.status_text.value = message
        self._progress_value = 0
        self.progress_bar.value = 0
        self.progress_text.value = "0%"
        # Reset the Cancel button state for this run (re-enable after a prior
        # cancellation) and reveal it only when the run is cancellable.
        self._on_cancel_cb = on_cancel
        self._cancelling = False
        self.cancel_button.visible = on_cancel is not None
        self.cancel_button.disabled = False
        # Add to overlay if not already present (ensures it intercepts input)
        if self not in page.overlay:
            page.overlay.append(self)
        self._is_showing = True
        self._flush_throttle.reset()
        # Full page update here (once per run, not per tick): it is what
        # attaches the overlay, after which ticks can update just this subtree.
        page.update()

    def _on_cancel_click(self, e):
        """Handle Cancel click: fire the callback, reflect the pending state,
        and disable the button to prevent double-clicks."""
        cb = self._on_cancel_cb
        if cb is not None:
            try:
                cb()
            except Exception:
                pass
        self._cancelling = True
        self.cancel_button.disabled = True
        self.status_text.value = "Cancelling…"
        if self._page_ref:
            self._page_ref.update()

    def hide(self, page: ft.Page):
        """Hide the loading overlay by removing from page.overlay"""
        # Remove from overlay to stop intercepting input events
        if self in page.overlay:
            page.overlay.remove(self)
        self._is_showing = False
        self._flush_throttle.reset()  # No trailing tick after we're gone
        # Full page update: removing from page.overlay changes the page's own
        # overlay list, which a narrow self.update() would not send.
        page.update()

    def _flush_progress(self, page: ft.Page | None) -> None:
        """Send the overlay's own subtree, not the whole page."""
        if not self._is_showing:
            return  # Not attached: show() resets these values anyway
        try:
            self.update()
        except RuntimeError:
            # Appended to page.overlay but not yet sent to the client.
            if page is not None:
                page.update()

    def _submit_progress(self, page: ft.Page | None) -> bool:
        """Offer the current displayed state to the flush throttle."""
        key = (self._progress_value, self.status_text.value)
        return self._flush_throttle.submit(
            key,
            lambda: self._flush_progress(page),
            final=self._progress_value >= 100,
        )

    def set_progress(self, percentage: int, page: ft.Page, message: str = None):
        """
        Update progress (0-100)

        Args:
            percentage: Progress percentage (0-100)
            page: Flet page instance
            message: Optional status message
        """
        self._progress_value = max(0, min(100, int(percentage)))
        self.progress_bar.value = self._progress_value / 100
        self.progress_text.value = f"{self._progress_value}%"

        # Don't clobber the pinned "Cancelling…" label once cancellation started.
        if message and not self._cancelling:
            self.status_text.value = message

        self._submit_progress(page)

    async def set_progress_async(self, percentage: int, page: ft.Page, message: str = None):
        """Async version with direct update (no animation loop).

        Optimized for performance:
        - Updates only the overlay's subtree (self.update()), never the page
        - Ticks that change nothing on screen are dropped
        - Non-final ticks are throttled to one flush per
          PROGRESS_FLUSH_INTERVAL_S, with a trailing flush for the latest state
        - The first tick after show() and the 100% tick always flush
        """
        end = max(0, min(100, int(percentage)))

        # Direct update - progress bar's built-in animation handles visual smoothing
        self.progress_text.value = f"{end}%"
        self.progress_bar.value = end / 100
        self._progress_value = end

        # Update message if changed (but keep the pinned "Cancelling…" label
        # once cancellation started).
        if message and not self._cancelling and message != self.status_text.value:
            self.status_text.value = message

        self._submit_progress(page)

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for loading overlay"""
        return {
            "progress_ring.color": MD3Colors.get_themed_pair("primary"),
            "progress_ring.bgcolor": MD3Colors.get_themed_pair("surface_variant"),
            "progress_bar.color": MD3Colors.get_themed_pair("primary"),
            "progress_bar.bgcolor": MD3Colors.get_themed_pair("surface_variant"),
            "status_text.color": MD3Colors.get_themed_pair("text_primary"),
            "progress_text.color": MD3Colors.get_themed_pair("text_primary"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with cascade animation support"""
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        try:
            # Apply basic properties via parent method
            properties = self.get_themed_properties()
            for prop_path, (dark_val, light_val) in properties.items():
                value = dark_val if is_dark else light_val
                self._set_nested_property(prop_path, value)

            # Update content container glassmorphism effect
            content_bg = "rgba(46, 46, 46, 0.95)" if is_dark else "rgba(255, 255, 255, 0.95)"
            border_color = "rgba(255, 255, 255, 0.1)" if is_dark else "rgba(0, 0, 0, 0.1)"
            self.content_container.bgcolor = content_bg
            self.content_container.border = ft.Border.all(1, border_color)

            # Update overlay background opacity
            overlay_bg = ft.Colors.with_opacity(0.7, ft.Colors.BLACK) if is_dark else ft.Colors.with_opacity(0.5, ft.Colors.BLACK)
            self.bgcolor = overlay_bg

            # Restyle the Cancel button for the active theme
            self.cancel_button.style = ft.ButtonStyle(color=MD3Colors.get_error(is_dark))

            if hasattr(self, 'update'):
                self.update()

        except Exception:
            pass  # Silent fail - component may have been garbage collected
