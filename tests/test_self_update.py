"""Tests for the application self-updater (dlss_updater.self_update).

All network IO is faked: these cover asset selection, version comparison,
digest verification and the capability gates, not GitHub connectivity.
"""

import hashlib
import sys
from pathlib import Path, PurePosixPath

import pytest

from dlss_updater import auto_updater
from dlss_updater.self_update import (
    SelfUpdateError,
    SelfUpdateProgress,
    SelfUpdateStage,
    SelfUpdater,
    UpdateInfo,
    _digest_hex,
    _downloads_dir,
)

# A release payload shaped like the real GitHub API response, including the
# per-asset `digest` field the updater verifies against.
RELEASE = {
    "tag_name": "V4.5.2",
    "assets": [
        {
            "name": "DLSS.Updater.4.5.2.msi",
            "size": 31052260,
            "digest": "sha256:" + "a" * 64,
            "browser_download_url": "https://example.invalid/DLSS.Updater.4.5.2.msi",
        },
        {
            "name": "DLSS_Updater-4.5.2.flatpak",
            "size": 35856088,
            "digest": "sha256:" + "b" * 64,
            "browser_download_url": "https://example.invalid/DLSS_Updater-4.5.2.flatpak",
        },
    ],
}


# =============================================================================
# Asset selection
# =============================================================================


def test_windows_asset_suffix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert auto_updater.get_platform_asset_suffix() == ".msi"


def test_linux_asset_suffix(monkeypatch):
    """Regression: the old prefix match looked for a tarball that no longer ships.

    Releases have shipped only `.msi` and `.flatpak` since before V4.3.1, so the
    previous `DLSS_Updater_Linux` pattern matched nothing and every Linux user
    silently fell through to the generic releases page.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    assert auto_updater.get_platform_asset_suffix() == ".flatpak"


def test_find_platform_asset_picks_flatpak_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    asset = auto_updater.find_platform_asset(RELEASE)
    assert asset is not None
    assert asset["name"] == "DLSS_Updater-4.5.2.flatpak"


def test_find_platform_asset_picks_msi_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    asset = auto_updater.find_platform_asset(RELEASE)
    assert asset is not None
    assert asset["name"] == "DLSS.Updater.4.5.2.msi"


def test_find_platform_asset_returns_none_on_other_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert auto_updater.find_platform_asset(RELEASE) is None


def test_find_platform_asset_handles_release_without_assets():
    assert auto_updater.find_platform_asset({"tag_name": "V4.5.2"}) is None


# =============================================================================
# Version comparison
# =============================================================================


@pytest.mark.parametrize(
    "latest, current, expected",
    [
        ("4.5.2", "4.5.1", True),
        ("V4.5.2", "4.5.1", True),      # tag prefix tolerated
        ("v4.5.2", "V4.5.1", True),
        ("4.5.1", "4.5.1", False),      # equal is not newer
        ("4.5.0", "4.5.1", False),      # downgrade is not newer
        ("4.6.0", "4.5.9", True),
        ("5.0.0", "4.99.99", True),
        ("not-a-version", "4.5.1", False),   # malformed can never trigger an update
        ("", "4.5.1", False),
    ],
)
def test_is_newer_version(latest, current, expected):
    assert auto_updater.is_newer_version(latest, current) is expected


# =============================================================================
# Capability gates
# =============================================================================


def test_not_supported_on_flathub(monkeypatch):
    """The Flathub build must never fetch its own updates."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("FLATPAK_ID", "io.github.recol.dlss_updater")
    assert SelfUpdater.is_supported() is False


def test_supported_for_github_flatpak_bundle(monkeypatch):
    """The GitHub-bundle Flatpak uses a different app id and stays enabled."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("FLATPAK_ID", "io.github.recol.dlss-updater")
    assert SelfUpdater.is_supported() is True


def test_not_supported_when_not_frozen(monkeypatch):
    """A source checkout is updated with git, not an installer."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert SelfUpdater.is_supported() is False


def test_not_supported_on_unknown_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert SelfUpdater.is_supported() is False


def test_applies_in_place_only_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert SelfUpdater().applies_in_place is True
    monkeypatch.setattr(sys, "platform", "linux")
    assert SelfUpdater().applies_in_place is False


# =============================================================================
# Digest handling
# =============================================================================


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("sha256:ABCDEF", "abcdef"),
        ("sha256:abcdef", "abcdef"),
        ("  sha256:AbCdEf  ", "abcdef"),
        ("abcdef", "abcdef"),          # bare hex, no algorithm prefix
        ("", ""),
    ],
)
def test_digest_hex(raw, expected):
    assert _digest_hex(raw) == expected


def _info(digest: str) -> UpdateInfo:
    return UpdateInfo(
        latest_version="4.5.2",
        download_url="https://example.invalid/x.msi",
        digest=digest,
        asset_name="x.msi",
        size=4,
    )


