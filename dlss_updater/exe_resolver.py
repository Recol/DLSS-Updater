"""
Per-game executable resolver for DLSS preset overrides.

The scanner only ever stores a game's *folder* (``Game.path``) and DLL paths -
never an executable. Applying a per-application DLSS preset override via NvAPI
(``dlss_updater.nvapi_drs.apply_presets_for_app``) requires the fully-qualified
path to the game's main executable. This module bridges that gap.

Resolution order (short-circuits on the first success):

    1. cache          - the exe previously resolved and saved for this game
                        (``db_manager.get_game_exe_sync``). High confidence if the
                        file still exists on disk.
    2. heuristic      - recursively scan the game folder for ``.exe`` files,
                        skipping junk dirs (``scanner._SKIP_DIRECTORIES``) and a
                        denylist of non-game executables (``_NON_GAME_EXE``),
                        ranked by file size (largest = most likely the game).
    3. driver-validate- for the top heuristic candidates, ask the NVIDIA driver
                        whether it already knows the exe
                        (``nvapi_drs.get_presets_for_app`` -> ``meta["found"]``).
                        The first known exe wins (high confidence: the driver maps
                        it to a profile, predefined or otherwise).
    4. steam_manifest - Steam games only: confirm/locate the install dir via the
                        ``appmanifest_<appid>.acf`` and pick the largest exe there.
    5. none           - nothing resolved. ``exe_path is None`` is the SOLE signal
                        to the UI to prompt a file picker. The resolver NEVER
                        raises for a not-found / I/O error - all such failures are
                        logged and downgraded to ``source="none"``.

Performance / free-threading:
    - The async ``resolve_game_exe`` offloads all blocking filesystem work via
      ``asyncio.to_thread`` so the Flet event loop is never blocked, and uses the
      existing async NvAPI call for driver validation.
    - ``_resolve_game_exe_sync`` is a fully synchronous variant for use inside
      ``HyperParallelLoader`` / ``ThreadPoolExecutor`` (true parallelism on the
      free-threaded 3.14 build). It reuses the NvAPI blocking implementation
      directly to keep the driver-first behaviour in batch loads.

This module is pure backend: it imports NO Flet symbols.
"""

from __future__ import annotations

import logging
import os

import anyio

from dlss_updater import nvapi_drs
from dlss_updater.concurrency_limiters import thread_io
from dlss_updater.scanner import _SKIP_DIRECTORIES

logger = logging.getLogger("DLSSUpdater")

# How many top-ranked candidates to probe against the NVIDIA driver. Each probe
# opens a short DRS session, so we cap this to keep resolution snappy.
_DRIVER_PROBE_LIMIT = 5

# Executables that are never the game itself. Matched case-insensitively against
# the exe basename. ``_NON_GAME_EXE_SUBSTRINGS`` match anywhere in the name;
# the helpers below also strip common redistributable / launcher / crash-handler
# binaries that share a folder with the real game.
_NON_GAME_EXE_EXACT: frozenset = frozenset({
    "launcher.exe",
    "unitycrashhandler.exe",
    "unitycrashhandler64.exe",
    "unitycrashhandler32.exe",
    "crashreportclient.exe",
    "crashreporter.exe",
    "crashpad_handler.exe",
    "dxsetup.exe",
    "dxwebsetup.exe",
    "vc_redist.x64.exe",
    "vc_redist.x86.exe",
    "vcredist_x64.exe",
    "vcredist_x86.exe",
    "oalinst.exe",
    "dotnetfx.exe",
    "uninstall.exe",
    "easyanticheat.exe",
    "easyanticheat_setup.exe",
    "battleye.exe",
    "beservice.exe",
    "activationui.exe",
    "touchupdater.exe",
    "notification_helper.exe",
})

# Substrings: an exe whose lowercase name contains any of these is treated as a
# non-game helper (launchers, installers, crash/error handlers, redistributables,
# anti-cheat, etc).
_NON_GAME_EXE_SUBSTRINGS: tuple[str, ...] = (
    "crash",
    "handler",
    "unins",        # uninstall / unins000.exe
    "vcredist",
    "vc_redist",
    "dxsetup",
    "setup",
    "redist",
    "installer",
    "install_",
    "launcher",
    "easyanticheat",
    "anticheat",
    "battleye",
    "report",       # crashreport / bugreport
    "helper",
    "diagnostic",
    "cleanup",
    "updater",      # in-game patchers/updaters are not the game exe
)


def _is_non_game_exe(exe_name: str) -> bool:
    """True if ``exe_name`` (basename) looks like a launcher/installer/helper."""
    low = exe_name.lower()
    if low in _NON_GAME_EXE_EXACT:
        return True
    return any(sub in low for sub in _NON_GAME_EXE_SUBSTRINGS)


