"""
App Update Checker Dialog
Checks for application updates from GitHub
Theme-aware: responds to light/dark mode changes
"""

import logging
import flet as ft

from dlss_updater.auto_updater import check_for_updates_async, get_platform_name
from dlss_updater.linux_paths import is_flathub
from dlss_updater.version import __version__
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.url_opener import open_url_async


class AppUpdateDialog(ThemeAwareMixin):
    """
    Dialog for checking and displaying application updates.
    Theme-aware: responds to light/dark mode changes.
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self._page_ref = page
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

        # Flathub builds must not offer out-of-band downloads (store policy);
        # updates arrive through Flathub itself. The GitHub-bundle Flatpak
        # (different app ID) keeps the normal update check.
        if is_flathub():
            flathub_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Updates via Flathub", color=MD3Colors.get_text_primary(is_dark)),
                content=ft.Container(
                    content=ft.Text(
                        "This copy of DLSS Updater is managed by Flathub. "
                        "Updates are delivered through your software center "
                        "or with: flatpak update",
                        size=14,
                        color=MD3Colors.get_text_primary(is_dark),
                    ),
                    padding=ft.Padding.all(16),
                ),
                actions=[
                    ft.TextButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
                ],
                bgcolor=MD3Colors.get_surface(is_dark),
            )
            self._page_ref.show_dialog(flathub_dialog)
            return

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
                padding=ft.Padding.all(16),
            ),
            bgcolor=MD3Colors.get_surface(is_dark),
        )
        self._page_ref.show_dialog(checking_dialog)

        try:
            # Check for updates asynchronously (non-blocking)
            # Returns tuple: (latest_version, is_update_available, download_url)
            latest_version, is_update_available, download_url = await check_for_updates_async()

            # Close checking dialog
            self._page_ref.pop_dialog()

            # Fallback URL if none returned
            if not download_url:
                download_url = "https://github.com/Recol/DLSS-Updater/releases/latest"

            platform_name = get_platform_name()

            if is_update_available:
                # Update available
                async def open_download(e):
                    self._page_ref.pop_dialog()
                    await open_url_async(download_url)

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
                        ft.TextButton("Later", on_click=lambda e: self._page_ref.pop_dialog()),
                        ft.FilledButton("Download", on_click=open_download),
                    ],
                )
                self._page_ref.show_dialog(update_dialog)

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
                        ft.FilledButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
                    ],
                )
                self._page_ref.show_dialog(no_update_dialog)

        except Exception as e:
            self.logger.error(f"Update check failed: {e}", exc_info=True)

            # Close checking dialog
            self._page_ref.pop_dialog()

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
                    ft.FilledButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
                ],
            )
            self._page_ref.show_dialog(error_dialog)
