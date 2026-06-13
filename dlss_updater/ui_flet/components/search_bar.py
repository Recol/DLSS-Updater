"""
Search Bar Component with History Dropdown

A styled search input with:
- Search icon and placeholder text
- Clear button when text present
- Search history popup menu (overlay, no layout shift)
- Expandable/compact mode (collapses to icon, expands on click)
- MD3 light/dark theme support via ThemeAwareMixin

Thread-safe for free-threaded Python 3.14+.
"""

import asyncio
from typing import Callable, Any
import flet as ft

from dlss_updater.logger import setup_logger
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.task_registry import register_task

logger = setup_logger()


class GameSearchBar(ft.Container):
    """
    Native Material search bar (ft.SearchBar / SearchAnchor) with live game-name
    suggestions and recent-search history in the dropdown view.

    Exposes the same surface GamesView used with the legacy custom SearchBar:
    update_history(), cleanup(), set_value(), get_value(), focus().

    Visual theming comes from the page-level Material 3 theme, so no
    ThemeAwareMixin registration is needed.
    """

    MAX_SUGGESTIONS = 8
    COLLAPSED_SIZE = 40

    def __init__(
        self,
        on_search: Callable[[str], None] | None = None,
        on_clear: Callable[[], None] | None = None,
        on_history_selected: Callable[[str], None] | None = None,
        get_suggestions: Callable[[str], list[str]] | None = None,
        placeholder: str = "Search games...",
        width: int = 260,
        expandable: bool = True,
    ):
        super().__init__()
        self.on_search_callback = on_search
        self.on_clear_callback = on_clear
        self.on_history_selected_callback = on_history_selected
        self.get_suggestions_callback = get_suggestions
        self._history_items: list[Any] = []
        self._debounce_task: asyncio.Task | None = None

        self._expandable = expandable
        self._expanded_width = width
        self._is_expanded = not expandable

        self._bar = ft.SearchBar(
            bar_hint_text=placeholder,
            view_hint_text="Type a game name…",
            bar_leading=ft.Icon(ft.Icons.SEARCH, size=20),
            bar_trailing=[
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    icon_size=16,
                    tooltip="Clear search",
                    on_click=self._on_clear_clicked,
                ),
            ],
            controls=[],
            on_change=self._on_change,
            on_submit=self._on_submit,
            on_tap=self._on_tap,
            # Collapse only when the user taps OUTSIDE the bar. We deliberately do
            # NOT collapse on on_blur: opening the bar's own suggestion view blurs
            # the input, which would otherwise collapse it the instant the history
            # dropdown appears.
            on_tap_outside_bar=self._on_tap_outside,
            expand=True,
        )

        # Collapsed state: a plain search icon button matching the
        # neighbouring header icons (options / refresh).
        self._collapsed_button = ft.IconButton(
            icon=ft.Icons.SEARCH,
            icon_size=20,
            tooltip="Search games",
            on_click=self._on_expand_click,
            width=self.COLLAPSED_SIZE,
            height=self.COLLAPSED_SIZE,
        )

        # Smooth width animation between icon and full pill
        self.animate = ft.Animation(250, ft.AnimationCurve.EASE_IN_OUT)
        self.clip_behavior = ft.ClipBehavior.HARD_EDGE
        self.height = 44

        if self._expandable:
            self.content = self._collapsed_button
            self.width = self.COLLAPSED_SIZE
        else:
            self.content = self._bar
            self.width = self._expanded_width

    # ----- expand / collapse -----

    async def _on_expand_click(self, e):
        """Expand from the compact icon to the full search pill.

        Only focuses the bar (does not auto-open the suggestion overlay) so a
        single click away triggers on_blur and collapses it. The suggestion view
        opens naturally when the user taps the bar or starts typing.
        """
        if self._is_expanded:
            return
        self._is_expanded = True
        self.content = self._bar
        self.width = self._expanded_width
        self.update()
        # Let the bar attach before focusing
        await asyncio.sleep(0.05)
        try:
            await self._bar.focus()
        except Exception:
            pass

    def _collapse_if_empty(self):
        """Collapse back to the icon when there is no active query."""
        if not self._expandable or not self._is_expanded:
            return
        if (self._bar.value or "").strip():
            return  # Keep expanded while a search is active
        self._is_expanded = False
        self.content = self._collapsed_button
        self.width = self.COLLAPSED_SIZE
        try:
            self.update()
        except RuntimeError:
            pass

    async def _on_tap_outside(self, e):
        """Collapse when the user taps outside the bar with an empty query."""
        # Small debounce so an outside tap that also lands on another control
        # settles before we measure the value.
        await asyncio.sleep(0.15)
        self._collapse_if_empty()

    # ----- internal helpers -----

    def _make_suggestion_tile(self, text: str, icon: str, from_history: bool) -> ft.ListTile:
        return ft.ListTile(
            leading=ft.Icon(icon, size=18),
            title=ft.Text(text, size=14, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            dense=True,
            on_click=lambda e, q=text, h=from_history: self._on_suggestion_clicked(q, h),
        )

    def _build_suggestions(self, query: str) -> list[ft.Control]:
        """Build the dropdown contents: live matches first, then history."""
        tiles: list[ft.Control] = []

        if query and self.get_suggestions_callback:
            try:
                for name in self.get_suggestions_callback(query)[: self.MAX_SUGGESTIONS]:
                    tiles.append(self._make_suggestion_tile(name, ft.Icons.VIDEOGAME_ASSET, False))
            except Exception:
                pass

        # Recent searches (skip ones duplicating a live suggestion)
        shown = {t.title.value.lower() for t in tiles if isinstance(t, ft.ListTile)}
        history_tiles = []
        for item in self._history_items:
            q = item.query if hasattr(item, "query") else item.get("query", "")
            if not q or q.lower() in shown:
                continue
            if query and query.lower() not in q.lower():
                continue
            history_tiles.append(self._make_suggestion_tile(q, ft.Icons.HISTORY, True))
            if len(history_tiles) >= self.MAX_SUGGESTIONS:
                break
        tiles.extend(history_tiles)
        return tiles

    def _refresh_suggestions(self, query: str):
        self._bar.controls = self._build_suggestions(query)
        try:
            self._bar.update()
        except RuntimeError:
            pass  # Not attached yet

    def _dispatch_search(self, query: str):
        """Invoke the (possibly async) search callback."""
        if not self.on_search_callback:
            return
        result = self.on_search_callback(query)
        if asyncio.iscoroutine(result):
            register_task(asyncio.create_task(result), "native_search_dispatch")

    async def _debounced_search(self, query: str):
        try:
            await asyncio.sleep(0.150)
            if self.on_search_callback:
                result = self.on_search_callback(query)
                if asyncio.iscoroutine(result):
                    await result
        except asyncio.CancelledError:
            pass

    # ----- event handlers -----

    async def _on_tap(self, e):
        """Open the suggestion view when the bar is tapped."""
        self._refresh_suggestions(self._bar.value or "")
        try:
            await self._bar.open_view()
        except Exception:
            pass

    async def _on_change(self, e):
        query = (self._bar.value or "").strip()
        self._refresh_suggestions(query)

        # Debounced live filtering of the grid behind the view
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
        self._debounce_task = asyncio.create_task(self._debounced_search(query))
        register_task(self._debounce_task, "search_debounce")

    async def _on_submit(self, e):
        query = (self._bar.value or "").strip()
        try:
            await self._bar.close_view(query)
        except Exception:
            pass
        self._dispatch_search(query)

    def _on_suggestion_clicked(self, query: str, from_history: bool):
        async def apply():
            try:
                await self._bar.close_view(query)
            except Exception:
                pass
            if from_history and self.on_history_selected_callback:
                result = self.on_history_selected_callback(query)
                if asyncio.iscoroutine(result):
                    await result
            else:
                self._dispatch_search(query)

        register_task(asyncio.create_task(apply()), "search_suggestion_apply")

    async def _on_clear_clicked(self, e):
        self._bar.value = ""
        try:
            self._bar.update()
        except RuntimeError:
            pass
        if self.on_clear_callback:
            result = self.on_clear_callback()
            if asyncio.iscoroutine(result):
                await result
        # Pressing the (x) clears the search and collapses the bar to its icon
        try:
            await self._bar.close_view("")
        except Exception:
            pass
        self._collapse_if_empty()

    # ----- public API (parity with legacy SearchBar) -----

    def update_history(self, history_items: list[Any]):
        self._history_items = history_items[:10]

    def set_value(self, value: str):
        # Auto-expand when a non-empty value is applied programmatically
        if self._expandable and value and not self._is_expanded:
            self._is_expanded = True
            self.content = self._bar
            self.width = self._expanded_width
            try:
                self.update()
            except RuntimeError:
                pass
        self._bar.value = value
        try:
            self._bar.update()
        except RuntimeError:
            pass

    def get_value(self) -> str:
        return self._bar.value or ""

    def focus(self):
        # ft.SearchBar.focus() is async — schedule it so callers stay sync
        try:
            register_task(asyncio.create_task(self._bar.focus()), "search_focus")
        except Exception:
            pass

    async def cleanup(self):
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
        self.on_search_callback = None
        self.on_clear_callback = None
        self.on_history_selected_callback = None
        self.get_suggestions_callback = None
        self._history_items.clear()


class SearchBar(ThemeAwareMixin, ft.Container):
    """
    Search bar component with history popup menu.
    Supports light/dark theme switching via ThemeAwareMixin.

    Usage:
        search_bar = SearchBar(
            on_search=on_search_callback,
            on_clear=on_clear_callback,
            on_history_selected=on_history_selected_callback,
            expandable=True,   # Optional: collapses to icon, expands on click
        )
    """

    def __init__(
        self,
        on_search: Callable[[str], None] | None = None,
        on_clear: Callable[[], None] | None = None,
        on_history_selected: Callable[[str], None] | None = None,
        on_focus_change: Callable[[bool], None] | None = None,
        placeholder: str = "Search games...",
        width: int | None = None,
        expandable: bool = False,
        expanded_width: int = 300,
    ):
        super().__init__()
        self.on_search_callback = on_search
        self.on_clear_callback = on_clear
        self.on_history_selected_callback = on_history_selected
        self.on_focus_change_callback = on_focus_change
        self.placeholder_text = placeholder
        self.search_width = width
        self._expandable = expandable
        self._expanded_width = expanded_width
        self._is_expanded = False

        # Theme registry
        self._registry = get_theme_registry()
        self._theme_priority = 15  # Search bar is higher priority (updates early)

        # State
        self._is_focused = False
        self._history_items: list[Any] = []
        self._debounce_task: asyncio.Task | None = None

        # Build UI
        self._build_ui()

        # Register for theme updates
        self._register_theme_aware()

    def _safe_update(self):
        """Safely update page - handles Flet 0.80.4 RuntimeError when control not yet added."""
        try:
            if self.page:
                self.page.update()
        except RuntimeError:
            pass  # Control not yet added to page

    def _build_ui(self):
        """Build the search bar UI with PopupMenuButton for history."""
        is_dark = self._registry.is_dark
        icon_default = MD3Colors.get_themed("icon_default", is_dark)

        # Search icon
        self.search_icon = ft.Icon(
            ft.Icons.SEARCH,
            size=20,
            color=icon_default,
        )

        # Clear button (hidden by default)
        self.clear_button = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_size=18,
            icon_color=MD3Colors.get_text_secondary(is_dark),
            tooltip="Clear search",
            on_click=self._on_clear_clicked,
            visible=False,
            width=32,
            height=32,
        )

        # History popup menu button (hidden until history exists)
        self.history_button = ft.PopupMenuButton(
            icon=ft.Icons.HISTORY,
            icon_size=20,
            tooltip="Recent searches",
            items=[],
            visible=False,
        )

        # Search text field
        self.search_field = ft.TextField(
            hint_text=self.placeholder_text,
            hint_style=ft.TextStyle(color=MD3Colors.get_text_secondary(is_dark)),
            text_style=ft.TextStyle(color=MD3Colors.get_on_surface(is_dark)),
            border_color=MD3Colors.get_outline(is_dark),
            focused_border_color=MD3Colors.get_primary(is_dark),
            bgcolor=MD3Colors.get_surface(is_dark),
            border_radius=8,
            content_padding=ft.Padding.only(left=40, right=40, top=8, bottom=8),
            on_change=self._on_text_changed,
            on_focus=self._on_focus,
            on_blur=self._on_blur,
            on_submit=self._on_submit,
            expand=True,
        )

        # Stack search field with icons
        search_stack = ft.Stack(
            controls=[
                self.search_field,
                ft.Container(
                    content=self.search_icon,
                    left=12,
                    top=8,
                ),
                ft.Container(
                    content=self.clear_button,
                    right=4,
                    top=4,
                ),
            ],
        )

        # Full search row (field + history button)
        self._search_row_content = ft.Row(
            controls=[
                ft.Container(
                    content=search_stack,
                    expand=True,
                    height=40,
                ),
                self.history_button,
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        if self._expandable:
            # Collapsed state: just a search icon button
            self._collapse_content = ft.IconButton(
                icon=ft.Icons.SEARCH,
                icon_size=18,
                tooltip="Search games",
                on_click=self._on_expand_click,
                width=40,
                height=40,
            )
            self.content = self._collapse_content
            self.width = 40
            self.height = 40
            self.animate = ft.Animation(250, ft.AnimationCurve.EASE_IN_OUT)
            self.clip_behavior = ft.ClipBehavior.HARD_EDGE
        else:
            self.content = self._search_row_content
            self.width = self.search_width

    async def _on_expand_click(self, e):
        """Expand from compact icon to full search bar."""
        if self._is_expanded:
            return
        self._is_expanded = True
        self.content = self._search_row_content
        self.width = self._expanded_width
        self.update()
        await asyncio.sleep(0.05)
        try:
            self.search_field.focus()
        except Exception:
            pass

    async def _on_text_changed(self, e):
        """Handle text input changes with debouncing."""
        query = e.control.value or ""
        is_dark = self._registry.is_dark

        # Update clear button visibility
        self.clear_button.visible = len(query) > 0
        self.clear_button.update()

        # Update search icon color
        self.search_icon.color = (
            MD3Colors.get_primary(is_dark) if query else MD3Colors.get_themed("icon_default", is_dark)
        )
        self.search_icon.update()

        # Cancel and await existing debounce task to prevent orphaning
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass  # Expected

        # Debounce search callback (150ms) - REGISTER THE TASK
        if self.on_search_callback:
            self._debounce_task = asyncio.create_task(
                self._debounced_search(query)
            )
            register_task(self._debounce_task, "search_debounce")

    async def _debounced_search(self, query: str):
        """Execute search callback after debounce delay."""
        try:
            await asyncio.sleep(0.150)  # 150ms debounce
            if self.on_search_callback:
                result = self.on_search_callback(query)
                if asyncio.iscoroutine(result):
                    await result
        except asyncio.CancelledError:
            pass  # Debounce cancelled by new input

    async def _on_focus(self, e):
        """Handle field focus."""
        self._is_focused = True
        is_dark = self._registry.is_dark
        self.search_field.border_color = MD3Colors.get_primary(is_dark)

        if self.on_focus_change_callback:
            result = self.on_focus_change_callback(True)
            if asyncio.iscoroutine(result):
                await result

    async def _on_blur(self, e):
        """Handle field blur."""
        self._is_focused = False
        is_dark = self._registry.is_dark

        if not self.search_field.value:
            self.search_field.border_color = MD3Colors.get_outline(is_dark)

        # Collapse to icon if expandable and no active search text
        if self._expandable and not self.search_field.value and self._is_expanded:
            # Debounce to avoid premature collapse when clicking history popup
            await asyncio.sleep(0.25)
            if not self._is_focused and not self.search_field.value and self._is_expanded:
                self._is_expanded = False
                self.content = self._collapse_content
                self.width = 40
                if self.page:
                    self.update()

        if self.on_focus_change_callback:
            result = self.on_focus_change_callback(False)
            if asyncio.iscoroutine(result):
                await result

    async def _on_submit(self, e):
        """Handle Enter key press."""
        query = e.control.value or ""
        if query and self.on_search_callback:
            result = self.on_search_callback(query)
            if asyncio.iscoroutine(result):
                await result

    async def _on_clear_clicked(self, e):
        """Handle clear button click."""
        is_dark = self._registry.is_dark
        self.search_field.value = ""
        self.clear_button.visible = False
        self.search_icon.color = MD3Colors.get_themed("icon_default", is_dark)

        self._safe_update()

        if self.on_clear_callback:
            result = self.on_clear_callback()
            if asyncio.iscoroutine(result):
                await result

    def update_history(self, history_items: list[Any]):
        """
        Update search history popup menu items.

        Args:
            history_items: List of SearchHistoryEntry objects or dicts
        """
        self._history_items = history_items[:10]  # Max 10 items
        is_dark = self._registry.is_dark

        if not self._history_items:
            self.history_button.visible = False
            self._safe_update()
            return

        menu_items = []
        secondary_color = MD3Colors.get_text_secondary(is_dark)

        # Header (non-clickable)
        menu_items.append(ft.PopupMenuItem(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.HISTORY, size=16, color=secondary_color),
                    ft.Text("Recent Searches", size=12, color=secondary_color),
                ],
                spacing=8,
            ),
        ))

        # Divider
        menu_items.append(ft.PopupMenuItem())

        # History items
        for item in self._history_items:
            # Handle both dict and SearchHistoryEntry objects
            if hasattr(item, 'query'):
                query = item.query
                result_count = getattr(item, 'result_count', 0)
            else:
                query = item.get('query', '')
                result_count = item.get('result_count', 0)

            menu_items.append(ft.PopupMenuItem(
                content=ft.Row(
                    controls=[
                        ft.Text(
                            query,
                            size=14,
                            color=MD3Colors.get_on_surface(is_dark),
                            expand=True,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(str(result_count), size=12, color=secondary_color),
                    ],
                    spacing=8,
                    width=200,
                ),
                on_click=lambda e, q=query: self._on_history_item_clicked(q),
            ))

        # Divider + Clear button
        menu_items.append(ft.PopupMenuItem())
        menu_items.append(ft.PopupMenuItem(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.DELETE_OUTLINE, size=16, color=secondary_color),
                    ft.Text("Clear History", size=12, color=secondary_color),
                ],
                spacing=8,
            ),
            on_click=self._on_clear_history_clicked,
        ))

        self.history_button.items = menu_items
        self.history_button.visible = True

        self._safe_update()

    def _on_history_item_clicked(self, query: str):
        """Handle history item selection."""
        is_dark = self._registry.is_dark
        self.search_field.value = query
        self.clear_button.visible = True
        self.search_icon.color = MD3Colors.get_primary(is_dark)

        self._safe_update()

        if self.on_history_selected_callback:
            result = self.on_history_selected_callback(query)
            if asyncio.iscoroutine(result):
                # Schedule the coroutine since this is called from a sync context - REGISTER THE TASK
                task = asyncio.create_task(result)
                register_task(task, "history_selection")

    async def _on_clear_history_clicked(self, e):
        """Handle clear history click."""
        from dlss_updater.search_service import search_service
        await search_service.clear_search_history()
        self._history_items = []
        self.history_button.visible = False

        self._safe_update()

    def set_value(self, value: str):
        """Set the search field value programmatically."""
        is_dark = self._registry.is_dark
        self.search_field.value = value
        self.clear_button.visible = len(value) > 0
        self.search_icon.color = (
            MD3Colors.get_primary(is_dark) if value else MD3Colors.get_themed("icon_default", is_dark)
        )

        # Auto-expand if setting a non-empty value in expandable mode
        if self._expandable and value and not self._is_expanded:
            self._is_expanded = True
            self.content = self._search_row_content
            self.width = self._expanded_width

        self._safe_update()

    def get_value(self) -> str:
        """Get the current search field value."""
        return self.search_field.value or ""

    def focus(self):
        """Focus the search field."""
        self.search_field.focus()

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """
        Return themed property mappings for theme switching.

        Returns:
            Dict mapping property paths to (dark_value, light_value) tuples.
        """
        return {
            # Search field colors
            "search_field.bgcolor": MD3Colors.get_themed_pair("surface"),
            "search_field.border_color": MD3Colors.get_themed_pair("outline"),
            "search_field.focused_border_color": MD3Colors.get_themed_pair("primary"),
            # Icons
            "search_icon.color": MD3Colors.get_themed_pair("icon_default"),
            "clear_button.icon_color": MD3Colors.get_themed_pair("text_secondary"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """
        Apply theme with optional cascade delay.

        Overrides base implementation to handle TextField text styles
        which require special handling.
        """
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            # Apply standard themed properties
            properties = self.get_themed_properties()
            for prop_path, (dark_val, light_val) in properties.items():
                value = dark_val if is_dark else light_val
                self._set_nested_property(prop_path, value)

            # Handle TextField text styles (require TextStyle objects)
            self.search_field.hint_style = ft.TextStyle(
                color=MD3Colors.get_text_secondary(is_dark)
            )
            self.search_field.text_style = ft.TextStyle(
                color=MD3Colors.get_on_surface(is_dark)
            )

            # Update icon color based on current search state
            has_text = bool(self.search_field.value)
            self.search_icon.color = (
                MD3Colors.get_primary(is_dark) if has_text
                else MD3Colors.get_themed("icon_default", is_dark)
            )

            # Update border based on focus state
            if self._is_focused:
                self.search_field.border_color = MD3Colors.get_primary(is_dark)
            else:
                self.search_field.border_color = MD3Colors.get_outline(is_dark)

            # Refresh history items if they exist (to update their colors)
            if self._history_items:
                self.update_history(self._history_items)

            if hasattr(self, 'update'):
                self.update()

        except Exception:
            # Silent fail - component may have been garbage collected
            pass

    async def cleanup(self):
        """Clean up resources when search bar is removed.

        Cancels pending tasks and breaks reference cycles to allow garbage collection.
        """
        # Cancel debounce task
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass

        # Unregister from theme system
        self._unregister_theme_aware()

        # Clear callbacks to break reference cycles
        self.on_search_callback = None
        self.on_clear_callback = None
        self.on_history_selected_callback = None
        self.on_focus_change_callback = None

        # Clear history items
        self._history_items.clear()