def _scan_candidate_exes(root_path: str) -> list[str]:
    """Recursively collect game-candidate ``.exe`` paths under ``root_path``.

    Honors ``scanner._SKIP_DIRECTORIES`` and the ``_NON_GAME_EXE`` denylist, and
    returns paths ranked largest-file-first (the main game binary is almost
    always the biggest exe in the tree). All filesystem errors are swallowed -
    this never raises.

    Returns an empty list if nothing usable is found (caller downgrades to
    ``source="none"``).
    """
    # Collect (size, path) then sort once at the end. Using a list of tuples
    # avoids repeated os.path.getsize during the walk.
    found: list[tuple[int, str]] = []

    def _walk(directory: str) -> None:
        subdirs: list[str] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        name_lower = entry.name.lower()
                        if entry.is_file(follow_symlinks=False):
                            if name_lower.endswith(".exe") and not _is_non_game_exe(entry.name):
                                try:
                                    size = entry.stat(follow_symlinks=False).st_size
                                except (OSError, PermissionError):
                                    size = 0
                                found.append((size, entry.path))
                        elif entry.is_dir(follow_symlinks=False):
                            if name_lower not in _SKIP_DIRECTORIES:
                                subdirs.append(entry.path)
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            return

        # Recurse after the scandir handle is closed (resource-leak safety,
        # mirroring scanner._parallel_scandir_walk).
        for sub in subdirs:
            _walk(sub)

    if not root_path or not os.path.isdir(root_path):
        return []

    _walk(root_path)

    # Largest first; stable on path for determinism between equal sizes.
    found.sort(key=lambda t: (-t[0], t[1].lower()))
    return [path for _size, path in found]


def _resolve_steam_manifest_exe(game, candidates: list[str]) -> str | None:
    """Best-effort Steam-manifest assist.

    The ``appmanifest_<appid>.acf`` does NOT record a launch executable - it only
    carries ``installdir``. So this step cannot name the exe directly; instead it
    confirms the manifest's install directory and returns the largest candidate
    exe that lives under it. This mainly disambiguates when the resolver is given
    a broader/parent path than the actual Steam install dir.

    Returns a candidate path (already in ``candidates``) or None.
    """
    app_id = getattr(game, "effective_steam_app_id", None) or getattr(game, "steam_app_id", None)
    launcher = getattr(game, "launcher", None)
    game_path = getattr(game, "path", None)
    if launcher != "Steam" or not app_id or not game_path:
        return None

    try:
        # steamapps/common/<installdir> -> steamapps/appmanifest_<appid>.acf
        common_dir = os.path.dirname(os.path.normpath(game_path))         # .../common
        steamapps_dir = os.path.dirname(common_dir)                        # .../steamapps
        manifest = os.path.join(steamapps_dir, f"appmanifest_{app_id}.acf")
        if not os.path.isfile(manifest):
            return None

        # Read installdir via the shared VDF/ACF parser (sync variant for this
        # ThreadPoolExecutor-safe path). Error-tolerant: returns {} on a
        # malformed/partial manifest rather than raising.
        from dlss_updater.vdf_parser import VDFParser

        manifest_data = VDFParser.parse_file_sync(manifest)
        app_state = manifest_data.get("AppState", manifest_data)
        installdir = app_state.get("installdir") if isinstance(app_state, dict) else None
        if not installdir:
            return None

        install_path = os.path.normcase(os.path.normpath(
            os.path.join(common_dir, installdir)
        ))
        # Return the largest candidate that lives under the manifest install dir.
        for cand in candidates:
            cand_norm = os.path.normcase(os.path.normpath(cand))
            if cand_norm.startswith(install_path + os.sep) or os.path.dirname(cand_norm) == install_path:
                return cand
        return None
    except Exception as e:
        logger.debug(f"Steam manifest exe assist failed for {game_path}: {e}")
        return None


def _primary_path(game) -> str | None:
    """Return the folder to resolve against (MergedGame -> primary_game.path)."""
    primary = getattr(game, "primary_game", None)
    if primary is not None:
        return getattr(primary, "path", None)
    return getattr(game, "path", None)


def _game_id(game) -> int | None:
    """Return the id to use for the cache lookup (MergedGame -> primary id)."""
    primary = getattr(game, "primary_game", None)
    if primary is not None:
        return getattr(primary, "id", None)
    return getattr(game, "id", None)


def _make_resolution(exe_path, source, confidence, candidates):
    """Construct an ExeResolution (imported lazily to keep import graph light)."""
    from dlss_updater.models import ExeResolution
    exe_name = os.path.basename(exe_path) if exe_path else None
    return ExeResolution(
        exe_path=exe_path,
        exe_name=exe_name,
        source=source,
        confidence=confidence,
        candidates=candidates,
    )


# =============================================================================
# Async resolver (event-loop friendly)
# =============================================================================

