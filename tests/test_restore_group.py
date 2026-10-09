"""restore_group_for_game accepts both kinds of group name the UI sends.

The game card's restore menu sends per-DLL types ("XeLL DLL") - the keys of
get_backups_grouped_by_dll_type. The DLL group dialog sends technology names
("XeSS") from DLL_GROUPS, which never matched those keys, so every per-
technology restore from the dialog failed with "No backups found for group"
(issue #324).
"""

from types import SimpleNamespace

import anyio
import pytest

from dlss_updater import backup_manager


def _backup(backup_id: int, filename: str):
    return SimpleNamespace(id=backup_id, dll_filename=filename)


GROUPED = {
    "XeLL DLL": [_backup(1, "libxell.dll")],
    "XeSS Frame Generation DLL": [_backup(2, "libxess_fg.dll")],
    "DLSS DLL": [_backup(3, "nvngx_dlss.dll")],
    "Unknown": [_backup(4, "mystery.dll")],
}


@pytest.fixture
def restored(monkeypatch):
    calls: list[int] = []

    async def fake_grouped(game_id):
        return GROUPED

    async def fake_restore(backup_id):
        calls.append(backup_id)
        return True, "ok"

    monkeypatch.setattr(backup_manager.db_manager, "get_backups_grouped_by_dll_type", fake_grouped)
    monkeypatch.setattr(backup_manager, "restore_dll_from_backup", fake_restore)
    return calls


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        ("XeSS", {1, 2}),  # technology name from the DLL group dialog
        ("DLSS", {3}),
        ("Other", {4}),  # the dialog's bucket for ungrouped DLLs
        ("XeLL DLL", {1}),  # per-DLL type from the game card menu
        ("all", {1, 2, 3, 4}),
    ],
)
def test_group_restores_the_right_backups(restored, group, expected):
    success, _summary, _results = anyio.run(backup_manager.restore_group_for_game, 7, group)

    assert success
    assert set(restored) == expected


def test_technology_with_no_backups_still_reports_failure(restored):
    success, summary, results = anyio.run(backup_manager.restore_group_for_game, 7, "FSR")

    assert not success and results == []
    assert "No backups found for group 'FSR'" in summary
    assert restored == []
