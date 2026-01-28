"""
Navigation Controller
Replaces tab-based navigation with hub + view switching via Stack-based visibility toggle.
"""

import asyncio
import logging
import time
from typing import Callable, Any

import flet as ft

from dlss_updater.ui_flet.components.floating_pill import FloatingPill


class NavigationController(ft.Column):
    """
    Central navigation controller managing hub view and content views.

    Uses a Stack with visibility + opacity toggles for GPU-accelerated fade transitions.
    Manages the floating pill navigation bar (hidden on hub, shown in views).
    """

    # View name constants
    HUB = "hub"
    LAUNCHERS = "launchers"
    GAMES = "games"
    SETTINGS = "settings"

    # All navigable view names (excluding hub)
    VIEW_NAMES = [LAUNCHERS, GAMES, SETTINGS]

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        hub_view: ft.Control,
        views: dict[str, ft.Control],
        on_view_load: Callable[[str], Any] | None = None,
        on_view_hidden: Callable[[str, str], Any] | None = None,
    ):
        """
        Args:
            page: Flet page reference
            logger: Application logger
            hub_view: The hub home screen view
            views: Dict mapping view names to view controls
            on_view_load: Async callback when a view needs loading (e.g., games data)
            on_view_hidden: Async callback when a view becomes hidden (old_name, new_name)
        """
        super().__init__()
        self._page_ref = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        self._hub_view = hub_view
        self._views = views
        self._on_view_load = on_view_load
        self._on_view_hidden = on_view_hidden

        # Navigation state
        self._current_view = self.HUB
        self._nav_stack: list[str] = []  # Max 10 entries
        self._transition_lock = asyncio.Lock()

        # Store view references separately from containers.
        # Only the ACTIVE view has its content attached; inactive containers
        # have content=None so their subtrees are excluded from serialization.
        self._view_refs: dict[str, ft.Control] = {}
        self._view_refs[self.HUB] = hub_view
        for name, view in views.items():
            self._view_refs[name] = view

        # Build view containers (Stack-based content detachment)
        self._view_containers: dict[str, ft.Container] = {}

        # Hub container (visible and attached by default — it's the initial view)
        hub_container = ft.Container(
            content=hub_view,
            visible=True,
            opacity=1.0,
            expand=True,
        )
        self._view_containers[self.HUB] = hub_container

        # Other view containers (hidden and detached by default)
        for name, view in views.items():
            container = ft.Container(
                content=None,  # Detached — excluded from serialization
                visible=False,
                opacity=0.0,
                expand=True,
            )
            self._view_containers[name] = container

        # Content stack with all views
        content_stack = ft.Stack(
            controls=[hub_container] + [
                self._view_containers[name]
                for name in self.VIEW_NAMES
                if name in self._view_containers
            ],
            expand=True,
        )

        # Floating pill navigation
        self._pill = FloatingPill(
            on_navigate=self._on_pill_navigate,
            on_home=self._on_pill_home,
            page=page,
        )

        # Pill wrapper (positioned at bottom center, hidden on hub)
        self._pill_wrapper = ft.Container(
            content=self._pill,
            alignment=ft.Alignment.BOTTOM_CENTER,
            bottom=16,
            visible=False,
            opacity=0.0,
            animate_opacity=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )

        # Outer stack: content + floating pill
        outer_stack = ft.Stack(
            controls=[
                content_stack,
                self._pill_wrapper,
            ],
            expand=True,
        )

        self.controls = [outer_stack]

    @property
    def current_view(self) -> str:
        """Get the current view name."""
        return self._current_view

    async def navigate_to(self, view_name: str):
        """
        Navigate to a view by name.

        Args:
            view_name: One of HUB, LAUNCHERS, GAMES, SETTINGS
        """
        if view_name == self._current_view:
            return

        async with self._transition_lock:
            await self._transition_to(view_name)

    async def navigate_back(self):
        """Navigate back in the stack, or return to hub if stack is empty."""
        if self._nav_stack:
            previous = self._nav_stack.pop()
            async with self._transition_lock:
                await self._transition_to(previous, push_stack=False)
        else:
            await self.navigate_to(self.HUB)

    async def _transition_to(self, view_name: str, push_stack: bool = True):
        """
        Perform instant view transition via content detachment.

        PERFORMANCE: Only the active view's subtree is attached to its container.
        Inactive containers have content=None, removing their subtrees from
        serialization entirely. This reduces page.update() from ~300-600ms
        (serializing ~4000 controls) to ~30-200ms (serializing only active view).

        Single page.update() — no fade-out gap needed.
        """
        t_start = time.perf_counter()
        old_view = self._current_view
        new_view = view_name

        if old_view == new_view:
            return

        # Push current to nav stack
        if push_stack and old_view != self.HUB:
            self._nav_stack.append(old_view)
            if len(self._nav_stack) > 10:
                self._nav_stack = self._nav_stack[-10:]

        # Get containers
        old_container = self._view_containers.get(old_view)
        new_container = self._view_containers.get(new_view)

        if not old_container or not new_container:
            self.logger.error(f"View container not found: old={old_view}, new={new_view}")
            return

        is_hub = new_view == self.HUB

        # SINGLE UPDATE: Detach old subtree, attach new subtree, swap visibility
        old_container.content = None  # Detach old subtree from serialization
        old_container.visible = False
        old_container.opacity = 0.0

        new_container.content = self._view_refs[new_view]  # Attach new subtree
        new_container.visible = True
        new_container.opacity = 1.0  # Instant show (no fade-out gap)

        # Pill state
        if is_hub:
            self._pill_wrapper.opacity = 0.0
            self._pill_wrapper.visible = False
        else:
            self._pill_wrapper.visible = True
            self._pill_wrapper.opacity = 1.0
            self._pill.set_active(new_view)

        # SINGLE page.update() — serializes only: nav shell + active view + pill
        t_update = time.perf_counter()
        self._page_ref.update()
        t_update_done = time.perf_counter()

        self._current_view = new_view

        # Callbacks (async, non-blocking)
        t_callbacks = time.perf_counter()
        if self._on_view_hidden and old_view != self.HUB:
            try:
                await self._on_view_hidden(old_view, new_view)
            except Exception as e:
                self.logger.warning(f"Error in on_view_hidden callback: {e}")

        if self._on_view_load and new_view != self.HUB:
            try:
                await self._on_view_load(new_view)
            except Exception as e:
                self.logger.warning(f"Error loading view '{new_view}': {e}")
        t_callbacks_done = time.perf_counter()

        # Performance metrics
        update_ms = (t_update_done - t_update) * 1000
        total_ms = (t_callbacks_done - t_start) * 1000
        callbacks_ms = (t_callbacks_done - t_callbacks) * 1000
        self.logger.debug(
            f"[PERF] Navigation {old_view} -> {new_view}: "
            f"total={total_ms:.1f}ms "
            f"(update={update_ms:.1f}ms, callbacks={callbacks_ms:.1f}ms)"
        )

    async def _on_pill_navigate(self, view_name: str):
        """Handle pill navigation icon click."""
        await self.navigate_to(view_name)

    async def _on_pill_home(self):
        """Handle pill home button click."""
        await self.navigate_to(self.HUB)

    def handle_keyboard(self, e: ft.KeyboardEvent):
        """Handle keyboard events. Call from page's on_keyboard_event."""
        if e.key == "Escape":
            if self._current_view != self.HUB:
                self._page_ref.run_task(self.navigate_back)
