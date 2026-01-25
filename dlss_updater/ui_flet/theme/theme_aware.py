"""
Theme-Aware Component System
Provides infrastructure for components to respond to theme changes with cascade animations.
Designed for Python 3.14 free-threaded compatibility.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from weakref import WeakSet

if TYPE_CHECKING:
    import flet as ft


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
            await asyncio.sleep(delay_ms / 1000)

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
    _lock = asyncio.Lock()

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
        self._is_dark: bool = True  # Default to dark mode
        self._cascade_lock = asyncio.Lock()
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

    async def apply_theme_to_all(
        self,
        is_dark: bool,
        cascade: bool = True,
        base_delay_ms: int = 30,
        page: "ft.Page | None" = None,
    ) -> None:
        """
        Apply theme to all registered components.

        Note: Cascade animation is DISABLED because theme toggle restarts the app
        via os.execv(), so users never see the cascade effect. All components are
        updated in a single pass with one page.update() for maximum efficiency.

        Args:
            is_dark: Whether to apply dark mode
            cascade: (Deprecated) Ignored - always applies instantly
            base_delay_ms: (Deprecated) Ignored - no delays used
            page: Optional Flet page for final update
        """
        async with self._cascade_lock:
            self._is_dark = is_dark

            # Get snapshot of components (WeakSet may change during iteration)
            components = list(self._components)

            if not components:
                return

            # Apply all components in single pass (no cascade - app restarts on theme change)
            for comp in components:
                try:
                    properties = comp.get_themed_properties()
                    for prop_path, (dark_val, light_val) in properties.items():
                        value = dark_val if is_dark else light_val
                        comp._set_nested_property(prop_path, value)
                except Exception:
                    pass  # Component may have been garbage collected

            # Single page update at the end
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
