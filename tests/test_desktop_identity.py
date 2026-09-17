"""Tests for the Flet client window identity (dlss_updater.desktop_identity).

flet_desktop 1.0 stamps the client window's taskbar identity from environment
variables it reads while spawning the client, so what matters is exactly which
variables end up in os.environ per platform. The platform, frozen state and
executable are faked, and the Win32 call is stubbed, so every case runs
anywhere.
"""

import ctypes
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from dlss_updater import desktop_identity
from dlss_updater.desktop_identity import AUMID, configure_desktop_identity

WINDOWS_VARS = (
    "FLET_APP_USER_MODEL_ID",
    "FLET_APP_RELAUNCH_COMMAND",
    "FLET_APP_RELAUNCH_DISPLAY_NAME",
    "FLET_APP_RELAUNCH_ICON",
)


@pytest.fixture(autouse=True)
def isolated_env():
    """Start every test without identity variables and undo whatever it sets.

    patch.dict restores os.environ wholesale, which also removes keys the
    code under test adds directly - monkeypatch only undoes its own writes.
    """
    with mock.patch.dict(os.environ):
        for name in (*WINDOWS_VARS, "FLET_APP_ID", "FLATPAK_ID"):
            os.environ.pop(name, None)
        yield


class _FakeShell32:
    def __init__(self, error: Exception | None = None):
        self.calls: list[str] = []
        self._error = error

    def SetCurrentProcessExplicitAppUserModelID(self, aumid):
        self.calls.append(aumid)
        if self._error is not None:
            raise self._error
        return 0


def _stub_shell32(monkeypatch, shell32: _FakeShell32) -> _FakeShell32:
    # raising=False: ctypes has no windll attribute off Windows.
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=shell32), raising=False)
    return shell32


@pytest.fixture
def shell32(monkeypatch):
    return _stub_shell32(monkeypatch, _FakeShell32())


@pytest.fixture
def frozen_exe(monkeypatch, tmp_path):
    """A frozen build installed under a path with a space, like Program Files."""
    exe = tmp_path / "DLSS Updater" / "DLSS_Updater.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return os.path.abspath(exe)


# =============================================================================
# Windows
# =============================================================================


def test_windows_frozen_sets_the_relaunch_identity(monkeypatch, shell32, frozen_exe):
    monkeypatch.setattr(sys, "platform", "win32")

    configure_desktop_identity()

    assert os.environ["FLET_APP_USER_MODEL_ID"] == "io.github.recol.DLSSUpdater"
    # Quoted, so a pin still relaunches from an install path with spaces.
    assert os.environ["FLET_APP_RELAUNCH_COMMAND"] == f'"{frozen_exe}"'
    assert os.environ["FLET_APP_RELAUNCH_DISPLAY_NAME"] == "DLSS Updater"
    assert os.environ["FLET_APP_RELAUNCH_ICON"] == f"{frozen_exe},0"
    assert shell32.calls == [AUMID]


def test_windows_frozen_overrides_an_inherited_identity(monkeypatch, shell32, frozen_exe):
    """The AUMID must always match the MSI shortcut's, whatever launched us."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("FLET_APP_USER_MODEL_ID", "com.example.OtherFletApp")
    monkeypatch.setenv("FLET_APP_RELAUNCH_COMMAND", '"C:\\Other\\other.exe"')

    configure_desktop_identity()

    assert os.environ["FLET_APP_USER_MODEL_ID"] == AUMID
    assert os.environ["FLET_APP_RELAUNCH_COMMAND"] == f'"{frozen_exe}"'


def test_windows_from_source_sets_only_the_process_aumid(monkeypatch, shell32):
    """Pinning python.exe is pointless, so flet_desktop gets nothing to stamp."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(sys, "frozen", raising=False)

    configure_desktop_identity()

    assert shell32.calls == [AUMID]
    for name in WINDOWS_VARS:
        assert name not in os.environ


def test_windows_process_aumid_failure_is_not_fatal(monkeypatch, frozen_exe):
    monkeypatch.setattr(sys, "platform", "win32")
    failing = _stub_shell32(monkeypatch, _FakeShell32(error=OSError("no shell32")))

    configure_desktop_identity()

    assert failing.calls == [AUMID]
    assert os.environ["FLET_APP_USER_MODEL_ID"] == AUMID


def test_windows_ignores_flatpak_id(monkeypatch, shell32, frozen_exe):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("FLATPAK_ID", "io.github.recol.dlss-updater")

    configure_desktop_identity()

    assert "FLET_APP_ID" not in os.environ


# =============================================================================
# Linux
# =============================================================================


@pytest.mark.parametrize(
    "flatpak_id",
    [
        "io.github.recol.dlss-updater",  # self-hosted channel
        "io.github.recol.dlss_updater",  # Flathub
    ],
)
def test_linux_flatpak_uses_the_app_id(monkeypatch, shell32, flatpak_id):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("FLATPAK_ID", flatpak_id)

    configure_desktop_identity()

    assert os.environ["FLET_APP_ID"] == flatpak_id
    assert shell32.calls == []
    for name in WINDOWS_VARS:
        assert name not in os.environ


def test_linux_outside_flatpak_keeps_the_default_identity(monkeypatch, shell32):
    monkeypatch.setattr(sys, "platform", "linux")

    configure_desktop_identity()

    assert "FLET_APP_ID" not in os.environ
    assert shell32.calls == []


def test_linux_existing_flet_app_id_is_not_overridden(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("FLATPAK_ID", "io.github.recol.dlss-updater")
    monkeypatch.setenv("FLET_APP_ID", "custom.app.id")

    configure_desktop_identity()

    assert os.environ["FLET_APP_ID"] == "custom.app.id"


def test_linux_blank_flet_app_id_counts_as_unset(monkeypatch):
    """flet_desktop strips FLET_APP_ID and ignores it when blank."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("FLATPAK_ID", "io.github.recol.dlss-updater")
    monkeypatch.setenv("FLET_APP_ID", "  ")

    configure_desktop_identity()

    assert os.environ["FLET_APP_ID"] == "io.github.recol.dlss-updater"


# =============================================================================
# Robustness
# =============================================================================


def test_never_raises(monkeypatch):
    """Identity is cosmetic - it must never stop the app from starting."""
    monkeypatch.setattr(sys, "platform", "linux")

    def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(desktop_identity, "_configure_linux", boom)

    configure_desktop_identity()


def test_other_platforms_are_left_alone(monkeypatch, shell32):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("FLATPAK_ID", "io.github.recol.dlss-updater")

    configure_desktop_identity()

    assert shell32.calls == []
    assert "FLET_APP_ID" not in os.environ
    for name in WINDOWS_VARS:
        assert name not in os.environ
