"""
In-app self-updater for the application itself (not game DLLs).

Two channels, two very different capabilities:

**Windows** — releases ship an MSI whose WiX configuration already carries a
proper major-upgrade setup (an ``Upgrade`` table driving ``FindRelatedProducts``
and ``RemoveExistingProducts``, under a stable UpgradeCode). Installing a newer
MSI over an older install is therefore a clean in-place upgrade, and the whole
update reduces to: download, verify, hand the file to ``msiexec``, relaunch.
Two properties of the app make this cheap:

  * It already elevates itself at startup (``main.py`` -> ``utils.run_as_admin``),
    so ``msiexec`` inherits admin rights and the user sees no second UAC prompt.
  * The Flet desktop client runs from ``~/.flet/client/...``, *outside* the
    install directory, so the only process holding files in ``INSTALLFOLDER`` is
    this one. Exiting before the installer runs is sufficient - there is no need
    to hunt down child processes or negotiate with Restart Manager.

**Linux** — releases ship a Flatpak bundle and the app runs inside the sandbox.
Installing a Flatpak from within the sandbox would require
``--talk-name=org.freedesktop.Flatpak``, which permits running arbitrary host
executables and is a full sandbox escape, so it is deliberately not requested.
The updater therefore downloads the bundle to the user's Downloads directory and
reveals it - the install itself stays a user action. Newer bundles are built with
``--repo-url`` (see ``build_flatpak.sh``), so the ordinary route on Linux is
``flatpak update`` / the desktop software centre picking the release up on its
own; this download path is the manual fallback.

The Flathub build (a separate application ID) never self-downloads at all -
``SelfUpdater.is_supported()`` is False there, and updates arrive through
Flathub.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path, PurePath

import aiohttp
import anyio
import msgspec

from dlss_updater.auto_updater import (
    fetch_latest_release,
    find_platform_asset,
    is_newer_version,
)
from dlss_updater.concurrency_limiters import thread_io
from dlss_updater.linux_paths import is_flathub
from dlss_updater.logger import setup_logger
from dlss_updater.version import __version__

logger = setup_logger()

# Streaming chunk size. 256KB is large enough that per-chunk overhead is noise
# against a ~31MB download, small enough to keep progress smooth.
_CHUNK_SIZE = 256 * 1024

# Minimum progress callback interval. Every callback drives a UI update, and the
# pill only shows whole percentages, so ~10/sec is already more than the display
# can distinguish.
_PROGRESS_INTERVAL_BYTES = 512 * 1024

# Headroom required beyond the download itself: the MSI is written to disk, then
# expanded into Program Files by the installer.
_FREE_SPACE_MULTIPLIER = 3

# CreateProcess flags for msiexec. It must escape any job object that would kill
# it when this process exits (CREATE_BREAKAWAY_FROM_JOB) and gets no console of
# its own (CREATE_NO_WINDOW) - which does not touch its /qb progress window,
# msiexec being a GUI application.
#
# Note DETACHED_PROCESS is deliberately NOT used: it is mutually exclusive with
# CREATE_NO_WINDOW, and combining them produces a process with no console at all,
# in which console-dependent child processes hang indefinitely.
_CREATE_NO_WINDOW = 0x08000000
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Prefix of the per-download temp directories, and how old one must be before a
# later run treats it as abandoned. An install takes well under a minute, so an
# hour is generous enough that a second instance can never delete an installer
# that is still being applied.
_DOWNLOAD_DIR_PREFIX = "dlss_updater_update_"
_STALE_DOWNLOAD_AGE_SECONDS = 60 * 60


class SelfUpdateStage(StrEnum):
    """Lifecycle of an application update, as surfaced to the UI."""

    IDLE = "idle"
    CHECKING = "checking"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY = "ready"
    INSTALLING = "installing"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class UpdateInfo(msgspec.Struct):
    """An available release and the asset to fetch for this platform."""

    latest_version: str
    download_url: str
    digest: str = ""       # "sha256:..." as served by the GitHub API; "" if absent
    asset_name: str = ""
    size: int = 0


class SelfUpdateProgress(msgspec.Struct):
    """Progress snapshot passed to the UI during a self-update.

    Named to avoid colliding with ``models.UpdateProgress``, which tracks game
    DLL update progress and is a different shape.
    """

    stage: SelfUpdateStage
    bytes_done: int = 0
    bytes_total: int = 0
    fraction: float = 0.0   # 0.0-1.0; 0.0 when the total is unknown
    message: str = ""


class SelfUpdateError(Exception):
    """Raised when an update cannot be downloaded, verified or applied."""


def _digest_hex(digest: str) -> str:
    """Extract the hex portion of a ``"sha256:<hex>"`` digest string."""
    return digest.split(":", 1)[1].strip().lower() if ":" in digest else digest.strip().lower()


def _downloads_dir() -> Path:
    """The user's Downloads directory, honouring ``XDG_DOWNLOAD_DIR``."""
    configured = os.environ.get("XDG_DOWNLOAD_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Downloads"


class SelfUpdater:
    """Downloads and applies application updates for the current platform."""

    def __init__(self) -> None:
        self._temp_dir: Path | None = None

    # -------------------------------------------------------------------------
    # Capability
    # -------------------------------------------------------------------------

    @staticmethod
    def is_supported() -> bool:
        """Whether this build may fetch its own updates.

        False for the Flathub build, whose updates are Flathub's responsibility
        and whose store listing must not offer out-of-band downloads. False on
        platforms with no release asset. Also False when not frozen: a source
        checkout is updated with git, and pointing an installer at it would be
        actively wrong.
        """
        if is_flathub():
            return False
        if sys.platform not in ("win32", "linux"):
            return False
        return bool(getattr(sys, "frozen", False))

    @property
    def applies_in_place(self) -> bool:
        """Whether :meth:`apply` actually installs the update.

        True on Windows (msiexec performs the upgrade and the app relaunches).
        False on Linux, where the sandbox forbids installing and ``apply`` only
        reveals the downloaded bundle.
        """
        return sys.platform == "win32"

    # -------------------------------------------------------------------------
    # Check
    # -------------------------------------------------------------------------

    async def check(self) -> UpdateInfo | None:
        """Return the available update, or None if up to date or unreachable."""
        if not self.is_supported():
            logger.debug("Self-update not supported for this build - skipping check")
            return None

        await self._sweep_stale_downloads()

        try:
            release = await fetch_latest_release()
        except (TimeoutError, aiohttp.ClientError) as e:
            logger.error(f"Update check failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during update check: {e}")
            return None

        if not release:
            return None

        latest = str(release.get("tag_name", "")).lstrip("Vv")
        if not latest or not is_newer_version(latest, __version__):
            logger.info("Application is up to date.")
            return None

        asset = find_platform_asset(release)
        if asset is None:
            logger.warning(
                f"Release {latest} has no asset for this platform - cannot self-update"
            )
            return None

        info = UpdateInfo(
            latest_version=latest,
            download_url=asset.get("browser_download_url", ""),
            digest=str(asset.get("digest") or ""),
            asset_name=str(asset.get("name") or ""),
            size=int(asset.get("size") or 0),
        )
        if not info.download_url:
            logger.warning(f"Asset {info.asset_name} has no download URL")
            return None

        logger.info(f"Update available: {__version__} -> {latest} ({info.asset_name})")
        return info

    # -------------------------------------------------------------------------
    # Download
    # -------------------------------------------------------------------------

    async def download(
        self,
        info: UpdateInfo,
        on_progress: Callable[[SelfUpdateProgress], Awaitable[None]] | None = None,
    ) -> Path:
        """Download ``info``'s asset, verify its digest, and return the file path.

        The download is streamed and hashed as it arrives, so verification costs
        no extra pass over the data. A failed or corrupt download deletes its
        partial file rather than leaving something that looks installable behind.
        """
        target_dir = await self._prepare_target_dir(info)
        target = target_dir / (info.asset_name or "DLSS_Updater_update")

        async def report(progress: SelfUpdateProgress) -> None:
            if on_progress is not None:
                await on_progress(progress)

        digest = hashlib.sha256()
        done = 0
        next_report = 0

        try:
            from dlss_updater.dll_repository import get_http_session

            session = await get_http_session()
            # No total timeout: a slow connection on a ~31MB asset would trip it.
            # sock_read guards the case that actually matters - a stalled socket.
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)

            async with session.get(info.download_url, timeout=timeout) as response:
                if response.status != 200:
                    raise SelfUpdateError(
                        f"Download failed with HTTP {response.status}"
                    )

                total = int(response.headers.get("Content-Length") or info.size or 0)

                with open(target, "wb") as fh:
                    async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
                        # Hashing and writing are both CPU/IO work on the event
                        # loop thread, but at 256KB granularity each iteration is
                        # sub-millisecond and the await above yields between them.
                        fh.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)

                        if done >= next_report:
                            next_report = done + _PROGRESS_INTERVAL_BYTES
                            await report(
                                SelfUpdateProgress(
                                    stage=SelfUpdateStage.DOWNLOADING,
                                    bytes_done=done,
                                    bytes_total=total,
                                    fraction=(done / total) if total else 0.0,
                                    message=f"Downloading {info.latest_version}",
                                )
                            )

            await report(
                SelfUpdateProgress(
                    stage=SelfUpdateStage.VERIFYING,
                    bytes_done=done,
                    bytes_total=done,
                    fraction=1.0,
                    message="Verifying download",
                )
            )
            self._verify(target, digest.hexdigest(), info)

        except Exception:
            # Never leave a partial or unverified file where it could be run.
            await self._unlink_quietly(target)
            raise

        logger.info(f"Update downloaded and verified: {target}")
        await report(
            SelfUpdateProgress(
                stage=SelfUpdateStage.READY,
                bytes_done=done,
                bytes_total=done,
                fraction=1.0,
                message=f"Ready to install {info.latest_version}",
            )
        )
        return target

    def _verify(self, target: Path, actual_hex: str, info: UpdateInfo) -> None:
        """Compare the streamed hash against the API-supplied digest."""
        expected = _digest_hex(info.digest)
        if not expected:
            # The API has supplied `digest` per asset since 2025, but don't hard
            # fail if a release predates it or the field is dropped - HTTPS to
            # api.github.com still authenticated where the URL came from.
            logger.warning(
                f"No digest published for {info.asset_name} - skipping hash verification"
            )
            return

        if actual_hex != expected:
            raise SelfUpdateError(
                f"Digest mismatch for {info.asset_name}: "
                f"expected {expected}, got {actual_hex}"
            )
        logger.info(f"Verified sha256 for {info.asset_name}")

    async def _prepare_target_dir(self, info: UpdateInfo) -> Path:
        """Choose and validate the download directory for this platform."""
        if self.applies_in_place:
            # Windows: a private temp dir. Nothing outlives this process to
            # clear it once msiexec is done, so it is swept by a later update
            # check instead - see _sweep_stale_downloads().
            if self._temp_dir is None:
                self._temp_dir = Path(
                    await anyio.to_thread.run_sync(
                        lambda: tempfile.mkdtemp(prefix=_DOWNLOAD_DIR_PREFIX),
                        limiter=thread_io,
                    )
                )
            target_dir = self._temp_dir
        else:
            # Linux: the user has to install it themselves, so put it somewhere
            # they can find. `home` is writable in both Flatpak manifests.
            target_dir = _downloads_dir()
            await anyio.to_thread.run_sync(
                lambda: target_dir.mkdir(parents=True, exist_ok=True),
                limiter=thread_io,
            )

        required = max(info.size, 1) * _FREE_SPACE_MULTIPLIER
        free = await anyio.to_thread.run_sync(
            lambda: shutil.disk_usage(target_dir).free, limiter=thread_io
        )
        if free < required:
            raise SelfUpdateError(
                f"Not enough free space in {target_dir}: "
                f"{free // (1024 * 1024)}MB available, "
                f"{required // (1024 * 1024)}MB needed"
            )
        return target_dir

    @staticmethod
    async def _unlink_quietly(path: Path) -> None:
        try:
            await anyio.to_thread.run_sync(
                lambda: path.unlink(missing_ok=True), limiter=thread_io
            )
        except Exception as e:
            logger.warning(f"Could not remove partial download {path}: {e}")

    # -------------------------------------------------------------------------
    # Apply
    # -------------------------------------------------------------------------

    async def apply(self, path: Path, info: UpdateInfo) -> None:
        """Apply the downloaded update.

        On Windows this launches msiexec and returns; **the caller must then
        shut the application down promptly**, because the installer cannot
        replace files this process still holds open. On Linux it reveals the
        downloaded bundle and returns - nothing is installed.
        """
        if sys.platform == "win32":
            await self._spawn_windows_installer(path, info)
        else:
            await self._reveal(path)

    async def _sweep_stale_downloads(self) -> None:
        """Delete installers left behind by earlier updates.

        Nothing can clean up after msiexec any more: this process is gone long
        before the install finishes, so the ~70MB MSI and its verbose log stay
        in the temp directory they were downloaded to. The old PowerShell
        helper deleted them because it outlived us; now the next update check
        does it instead, which is the first moment this app is running again
        and any earlier install is certainly over.

        Never raises - a failed sweep is disk hygiene, not an update failure.
        """
        if not self.applies_in_place:
            return

        def _sweep() -> int:
            removed = 0
            cutoff = time.time() - _STALE_DOWNLOAD_AGE_SECONDS
            for entry in Path(tempfile.gettempdir()).glob(f"{_DOWNLOAD_DIR_PREFIX}*"):
                if entry == self._temp_dir or not entry.is_dir():
                    continue
                try:
                    # The age guard is what makes this safe to run while another
                    # instance may be mid-install: its directory is minutes old,
                    # not hours.
                    if entry.stat().st_mtime > cutoff:
                        continue
                except OSError:
                    continue
                shutil.rmtree(entry, ignore_errors=True)
                removed += not entry.exists()
            return removed

        try:
            removed = await anyio.to_thread.run_sync(_sweep, limiter=thread_io)
        except Exception as e:
            logger.debug(f"Could not sweep stale update downloads: {e}")
            return
        if removed:
            logger.info(f"Cleaned up {removed} leftover update download(s)")

    @staticmethod
    def _msiexec_path() -> str:
        """Absolute path to msiexec, so PATH cannot decide what we execute."""
        system_root = os.environ.get("SystemRoot")
        if system_root:
            candidate = Path(system_root) / "System32" / "msiexec.exe"
            if candidate.exists():
                return str(candidate)
        return "msiexec.exe"

    async def _spawn_windows_installer(self, msi: Path, info: UpdateInfo) -> None:
        """Launch msiexec detached, for the caller to then shut the app down.

        No helper process, and deliberately no PowerShell. The previous design
        wrote ``apply_update.ps1`` beside the downloaded installer and ran it
        with ``-ExecutionPolicy Bypass -WindowStyle Hidden`` so it could wait
        for this process to exit, install, and relaunch us. Feature for feature
        that is a dropper - an unsigned binary fetching an executable payload,
        dropping a script next to it and running it hidden with the execution
        policy overridden - and Defender's ML classifier scored it as one:
        5.0.2 drew a Trojan:Script/Wacatac.C!ml verdict on DLSS_Updater.exe
        within hours of release (issue #306).

        Windows Installer does not need any of it. msiexec outlives us on its
        own, and its Restart Manager integration handles files that are still
        open when it reaches InstallValidate.

        The cost is the relaunch: nothing is left running to start the new
        build, so the app does not reappear by itself after an update. Restart
        Manager cannot stand in for it either - ``RmRestart`` only restarts
        applications that registered via ``RegisterApplicationRestart`` AND run
        as the logged-on user, and this app relaunches itself elevated.

        Races with our own shutdown are benign: if this process still holds its
        files when msiexec gets there, the installer's own files-in-use handling
        takes over, which is a prompt rather than a failed install.
        """
        log_path = msi.with_suffix(".install.log")

        # /qb (basic UI) rather than /qn: the app exits immediately afterwards,
        # so a silent install would leave the user staring at nothing for ~20
        # seconds with no indication that anything is happening.
        #
        # Arguments go through the list form, so paths containing spaces (and
        # Briefcase names the MSI "DLSS Updater-X.Y.Z.msi") are quoted by
        # subprocess rather than by hand.
        command = [
            self._msiexec_path(),
            "/i", str(msi),
            "/qb",
            "/norestart",
            "/l*v", str(log_path),
        ]
        logger.info(
            f"Launching installer for {info.latest_version}: {msi} "
            f"(msiexec log: {log_path})"
        )
        await anyio.to_thread.run_sync(
            lambda: self._popen_detached(command), limiter=thread_io
        )

    @staticmethod
    def _popen_detached(command: list[str]) -> subprocess.Popen:
        """Start a process that outlives this one, with no console of its own.

        ``CREATE_NO_WINDOW`` gives the child a console it simply never shows -
        which is what console-dependent tooling needs - and is mutually exclusive
        with ``DETACHED_PROCESS``; passing both yields a process with NO console
        at all, where the child's own child processes can hang.

        ``CREATE_BREAKAWAY_FROM_JOB`` escapes a job object that would otherwise
        kill the installer when this process dies, but CreateProcess REFUSES it
        outright when the current job disallows breakaway, so it is attempted
        first and dropped if rejected.
        """
        kwargs = {
            "close_fds": True,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        try:
            return subprocess.Popen(
                command,
                creationflags=_CREATE_NO_WINDOW | _CREATE_BREAKAWAY_FROM_JOB,
                **kwargs,
            )
        except OSError as e:
            logger.info(f"Job breakaway refused ({e}); starting installer without it")
            return subprocess.Popen(
                command, creationflags=_CREATE_NO_WINDOW, **kwargs
            )

    async def _reveal(self, path: Path) -> None:
        """Open the downloaded file's directory in the user's file manager."""
        try:
            await anyio.to_thread.run_sync(
                lambda: subprocess.Popen(
                    ["xdg-open", str(path.parent)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ),
                limiter=thread_io,
            )
        except Exception as e:
            logger.warning(f"Could not open {path.parent} in a file manager: {e}")

    @staticmethod
    def install_command(path: PurePath) -> str:
        """The command a Linux user runs to install a downloaded bundle.

        Accepts any PurePath so the rendering can be exercised with POSIX paths
        from a test running on Windows; quoted because Downloads paths routinely
        contain spaces.
        """
        return f'flatpak install --user "{path}"'
