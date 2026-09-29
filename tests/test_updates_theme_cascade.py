"""ThemeRegistry cascade vs. the nav controller's content detachment.

A theme toggle only happens from Settings, so every other nav view is
detached while the cascade runs. Updating those views is pure waste (the
client drops patches to detached subtrees - CLAUDE.md pitfall #2), so the
cascade must not flush them, must report them so MainView marks them stale,
and must keep flushing attached components exactly as before.
"""

import weakref

import flet as ft
import pytest

from dlss_updater.ui_flet.theme.theme_aware import (
    THEME_REBUILT_ON_ATTACH_ATTR,
    ThemeAwareMixin,
    find_detached_nav_view,
    get_theme_registry,
    reset_theme_registry,
)


class ThemedBox(ThemeAwareMixin, ft.Container):
    """Real Flet control using the mixin's default apply_theme()."""

    def __init__(self):
        super().__init__(bgcolor="#000000")
        self.update_calls = 0
        self.apply_calls = 0
        self._register_theme_aware()

    def get_themed_properties(self):
        return {"bgcolor": ("#111111", "#EEEEEE")}

    async def apply_theme(self, is_dark, delay_ms=0):
        self.apply_calls += 1
        await super().apply_theme(is_dark, delay_ms)

    def update(self):  # stands in for a real flush; no page in unit tests
        self.update_calls += 1


class OverridingView(ThemeAwareMixin, ft.Column):
    """A view whose override calls self.update() directly (like GamesView)."""

    def __init__(self):
        super().__init__()
        self.update_calls = 0
        self.header_color = None
        self._register_theme_aware()

    async def apply_theme(self, is_dark, delay_ms=0):
        self.header_color = "dark" if is_dark else "light"
        self.update()

    def update(self):
        self.update_calls += 1


def _mount(child, parent):
    """Emulate what Flet's patcher does when a subtree is serialised."""
    child._parent = weakref.ref(parent)


@pytest.fixture(autouse=True)
def fresh_registry():
    reset_theme_registry()
    yield
    reset_theme_registry()


def test_find_detached_nav_view_walks_ancestors():
    page_like = ft.Column()
    wrapper = ft.Column()
    view = ft.Column()
    view._nav_attached = False
    child = ft.Container()
    _mount(wrapper, page_like)
    _mount(view, wrapper)
    _mount(child, view)

    assert find_detached_nav_view(child) is view
    assert find_detached_nav_view(view) is view
    view._nav_attached = True
    assert find_detached_nav_view(child) is None
    # Unmanaged / never-mounted controls behave as attached.
    assert find_detached_nav_view(ft.Container()) is None


@pytest.mark.anyio
async def test_detached_view_is_themed_but_not_flushed_and_reported():
    view = OverridingView()
    view._nav_attached = False
    box = ThemedBox()
    _mount(box, view)

    await get_theme_registry().apply_theme_to_all(is_dark=False, cascade=True)

    # Python-side state is still themed...
    assert view.header_color == "light"
    assert box.bgcolor == "#EEEEEE"
    assert box.apply_calls == 1
    # ...but nothing was flushed, including the override's direct self.update().
    assert view.update_calls == 0
    assert box.update_calls == 0
    # The shadow is removed afterwards, so later updates work normally.
    assert "update" not in vars(view) and "update" not in vars(box)
    view.update()
    assert view.update_calls == 1
    # Reported so the owner marks the view theme-stale.
    assert get_theme_registry().last_detached_views == [view]


@pytest.mark.anyio
async def test_attached_view_is_themed_and_flushed_once():
    view = OverridingView()
    view._nav_attached = True
    box = ThemedBox()
    _mount(box, view)

    await get_theme_registry().apply_theme_to_all(is_dark=True, cascade=False)

    assert view.header_color == "dark"
    assert box.bgcolor == "#111111"
    assert view.update_calls == 1
    assert box.update_calls == 1
    assert get_theme_registry().last_detached_views == []


@pytest.mark.anyio
async def test_rebuilt_on_attach_view_is_skipped_entirely_but_still_reported():
    view = OverridingView()
    view._nav_attached = False
    setattr(view, THEME_REBUILT_ON_ATTACH_ATTR, True)
    box = ThemedBox()
    _mount(box, view)

    await get_theme_registry().apply_theme_to_all(is_dark=False, cascade=False)

    assert view.header_color is None
    assert box.apply_calls == 0
    assert view.update_calls == 0 and box.update_calls == 0
    assert get_theme_registry().last_detached_views == [view]


@pytest.mark.anyio
async def test_mixed_tree_only_flushes_the_attached_side():
    settings = OverridingView()
    settings._nav_attached = True
    games = OverridingView()
    games._nav_attached = False
    attached_box, detached_box = ThemedBox(), ThemedBox()
    _mount(attached_box, settings)
    _mount(detached_box, games)
    overlay_box = ThemedBox()  # unmanaged (dialog/panel) -> attached

    await get_theme_registry().apply_theme_to_all(is_dark=False, cascade=True)

    assert settings.update_calls == 1
    assert attached_box.update_calls == 1
    assert overlay_box.update_calls == 1
    assert games.update_calls == 0
    assert detached_box.update_calls == 0
    assert detached_box.bgcolor == attached_box.bgcolor == "#EEEEEE"
    assert get_theme_registry().last_detached_views == [games]


def test_main_view_maps_reported_views_to_nav_names():
    from dlss_updater.ui_flet.views.main_view import MainView

    games, backups, replaced = ft.Column(), ft.Column(), ft.Column()

    class _Nav:
        _view_refs = {"games": games, "backups": backups, "hub": ft.Column()}

    class _Host:
        navigation_controller = _Nav()

    names = MainView._nav_names_for_views(_Host(), [games, backups, replaced])
    assert names == {"games", "backups"}
    assert MainView._nav_names_for_views(_Host(), []) == set()


def test_hub_card_mount_resync_only_runs_on_theme_mismatch():
    """did_mount fires on every hub re-attach; re-theming must be gated."""
    from dlss_updater.ui_flet.components.hub_card import _resync_theme_on_mount

    scheduled = []

    class _Page:
        def run_task(self, fn, *args):
            scheduled.append(args)

    class _Card:
        _page_ref = _Page()

        async def apply_theme(self, is_dark, delay_ms=0):
            pass

    registry = get_theme_registry()
    registry.is_dark = True
    card = _Card()
    card._themed_for = True
    _resync_theme_on_mount(card)
    assert scheduled == []

    registry.is_dark = False  # themed while skipped/detached -> heal
    _resync_theme_on_mount(card)
    assert scheduled == [(False,)]
