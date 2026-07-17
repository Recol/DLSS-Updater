"""
Steam Display Edit Dialog
Lets users override a game's display picture and name from Steam (issue #202).
"""

import asyncio
import anyio
import logging
from typing import Callable

import flet as ft

from dlss_updater.models import Game
from dlss_updater.ui_flet.theme.colors import MD3Colors, TabColors
from dlss_updater.ui_flet.theme.theme_aware import get_theme_registry
from dlss_updater.ui_flet.components.hero_surface import (
    build_brand_wash,
    build_pill,
    themed_accent,
)

_SEARCH_DEBOUNCE = 0.30
_SEARCH_LIMIT = 20
# Skip the (rate-limited) Steam Store API for very short queries — the local
# FTS index answers those instantly and short terms waste the ~200 req / 5 min budget.
_MIN_API_QUERY_LEN = 3


class SteamResolveDialog:
    """Modal dialog for overriding a game's display picture and name.

    Searches the local FTS5 Steam app list (always available) and the public
    Steam Store API — no credentials required (issue #246 removed the API-key
    dependency). Results merge with the local FTS entry winning on dedupe.

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
        # The query the UI currently reflects. Used to discard stale/out-of-order
        # async search completions (a slow Steam API response for an old query
        # must not overwrite results for the query the user has since typed).
        self._active_query: str = ""

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
        surface_variant = MD3Colors.get_surface_variant(is_dark)
        text_primary = MD3Colors.get_text_primary(is_dark)
        text_secondary = MD3Colors.get_text_secondary(is_dark)
        primary = MD3Colors.get_primary(is_dark)
        success = MD3Colors.get_success(is_dark)
        outline = ft.Colors.with_opacity(0.15, text_secondary)
        header_accent = themed_accent((TabColors.GAMES, TabColors.GAMES_LIGHT), is_dark)

        # ---- Current-state pill (hero_surface.build_pill) ----
        if self.game.override_steam_app_id:
            # Guard against very long custom names overflowing the header band.
            name_disp = self.game.display_name
            if len(name_disp) > 30:
                name_disp = name_disp[:29] + "…"
            state_pill = build_pill(
                f"Custom · {name_disp} · App {self.game.override_steam_app_id}",
                icon=ft.Icons.CHECK_CIRCLE,
                bgcolor=ft.Colors.with_opacity(0.15, success),
                text_color=success,
                icon_color=success,
            )
        else:
            state_pill = build_pill(
                "Default image & name",
                icon=ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED,
                bgcolor=ft.Colors.with_opacity(0.12, text_secondary),
                text_color=text_secondary,
                icon_color=text_secondary,
            )

        # ---- Pill row: state pill + (Reset-to-default only when an override exists) ----
        pill_row_controls: list[ft.Control] = [state_pill, ft.Container(expand=True)]
        if self.game.override_steam_app_id:
            pill_row_controls.append(
                ft.TextButton(
                    "Reset to default",
                    icon=ft.Icons.RESTART_ALT,
                    style=ft.ButtonStyle(color=ft.Colors.RED_400),
                    on_click=self._on_clear_clicked,
                )
            )

        # ---- Search field (integrated inside the hero band) ----
        self._search_field = ft.TextField(
            label=f'Search Steam for "{self.game.name}"',
            hint_text="e.g. Cyberpunk 2077",
            prefix_icon=ft.Icons.SEARCH,
            autofocus=True,
            filled=True,
            fill_color=ft.Colors.with_opacity(0.85, surface),
            border_color=ft.Colors.with_opacity(0.4, primary),
            focused_border_color=primary,
            border_radius=10,
            color=text_primary,
            label_style=ft.TextStyle(color=text_secondary),
            on_change=self._on_search_changed,
        )

        # ---- Hero header (brand-washed, shadowed) ----
        # Opaque wash on a shadowed Container is the sanctioned pattern
        # (CLAUDE.md pitfall #1) — build_brand_wash emits a fully opaque
        # gradient, never alpha stops, so the box-shadow can't bleed through.
        header_wash = ft.Container(
            gradient=build_brand_wash(header_accent, is_dark),
            left=0,
            top=0,
            right=0,
            bottom=0,
        )
        header_foreground = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.EDIT, size=22, color=primary),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        "Edit display",
                                        size=16,
                                        weight=ft.FontWeight.BOLD,
                                        color=text_primary,
                                    ),
                                    ft.Text(
                                        self.game.name,
                                        size=12,
                                        color=text_secondary,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=1,
                                tight=True,
                                expand=True,
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        controls=pill_row_controls,
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._search_field,
                ],
                spacing=12,
            ),
            padding=16,
        )
        header = ft.Container(
            content=ft.Stack(controls=[header_wash, header_foreground]),
            bgcolor=surface_variant,
            border_radius=ft.BorderRadius.only(top_left=12, top_right=12),
            shadow=ft.BoxShadow(
                spread_radius=1,
                blur_radius=14,
                color=ft.Colors.with_opacity(0.35, ft.Colors.BLACK),
                offset=ft.Offset(0, 4),
                blur_style=ft.BlurStyle.NORMAL,
            ),
        )

        # ---- Status line + virtualized results ----
        self._status_text = ft.Text(
            "Type to search — selecting a result sets the display image and name.",
            size=12,
            color=text_secondary,
            italic=True,
        )
        # item_extent enables virtualization (only visible rows are live), which
        # also caps the CDN capsule-image burst to the visible window.
        self._result_list = ft.ListView(
            controls=[],
            item_extent=64,
            expand=True,
            spacing=0,
        )

        # Bounded-height body: Column (NOT tight) with the results wrapper set to
        # expand=True directly under the status line so no dead gap can form.
        body = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            content=ft.Column(
                controls=[
                    self._status_text,
                    ft.Container(
                        content=self._result_list,
                        expand=True,
                        border=ft.Border.all(1, outline),
                        border_radius=10,
                        padding=ft.Padding.all(4),
                    ),
                ],
                spacing=8,
            ),
        )

        self._save_button = ft.FilledButton(
            "Apply",
            icon=ft.Icons.CHECK,
            disabled=True,
            on_click=self._on_save_clicked,
        )

        content_container = ft.Container(
            width=548,
            height=544,
            content=ft.Column(controls=[header, body], spacing=0),
        )

        return ft.AlertDialog(
            modal=True,
            content=content_container,
            content_padding=ft.Padding.all(0),
            bgcolor=surface,
            shape=ft.RoundedRectangleBorder(radius=12),
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
        self._active_query = query
        if not query:
            self._clear_results("Type to search — selecting a result sets the display image and name.")
            return
        self._search_task = asyncio.create_task(self._run_search(query))

    async def _run_search(self, query: str):
        """Two-stage search: local FTS first (instant, offline), then the Steam
        Store API merged in when it arrives (works without credentials).

        Every stage re-checks self._active_query so a stale keystroke's results
        can never clobber the current query's view (out-of-order async guard).
        """
        from dlss_updater.steam_integration import search_steam_apps, search_store_apps

        await anyio.sleep(_SEARCH_DEBOUNCE)
        if query != self._active_query:
            return
        self._set_status("Searching…")

        # Stage 1 — local FTS5 index (instant, always available).
        try:
            local = await search_steam_apps(query, _SEARCH_LIMIT)
        except anyio.get_cancelled_exc_class():
            return
        except Exception as exc:
            self.logger.warning(f"Local Steam search error: {exc}")
            local = []

        if query != self._active_query:
            return

        query_api = len(query) >= _MIN_API_QUERY_LEN
        merged = self._merge_results(local, [])
        self._results = merged
        self._render_results(merged, searching_more=query_api)

        # Stage 2 — Steam Store API (punctuation-tolerant, no credentials needed).
        if not query_api:
            return
        try:
            store = await search_store_apps(query, _SEARCH_LIMIT)
        except anyio.get_cancelled_exc_class():
            return
        except Exception as exc:
            self.logger.debug(f"Steam store search error: {exc}")
            store = []

        # Discard if the user has moved on since the request was issued.
        if query != self._active_query:
            return

        merged = self._merge_results(local, store)
        self._results = merged
        self._render_results(merged, searching_more=False)

    def _merge_results(
        self,
        local: list[tuple[int, str]],
        store: list[tuple[int, str]],
    ) -> list[tuple[int, str]]:
        """Merge local + store results, dedupe by app_id (local wins), cap to limit.

        When a Steam API key is configured, owned games are floated to the top as
        a ranking boost (stable otherwise) — never as a substitute for search.
        """
        from dlss_updater.steam_integration import steam_integration

        seen: set[int] = set()
        merged: list[tuple[int, str]] = []
        for app_id, name in (*local, *store):
            if app_id in seen:
                continue
            seen.add(app_id)
            merged.append((app_id, name))

        owned = steam_integration.get_owned_app_ids()
        if owned:
            # Stable sort: owned entries first, original relative order preserved.
            merged.sort(key=lambda r: r[0] not in owned)

        return merged[:_SEARCH_LIMIT]

    def _render_results(self, results: list[tuple[int, str]], searching_more: bool = False):
        is_dark = self._registry.is_dark
        primary = MD3Colors.get_primary(is_dark)
        text_primary = MD3Colors.get_text_primary(is_dark)
        text_secondary = MD3Colors.get_text_secondary(is_dark)
        skeleton_start = MD3Colors.get_themed("skeleton_start", is_dark)
        skeleton_highlight = MD3Colors.get_themed("skeleton_highlight", is_dark)
        skeleton_base = MD3Colors.get_themed("skeleton_base", is_dark)

        if not results:
            # Don't declare "no results" while the Steam API is still in flight.
            self._set_status("Searching Steam…" if searching_more else "No results — try a different name.")
            self._result_list.controls = []
            self._page_ref.update()
            return

        if searching_more:
            self._set_status(f"{len(results)} result(s) so far — searching Steam…")
        else:
            self._set_status(f"{len(results)} result(s) — click one to select.")

        rows = []
        for app_id, name in results:
            is_selected = app_id == self._selected_app_id

            # Capsule thumb: Shimmer placeholder sits BEHIND the Image, so while
            # the CDN capsule loads (transparent) the shimmer shows through; once
            # loaded the opaque COVER image hides it. error_content is the fallback.
            thumb = ft.Container(
                width=92,
                height=35,
                border_radius=6,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                content=ft.Stack(
                    controls=[
                        ft.Shimmer(
                            base_color=skeleton_start,
                            highlight_color=skeleton_highlight,
                            period=1200,
                            direction=ft.ShimmerDirection.LTR,
                            content=ft.Container(width=92, height=35, bgcolor=skeleton_base),
                        ),
                        ft.Image(
                            src=f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/capsule_231x87.jpg",
                            width=92,
                            height=35,
                            fit=ft.BoxFit.COVER,
                            error_content=ft.Container(
                                width=92,
                                height=35,
                                bgcolor=skeleton_base,
                                alignment=ft.Alignment.CENTER,
                                content=ft.Icon(
                                    ft.Icons.VIDEOGAME_ASSET, size=18, color=text_secondary
                                ),
                            ),
                        ),
                    ],
                ),
            )

            info = ft.Column(
                controls=[
                    ft.Text(
                        name,
                        size=14,
                        color=text_primary,
                        weight=ft.FontWeight.W_600,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(f"App ID: {app_id}", size=11, color=text_secondary),
                ],
                spacing=2,
                tight=True,
                expand=True,
            )

            row_controls: list[ft.Control] = [thumb, info]
            if is_selected:
                # Selected indicator: build_pill-style "Selected" badge with check
                # icon (replaces the bare check icon).
                row_controls.append(
                    build_pill(
                        "Selected",
                        icon=ft.Icons.CHECK,
                        bgcolor=primary,
                        text_color=ft.Colors.WHITE,
                        icon_color=ft.Colors.WHITE,
                    )
                )

            rows.append(
                ft.Container(
                    content=ft.Row(
                        controls=row_controls,
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                    border_radius=10,
                    bgcolor=ft.Colors.with_opacity(0.12, primary)
                    if is_selected
                    else ft.Colors.TRANSPARENT,
                    # Transparent 1px border when unselected keeps row geometry
                    # stable (no reflow) when the selected border appears.
                    border=ft.Border.all(1, primary)
                    if is_selected
                    else ft.Border.all(1, ft.Colors.TRANSPARENT),
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
