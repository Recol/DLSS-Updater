"""Floating pill icons reuse the hub's hover motions (real ft.Icon objects - a
stub would accept writes to fields that don't exist)."""

import math
from types import SimpleNamespace

import flet as ft

from dlss_updater.ui_flet.components.floating_pill import _HOME_MOTION, FloatingPill
from dlss_updater.ui_flet.components.hub_card import ICON_MOTIONS


def _pill() -> FloatingPill:
    return FloatingPill(on_navigate=None, on_home=None, page=None)


def _hover(on: bool):
    return SimpleNamespace(data=on)


def _at_rest(icon: ft.Icon) -> bool:
    return icon.rotate == 0.0 and icon.scale == 1.0 and icon.offset == ft.Offset(0, 0)


def test_every_pill_icon_is_primed_and_has_a_motion():
    pill = _pill()
    for icon in (pill._home_icon, *pill._icon_widgets.values()):
        assert _at_rest(icon)
        assert isinstance(icon.animate_rotation, ft.Animation)
    for icon in pill._icon_widgets.values():
        assert icon.icon in ICON_MOTIONS, icon.icon


def test_view_icon_hover_plays_the_hub_motion_and_leave_returns_to_rest():
    pill = _pill()
    icon = pill._icon_widgets["backups"]

    pill._on_icon_hover(_hover(True), "backups")
    assert icon.rotate == -2 * math.pi  # same rewind as the hub card
    assert pill._icon_containers["backups"].bgcolor is not None

    pill._on_icon_hover(_hover(False), "backups")
    assert _at_rest(icon)
    assert pill._icon_containers["backups"].bgcolor is None


def test_active_icon_still_moves_but_keeps_its_fill():
    pill = _pill()
    pill.set_active("settings")
    fill = pill._icon_containers["settings"].bgcolor

    pill._on_icon_hover(_hover(True), "settings")

    assert pill._icon_widgets["settings"].rotate == math.pi
    assert pill._icon_containers["settings"].bgcolor == fill


def test_home_hops_and_hide_resets_every_icon():
    pill = _pill()
    pill._on_home_hover(_hover(True))
    pill._on_icon_hover(_hover(True), "launchers")
    assert pill._home_icon.offset == ft.Offset(0, _HOME_MOTION.dy)

    pill.hide()

    for icon in (pill._home_icon, *pill._icon_widgets.values()):
        assert _at_rest(icon)