async def resolve_game_exe(game, db_manager) -> "object":
    """Resolve the primary executable for ``game`` (Game or MergedGame).

    See module docstring for the resolution order. Returns an
    ``ExeResolution``. Never raises for not-found / I/O errors - those downgrade
    to ``source="none"`` so the UI can offer a file picker.

    All blocking work (filesystem walks) runs via ``asyncio.to_thread``; driver
    validation uses the existing async ``nvapi_drs.get_presets_for_app`` call.
    """
    game_path = _primary_path(game)
    gid = _game_id(game)

    # --- 1. cache ---------------------------------------------------------
    if gid is not None:
        try:
            cached = await anyio.to_thread.run_sync(db_manager.get_game_exe_sync, gid, limiter=thread_io)
        except Exception as e:
            logger.debug(f"Cache lookup failed for game {gid}: {e}")
            cached = None
        if cached:
            try:
                exists = await anyio.to_thread.run_sync(os.path.isfile, cached, limiter=thread_io)
            except Exception:
                exists = False
            if exists:
                return _make_resolution(cached, "cache", "high", [cached])
            # File gone - fall through to re-resolve.

    # --- 2. heuristic (ranked candidates) --------------------------------
    try:
        candidates = await anyio.to_thread.run_sync(_scan_candidate_exes, game_path, limiter=thread_io)
    except Exception as e:
        logger.debug(f"Heuristic exe scan failed for {game_path}: {e}")
        candidates = []

    if not candidates:
        # Nothing to work with -> none (UI prompts file picker).
        return _make_resolution(None, "none", "low", [])

    # --- 3. driver-validate ----------------------------------------------
    if nvapi_drs.is_available():
        for cand in candidates[:_DRIVER_PROBE_LIMIT]:
            try:
                _values, meta, error = await nvapi_drs.get_presets_for_app(cand)
            except Exception as e:
                logger.debug(f"Driver probe errored for {cand}: {e}")
                continue
            if error:
                logger.debug(f"Driver probe error for {cand}: {error}")
                continue
            if meta.get("found") is True:
                # The driver already maps this exe to a profile -> high confidence.
                return _make_resolution(cand, "driver", "high", candidates)

    # --- 4. steam_manifest ------------------------------------------------
    try:
        manifest_exe = await anyio.to_thread.run_sync(_resolve_steam_manifest_exe, game, candidates, limiter=thread_io)
    except Exception as e:
        logger.debug(f"Steam manifest assist failed: {e}")
        manifest_exe = None
    if manifest_exe:
        return _make_resolution(manifest_exe, "steam_manifest", "medium", candidates)

    # --- fallback: largest heuristic pick --------------------------------
    return _make_resolution(candidates[0], "heuristic", "medium", candidates)


# =============================================================================
# Synchronous resolver (HyperParallelLoader / ThreadPoolExecutor)
# =============================================================================

def _resolve_game_exe_sync(game, db_path_or_manager) -> "object":
    """Fully synchronous resolver for use inside ThreadPoolExecutor / batch loads.

    ``db_path_or_manager`` may be a DatabaseManager (we call
    ``get_game_exe_sync``) or anything falsy/None to skip the cache step. The
    driver-validate step reuses the existing NvAPI blocking implementation
    (``nvapi_drs._read_app_blocking``) so true parallel batch loads still get the
    driver-first behaviour without spinning the event loop.

    Returns an ``ExeResolution``. Never raises.
    """
    game_path = _primary_path(game)
    gid = _game_id(game)

    # --- 1. cache ---------------------------------------------------------
    get_exe = getattr(db_path_or_manager, "get_game_exe_sync", None)
    if gid is not None and callable(get_exe):
        try:
            cached = get_exe(gid)
        except Exception as e:
            logger.debug(f"Sync cache lookup failed for game {gid}: {e}")
            cached = None
        if cached:
            try:
                exists = os.path.isfile(cached)
            except Exception:
                exists = False
            if exists:
                return _make_resolution(cached, "cache", "high", [cached])

    # --- 2. heuristic -----------------------------------------------------
    try:
        candidates = _scan_candidate_exes(game_path)
    except Exception as e:
        logger.debug(f"Sync heuristic scan failed for {game_path}: {e}")
        candidates = []

    if not candidates:
        return _make_resolution(None, "none", "low", [])

    # --- 3. driver-validate (blocking NvAPI) ------------------------------
    if nvapi_drs.is_available():
        for cand in candidates[:_DRIVER_PROBE_LIMIT]:
            try:
                _values, meta, error = nvapi_drs._read_app_blocking(cand)
            except Exception as e:
                logger.debug(f"Sync driver probe errored for {cand}: {e}")
                continue
            if error:
                continue
            if meta.get("found") is True:
                return _make_resolution(cand, "driver", "high", candidates)

    # --- 4. steam_manifest ------------------------------------------------
    try:
        manifest_exe = _resolve_steam_manifest_exe(game, candidates)
    except Exception as e:
        logger.debug(f"Sync steam manifest assist failed: {e}")
        manifest_exe = None
    if manifest_exe:
        return _make_resolution(manifest_exe, "steam_manifest", "medium", candidates)

    # --- fallback ---------------------------------------------------------
    return _make_resolution(candidates[0], "heuristic", "medium", candidates)
