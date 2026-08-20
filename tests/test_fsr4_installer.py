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
        assert "RDNA 3/4" in plan.skipped_reason

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
