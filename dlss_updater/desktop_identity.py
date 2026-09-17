"""
Tell the desktop shell which app owns the Flet client window.

The GUI window belongs to the Flet client process (flet.exe / flet), not to
DLSS Updater, so by default the shell attributes it to that binary. On Windows
a taskbar pin then relaunches bare flet.exe, which shows a white screen (issue
#184); on Linux the window reports itself as "flet" and matches no desktop
entry, so it gets a generic icon and name.

flet_desktop 1.0 fixes both from environment variables it reads while
launching the client:

- Windows: FLET_APP_USER_MODEL_ID and FLET_APP_RELAUNCH_* are stamped onto
  the client window as System.AppUserModel.* properties
  (flet_desktop/win_taskbar.py) - while it is still hidden, before the first
  frame, so a pin can never catch it unstamped.
- Linux: FLET_APP_ID becomes the client's argv[0], from which GTK derives the
  X11 WM_CLASS and the Wayland app_id.

The variables must be in place before ft.run() starts the client, so
configure_desktop_identity() is called from main.py's __main__ block.
"""

import os
import sys

# Must match the System.AppUserModel.ID that build_msi.ps1 (Step 4b) injects
# into the MSI's Start Menu shortcut, or pins and the shortcut group apart.
AUMID = "io.github.recol.DLSSUpdater"
DISPLAY_NAME = "DLSS Updater"


def configure_desktop_identity() -> None:
    """Export the window identity for this platform. Never raises."""
    try:
        if sys.platform == "win32":
            _configure_windows()
        elif sys.platform == "linux":
            _configure_linux()
    except Exception:
        # Identity is cosmetic - a wrong taskbar icon beats failing to start.
        pass


def _configure_windows() -> None:
    # Our own process too (jump list, startup-failure dialog), so everything
    # DLSS Updater shows shares the client window's taskbar identity.
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:
        pass

    # From source the relaunch target would be python.exe, which is pointless
    # to pin. flet_desktop does nothing without a relaunch command, so leave
    # every variable unset there.
    if not getattr(sys, "frozen", False):
        return

    exe = os.path.abspath(sys.executable)
    # Assigned outright rather than setdefault: an inherited value (e.g. from
    # another Flet app that launched us) must not break the match with the
    # MSI shortcut.
    os.environ["FLET_APP_USER_MODEL_ID"] = AUMID
    os.environ["FLET_APP_RELAUNCH_COMMAND"] = f'"{exe}"'
    os.environ["FLET_APP_RELAUNCH_DISPLAY_NAME"] = DISPLAY_NAME
    os.environ["FLET_APP_RELAUNCH_ICON"] = f"{exe},0"


def _configure_linux() -> None:
    # Flatpak exports the app id as FLATPAK_ID, which is also the desktop
    # entry's name - so this covers the self-hosted (dlss-updater) and Flathub
    # (dlss_updater) ids alike. Outside a Flatpak there is no desktop entry to
    # match, so the client keeps its default identity.
    flatpak_id = os.environ.get("FLATPAK_ID", "").strip()
    # flet_desktop treats a blank FLET_APP_ID as unset, so do the same.
    if flatpak_id and not os.environ.get("FLET_APP_ID", "").strip():
        os.environ["FLET_APP_ID"] = flatpak_id
