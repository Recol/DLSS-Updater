"""Hub card icons animate on hover: the motion is primed once at build time
and hover only swaps target values (real ft.Icon objects - a stub would
accept writes to fields that don't exist)."""

import math
from types import SimpleNamespace

import flet as ft

from dlss_updater.ui_flet.components.hub_card import (
    ICON_MOTIONS,
    WATERMARK_MOTION_FACTOR,
    HubActionCard,
    IconMotion,
    apply_icon_motion,
    motion_for_icon,
    prime_icon_motion,
)


def test_every_hub_icon_has_a_motion():
    for icon in (
        ft.Icons.ROCKET_LAUNCH,
        ft.Icons.AUTO_AWESOME,
        ft.Icons.SETTINGS_BACKUP_RESTORE,
        ft.Icons.SETTINGS,
        ft.Icons.SPORTS_ESPORTS,
        ft.Icons.SYSTEM_UPDATE_ALT,
        ft.Icons.SEARCH,
        ft.Icons.TASK_ALT,
    ):
        assert isinstance(motion_for_icon(icon), IconMotion), icon


def test_priming_sets_rest_state_and_implicit_animations():
    icon = ft.Icon(ft.Icons.SETTINGS)

    prime_icon_motion(icon, 350)

    assert icon.rotate == 0.0 and icon.scale == 1.0
    assert icon.offset == ft.Offset(0, 0)
    for anim in (icon.animate_rotation, icon.animate_scale, icon.animate_offset):
        assert isinstance(anim, ft.Animation) and anim.duration == 350


def test_hover_moves_to_target_and_leave_returns_to_rest():
    icon = ft.Icon(ft.Icons.SETTINGS_BACKUP_RESTORE)
    prime_icon_motion(icon)
    motion = ICON_MOTIONS[ft.Icons.SETTINGS_BACKUP_RESTORE]

    apply_icon_motion(icon, motion, hovering=True)
    assert icon.rotate == -2 * math.pi  # one full anticlockwise turn (radians)

    apply_icon_motion(icon, motion, hovering=False)
    assert icon.rotate == 0.0 and icon.scale == 1.0 and icon.offset == ft.Offset(0, 0)


def test_watermark_factor_halves_the_motion():
    icon = ft.Icon(ft.Icons.ROCKET_LAUNCH)
    motion = ICON_MOTIONS[ft.Icons.ROCKET_LAUNCH]

    apply_icon_motion(icon, motion, True, WATERMARK_MOTION_FACTOR)

    assert icon.offset == ft.Offset(motion.dx * 0.5, motion.dy * 0.5)
    assert icon.scale == 1.0 + (motion.scale - 1.0) * 0.5


def test_no_motion_is_a_no_op():
    icon = ft.Icon(ft.Icons.HELP)
    apply_icon_motion(icon, None, True)
    assert icon.rotate is None


def test_action_card_badge_uses_the_motion_of_the_live_state_icon():
    badge = ft.Icon(ft.Icons.TASK_ALT)
    prime_icon_motion(badge)
    updated = []
    badge.update = lambda: updated.append(True)
    card = SimpleNamespace(_badge_icon=badge, _page_ref=object())

    HubActionCard._on_card_hover(card, SimpleNamespace(data=True))

    assert badge.scale == ICON_MOTIONS[ft.Icons.TASK_ALT].scale
    assert updated == [True], "only the badge is updated"