def test_verify_accepts_matching_digest(tmp_path):
    payload = b"data"
    target = tmp_path / "x.msi"
    target.write_bytes(payload)
    actual = hashlib.sha256(payload).hexdigest()

    # Does not raise.
    SelfUpdater()._verify(target, actual, _info(f"sha256:{actual}"))


def test_verify_rejects_mismatched_digest(tmp_path):
    target = tmp_path / "x.msi"
    target.write_bytes(b"data")

    with pytest.raises(SelfUpdateError, match="Digest mismatch"):
        SelfUpdater()._verify(target, "0" * 64, _info("sha256:" + "f" * 64))


def test_verify_tolerates_missing_digest(tmp_path):
    """A release predating the API's per-asset digest must still be installable."""
    target = tmp_path / "x.msi"
    target.write_bytes(b"data")

    SelfUpdater()._verify(target, "0" * 64, _info(""))


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    """Minimal stand-in for an aiohttp response used as an async context manager."""

    def __init__(self, chunks, status=200):
        self.status = status
        self.content = _FakeContent(chunks)
        self.headers = {"Content-Length": str(sum(len(c) for c in chunks))}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, url, timeout=None):
        return self._response


def _patch_download(monkeypatch, updater, tmp_path, chunks, status=200):
    """Point download() at a fake session and a temp target directory."""

    async def fake_prepare(info):
        return tmp_path

    monkeypatch.setattr(updater, "_prepare_target_dir", fake_prepare)

    async def fake_get_session():
        return _FakeSession(_FakeResponse(chunks, status=status))

    import dlss_updater.dll_repository as dll_repository

    monkeypatch.setattr(dll_repository, "get_http_session", fake_get_session)


@pytest.mark.anyio
async def test_download_verifies_and_reports_progress(tmp_path, monkeypatch):
    """A good download lands on disk and drives the progress callback."""
    payload = b"x" * (600 * 1024)          # spans multiple progress intervals
    chunks = [payload[i:i + 65536] for i in range(0, len(payload), 65536)]
    digest = hashlib.sha256(payload).hexdigest()

    updater = SelfUpdater()
    _patch_download(monkeypatch, updater, tmp_path, chunks)

    seen = []

    async def on_progress(progress):
        seen.append(progress)

    info = UpdateInfo(
        latest_version="4.5.2",
        download_url="https://example.invalid/x.msi",
        digest=f"sha256:{digest}",
        asset_name="x.msi",
        size=len(payload),
    )
    result = await updater.download(info, on_progress)

    assert result == tmp_path / "x.msi"
    assert result.read_bytes() == payload
    stages = [p.stage for p in seen]
    assert SelfUpdateStage.DOWNLOADING in stages
    assert stages[-1] is SelfUpdateStage.READY
    # Throttled: far fewer callbacks than the 10 chunks delivered.
    assert len([s for s in stages if s is SelfUpdateStage.DOWNLOADING]) <= 3


@pytest.mark.anyio
async def test_download_deletes_partial_file_on_digest_mismatch(tmp_path, monkeypatch):
    """A corrupt download must not be left behind looking installable."""
    payload = b"tampered payload"
    updater = SelfUpdater()
    _patch_download(monkeypatch, updater, tmp_path, [payload])

    info = UpdateInfo(
        latest_version="4.5.2",
        download_url="https://example.invalid/x.msi",
        digest="sha256:" + "f" * 64,       # deliberately not the payload's hash
        asset_name="x.msi",
        size=len(payload),
    )

    with pytest.raises(SelfUpdateError, match="Digest mismatch"):
        await updater.download(info)

    assert not (tmp_path / "x.msi").exists()


@pytest.mark.anyio
async def test_download_raises_and_cleans_up_on_http_error(tmp_path, monkeypatch):
    updater = SelfUpdater()
    _patch_download(monkeypatch, updater, tmp_path, [b"nope"], status=404)

    info = UpdateInfo(
        latest_version="4.5.2",
        download_url="https://example.invalid/x.msi",
        digest="",
        asset_name="x.msi",
        size=4,
    )

    with pytest.raises(SelfUpdateError, match="HTTP 404"):
        await updater.download(info)

    assert not (tmp_path / "x.msi").exists()


# =============================================================================
# Progress reporting
# =============================================================================


def test_progress_defaults():
    progress = SelfUpdateProgress(stage=SelfUpdateStage.DOWNLOADING)
    assert progress.bytes_done == 0
    assert progress.fraction == 0.0
    assert progress.message == ""


def test_stage_is_a_string_enum():
    """StrEnum so stages can be logged and compared without conversion."""
    assert SelfUpdateStage.READY == "ready"
    assert str(SelfUpdateStage.FAILED) == "failed"


# =============================================================================
# Download location
# =============================================================================


def test_downloads_dir_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(tmp_path / "Downloads"))
    assert _downloads_dir() == tmp_path / "Downloads"


