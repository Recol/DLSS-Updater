"""
Steam Display Edit Dialog
Lets users override a game's display picture and name from Steam (issue #202).
"""

import asyncio
import logging
from typing import Callable

import flet as ft

from dlss_updater.models import Game
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import get_theme_registry

_SEARCH_DEBOUNCE = 0.30
_SEARCH_LIMIT = 20


class SteamResolveDialog:
    """Modal dialog for overriding a game's display picture and name.

    Searches the local FTS5 Steam app list (always available). Steam API
    credentials improve result quality when configured.

    Callback: on_resolved(override_steam_app_id: int, display_name_override: str)
    Pass (0, "") to signal a cleared override.
    """

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        game: Game,
        on_resolved: Callable[[int, str], None] | None = None,
    ):
        self._page_ref = page
        self.logger = logger
        self.game = game
        self.on_resolved = on_resolved
        self._registry = get_theme_registry()

        self._selected_app_id: int | None = None
        self._selected_name: str | None = None
        self._search_task: asyncio.Task | None = None
        self._results: list[tuple[int, str]] = []

        self._result_list: ft.ListView | None = None
        self._save_button: ft.FilledButton | None = None
        self._search_field: ft.TextField | None = None
        self._status_text: ft.Text | None = None

    async def show(self):
        is_dark = self._registry.is_dark
        dialog = self._build_dialog(is_dark)
        self._page_ref.show_dialog(dialog)
        self._page_ref.update()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_dialog(self, is_dark: bool) -> ft.AlertDialog:
        surface = MD3Colors.get_surface(is_dark)
        text_primary = MD3Colors.get_text_primary(is_dark)
        text_secondary = MD3Colors.get_text_secondary(is_dark)
        primary = MD3Colors.get_primary(is_dark)
        outline = ft.Colors.with_opacity(0.15, text_secondary)

        # ---- Current state row ----
        if self.game.override_steam_app_id:
            current_row = ft.Row(
                controls=[
                    ft.Icon(ft.Icons.IMAGE, size=15, color=primary),
                    ft.Text(
                        f"{self.game.display_name}  (App ID {self.game.override_steam_app_id})",
                        size=12,
                        color=text_secondary,
                        expand=True,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.TextButton(
                        "Reset to default",
                        style=ft.ButtonStyle(color=ft.Colors.RED_400),
                        on_click=self._on_clear_clicked,
                    ),
                ],
                spacing=6,
            )
        else:
            current_row = ft.Row(
                controls=[
                    ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED, size=15, color=text_secondary),
                    ft.Text(
                        "Using default name and image. Search below to customise.",
                        size=12,
                        color=text_secondary,
                        italic=True,
                    ),
                ],
                spacing=6,
            )

        # ---- Steam API note ----
        api_note = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=13, color=primary),
                    ft.Text(
                        "Steam API credentials give better search results — configure in Settings.",
                        size=11,
                        color=text_secondary,
                        italic=True,
                        expand=True,
                    ),
                ],
                spacing=6,
            ),
            bgcolor=ft.Colors.with_opacity(0.07, primary),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        )

        # ---- Search field ----
        self._search_field = ft.TextField(
            label=f'Search Steam for "{self.game.name}"',
            hint_text="e.g. Cyberpunk 2077",
            prefix_icon=ft.Icons.SEARCH,
            autofocus=True,
            border_color=primary,
            focused_border_color=primary,
            color=text_primary,
            label_style=ft.TextStyle(color=text_secondary),
            on_change=self._on_search_changed,
            expand=True,
        )

        self._status_text = ft.Text(
            "Type to search — selecting a result sets the display image and name.",
            size=12,
            color=text_secondary,
            italic=True,
        )

        self._result_list = ft.ListView(controls=[], spacing=2, height=250)

        self._save_button = ft.FilledButton(
            "Apply",
            icon=ft.Icons.CHECK,
            disabled=True,
            on_click=self._on_save_clicked,
        )

        content = ft.Column(
            controls=[
                ft.Container(
                    content=current_row,
                    padding=ft.Padding.only(top=2, bottom=6),
                ),
                ft.Divider(height=1, color=outline),
                api_note,
                ft.Container(height=4),
                self._search_field,
                ft.Container(height=4),
                self._status_text,
                ft.Container(
                    content=self._result_list,
                    border=ft.Border.all(1, outline),
                    border_radius=8,
                ),
            ],
            spacing=6,
            tight=True,
        )

        return ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.EDIT, size=20, color=primary),
                    ft.Text(
                        f'Edit display — {self.game.name}',
                        color=text_primary,
                        size=16,
                        weight=ft.FontWeight.BOLD,
                        expand=True,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(content=content, width=500, padding=ft.Padding.only(top=8)),
            bgcolor=surface,
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._close()),
                self._save_button,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search_changed(self, e):
        if self._page_ref:
            self._page_ref.run_task(self._debounced_search, e.control.value)

    async def _debounced_search(self, query: str):
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()
        query = query.strip()
        if not query:
            self._clear_results("Type to search — selecting a result sets the display image and name.")
            return
        self._search_task = asyncio.create_task(self._run_search(query))

    async def _run_search(self, query: str):
        from dlss_updater.steam_integration import search_steam_apps
        await asyncio.sleep(_SEARCH_DEBOUNCE)
        self._set_status("Searching…")
        try:
            results = await search_steam_apps(query, _SEARCH_LIMIT)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.logger.warning(f"Steam search error: {exc}")
            self._set_status("Search failed — check logs.")
            return
        self._results = results
        self._render_results(results)

    def _render_results(self, results: list[tuple[int, str]]):
        is_dark = self._registry.is_dark
        primary = MD3Colors.get_primary(is_dark)
        text_primary = MD3Colors.get_text_primary(is_dark)
        text_secondary = MD3Colors.get_text_secondary(is_dark)

        if not results:
            self._set_status("No results — try a different name.")
            self._result_list.controls = []
            self._page_ref.update()
            return

        self._set_status(f"{len(results)} result(s) — click one to select.")

        rows = []
        for app_id, name in results:
            is_selected = app_id == self._selected_app_id
            rows.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Container(
                                content=ft.Image(
                                    src=f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/capsule_sm_120.jpg",
                                    width=40,
                                    height=19,
                                    fit=ft.BoxFit.COVER,
                                    border_radius=3,
                                    error_content=ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=16, color=text_secondary),
                                ),
                                border_radius=3,
                                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                            ),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        name,
                                        size=13,
                                        color=text_primary,
                                        weight=ft.FontWeight.W_500,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(f"App ID: {app_id}", size=11, color=text_secondary),
                                ],
                                spacing=1,
                                tight=True,
                                expand=True,
                            ),
                            ft.Icon(ft.Icons.CHECK_CIRCLE, size=18, color=primary, visible=is_selected),
                        ],
                        spacing=10,
                    ),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                    border_radius=6,
                    bgcolor=ft.Colors.with_opacity(0.12, primary) if is_selected else ft.Colors.TRANSPARENT,
                    on_click=lambda e, aid=app_id, n=name: self._on_result_selected(aid, n),
                    ink=True,
                )
            )

        self._result_list.controls = rows
        self._page_ref.update()

    # ------------------------------------------------------------------
    # Selection / actions
    # ------------------------------------------------------------------

    def _on_result_selected(self, app_id: int, name: str):
        self._selected_app_id = app_id
        self._selected_name = name
        if self._save_button:
            self._save_button.disabled = False
        self._render_results(self._results)

    def _on_save_clicked(self, e):
        if self._selected_app_id and self._selected_name and self._page_ref:
            self._page_ref.run_task(self._perform_save, self._selected_app_id, self._selected_name)

    async def _perform_save(self, app_id: int, name: str):
        from dlss_updater.database import db_manager
        self._set_status("Saving…")
        old_app_id = self.game.effective_steam_app_id
        if old_app_id and old_app_id != app_id:
            try:
                await db_manager.delete_cached_images_for_app_ids([old_app_id])
                await db_manager.clear_fetch_failed_for_app_ids([old_app_id])
            except Exception as exc:
                self.logger.warning(f"Could not clear old image cache: {exc}")
        success = await db_manager.set_game_override(self.game.id, app_id, name)
        if not success:
            self._set_status("Failed to save — check logs.")
            return
        self.logger.info(f"Display override set for '{self.game.name}': {name} (App ID {app_id})")
        self._close()
        if self.on_resolved:
            self.on_resolved(app_id, name)

    def _on_clear_clicked(self, e):
        if self._page_ref:
            self._page_ref.run_task(self._perform_clear)

    async def _perform_clear(self):
        from dlss_updater.database import db_manager
        self._set_status("Resetting…")
        success = await db_manager.set_game_override(self.game.id, None, None)
        if not success:
            self._set_status("Failed to reset — check logs.")
            return
        self.logger.info(f"Display override cleared for '{self.game.name}'")
        self._close()
        if self.on_resolved:
            self.on_resolved(0, "")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str):
        if self._status_text:
            self._status_text.value = msg
            try:
                self._page_ref.update()
            except Exception:
                pass

    def _clear_results(self, status: str):
        self._results = []
        self._set_status(status)
        if self._result_list:
            self._result_list.controls = []
        try:
            self._page_ref.update()
        except Exception:
            pass

    def _close(self):
        try:
            self._page_ref.pop_dialog()
            self._page_ref.update()
        except Exception:
            pass
