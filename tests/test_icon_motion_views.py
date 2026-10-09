"""Hover/state motion beyond the hub: game card footer + selection plate,
settings tiles, backup rows, the Games refresh spin, and the restore menu's
technology grouping. Real ft controls throughout - a stub would accept writes
to fields that don't exist."""

import math
from types import SimpleNamespace

import anyio
import flet as ft
import pytest

from dlss_updater.models import GameDLLBackup
from dlss_updater.ui_flet.components.backup_group import BackupRow
from dlss_updater.ui_flet.components.game_card import (
    SELECTED_PLATE_SCALE,
    GameCard,
    restore_menu_groups,
)
from dlss_updater.ui_flet.components.hub_card import prime_icon_motion
from dlss_updater.ui_flet.views import games_view as games_view_module
from dlss_updater.ui_flet.views.games_view import GamesView
from dlss_updater.ui_flet.views.settings_view import SettingsView


def _hover(on: bool):
    return SimpleNamespace(data=on)


def _at_rest(control) -> bool:
    return control.rotate == 0.0 and control.scale == 1.0 and control.offset == ft.Offset(0, 0)


def _backup(backup_id: int, filename: str) -> GameDLLBackup:
    return GameDLLBackup(
        id=backup_id, game_dll_id=backup_id, game_id=1, game_name="G",
        dll_type="t", dll_filename=filename, backup_path="p", backup_size=1,
    )


# ---- restore menu grouping ----

def test_restore_menu_groups_by_technology_with_other_last():
    by_dll_type = {
        "XeLL DLL": [_backup(1, "libxell.dll")],
        "XeSS Frame Generation DLL": [_backup(2, "libxess_fg.dll")],
        "Unknown": [_backup(3, "mystery.dll")],
        "Streamline Reflex Low-Latency DLL": [_backup(4, "sl.reflex.dll")],
        "Streamline Shared Library DLL": [_backup(5, "sl.common.dll")],
        "DLSS DLL": [_backup(6, "nvngx_dlss.dll")],
    }

    groups = restore_menu_groups(by_dll_type)

    assert groups == {"DLSS": 1, "Streamline": 2, "XeSS": 2, "Other": 1}
    assert list(groups)[-1] == "Other"


# ---- game card footer + selection plate ----

def _footer_stub(update_glyph, *, backups=True, hovering=False, ignored=False):
    update_icon = ft.Icon(update_glyph)
    restore_icon = ft.Icon(ft.Icons.RESTORE)
    prime_icon_motion(update_icon)
    prime_icon_motion(restore_icon)
    return SimpleNamespace(
        update_button_icon=update_icon,
        restore_button_icon=restore_icon,
        backup_groups={"DLSS DLL": [object()]} if backups else {},
        is_ignored=ignored,
        _is_hovering=hovering,
        _menu_open=False,
    )


def test_footer_hover_drops_the_update_arrow_and_rewinds_restore():
    card = _footer_stub(ft.Icons.ARROW_DOWNWARD)

    GameCard._apply_footer_motion(card, True)

    assert card.update_button_icon.offset == ft.Offset(0, 0.18)
    assert card.restore_button_icon.rotate == -2 * math.pi

    GameCard._apply_footer_motion(card, False)
    assert _at_rest(card.update_button_icon) and _at_rest(card.restore_button_icon)


def test_footer_stays_still_with_nothing_to_do():
    card = _footer_stub(ft.Icons.UPDATE, backups=False)  # up to date, no backups

    GameCard._apply_footer_motion(card, True)

    assert _at_rest(card.update_button_icon) and _at_rest(card.restore_button_icon)


def test_glyph_swapped_mid_hover_drops_the_old_pose():
    card = _footer_stub(ft.Icons.ARROW_DOWNWARD)
    GameCard._apply_footer_motion(card, True)

    card.update_button_icon.icon = ft.Icons.HOURGLASS_TOP  # update started
    GameCard._apply_footer_motion(card, True)

    assert _at_rest(card.update_button_icon)


def test_selecting_pops_the_plate_and_clearing_settles_it():
    plate = ft.Container(scale=1.0)
    card = SimpleNamespace(
        _select_icon=ft.Icon(ft.Icons.CHECK_BOX_OUTLINE_BLANK),
        _select_button=plate,
        _card_body=None,
        _registry=SimpleNamespace(is_dark=True),
    )

    GameCard.set_selected(card, True)
    assert plate.scale == SELECTED_PLATE_SCALE

    GameCard.set_selected(card, False)
    assert plate.scale == 1.0


# ---- settings tiles ----

def _tile_meta(glyph):
    icon, watermark = ft.Icon(glyph), ft.Icon(glyph)
    prime_icon_motion(icon)
    prime_icon_motion(watermark)
    return {"tile": ft.Container(), "icon_widget": icon, "watermark_widget": watermark}


def test_settings_tile_icon_moves_and_watermark_follows_at_half():
    view = SimpleNamespace(_page_ref=None)
    meta = _tile_meta(ft.Icons.BLOCK)

    SettingsView._on_tile_hover(view, _hover(True), meta)

    assert meta["icon_widget"].rotate == math.pi / 2
    assert meta["watermark_widget"].rotate == math.pi / 4
    assert meta["tile"].scale == 1.01

    SettingsView._on_tile_hover(view, _hover(False), meta)
    assert _at_rest(meta["icon_widget"]) and _at_rest(meta["watermark_widget"])


# ---- backup rows ----

def test_backup_row_hover_rewinds_its_restore_button():
    row = BackupRow(_backup(1, "nvngx_dlss.dll"), True, on_restore=lambda b: None)
    row.update = lambda: None

    row._on_hover(_hover(True))
    assert row._restore_button.rotate == -2 * math.pi

    row._on_hover(_hover(False))
    assert _at_rest(row._restore_button)


def test_orphan_row_without_restore_still_hovers():
    row = BackupRow(_backup(1, "nvngx_dlss.dll"), True)  # no on_restore
    row.update = lambda: None

    row._on_hover(_hover(True))  # must not raise on the missing button

    assert row._restore_button is None


# ---- Games refresh spin ----

@pytest.mark.anyio
async def test_refresh_spins_until_done_and_ignores_a_second_click(monkeypatch):
    monkeypatch.setattr(games_view_module, "REFRESH_SPIN_MS", 10)
    monkeypatch.setattr(games_view_module, "is_view_attached", lambda v: True)
    button = ft.IconButton(icon=ft.Icons.REFRESH, rotate=0)
    flushes = []
    button.update = lambda: flushes.append(button.rotate)
    runs = []

    view = SimpleNamespace(
        refresh_button_ref=SimpleNamespace(current=button), _refreshing=False
    )
    view._spin_refresh_button = lambda done: GamesView._spin_refresh_button(view, done)

    async def slow_refresh():
        runs.append("refresh")
        await GamesView._on_refresh_clicked(view, None)  # re-entrant click
        await anyio.sleep(0.05)

    view._refresh_games = slow_refresh

    await GamesView._on_refresh_clicked(view, None)

    assert runs == ["refresh"], "a click mid-refresh must not start another"
    assert len(flushes) >= 2, "the icon keeps turning while the refresh runs"
    turns = [r / (2 * math.pi) for r in flushes]
    assert all(math.isclose(t, round(t)) for t in turns), "every turn is whole"
    assert view._refreshing is False