def test_downloads_dir_defaults_to_home(monkeypatch):
    monkeypatch.delenv("XDG_DOWNLOAD_DIR", raising=False)
    assert _downloads_dir() == Path.home() / "Downloads"


# =============================================================================
# Windows installer helper
# =============================================================================


@pytest.mark.anyio
async def test_helper_script_is_powershell_and_waits_without_polling(tmp_path, monkeypatch):
    """Regression: the original batch helper hung forever in a windowless process.

    ``tasklist | find`` and ``timeout`` all require a console; spawned without
    one, ``find.exe`` blocked indefinitely and the update silently never
    installed. The helper must wait on a handle (``Wait-Process``) instead.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    msi = tmp_path / "DLSS Updater-4.6.0.msi"       # space is deliberate
    msi.write_bytes(b"x")

    spawned = {}
    monkeypatch.setattr(
        SelfUpdater, "_popen_detached", staticmethod(lambda cmd: spawned.update(cmd=cmd))
    )

    await SelfUpdater()._spawn_windows_installer(msi, _info(""))

    script = tmp_path / "apply_update.ps1"
    assert script.exists(), "helper should be a .ps1, not a .cmd"
    body = script.read_text(encoding="utf-8")

    assert "Wait-Process" in body
    for banned in ("tasklist", "timeout /t", "find "):
        assert banned not in body, f"{banned!r} needs a console and hangs without one"

    # Paths containing spaces must survive as single quoted literals.
    assert f"'{msi}'" in body

    # Invoked through powershell with policy bypassed and no window.
    assert spawned["cmd"][0] == "powershell.exe"
    assert "-ExecutionPolicy" in spawned["cmd"] and "Bypass" in spawned["cmd"]
    assert str(script) in spawned["cmd"]


def test_detached_spawn_never_combines_no_window_with_detached_process(monkeypatch):
    """CREATE_NO_WINDOW and DETACHED_PROCESS are mutually exclusive.

    Passing both yields a process with no console at all - the exact condition
    that wedged the original helper.
    """
    from dlss_updater import self_update as su

    seen = {}

    def fake_popen(cmd, creationflags=0, **kwargs):
        seen["flags"] = creationflags
        return object()

    monkeypatch.setattr(su.subprocess, "Popen", fake_popen)
    su.SelfUpdater._popen_detached(["powershell.exe", "-File", "x.ps1"])

    assert seen["flags"] & su._CREATE_NO_WINDOW
    assert seen["flags"] & su._CREATE_BREAKAWAY_FROM_JOB
    assert not hasattr(su, "_DETACHED_PROCESS"), "DETACHED_PROCESS must not be reintroduced"


def test_detached_spawn_retries_without_breakaway_when_refused(monkeypatch):
    """CreateProcess refuses CREATE_BREAKAWAY_FROM_JOB inside a job that forbids it."""
    from dlss_updater import self_update as su

    attempts = []

    def fake_popen(cmd, creationflags=0, **kwargs):
        attempts.append(creationflags)
        if creationflags & su._CREATE_BREAKAWAY_FROM_JOB:
            raise OSError("access denied")
        return object()

    monkeypatch.setattr(su.subprocess, "Popen", fake_popen)
    su.SelfUpdater._popen_detached(["powershell.exe"])

    assert len(attempts) == 2, "should retry once without breakaway"
    assert not (attempts[1] & su._CREATE_BREAKAWAY_FROM_JOB)
    assert attempts[1] & su._CREATE_NO_WINDOW


@pytest.mark.anyio
async def test_helper_handles_downgrade_and_failure_exit_codes(tmp_path, monkeypatch):
    """1638 is benign; any other failure still relaunches the existing install."""
    monkeypatch.setattr(sys, "platform", "win32")
    msi = tmp_path / "x.msi"
    msi.write_bytes(b"x")
    monkeypatch.setattr(SelfUpdater, "_popen_detached", staticmethod(lambda cmd: None))

    await SelfUpdater()._spawn_windows_installer(msi, _info(""))
    body = (tmp_path / "apply_update.ps1").read_text(encoding="utf-8")

    assert "1638" in body                       # downgrade treated as success
    assert "0, 1641, 3010" in body              # reboot codes treated as success
    # The app is restarted on every path, so a failed update never leaves the
    # user with no application.
    assert body.count("Start-Process -FilePath $exe") == 1
    assert "if (Test-Path $exe)" in body


def test_install_command_quotes_the_path():
    """Bundle paths land in Downloads, which frequently contains spaces.

    Uses PurePosixPath so the assertion holds when the suite runs on Windows -
    this command is only ever produced for Linux users.
    """
    command = SelfUpdater.install_command(PurePosixPath("/home/u/My Downloads/x.flatpak"))
    assert command == 'flatpak install --user "/home/u/My Downloads/x.flatpak"'
