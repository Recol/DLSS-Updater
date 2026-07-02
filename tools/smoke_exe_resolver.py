"""
Reversible, read-only smoke test for dlss_updater.exe_resolver.

Resolves a real game install on this machine and prints the ExeResolution. Also
exercises the empty-folder path to confirm graceful ``source="none"``.

This script READS ONLY - it never writes the driver or the database. Run:

    python tools/smoke_exe_resolver.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

# Ensure the repo root is importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dlss_updater import nvapi_drs  # noqa: E402
from dlss_updater.exe_resolver import resolve_game_exe, _resolve_game_exe_sync  # noqa: E402
from dlss_updater.models import Game  # noqa: E402


# Known-good target: BF6's bf6.exe is a profile the NVIDIA driver KNOWS, so it
# should resolve via the driver-validate step with confidence="high".
BF6_PATH = r"F:\SteamLibrary\steamapps\common\Battlefield 6"


def _make_game(path: str, game_id: int = 1, launcher: str = "Steam",
               steam_app_id: int | None = None) -> Game:
    return Game(
        id=game_id,
        name=os.path.basename(path) or path,
        path=path,
        launcher=launcher,
        steam_app_id=steam_app_id,
    )


def _print_resolution(label: str, res) -> None:
    print(f"\n=== {label} ===")
    print(f"  exe_path   : {res.exe_path}")
    print(f"  exe_name   : {res.exe_name}")
    print(f"  source     : {res.source}")
    print(f"  confidence : {res.confidence}")
    print(f"  candidates : {len(res.candidates)} found")
    for i, c in enumerate(res.candidates[:8]):
        print(f"      [{i}] {c}")


async def main() -> None:
    print(f"nvapi_drs.is_available() = {nvapi_drs.is_available()}")

    # No DB manager -> cache step is skipped (None). Pure heuristic+driver path.
    # --- 1. BF6 (driver-known exe expected) ---
    if os.path.isdir(BF6_PATH):
        game = _make_game(BF6_PATH, steam_app_id=None)
        res = await resolve_game_exe(game, db_manager=_NoCacheDB())
        _print_resolution("BF6 (async resolve_game_exe)", res)

        ok_driver = (res.exe_name and res.exe_name.lower() == "bf6.exe")
        print(f"  -> bf6.exe resolved: {bool(ok_driver)}")
        print(f"  -> source==driver  : {res.source == 'driver'}")
        print(f"  -> confidence==high: {res.confidence == 'high'}")

        # Sync variant should agree.
        res_sync = _resolve_game_exe_sync(game, _NoCacheDB())
        _print_resolution("BF6 (sync _resolve_game_exe_sync)", res_sync)
    else:
        print(f"\n[skip] BF6 path not present: {BF6_PATH}")

    # --- 2. Empty folder -> graceful source="none" ---
    with tempfile.TemporaryDirectory() as empty_dir:
        game_empty = _make_game(empty_dir, game_id=999, launcher="Other")
        res_none = await resolve_game_exe(game_empty, db_manager=_NoCacheDB())
        _print_resolution("Empty folder (no exe)", res_none)
        print(f"  -> exe_path is None: {res_none.exe_path is None}")
        print(f"  -> source=='none'  : {res_none.source == 'none'}")


class _NoCacheDB:
    """Minimal stand-in: no get_game_exe_sync attribute -> cache step skipped.

    (Intentionally does NOT define get_game_exe_sync so the resolver's cache
    branch is a no-op; this keeps the smoke test free of any DB dependency.)"""
    pass


if __name__ == "__main__":
    asyncio.run(main())
