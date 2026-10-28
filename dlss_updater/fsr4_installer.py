"""
FSR 4 group installation for games that ship FidelityFX SDK 1.x / FSR 3.1.

Everything else in this application *replaces* a DLL a game already has. FSR 4
cannot work that way, and this module exists because of that difference.

Background
----------
Up to FidelityFX SDK 1.1.4 a game shipped one monolithic ``amd_fidelityfx_dx12.dll``
(~6.7 MB) implementing the whole FFX API. SDK 2.0.0 split it into a ~26 KB
dispatch shim plus one DLL per effect. AMD's own documentation states the shim
"is interface- and behavior-compatible with amd_fidelityfx_dx12.dll", which is
what makes the upgrade possible: install the shim *under the old name*, and put
the effect DLLs beside it.

Consequences that shape this module:

1. Two of the three files DO NOT EXIST in an FSR 3.1 game. They must be created,
   which is why installs are recorded with ``was_added=True`` — restoring such a
   record deletes the file rather than copying an older build back. The file
   that IS replaced (the monolith) always gets an ordinary ``.dlsss`` backup,
   whatever the user's backup preference: restoring the added files without it
   would leave the loader shim with no effect DLLs, i.e. no FSR at all.
2. The three files are meaningless apart. A 26 KB shim next to missing effect
   DLLs is a broken game, not a partially-updated one, so the install is
   all-or-nothing: any failure rolls the whole set back.
3. FSR 4 needs RDNA 2 (discrete RX 6000, since FSR 4.1.2), RDNA 3 or RDNA 4. On
   any other GPU this would replace working FSR 3.1 upscaling with none at all,
   so the hardware gate is mandatory and fails closed.

Vulkan
------
FidelityFX SDK 2.4.0 is the first 2.x release with Vulkan builds. Its
``amd_fidelityfx_loader_vk.dll`` is, per AMD, "interface- and behavior-compatible
with amd_fidelityfx_vk.dll", so a Vulkan FSR 3.1 game is upgraded exactly the
same way: the loader under the monolith's name, plus the ``_vk`` effect DLLs.
Each API has its own anchor and its own install map (``FSR4_INSTALL_MAPS``); a
folder that ships both monoliths gets two independent plans.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import anyio
import msgspec

from dlss_updater.concurrency_limiters import thread_io
from dlss_updater.database import db_manager
from dlss_updater.gpu_detection import system_supports_fsr4_sync
from dlss_updater.logger import setup_logger

logger = setup_logger()


# The DLL a game must already have for an FSR 4 upgrade to be meaningful. Its
# presence is what identifies "a game that uses FidelityFX via the FFX API".
FSR4_ANCHOR_DLL = "amd_fidelityfx_dx12.dll"
FSR4_VK_ANCHOR_DLL = "amd_fidelityfx_vk.dll"

# target filename in the game folder -> name of the DLL in our local cache.
#
# The anchor entry is the rename: the cached *loader* is installed under the
# monolith's name, because that is the filename the game calls LoadLibrary on.
FSR4_INSTALL_MAP: dict[str, str] = {
    "amd_fidelityfx_dx12.dll": "amd_fidelityfx_loader_dx12.dll",
    "amd_fidelityfx_upscaler_dx12.dll": "amd_fidelityfx_upscaler_dx12.dll",
    "amd_fidelityfx_framegeneration_dx12.dll": "amd_fidelityfx_framegeneration_dx12.dll",
}

# The Vulkan equivalent (SDK 2.4.0+), with the same loader-under-the-old-name rename.
FSR4_VK_INSTALL_MAP: dict[str, str] = {
    "amd_fidelityfx_vk.dll": "amd_fidelityfx_loader_vk.dll",
    "amd_fidelityfx_upscaler_vk.dll": "amd_fidelityfx_upscaler_vk.dll",
    "amd_fidelityfx_framegeneration_vk.dll": "amd_fidelityfx_framegeneration_vk.dll",
}

# anchor DLL -> the install map it triggers.
FSR4_INSTALL_MAPS: dict[str, dict[str, str]] = {
    FSR4_ANCHOR_DLL: FSR4_INSTALL_MAP,
    FSR4_VK_ANCHOR_DLL: FSR4_VK_INSTALL_MAP,
}


class FSR4Action(msgspec.Struct):
    """One file to write as part of an FSR 4 upgrade."""
    target_path: str      # Where it goes in the game folder
    source_path: str      # Cached DLL to copy from
    target_name: str      # Filename as installed
    source_name: str      # Cached DLL name (differs for the renamed loader)
    is_new_file: bool     # True = created (restore deletes), False = replaced


class FSR4Plan(msgspec.Struct):
    """The complete, validated set of writes for one game."""
    game_dir: str
    actions: list[FSR4Action] = msgspec.field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def is_actionable(self) -> bool:
        return not self.skipped_reason and bool(self.actions)

    @property
    def added_count(self) -> int:
        return sum(1 for a in self.actions if a.is_new_file)


def plan_fsr4_upgrade(
    game_dir: Path | str,
    cached_dll_paths: dict[str, str],
    *,
    anchor_dll: str = FSR4_ANCHOR_DLL,
    require_capable_gpu: bool = True,
) -> FSR4Plan:
    """
    Work out what an FSR 4 upgrade would write, without touching anything.

    Args:
        game_dir: Directory holding the game's FidelityFX DLLs.
        cached_dll_paths: Mapping of cached DLL name -> local path
            (``config.LATEST_DLL_PATHS``).
        anchor_dll: Which monolith to upgrade — a key of ``FSR4_INSTALL_MAPS``
            (DX12 by default, ``FSR4_VK_ANCHOR_DLL`` for Vulkan).
        require_capable_gpu: Enforce the hardware gate. Only disabled by tests.

    Returns:
        An :class:`FSR4Plan`; check ``is_actionable`` before applying.
    """
    game_dir = Path(game_dir)
    plan = FSR4Plan(game_dir=str(game_dir))
    install_map = FSR4_INSTALL_MAPS[anchor_dll]

    anchor = game_dir / anchor_dll
    if not anchor.exists():
        plan.skipped_reason = (
            f"{anchor_dll} not present - this game does not use the FidelityFX API"
        )
        return plan

    # Hardware gate. Fails closed: an unknown GPU keeps replace-only behaviour
    # rather than risking the removal of working FSR 3.1 upscaling.
    if require_capable_gpu and not system_supports_fsr4_sync():
        plan.skipped_reason = (
            "no FSR 4 capable GPU (RDNA 2/3/4) detected - FSR 4 would replace working FSR 3.1 "
            "upscaling with none at all"
        )
        return plan

    # Every member must be available locally, or we install nothing. A partial
    # set is worse than no change: see the module docstring.
    missing = [
        source for source in install_map.values()
        if not cached_dll_paths.get(source) or not Path(cached_dll_paths[source]).exists()
    ]
    if missing:
        plan.skipped_reason = f"FSR 4 DLLs missing from local cache: {', '.join(sorted(missing))}"
        return plan

    for target_name, source_name in install_map.items():
        target_path = game_dir / target_name
        plan.actions.append(
            FSR4Action(
                target_path=str(target_path),
                source_path=str(cached_dll_paths[source_name]),
                target_name=target_name,
                source_name=source_name,
                is_new_file=not target_path.exists(),
            )
        )

    return plan


def _apply_plan_sync(plan: FSR4Plan) -> tuple[bool, str, list[dict]]:
    """
    Write every file in the plan, or leave the folder exactly as it was found.

    Rollback restores replaced files from the temporary copies taken here and
    deletes files that were created, so a mid-way failure cannot leave the shim
    installed without its effect DLLs.

    Once every write has succeeded, each replaced original is kept as
    ``<name>.dlsss`` beside it — the same backup the ordinary update path makes,
    so the normal restore finds it. Failing to write that backup rolls the
    install back too: an FSR 4 install that cannot be undone is not offered.
    """
    written: list[Path] = []                 # Files created by this run
    replaced: list[tuple[Path, Path]] = []   # (target, temp copy of the original)
    results: list[dict] = []
    temp_dir = Path(plan.game_dir) / ".dlss_updater_fsr4_tmp"

    try:
        temp_dir.mkdir(exist_ok=True)

        for action in plan.actions:
            target = Path(action.target_path)

            if not action.is_new_file:
                # Stash the original so rollback can put it back byte-for-byte.
                stash = temp_dir / target.name
                shutil.copy2(target, stash)
                replaced.append((target, stash))

            shutil.copyfile(action.source_path, target)
            if action.is_new_file:
                written.append(target)

            results.append({
                "target_name": action.target_name,
                "source_name": action.source_name,
                "is_new_file": action.is_new_file,
                "backup_path": None,
            })
            logger.info(
                f"FSR 4: {'added' if action.is_new_file else 'replaced'} "
                f"{action.target_name} in {plan.game_dir}"
            )

        # Persist the originals from the stash (which holds their exact bytes).
        # A .dlsss already present from an earlier update is overwritten, as
        # updater.create_backup does.
        backups = {target.name.lower(): stash for target, stash in replaced}
        for result, action in zip(results, plan.actions):
            stash = backups.get(action.target_name.lower())
            if stash is None:
                continue
            backup_path = Path(action.target_path).with_suffix(".dlsss")
            shutil.copy2(stash, backup_path)
            result["backup_path"] = str(backup_path)

        return True, f"Installed {len(plan.actions)} FidelityFX DLL(s)", results

    except Exception as e:
        logger.error(f"FSR 4 install failed, rolling back: {e}", exc_info=True)

        for target in written:
            try:
                target.unlink(missing_ok=True)
            except OSError as undo_error:
                logger.error(f"Rollback could not remove {target}: {undo_error}")
        for target, stash in replaced:
            try:
                shutil.copyfile(stash, target)
            except OSError as undo_error:
                logger.error(f"Rollback could not restore {target}: {undo_error}")

        return False, f"FSR 4 install failed and was rolled back: {e}", []

    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


async def apply_fsr4_upgrade(plan: FSR4Plan, game_id: int) -> tuple[bool, str, list[dict]]:
    """
    Apply a plan and record it so the whole set can be reverted later.

    Files that were CREATED get a ``was_added=True`` backup record, whose
    restore removes them again; files that were REPLACED get an ordinary backup
    record pointing at the ``.dlsss`` copy ``_apply_plan_sync`` wrote.
    """
    if not plan.is_actionable:
        return False, plan.skipped_reason or "Nothing to install", []

    success, message, results = await anyio.to_thread.run_sync(
        _apply_plan_sync, plan, limiter=thread_io
    )
    if not success:
        return success, message, results

    backup_paths = {r["target_name"]: r.get("backup_path") for r in results}

    for action in plan.actions:
        target = Path(action.target_path)
        try:
            dll_row = await db_manager.upsert_game_dll({
                "game_id": game_id,
                "dll_path": str(target),
                "dll_filename": action.target_name,
                "dll_type": _dll_type_for(action.target_name),
                "current_version": _safe_version(target),
            })
            dll_id = getattr(dll_row, "id", dll_row)

            if action.is_new_file:
                await db_manager.insert_backup({
                    "game_dll_id": dll_id,
                    "backup_path": str(target),
                    "backup_size": target.stat().st_size if target.exists() else 0,
                    "was_added": True,
                })
            elif backup_paths.get(action.target_name):
                backup = Path(backup_paths[action.target_name])
                await db_manager.insert_backup({
                    "game_dll_id": dll_id,
                    "backup_path": str(backup),
                    "original_version": _safe_version(backup),
                    "backup_size": backup.stat().st_size if backup.exists() else 0,
                })
        except Exception as e:
            # The files are already correct on disk; losing a bookkeeping row
            # must not be reported as an install failure.
            logger.error(f"FSR 4: could not record {action.target_name}: {e}", exc_info=True)

    return True, message, results


def _dll_type_for(dll_name: str) -> str:
    from dlss_updater.constants import DLL_TYPE_MAP
    return DLL_TYPE_MAP.get(dll_name.lower(), "AMD FidelityFX DLL")


def _safe_version(path: Path) -> str | None:
    from dlss_updater.updater import get_dll_version
    try:
        return get_dll_version(str(path))
    except Exception:
        return None
