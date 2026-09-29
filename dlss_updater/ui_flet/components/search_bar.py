"""
Search Bar Component

GameSearchBar wraps Flet's native Material search bar (ft.SearchBar /
SearchAnchor) with live game-name suggestions and recent-search history in
the dropdown view. Theming comes from the page-level Material 3 theme.

Thread-safe for free-threaded Python 3.14+.
"""

import asyncio
import anyio
import inspect
from typing import Callable, Any
import flet as ft

from dlss_updater.task_registry import register_task


class GameSearchBar(ft.Container):
    """
    Native Material search bar (ft.SearchBar / SearchAnchor) with live game-name
    suggestions and recent-search history in the dropdown view.

    Exposes the surface GamesView uses (the legacy custom SearchBar it
    replaced has been removed):
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
        await anyio.sleep(0.05)
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
        await anyio.sleep(0.15)
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
        if inspect.iscoroutine(result):
            register_task(asyncio.create_task(result), "native_search_dispatch")

    async def _debounced_search(self, query: str):
        try:
            await anyio.sleep(0.150)
            if self.on_search_callback:
                result = self.on_search_callback(query)
                if inspect.iscoroutine(result):
                    await result
        except anyio.get_cancelled_exc_class():
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
            except anyio.get_cancelled_exc_class():
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
                if inspect.iscoroutine(result):
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
            if inspect.iscoroutine(result):
                await result
        # Pressing the (x) clears the search and collapses the bar to its icon
        try:
            await self._bar.close_view("")
        except Exception:
            pass
        self._collapse_if_empty()

    # ----- public API (used by GamesView) -----

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
            except anyio.get_cancelled_exc_class():
                pass
        self.on_search_callback = None
        self.on_clear_callback = None
        self.on_history_selected_callback = None
        self.get_suggestions_callback = None
        self._history_items.clear()
