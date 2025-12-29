"""
Search Bar Component with History Dropdown

A styled search input with:
- Search icon and placeholder text
- Clear button when text present
- Search history popup menu (overlay, no layout shift)
- MD3 dark theme styling

Thread-safe for free-threaded Python 3.14+.
"""

import asyncio
from typing import Callable, List, Optional, Dict, Any, Union
import flet as ft

from dlss_updater.logger import setup_logger

logger = setup_logger()

# MD3 Dark Theme Colors for Search
SEARCH_COLORS = {
    "field_bg": "#1A1A1A",
    "field_border": "#3C3C3C",
    "field_border_focused": "#2D6E88",
    "field_text": "#E4E2E0",
    "placeholder": "#888888",
    "dropdown_bg": "#2E2E2E",
    "dropdown_border": "#3C3C3C",
    "history_hover": "rgba(45, 110, 136, 0.12)",
    "icon_default": "#888888",
    "icon_active": "#2D6E88",
    "clear_button": "#888888",
    "clear_button_hover": "#E4E2E0",
}


class SearchBar(ft.Container):
    """
    Search bar component with history popup menu.

    Usage:
        search_bar = SearchBar(
            on_search=on_search_callback,
            on_clear=on_clear_callback,
            on_history_selected=on_history_selected_callback,
        )
    """

    def __init__(
        self,
        on_search: Optional[Callable[[str], None]] = None,
        on_clear: Optional[Callable[[], None]] = None,
        on_history_selected: Optional[Callable[[str], None]] = None,
        on_focus_change: Optional[Callable[[bool], None]] = None,
        placeholder: str = "Search games...",
        width: Optional[int] = None,
    ):
        super().__init__()
        self.on_search_callback = on_search
        self.on_clear_callback = on_clear
        self.on_history_selected_callback = on_history_selected
        self.on_focus_change_callback = on_focus_change
        self.placeholder_text = placeholder
        self.search_width = width

        # State
        self._is_focused = False
        self._history_items: List[Any] = []
        self._debounce_task: Optional[asyncio.Task] = None

        # Build UI
        self._build_ui()

    def _build_ui(self):
        """Build the search bar UI with PopupMenuButton for history."""
        # Search icon
        self.search_icon = ft.Icon(
            ft.Icons.SEARCH,
            size=20,
            color=SEARCH_COLORS["icon_default"],
        )

        # Clear button (hidden by default)
        self.clear_button = ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_size=18,
            icon_color=SEARCH_COLORS["clear_button"],
            tooltip="Clear search",
            on_click=self._on_clear_clicked,
            visible=False,
            width=32,
            height=32,
        )

        # History popup menu button (hidden until history exists)
        self.history_button = ft.PopupMenuButton(
            icon=ft.Icons.HISTORY,
            icon_color=SEARCH_COLORS["icon_default"],
            icon_size=20,
            tooltip="Recent searches",
            items=[],
            visible=False,
        )

        # Search text field
        self.search_field = ft.TextField(
            hint_text=self.placeholder_text,
            hint_style=ft.TextStyle(color=SEARCH_COLORS["placeholder"]),
            text_style=ft.TextStyle(color=SEARCH_COLORS["field_text"]),
            border_color=SEARCH_COLORS["field_border"],
            focused_border_color=SEARCH_COLORS["field_border_focused"],
            bgcolor=SEARCH_COLORS["field_bg"],
            border_radius=8,
            content_padding=ft.padding.only(left=40, right=40, top=8, bottom=8),
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

        # Row with search field + history button
        search_row = ft.Row(
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

        # Set container properties
        self.content = search_row
        self.width = self.search_width

    async def _on_text_changed(self, e):
        """Handle text input changes with debouncing."""
        query = e.control.value or ""

        # Update clear button visibility
        self.clear_button.visible = len(query) > 0
        self.clear_button.update()

        # Update search icon color
        self.search_icon.color = (
            SEARCH_COLORS["icon_active"] if query else SEARCH_COLORS["icon_default"]
        )
        self.search_icon.update()

        # Cancel existing debounce task
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        # Debounce search callback (150ms)
        if self.on_search_callback:
            self._debounce_task = asyncio.create_task(
                self._debounced_search(query)
            )

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
        self.search_field.border_color = SEARCH_COLORS["field_border_focused"]

        if self.on_focus_change_callback:
            result = self.on_focus_change_callback(True)
            if asyncio.iscoroutine(result):
                await result

    async def _on_blur(self, e):
        """Handle field blur."""
        self._is_focused = False

        if not self.search_field.value:
            self.search_field.border_color = SEARCH_COLORS["field_border"]

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
        self.search_field.value = ""
        self.clear_button.visible = False
        self.search_icon.color = SEARCH_COLORS["icon_default"]

        if self.page:
            self.page.update()

        if self.on_clear_callback:
            result = self.on_clear_callback()
            if asyncio.iscoroutine(result):
                await result

    def update_history(self, history_items: List[Any]):
        """
        Update search history popup menu items.

        Args:
            history_items: List of SearchHistoryEntry objects or dicts
        """
        self._history_items = history_items[:10]  # Max 10 items

        if not self._history_items:
            self.history_button.visible = False
            if self.page:
                self.page.update()
            return

        menu_items = []

        # Header (non-clickable)
        menu_items.append(ft.PopupMenuItem(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.HISTORY, size=16, color="#888888"),
                    ft.Text("Recent Searches", size=12, color="#888888"),
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
                            color=SEARCH_COLORS["field_text"],
                            expand=True,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(str(result_count), size=12, color="#888888"),
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
                    ft.Icon(ft.Icons.DELETE_OUTLINE, size=16, color="#888888"),
                    ft.Text("Clear History", size=12, color="#888888"),
                ],
                spacing=8,
            ),
            on_click=self._on_clear_history_clicked,
        ))

        self.history_button.items = menu_items
        self.history_button.visible = True

        if self.page:
            self.page.update()

    def _on_history_item_clicked(self, query: str):
        """Handle history item selection."""
        self.search_field.value = query
        self.clear_button.visible = True
        self.search_icon.color = SEARCH_COLORS["icon_active"]

        if self.page:
            self.page.update()

        if self.on_history_selected_callback:
            result = self.on_history_selected_callback(query)
            if asyncio.iscoroutine(result):
                # Schedule the coroutine since this is called from a sync context
                asyncio.create_task(result)

    async def _on_clear_history_clicked(self, e):
        """Handle clear history click."""
        from dlss_updater.search_service import search_service
        await search_service.clear_search_history()
        self._history_items = []
        self.history_button.visible = False

        if self.page:
            self.page.update()

    def set_value(self, value: str):
        """Set the search field value programmatically."""
        self.search_field.value = value
        self.clear_button.visible = len(value) > 0
        self.search_icon.color = (
            SEARCH_COLORS["icon_active"] if value else SEARCH_COLORS["icon_default"]
        )

        if self.page:
            self.page.update()

    def get_value(self) -> str:
        """Get the current search field value."""
        return self.search_field.value or ""

    def focus(self):
        """Focus the search field."""
        self.search_field.focus()
