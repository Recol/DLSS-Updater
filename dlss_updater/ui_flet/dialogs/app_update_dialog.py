"""
App Update Checker Dialog
Checks for application updates from GitHub
"""

import logging
import webbrowser
import asyncio
import flet as ft

from dlss_updater.auto_updater import check_for_updates
from dlss_updater.version import __version__


class AppUpdateDialog:
    """
    Dialog for checking and displaying application updates
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger

    async def check_and_show(self):
        """Check for updates and show appropriate dialog"""

        # Show loading
        checking_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Checking for Updates"),
            content=ft.Container(
                content=ft.Row(
                    controls=[
                        ft.ProgressRing(width=30, height=30),
                        ft.Text("Checking for updates...", size=14),
                    ],
                    spacing=16,
                ),
                padding=ft.padding.all(16),
            ),
        )
        self.page.open(checking_dialog)
        self.page.update()

        try:
            # Check for updates in background thread (it's a network call)
            # Returns tuple: (latest_version, is_update_available)
            latest_version, is_update_available = await asyncio.to_thread(check_for_updates)

            # Close checking dialog
            self.page.close(checking_dialog)

            if is_update_available:
                # Update available
                download_url = "https://github.com/Recol/DLSS-Updater/releases/latest"

                def open_download(e):
                    webbrowser.open(download_url)
                    self.page.close(update_dialog)

                update_dialog = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Update Available"),
                    content=ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Icon(ft.Icons.INFO, color="#2D6E88", size=32),
                                        ft.Column(
                                            controls=[
                                                ft.Text(
                                                    f"Current Version: {__version__}",
                                                    size=14,
                                                    color=ft.Colors.GREY,
                                                ),
                                                ft.Text(
                                                    f"Latest Version: {latest_version}",
                                                    size=16,
                                                    weight=ft.FontWeight.BOLD,
                                                    color="#2D6E88",
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
                                ),
                            ],
                        ),
                        width=400,
                    ),
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
                    title=ft.Text("No Updates Available"),
                    content=ft.Text(
                        f"You are running the latest version ({__version__})",
                        size=14,
                    ),
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
                title=ft.Text("Update Check Failed"),
                content=ft.Text(
                    "Could not check for updates. Please check your internet connection.",
                    size=14,
                ),
                actions=[
                    ft.FilledButton("OK", on_click=lambda e: self.page.close(error_dialog)),
                ],
            )
            self.page.open(error_dialog)
