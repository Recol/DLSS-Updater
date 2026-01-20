"""
App Update Checker Dialog
Checks for application updates from GitHub
Theme-aware: responds to light/dark mode changes
"""

import logging
import subprocess
import webbrowser
import flet as ft

from dlss_updater.auto_updater import check_for_updates_async, get_platform_name
from dlss_updater.version import __version__
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors


def _open_url(url: str) -> bool:
    """Open a URL in the default browser (cross-platform)."""
    import sys
    import os
    from pathlib import Path

    # On Linux (including WSL2), try multiple methods
    if sys.platform == 'linux':
        # Check if running in WSL
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
            subprocess.Popen(['xdg-open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


class AppUpdateDialog(ThemeAwareMixin):
    """
    Dialog for checking and displaying application updates.
    Theme-aware: responds to light/dark mode changes.
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger

        # Theme registry setup
        self._registry = get_theme_registry()
        self._theme_priority = 70  # Dialogs are low priority (animate last)

        # Themed element references
        self._themed_elements: dict[str, ft.Control] = {}

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware updates."""
        return {}  # Dialog rebuilds on show, individual elements handle themes

    async def check_and_show(self):
        """Check for updates and show appropriate dialog"""
        is_dark = self._registry.is_dark

        # Show loading
        checking_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Checking for Updates", color=MD3Colors.get_text_primary(is_dark)),
            content=ft.Container(
                content=ft.Row(
                    controls=[
                        ft.ProgressRing(width=30, height=30, color=MD3Colors.get_primary(is_dark)),
                        ft.Text("Checking for updates...", size=14, color=MD3Colors.get_text_primary(is_dark)),
                    ],
                    spacing=16,
                ),
                padding=ft.padding.all(16),
            ),
            bgcolor=MD3Colors.get_surface(is_dark),
        )
        self.page.open(checking_dialog)
        self.page.update()

        try:
            # Check for updates asynchronously (non-blocking)
            # Returns tuple: (latest_version, is_update_available, download_url)
            latest_version, is_update_available, download_url = await check_for_updates_async()

            # Close checking dialog
            self.page.close(checking_dialog)

            # Fallback URL if none returned
            if not download_url:
                download_url = "https://github.com/Recol/DLSS-Updater/releases/latest"

            platform_name = get_platform_name()

            if is_update_available:
                # Update available
                def open_download(e):
                    _open_url(download_url)
                    self.page.close(update_dialog)

                update_dialog = ft.AlertDialog(
                    modal=True,
                    title=ft.Text(f"Update Available ({platform_name})", color=MD3Colors.get_text_primary(is_dark)),
                    content=ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Icon(ft.Icons.INFO, color=MD3Colors.get_primary(is_dark), size=32),
                                        ft.Column(
                                            controls=[
                                                ft.Text(
                                                    f"Current Version: {__version__}",
                                                    size=14,
                                                    color=MD3Colors.get_text_secondary(is_dark),
                                                ),
                                                ft.Text(
                                                    f"Latest Version: {latest_version}",
                                                    size=16,
                                                    weight=ft.FontWeight.BOLD,
                                                    color=MD3Colors.get_primary(is_dark),
                                                ),
                                            ],
                                            spacing=4,
                                        ),
                                    ],
                                    spacing=12,
                                ),
                                ft.Container(height=12),
                                ft.Text(
                                    "A new version is available!",
                                    size=14,
                                    color=MD3Colors.get_text_primary(is_dark),
                                ),
                            ],
                        ),
                        width=400,
                    ),
                    bgcolor=MD3Colors.get_surface(is_dark),
                    actions=[
                        ft.TextButton("Later", on_click=lambda e: self.page.close(update_dialog)),
                        ft.FilledButton("Download", on_click=open_download),
                    ],
                )
                self.page.open(update_dialog)

            else:
                # No update available
                no_update_dialog = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("No Updates Available", color=MD3Colors.get_text_primary(is_dark)),
                    content=ft.Text(
                        f"You are running the latest version ({__version__})",
                        size=14,
                        color=MD3Colors.get_text_primary(is_dark),
                    ),
                    bgcolor=MD3Colors.get_surface(is_dark),
                    actions=[
                        ft.FilledButton("OK", on_click=lambda e: self.page.close(no_update_dialog)),
                    ],
                )
                self.page.open(no_update_dialog)

        except Exception as e:
            self.logger.error(f"Update check failed: {e}", exc_info=True)

            # Close checking dialog
            self.page.close(checking_dialog)

            # Show error
            error_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Update Check Failed", color=MD3Colors.get_text_primary(is_dark)),
                content=ft.Text(
                    "Could not check for updates. Please check your internet connection.",
                    size=14,
                    color=MD3Colors.get_text_primary(is_dark),
                ),
                bgcolor=MD3Colors.get_surface(is_dark),
                actions=[
                    ft.FilledButton("OK", on_click=lambda e: self.page.close(error_dialog)),
                ],
            )
            self.page.open(error_dialog)
