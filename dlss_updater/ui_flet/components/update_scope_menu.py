"""The run button for a bulk update, and the checklist that scopes it.

The host hands in the button it already designed - icon circle, label, fill and
all - as trigger_content. Clicking it opens the technology checklist, and
the menu's own accent-filled row starts the run. The button therefore keeps its
design instead of surrendering its right edge to a caret, and all three hosts
(Launchers bar, Hub CTA, Games header) gain scope control identically - the
Games header previously had none at all.

Opening a menu costs the common path one extra click. That is the deliberate
trade: a run overwrites DLLs across every scanned game, so the user sees what
it will touch before it starts.

A MenuBar rather than a PopupMenuButton: PopupMenuItem pops the route on every
tap, so a checklist would close on each toggle. MenuItemButton(close_on_click=
False) is the only native way to keep a multi-toggle menu open.

The menu deliberately cannot reach FSR_RadianceCache. Preview components stay a
Settings-only, separately-acknowledged opt-in; scope carries the saved value
through untouched.
"""

from __future__ import annotations

import inspect
from typing import Callable

import flet as ft

from dlss_updater import update_scope
from dlss_updater.ui_flet.theme.colors import MD3Colors, TechnologyColors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


class UpdateScopeMenu(ThemeAwareMixin, ft.Container):
    """The host's own run button, wired to open a technology checklist."""

    _theme_priority = 20

    def __init__(
        self,
        page: ft.Page | None,
        get_scope: Callable[[], frozenset[str]],
        on_scope_changed: Callable[[frozenset[str]], None],
        accent: str,
        trigger_content: ft.Control,
        on_run: Callable | None = None,
        run_label: Callable[[], str] | None = None,
        width: int | None = None,
        height: int | None = None,
        radius: int = 12,
        tooltip: str | None = None,
    ):
        self._page_ref = page
        self._get_scope = get_scope
        self._on_scope_changed = on_scope_changed
        self._accent = accent
        self._on_run = on_run
        self._run_label = run_label or (lambda: "Update now")
        self._radius = radius
        self._registry = get_theme_registry()
        self._items: dict[str, ft.MenuItemButton] = {}
        self._default_item: ft.MenuItemButton | None = None
        self._run_item: ft.MenuItemButton | None = None

        # The host's finished button. It is the menu's trigger, so nothing is
        # bolted onto its edge and its design carries through untouched.
        self._trigger_content = trigger_content

        super().__init__(
            content=ft.MenuBar(
                style=self._menu_bar_style(),
                controls=[self._build_submenu()],
            ),
            width=width,
            height=height,
            alignment=ft.Alignment.CENTER,
            border_radius=radius,
            tooltip=tooltip or "Choose what this update touches, then start it",
        )

        self._apply_colors(self._registry.is_dark)
        self._sync_badge()
        self._register_theme_aware()

    # ---- construction -------------------------------------------------

    def _menu_bar_style(self) -> ft.MenuStyle:
        """The MenuBar itself is invisible chrome - only the trigger shows."""
        return ft.MenuStyle(
            bgcolor=ft.Colors.TRANSPARENT,
            elevation=0,
            padding=ft.Padding.all(0),
        )

    def _build_submenu(self) -> ft.SubmenuButton:
        """Items are built UPFRONT: on_open fires after the menu renders, so
        anything added there does not appear until the next open."""
        is_dark = self._registry.is_dark
        scope = self._get_scope()
        self._items = {}

        rows: list[ft.Control] = [self._header(is_dark)]

        for token in sorted(update_scope.all_technologies()):
            item = ft.MenuItemButton(
                content=ft.Text(
                    token,
                    size=13,
                    weight=ft.FontWeight.W_500,
                    color=MD3Colors.get_on_surface(is_dark),
                ),
                leading=self._tick(token, token in scope),
                trailing=self._swatch(token, token in scope),
                close_on_click=False,
                on_click=self._make_toggle(token),
                style=self._row_style(token),
            )
            self._items[token] = item
            rows.append(item)

        rows.append(ft.Divider(height=1, color=MD3Colors.get_outline(is_dark)))

        self._default_item = ft.MenuItemButton(
            content=ft.Text("Make this my default", size=13),
            leading=ft.Icon(ft.Icons.CHECK_BOX_OUTLINE_BLANK, size=18),
            close_on_click=False,
            on_click=self._on_make_default,
        )
        rows.append(self._default_item)
        rows.append(
            ft.MenuItemButton(
                content=ft.Text("Reset to my defaults", size=13),
                leading=ft.Icon(ft.Icons.RESTART_ALT, size=18),
                on_click=self._on_reset,
            )
        )

        if self._on_run is not None:
            rows.append(ft.Divider(height=1, color=MD3Colors.get_outline(is_dark)))
            self._run_item = self._build_run_item(is_dark)
            rows.append(self._run_item)

        return ft.SubmenuButton(
            content=self._trigger_content,
            # Small drop below the button.
            alignment_offset=ft.Offset(0, 4),
            style=self._trigger_style(),
            menu_style=ft.MenuStyle(
                bgcolor=MD3Colors.get_surface_container(is_dark),
                elevation=8,
                padding=ft.Padding.symmetric(vertical=6),
                shape=ft.RoundedRectangleBorder(radius=12),
            ),
            controls=rows,
        )

    def _header(self, is_dark: bool) -> ft.MenuItemButton:
        """Non-interactive header. No on_click => Flet disables it, which is
        exactly the affordance we want and costs no extra control."""
        return ft.MenuItemButton(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.TUNE,
                        size=14,
                        color=MD3Colors.get_on_surface_variant(is_dark),
                    ),
                    ft.Text(
                        "UPDATE THIS RUN",
                        size=11,
                        weight=ft.FontWeight.W_700,
                        color=MD3Colors.get_on_surface_variant(is_dark),
                        style=ft.TextStyle(letter_spacing=1.2),
                    ),
                ],
                spacing=6,
                tight=True,
            ),
        )

    def _tech_color(self, token: str) -> str:
        """Brand colour for a technology, falling back to the host accent."""
        return getattr(TechnologyColors, token, self._accent)

    def _row_style(self, token: str) -> ft.ButtonStyle:
        """Hovering a row tints it in that technology's brand colour, so the
        menu speaks the same colour language as the DLL badges it governs."""
        return ft.ButtonStyle(
            bgcolor={
                ft.ControlState.HOVERED: ft.Colors.with_opacity(
                    0.12, self._tech_color(token)
                ),
            },
        )

    def _tick(self, token: str, on: bool) -> ft.Icon:
        return ft.Icon(
            ft.Icons.CHECK_BOX if on else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
            size=18,
            color=self._tech_color(token) if on
            else MD3Colors.get_on_surface_variant(self._registry.is_dark),
        )

    def _swatch(self, token: str, on: bool) -> ft.Container:
        """The same 10px brand dot game_card._build_dll_popover_items uses, so a
        row here and a DLL row there read as the same technology."""
        return ft.Container(
            width=10,
            height=10,
            bgcolor=self._tech_color(token),
            border_radius=5,
            opacity=1.0 if on else 0.3,
        )

    def _trigger_style(self) -> ft.ButtonStyle:
        """The SubmenuButton is a transparent shell. The host's own button
        supplies every pixel, so strip the Material padding and overlay that
        would otherwise pad it out and double up its ink."""
        return ft.ButtonStyle(
            padding=ft.Padding.all(0),
            bgcolor=ft.Colors.TRANSPARENT,
            overlay_color=ft.Colors.TRANSPARENT,
            shadow_color=ft.Colors.TRANSPARENT,
            elevation=0,
            shape=ft.RoundedRectangleBorder(radius=self._radius),
        )

    def _run_style(self, is_dark: bool) -> ft.ButtonStyle:
        """Accent fill so the run row reads as the primary action rather than
        a sixth checklist entry."""
        return ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DISABLED: ft.Colors.with_opacity(
                    0.20, MD3Colors.get_on_surface_variant(is_dark)
                ),
                ft.ControlState.HOVERED: ft.Colors.with_opacity(0.85, self._accent),
                ft.ControlState.DEFAULT: self._accent,
            },
        )

    def _build_run_item(self, is_dark: bool) -> ft.MenuItemButton:
        return ft.MenuItemButton(
            content=ft.Text(
                self._run_label(),
                size=13,
                weight=ft.FontWeight.W_600,
                color=ft.Colors.WHITE,
            ),
            leading=ft.Icon(ft.Icons.DOWNLOAD, size=18, color=ft.Colors.WHITE),
            # The one row that SHOULD dismiss the menu: it ends the decision.
            close_on_click=True,
            on_click=self._on_run_click,
            style=self._run_style(is_dark),
            disabled=not self._runnable(),
        )

    def _runnable(self) -> bool:
        """An empty scope would touch nothing, so the action goes dead rather
        than starting a run that silently does nothing."""
        return bool(self._get_scope() & update_scope.all_technologies())

    def _sync_run_row(self) -> None:
        """Re-label and re-enable the run row from the live scope. The label
        is host-supplied, so it can carry a count that moves as ticks change."""
        if self._run_item is None:
            return
        self._run_item.content = ft.Text(
            self._run_label(),
            size=13,
            weight=ft.FontWeight.W_600,
            color=ft.Colors.WHITE,
        )
        self._run_item.disabled = not self._runnable()

    # ---- events -------------------------------------------------------

    def _make_toggle(self, token: str):
        def handler(e):
            scope = set(self._get_scope())
            scope.symmetric_difference_update({token})
            self._on_scope_changed(frozenset(scope))
            self.refresh_ticks()
        return handler

    def _on_make_default(self, e):
        from dlss_updater.config import config_manager

        scope = self._get_scope()
        for token in update_scope.all_technologies():
            config_manager.set_update_preference(token, token in scope)
        # The override now equals the saved state, so drop it: one source of
        # truth at rest.
        self._on_scope_changed(update_scope.from_preferences())
        self.refresh_ticks()

    def _on_reset(self, e):
        self._on_scope_changed(update_scope.from_preferences())
        self.refresh_ticks()

    async def _on_run_click(self, e):
        """Hosts pass either a plain callback or a coroutine function, so await
        whatever comes back rather than assuming one shape."""
        if self._on_run is None or not self._runnable():
            return
        result = self._on_run(e)
        if inspect.isawaitable(result):
            await result

    # ---- state --------------------------------------------------------

    def refresh_ticks(self) -> None:
        """Repaint every tick and the badge from the current scope."""
        scope = self._get_scope()
        for token, item in self._items.items():
            on = token in scope
            item.leading = self._tick(token, on)
            item.trailing = self._swatch(token, on)
        if self._default_item is not None:
            matches_saved = (
                scope & update_scope.all_technologies()
            ) == (update_scope.from_preferences() & update_scope.all_technologies())
            self._default_item.leading = ft.Icon(
                ft.Icons.CHECK_BOX if matches_saved else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                size=18,
            )
        self._sync_run_row()
        self._sync_badge()
        if self._page_ref:
            self.update()

    def set_accent(self, accent: str) -> None:
        """Repaint accent-derived chrome. The hub CTA's accent tracks card
        state, so its badge must follow; the Launchers menu never calls this."""
        self._accent = accent
        if self._run_item is not None:
            self._run_item.style = self._run_style(self._registry.is_dark)
        self._sync_badge()

    def current_scope(self) -> frozenset[str]:
        """The scope this menu is showing. Hosts need it to decide whether their
        own run button would do anything."""
        return self._get_scope()

    def _sync_badge(self) -> None:
        """Show a count only while the run is narrower than everything."""
        scope = self._get_scope() & update_scope.all_technologies()
        total = len(update_scope.all_technologies())
        if len(scope) == total:
            self.badge = None
        else:
            self.badge = ft.Badge(
                label=str(len(scope)),
                bgcolor=self._accent,
                text_color=ft.Colors.WHITE,
                small_size=14,
            )

    def _apply_colors(self, is_dark: bool) -> None:
        """Transparent shell: the trigger content carries the host's own fill,
        so painting a surface here would draw a second box behind it."""
        self.bgcolor = ft.Colors.TRANSPARENT
        self.border = None

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        return {}

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        # Menu items carry themed colours, so rebuild them rather than
        # re-applying values (CLAUDE.md: PopupMenuButton/menu item pattern).
        self._apply_colors(is_dark)
        self.content = ft.MenuBar(
            style=self._menu_bar_style(),
            controls=[self._build_submenu()],
        )
        self._sync_badge()
        await super().apply_theme(is_dark, delay_ms)
