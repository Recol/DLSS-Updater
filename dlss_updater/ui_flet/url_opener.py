"""
Opening URLs without blocking the UI event loop.

``webbrowser.open()`` goes through ShellExecute on Windows and can block for
as long as the target application takes to accept the URL; calling it from a
Flet handler froze the UI for that time. ``open_url_async`` runs the same
cross-platform logic on a bounded I/O worker thread.

For a plain https link on a control that supports it, prefer the client-side
``action=ft.OpenUrl(url)`` (no Python round trip at all). This module is for
the cases that can't: menu callbacks, conditional opens, WSL/Flatpak paths.
"""

import os
import subprocess
import sys
import webbrowser
from pathlib import Path

import anyio

from dlss_updater.concurrency_limiters import thread_io


def open_url(url: str) -> bool:
    """
    Open a URL in the default browser (cross-platform).

    Args:
        url: The URL to open

    Returns:
        True if successful, False otherwise
    """
    # On Linux (including WSL2), try multiple methods
    if sys.platform == 'linux':
        # Check if running in WSL by looking for Windows interop
        is_wsl = 'microsoft' in os.uname().release.lower() or Path('/mnt/c/Windows').exists()

        if is_wsl:
            # In WSL2, use cmd.exe to open URL in Windows browser
            try:
                subprocess.Popen(
                    ['cmd.exe', '/c', 'start', '', url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return True
            except Exception:
                pass

        # Try xdg-open for native Linux
        try:
            subprocess.Popen(
                ['xdg-open', url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            pass

    # On Windows/other platforms, use webbrowser
    try:
        webbrowser.open(url)
        return True
    except Exception:
        pass

    return False


async def open_url_async(url: str) -> bool:
    """Open ``url`` on a worker thread; see ``open_url``."""
    return await anyio.to_thread.run_sync(open_url, url, limiter=thread_io)
