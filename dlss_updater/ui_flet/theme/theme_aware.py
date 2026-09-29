"""
Theme-Aware Component System
Provides infrastructure for components to respond to theme changes with cascade animations.
Designed for Python 3.14 free-threaded compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from weakref import WeakSet, ref

import anyio

if TYPE_CHECKING:
    import flet as ft


# Opt-in marker a nav view sets when its owner (MainView) REBUILDS the whole
# view from fresh instances on its next attach after a theme toggle. The
# cascade then skips its components' apply_theme() entirely while it is
# detached - the work would be thrown away with the old instances.
THEME_REBUILT_ON_ATTACH_ATTR = "_theme_rebuilt_on_attach"

# Upper bound on the parent walk (real trees are ~20-40 deep); guards against
# a pathological cycle rather than limiting any legitimate tree.
_MAX_PARENT_DEPTH = 512


def find_detached_nav_view(control: Any) -> Any | None:
    """Return the nav-detached view ``control`` lives in, or None.

    Walks ``control`` and its ancestors via Flet's ``parent`` link looking for
    a node whose ``_nav_attached`` flag (maintained by NavigationController,
    see navigation_controller.is_view_attached) is False. Flet never clears
    ``_parent`` when a subtree is detached, so the chain from a control inside
    a detached view still reaches that view - which is what makes this
    reliable. Anything the nav controller doesn't manage (dialogs, overlay
    panels, the app bar, controls never mounted) returns None, i.e. is
    treated as attached exactly as before.
    """
    node = control
    for _ in range(_MAX_PARENT_DEPTH):
        if node is None:
            return None
        if getattr(node, "_nav_attached", True) is False:
            return node
        try:
            node = getattr(node, "parent", None)
        except Exception:
            return None
    return None


def _suppressed_update(*_args, **_kwargs) -> None:
    """Stand-in for ``update()`` on a component inside a detached view."""
    return None


@runtime_checkable
class ThemeAwareProtocol(Protocol):
    """Protocol for theme-aware components"""

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return {property_path: (dark_value, light_value)}"""
        ...

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay"""
        ...


class ThemeAwareMixin:
    """
    Mixin for components that respond to theme changes.

    Components should:
    1. Inherit from this mixin AND their Flet base class
    2. Call _register_theme_aware() in __init__ after building UI
    3. Implement get_themed_properties() to return color mappings

    Example:
        class GameCard(ThemeAwareMixin, ft.Card):
            def __init__(self, ...):
                super().__init__()
                self._build_ui()
                self._register_theme_aware()

            def get_themed_properties(self) -> dict[str, tuple[str, str]]:
                return {
                    "bgcolor": ("#1E1E1E", "#FFFFFF"),
                    "title.color": ("#E4E2E0", "#1C1B1F"),
                }
    """

    # Cascade priority for animation ordering
    # Lower numbers animate first
    _theme_priority: int = 50  # Default: mid-priority

    def _register_theme_aware(self) -> None:
        """Register with ThemeRegistry for updates"""
        registry = get_theme_registry()
        registry.register(self)

    def _unregister_theme_aware(self) -> None:
        """Unregister from ThemeRegistry (called on dispose)"""
        registry = get_theme_registry()
        registry.unregister(self)

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """
        Return themed property mappings.

        Override in subclasses to define theme-sensitive properties.

        Returns:
            Dict mapping property paths to (dark_value, light_value) tuples.
            Property paths support nested attributes via dot notation.

        Example:
            {
                "bgcolor": ("#1E1E1E", "#FFFFFF"),
                "title_text.color": ("#E4E2E0", "#1C1B1F"),
                "icon.color": ("#2D6E88", "#1A5A70"),
            }
        """
        return {}

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """
        Apply theme with optional cascade delay.

        This method:
        1. Waits for cascade delay if specified
        2. Gets themed properties from get_themed_properties()
        3. Sets each property to the appropriate theme value
        4. Calls update() to refresh the UI

        Args:
            is_dark: Whether dark mode is active
            delay_ms: Milliseconds to wait before applying (for cascade effect)
        """
        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        try:
            properties = self.get_themed_properties()

            for prop_path, (dark_val, light_val) in properties.items():
                value = dark_val if is_dark else light_val
                self._set_nested_property(prop_path, value)

            # Call update() if this is a Flet control
            if hasattr(self, 'update'):
                self.update()

        except Exception:
            # Silent fail - component may have been garbage collected
            pass

    def _set_nested_property(self, prop_path: str, value) -> None:
        """
        Set a potentially nested property using dot notation.

        Args:
            prop_path: Property path like "bgcolor" or "title_text.color"
            value: Value to set
        """
        parts = prop_path.split('.')
        obj = self

        # Navigate to the parent object
        for part in parts[:-1]:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                return  # Property path doesn't exist

        # Set the final property
        final_attr = parts[-1]
        if hasattr(obj, final_attr):
            setattr(obj, final_attr, value)


class ThemeRegistry:
    """
    Central registry for theme-aware components.

    Uses WeakSet to automatically clean up garbage-collected components.
    Thread-safe for Python 3.14 free-threaded compatibility.

    Cascade animation timing (based on priority):
    - Priority 0-9: 0ms (page background, app bar)
    - Priority 10-19: 30ms (navigation tabs)
    - Priority 20-39: 60-120ms (cards, staggered)
    - Priority 40-59: 150ms (badges, buttons)
    - Priority 60-79: 180ms (dialogs)
    - Priority 80+: 200ms+ (low priority)
    """

    _instance: ThemeRegistry | None = None
    _lock = anyio.Lock()

    def __new__(cls) -> ThemeRegistry:
        """Singleton pattern"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._components: WeakSet[ThemeAwareMixin] = WeakSet()
        # Weakrefs to the nav-detached views the most recent cascade did not
        # flush (see apply_theme_to_all). MainView reads it via
        # last_detached_views to mark each one theme-stale.
        self._last_detached_views: list = []
        self._is_dark: bool = True  # Default to dark mode
        self._cascade_lock = anyio.Lock()
        self._initialized = True

    @property
    def is_dark(self) -> bool:
        """Current theme mode"""
        return self._is_dark

    @is_dark.setter
    def is_dark(self, value: bool) -> None:
        """Set theme mode (does not trigger cascade - call apply_theme_to_all)"""
        self._is_dark = value

    def register(self, component: ThemeAwareMixin) -> None:
        """
        Register a component for theme updates.

        Args:
            component: A ThemeAwareMixin instance
        """
        if component is not None:
            self._components.add(component)

    def unregister(self, component: ThemeAwareMixin) -> None:
        """
        Unregister a component from theme updates.

        Args:
            component: A ThemeAwareMixin instance
        """
        try:
            self._components.discard(component)
        except (KeyError, TypeError):
            pass  # Component already removed or GC'd

    def get_component_count(self) -> int:
        """Get the number of registered components (for debugging)"""
        return len(self._components)

    @property
    def last_detached_views(self) -> list:
        """Nav-detached views the most recent cascade left unflushed.

        Every one of them is theme-stale on the client and must be healed on
        its next attach (MainView._theme_stale_views). Dead weakrefs dropped.
        """
        return [v for v in (r() for r in self._last_detached_views) if v is not None]

    async def apply_theme_to_all(
        self,
        is_dark: bool,
        cascade: bool = True,
        base_delay_ms: int = 30,
        page: "ft.Page | None" = None,
    ) -> None:
        """
        Apply theme to all registered components (live, no restart).

        Each component's own ``apply_theme()`` is invoked so that subclasses with
        custom theming logic (menu rebuilds, in-place tile recoloring, TextField
        styles, etc.) update correctly — not just their static
        ``get_themed_properties()`` map.

        When ``cascade`` is True, components are updated with a staggered delay
        derived from their ``_theme_priority`` (lower priority animates first),
        producing a sweep across the UI. Each component self-updates after its
        delay; a final ``page.update()`` flushes any page-level changes.

        Components inside a nav-DETACHED view (find_detached_nav_view) are
        never flushed: the client drops patches to detached subtrees anyway
        (CLAUDE.md pitfall #2), and each such update still diffed the whole
        detached subtree server-side. They are handled in one of two ways:

        - view marked ``_theme_rebuilt_on_attach`` (hub, launchers): skipped
          entirely - the owner replaces the whole view on its next attach.
        - any other detached view (games, backups, ...): ``apply_theme()``
          still runs, so Python-side state is exactly what it was before this
          optimisation, but with ``update`` shadowed by a no-op on the
          component for the duration of the call. Updates the override issues
          on OTHER controls (child menus etc.) are not intercepted.

        Every such view is recorded in ``last_detached_views`` so the owner
        can mark it theme-stale; the owner's rebuild-on-attach heal is what
        brings it up to date on the client.

        Args:
            is_dark: Whether to apply dark mode
            cascade: Stagger updates by priority for a sweep effect
            base_delay_ms: Per-priority-tier delay when cascading (ms)
            page: Optional Flet page for the final update
        """
        async with self._cascade_lock:
            self._is_dark = is_dark
            self._last_detached_views = []

            # Snapshot of components (WeakSet may change during iteration)
            components = list(self._components)
            if not components:
                if page is not None:
                    try:
                        page.update()
                    except Exception:
                        pass
                return

            attached: list[ThemeAwareMixin] = []
            unflushed: list[ThemeAwareMixin] = []
            detached_views: dict[int, Any] = {}
            for comp in components:
                view = find_detached_nav_view(comp)
                if view is None:
                    attached.append(comp)
                    continue
                detached_views[id(view)] = view
                if not getattr(view, THEME_REBUILT_ON_ATTACH_ATTR, False):
                    unflushed.append(comp)
            self._last_detached_views = [ref(v) for v in detached_views.values()]

            async def _apply(comp: "ThemeAwareMixin") -> None:
                # Delay is baked into apply_theme() so each component sleeps then
                # applies + self-updates, giving the staggered cascade sweep.
                if cascade:
                    delay = (getattr(comp, "_theme_priority", 50) // 10) * base_delay_ms
                else:
                    delay = 0
                try:
                    await comp.apply_theme(is_dark, delay_ms=delay)
                except Exception:
                    pass  # Component may have been GC'd or detached

            async def _apply_unflushed(comp: "ThemeAwareMixin") -> None:
                # Invisible, so no cascade delay. The instance attribute shadows
                # the bound update() for the call, covering both the mixin's
                # own self.update() and any override's direct self.update().
                shadowed = False
                try:
                    comp.update = _suppressed_update
                    shadowed = True
                except Exception:
                    pass
                try:
                    await comp.apply_theme(is_dark, delay_ms=0)
                except Exception:
                    pass
                finally:
                    if shadowed:
                        try:
                            del comp.update
                        except Exception:
                            pass

            # Run all component updates concurrently; each waits its own delay.
            # _apply() swallows its own exceptions, so no error aggregation needed.
            async with anyio.create_task_group() as tg:
                for c in unflushed:
                    tg.start_soon(_apply_unflushed, c)
                for c in attached:
                    tg.start_soon(_apply, c)

            # Final page update flushes page-level (bgcolor/theme) changes. It
            # only serialises the attached tree, so it never reaches the
            # detached views above either.
            if page is not None:
                try:
                    page.update()
                except Exception:
                    pass


# Global registry instance
_registry: ThemeRegistry | None = None


def get_theme_registry() -> ThemeRegistry:
    """
    Get the global ThemeRegistry singleton.

    Returns:
        The ThemeRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = ThemeRegistry()
    return _registry


def reset_theme_registry() -> None:
    """
    Reset the global registry (for testing purposes).
    """
    global _registry
    _registry = None
    ThemeRegistry._instance = None


# Export public API
__all__ = [
    'ThemeAwareProtocol',
    'ThemeAwareMixin',
    'ThemeRegistry',
    'get_theme_registry',
    'reset_theme_registry',
]
