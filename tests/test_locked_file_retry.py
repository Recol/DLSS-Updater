"""Locked DLLs: the updater fails fast (no sleeping worker thread) and
process_dlls_parallel retries the locked ones asynchronously in rounds."""

import time
from pathlib import Path

import anyio

import dlss_updater.updater as updater
import dlss_updater.utils as utils
from dlss_updater.models import ProcessedDLLResult


def test_update_dll_fails_fast_on_a_locked_file_without_touching_it(tmp_path, monkeypatch):
    target = tmp_path / "nvngx_dlss.dll"
    target.write_bytes(b"old")
    latest = tmp_path / "latest.dll"
    latest.write_bytes(b"new")

    monkeypatch.setattr(updater, "get_dll_versions_parallel", lambda a, b: ("310.1.0.0", "310.9.1.0"))
    monkeypatch.setattr(updater, "is_known_bad_dll", lambda *a: False)
    monkeypatch.setattr(updater, "is_file_in_use", lambda p: True)
    backups = []
    monkeypatch.setattr(updater, "create_backup", lambda p: backups.append(p) or tmp_path / "b")

    start = time.perf_counter()
    result = updater.update_dll(str(target), str(latest))
    elapsed = time.perf_counter() - start

    assert result.locked is True and result.success is False
    assert result.skip_reason == updater.FILE_IN_USE_REASON
    assert elapsed < 0.5, "must not sleep on the worker thread"
    assert backups == [], "no backup for a file we could not update"
    assert target.read_bytes() == b"old"


def _run(tasks, results_by_attempt, monkeypatch, delay=0.01):
    """Drive process_dlls_parallel with a fake process_single_dll whose result
    for each path changes per attempt."""
    attempts: dict[str, int] = {}

    async def fake_process_single_dll(dll_path, launcher, scope=None):
        n = attempts.get(str(dll_path), 0)
        attempts[str(dll_path)] = n + 1
        seq = results_by_attempt[str(dll_path)]
        return seq[min(n, len(seq) - 1)]

    monkeypatch.setattr(utils, "process_single_dll", fake_process_single_dll)
    monkeypatch.setattr(utils, "LOCKED_RETRY_DELAY_S", delay)
    results = anyio.run(utils.process_dlls_parallel, tasks)
    return results, attempts


LOCKED = ProcessedDLLResult(success=False, dll_type="DLSS DLL", skip_reason="in use", locked=True)
OK = ProcessedDLLResult(success=True, dll_type="DLSS DLL")


def test_locked_file_is_retried_and_succeeds_once_released(monkeypatch):
    tasks = [(Path("a.dll"), "Steam"), (Path("b.dll"), "Steam")]
    results, attempts = _run(tasks, {"a.dll": [LOCKED, OK], "b.dll": [OK]}, monkeypatch)

    assert attempts == {"a.dll": 2, "b.dll": 1}, "only the locked file is retried"
    assert len(results["updated_games"]) == 2
    assert results["skipped_games"] == []


def test_still_locked_after_all_rounds_is_reported_with_its_reason(monkeypatch):
    tasks = [(Path("a.dll"), "Steam")]
    results, attempts = _run(tasks, {"a.dll": [LOCKED]}, monkeypatch)

    assert attempts["a.dll"] == 1 + utils.LOCKED_RETRY_ROUNDS
    (path, launcher, reason, _type) = results["skipped_games"][0]
    assert reason == "in use"
