"""
FSR 4 group install: hardware gate, all-or-nothing writes, and add-vs-replace.

These are the three properties that make this path different from every other
update in the app, and each has a way of failing that leaves a game broken.
"""

import shutil
from pathlib import Path

import pytest

from dlss_updater.fsr4_installer import (
    FSR4_ANCHOR_DLL,
    FSR4_INSTALL_MAP,
    FSR4_VK_ANCHOR_DLL,
    FSR4_VK_INSTALL_MAP,
    plan_fsr4_upgrade,
    _apply_plan_sync,
)

UPSCALER = "amd_fidelityfx_upscaler_dx12.dll"
FRAMEGEN = "amd_fidelityfx_framegeneration_dx12.dll"
LOADER = "amd_fidelityfx_loader_dx12.dll"


@pytest.fixture
def cache(tmp_path):
    """A local DLL cache holding all three FSR 4 sources."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    paths = {}
    for name, blob in ((LOADER, b"L" * 64), (UPSCALER, b"U" * 128), (FRAMEGEN, b"F" * 256)):
        p = cache_dir / name
        p.write_bytes(blob)
        paths[name] = str(p)
    return paths


@pytest.fixture
def fsr31_game(tmp_path):
    """A game as it ships today: the monolith only, no effect DLLs."""
    game = tmp_path / "game"
    game.mkdir()
    (game / FSR4_ANCHOR_DLL).write_bytes(b"M" * 512)
    return game


class TestHardwareGate:
    def test_incapable_gpu_blocks_the_upgrade(self, fsr31_game, cache, monkeypatch):
        monkeypatch.setattr(
            "dlss_updater.fsr4_installer.system_supports_fsr4_sync", lambda: False
        )
        plan = plan_fsr4_upgrade(fsr31_game, cache)
        assert not plan.is_actionable
        assert "RDNA 2/3/4" in plan.skipped_reason

    def test_capable_gpu_allows_it(self, fsr31_game, cache, monkeypatch):
        monkeypatch.setattr(
            "dlss_updater.fsr4_installer.system_supports_fsr4_sync", lambda: True
        )
        assert plan_fsr4_upgrade(fsr31_game, cache).is_actionable


class TestPlanning:
    def test_skips_games_without_the_anchor(self, tmp_path, cache):
        (tmp_path / "empty").mkdir()
        plan = plan_fsr4_upgrade(tmp_path / "empty", cache, require_capable_gpu=False)
        assert not plan.is_actionable
        assert FSR4_ANCHOR_DLL in plan.skipped_reason

    def test_incomplete_cache_installs_nothing(self, fsr31_game, cache):
        """A partial set is worse than no change - the shim needs its effects."""
        Path(cache.pop(UPSCALER)).unlink()
        plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
        assert not plan.is_actionable
        assert "missing from local cache" in plan.skipped_reason

    def test_monolith_is_a_replace_and_effects_are_adds(self, fsr31_game, cache):
        plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
        by_name = {a.target_name: a for a in plan.actions}
        assert by_name[FSR4_ANCHOR_DLL].is_new_file is False
        assert by_name[UPSCALER].is_new_file is True
        assert by_name[FRAMEGEN].is_new_file is True
        assert plan.added_count == 2

    def test_anchor_is_sourced_from_the_loader(self, fsr31_game, cache):
        """The rename is the whole mechanism - guard it explicitly."""
        plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
        anchor = next(a for a in plan.actions if a.target_name == FSR4_ANCHOR_DLL)
        assert anchor.source_name == LOADER
        assert FSR4_INSTALL_MAP[FSR4_ANCHOR_DLL] == LOADER


class TestApplyIsAtomic:
    def test_successful_install_writes_the_whole_set(self, fsr31_game, cache):
        plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
        ok, _, results = _apply_plan_sync(plan)
        assert ok
        assert len(results) == 3
        # The monolith is now the loader's bytes, under the monolith's name
        assert (fsr31_game / FSR4_ANCHOR_DLL).read_bytes() == b"L" * 64
        assert (fsr31_game / UPSCALER).read_bytes() == b"U" * 128
        assert (fsr31_game / FRAMEGEN).read_bytes() == b"F" * 256

    def test_failure_restores_the_original_monolith(self, fsr31_game, cache, monkeypatch):
        plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
        original = (fsr31_game / FSR4_ANCHOR_DLL).read_bytes()

        real_copyfile = shutil.copyfile
        calls = {"n": 0}

        def explode_on_second_write(src, dst, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            return real_copyfile(src, dst, *a, **kw)

        monkeypatch.setattr("dlss_updater.fsr4_installer.shutil.copyfile", explode_on_second_write)

        ok, message, _ = _apply_plan_sync(plan)
        assert not ok
        assert "rolled back" in message
        # The game is exactly as it was: monolith intact, no orphan effect DLLs
        assert (fsr31_game / FSR4_ANCHOR_DLL).read_bytes() == original
        assert not (fsr31_game / UPSCALER).exists()
        assert not (fsr31_game / FRAMEGEN).exists()

    def test_rollback_leaves_no_temp_directory(self, fsr31_game, cache, monkeypatch):
        plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
        monkeypatch.setattr(
            "dlss_updater.fsr4_installer.shutil.copyfile",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")),
        )
        _apply_plan_sync(plan)
        assert not (fsr31_game / ".dlss_updater_fsr4_tmp").exists()


VK_UPSCALER = "amd_fidelityfx_upscaler_vk.dll"
VK_FRAMEGEN = "amd_fidelityfx_framegeneration_vk.dll"
VK_LOADER = "amd_fidelityfx_loader_vk.dll"


@pytest.fixture
def vk_cache(cache, tmp_path):
    """The DX12 cache plus the SDK 2.4.0 Vulkan split DLLs."""
    cache_dir = tmp_path / "cache"
    for name, blob in ((VK_LOADER, b"l" * 64), (VK_UPSCALER, b"u" * 128), (VK_FRAMEGEN, b"f" * 256)):
        p = cache_dir / name
        p.write_bytes(blob)
        cache[name] = str(p)
    return cache


@pytest.fixture
def vk_fsr31_game(tmp_path):
    """A Vulkan FSR 3.1 game: the Vulkan monolith only."""
    game = tmp_path / "vkgame"
    game.mkdir()
    (game / FSR4_VK_ANCHOR_DLL).write_bytes(b"V" * 512)
    return game


class TestVulkan:
    def test_vk_anchor_is_sourced_from_the_vk_loader(self, vk_fsr31_game, vk_cache):
        plan = plan_fsr4_upgrade(
            vk_fsr31_game, vk_cache, anchor_dll=FSR4_VK_ANCHOR_DLL, require_capable_gpu=False
        )
        assert plan.is_actionable
        by_name = {a.target_name: a for a in plan.actions}
        assert set(by_name) == set(FSR4_VK_INSTALL_MAP)
        assert by_name[FSR4_VK_ANCHOR_DLL].source_name == VK_LOADER
        assert by_name[FSR4_VK_ANCHOR_DLL].is_new_file is False
        assert plan.added_count == 2

    def test_vk_install_writes_vk_set_only(self, vk_fsr31_game, vk_cache):
        plan = plan_fsr4_upgrade(
            vk_fsr31_game, vk_cache, anchor_dll=FSR4_VK_ANCHOR_DLL, require_capable_gpu=False
        )
        ok, _, results = _apply_plan_sync(plan)
        assert ok and len(results) == 3
        assert (vk_fsr31_game / FSR4_VK_ANCHOR_DLL).read_bytes() == b"l" * 64
        assert (vk_fsr31_game / VK_UPSCALER).read_bytes() == b"u" * 128
        assert (vk_fsr31_game / VK_FRAMEGEN).read_bytes() == b"f" * 256
        # No DX12 files are dropped into a Vulkan game
        assert not (vk_fsr31_game / UPSCALER).exists()
        assert not (vk_fsr31_game / FRAMEGEN).exists()

    def test_dx12_plan_ignores_a_vulkan_only_game(self, vk_fsr31_game, vk_cache):
        plan = plan_fsr4_upgrade(vk_fsr31_game, vk_cache, require_capable_gpu=False)
        assert not plan.is_actionable
        assert FSR4_ANCHOR_DLL in plan.skipped_reason

    def test_missing_vk_dlls_install_nothing(self, vk_fsr31_game, cache):
        """A pre-2.4.0 cache (no _vk DLLs) must not half-upgrade a Vulkan game."""
        plan = plan_fsr4_upgrade(
            vk_fsr31_game, cache, anchor_dll=FSR4_VK_ANCHOR_DLL, require_capable_gpu=False
        )
        assert not plan.is_actionable
        assert "missing from local cache" in plan.skipped_reason

    def test_vk_dlls_are_known_to_the_scanner_and_cache(self):
        from dlss_updater.constants import DLL_GROUPS, DLL_TYPE_MAP
        for name in FSR4_VK_INSTALL_MAP.values():
            assert name in DLL_TYPE_MAP
            assert name in DLL_GROUPS["FSR"]


class TestReplacedFilesAreBackedUp:
    """Restore deletes the added effect DLLs; without a backup of the monolith it
    would leave the loader shim alone, i.e. no FSR at all."""

    def test_monolith_is_kept_as_dlsss(self, fsr31_game, cache):
        original = (fsr31_game / FSR4_ANCHOR_DLL).read_bytes()
        plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
        ok, _, results = _apply_plan_sync(plan)
        assert ok
        backup = fsr31_game / "amd_fidelityfx_dx12.dlsss"
        assert backup.read_bytes() == original
        by_name = {r["target_name"]: r for r in results}
        assert by_name[FSR4_ANCHOR_DLL]["backup_path"] == str(backup)
        # Added files have nothing to back up - their restore is a delete
        assert by_name[UPSCALER]["backup_path"] is None
        assert by_name[FRAMEGEN]["backup_path"] is None

    def test_vulkan_monolith_is_kept_as_dlsss(self, vk_fsr31_game, vk_cache):
        plan = plan_fsr4_upgrade(
            vk_fsr31_game, vk_cache, anchor_dll=FSR4_VK_ANCHOR_DLL, require_capable_gpu=False
        )
        ok, _, _ = _apply_plan_sync(plan)
        assert ok
        assert (vk_fsr31_game / "amd_fidelityfx_vk.dlsss").read_bytes() == b"V" * 512

    def test_backup_failure_rolls_the_install_back(self, fsr31_game, cache, monkeypatch):
        original = (fsr31_game / FSR4_ANCHOR_DLL).read_bytes()
        plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
        real_copy2 = shutil.copy2

        def refuse_dlsss(src, dst, *a, **kw):
            if str(dst).endswith(".dlsss"):
                raise OSError("disk full")
            return real_copy2(src, dst, *a, **kw)

        monkeypatch.setattr("dlss_updater.fsr4_installer.shutil.copy2", refuse_dlsss)
        ok, message, _ = _apply_plan_sync(plan)
        assert not ok and "rolled back" in message
        assert (fsr31_game / FSR4_ANCHOR_DLL).read_bytes() == original
        assert not (fsr31_game / UPSCALER).exists()
        assert not (fsr31_game / FRAMEGEN).exists()


class _RecordingDB:
    def __init__(self):
        self.backups = []
        self._next = 0

    async def upsert_game_dll(self, data):
        self._next += 1
        return self._next

    async def insert_backup(self, data):
        self.backups.append(data)
        return len(self.backups)


async def test_apply_records_a_restorable_backup_for_the_monolith(fsr31_game, cache, monkeypatch):
    from dlss_updater import fsr4_installer

    db = _RecordingDB()
    monkeypatch.setattr(fsr4_installer, "db_manager", db)
    monkeypatch.setattr(fsr4_installer, "_safe_version", lambda p: "1.0.1.41314")

    plan = plan_fsr4_upgrade(fsr31_game, cache, require_capable_gpu=False)
    ok, _, _ = await fsr4_installer.apply_fsr4_upgrade(plan, game_id=7)
    assert ok

    added = [b for b in db.backups if b.get("was_added")]
    replaced = [b for b in db.backups if not b.get("was_added")]
    assert {Path(b["backup_path"]).name for b in added} == {UPSCALER, FRAMEGEN}
    assert len(replaced) == 1
    # The ordinary restore resolves the game DLL as backup_path.with_suffix(".dll")
    assert Path(replaced[0]["backup_path"]).with_suffix(".dll") == fsr31_game / FSR4_ANCHOR_DLL
    assert replaced[0]["original_version"] == "1.0.1.41314"
