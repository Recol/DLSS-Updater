"""
Update Preferences Dialog
Configure which DLL technologies to update
"""

import logging
import flet as ft

from dlss_updater.config import config_manager


class UpdatePreferencesDialog:
    """
    Dialog for managing update preferences (DLSS, XeSS, FSR, etc.)
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger

        # Load current preferences
        self.prefs = {
            "dlss": config_manager.get_update_preference("DLSS"),
            "streamline": config_manager.get_update_preference("Streamline"),
            "directstorage": config_manager.get_update_preference("DirectStorage"),
            "xess": config_manager.get_update_preference("XeSS"),
            "fsr": config_manager.get_update_preference("FSR"),
        }
        self.backup_pref = config_manager.get_backup_preference()

        # Create switches with ListTile layout
        self.dlss_switch = ft.Switch(
            value=self.prefs["dlss"],
            active_color="#2D6E88",
        )
        self.dlss_tile = ft.ListTile(
            title=ft.Text("DLSS", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Deep Learning Super Sampling"),
            trailing=self.dlss_switch,
        )

        self.streamline_switch = ft.Switch(
            value=self.prefs["streamline"],
            active_color="#2D6E88",
        )
        self.streamline_tile = ft.ListTile(
            title=ft.Text("Streamline", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Reflex, DLSS Frame Gen, etc."),
            trailing=self.streamline_switch,
        )

        self.directstorage_switch = ft.Switch(
            value=self.prefs["directstorage"],
            active_color="#2D6E88",
        )
        self.directstorage_tile = ft.ListTile(
            title=ft.Text("DirectStorage", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Fast loading"),
            trailing=self.directstorage_switch,
        )

        self.xess_switch = ft.Switch(
            value=self.prefs["xess"],
            active_color="#2D6E88",
        )
        self.xess_tile = ft.ListTile(
            title=ft.Text("XeSS", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Intel Xe Super Sampling"),
            trailing=self.xess_switch,
        )

        self.fsr_switch = ft.Switch(
            value=self.prefs["fsr"],
            active_color="#2D6E88",
        )
        self.fsr_tile = ft.ListTile(
            title=ft.Text("FSR", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("AMD FidelityFX"),
            trailing=self.fsr_switch,
        )

        self.backup_switch = ft.Switch(
            value=self.backup_pref,
            active_color="#2D6E88",
        )
        self.backup_tile = ft.ListTile(
            title=ft.Text("Create backups before updating", weight=ft.FontWeight.BOLD),
            trailing=self.backup_switch,
        )

    async def show(self):
        """Show the preferences dialog"""

        async def save_clicked(e):
            # Validate at least one is selected
            if not any([
                self.dlss_switch.value,
                self.streamline_switch.value,
                self.directstorage_switch.value,
                self.xess_switch.value,
                self.fsr_switch.value,
            ]):
                # Show error
                error_dialog = ft.AlertDialog(
                    title=ft.Text("Invalid Configuration"),
                    content=ft.Text("At least one technology must be selected."),
                    actions=[
                        ft.FilledButton("OK", on_click=lambda e: self.page.close(error_dialog)),
                    ],
                )
                self.page.open(error_dialog)
                return

            # Save preferences
            config_manager.set_update_preference("DLSS", self.dlss_switch.value)
            config_manager.set_update_preference("Streamline", self.streamline_switch.value)
            config_manager.set_update_preference("DirectStorage", self.directstorage_switch.value)
            config_manager.set_update_preference("XeSS", self.xess_switch.value)
            config_manager.set_update_preference("FSR", self.fsr_switch.value)
            config_manager.set_backup_preference(self.backup_switch.value)

            self.logger.info("Update preferences saved")
            self.page.close(dialog)

            # Show success snackbar
            snackbar = ft.SnackBar(
                content=ft.Text("Preferences saved successfully"),
                bgcolor="#2D6E88",
            )
            self.page.overlay.append(snackbar)
            snackbar.open = True
            self.page.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Update Preferences"),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text("Technologies to Update:", weight=ft.FontWeight.BOLD),
                        self.dlss_tile,
                        self.streamline_tile,
                        self.directstorage_tile,
                        self.xess_tile,
                        self.fsr_tile,
                        ft.Divider(),
                        ft.Text("Backup Options:", weight=ft.FontWeight.BOLD, size=14),
                        self.backup_tile,
                    ],
                    spacing=12,
                    tight=True,
                ),
                width=500,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("Save", on_click=save_clicked),
            ],
        )

        self.page.open(dialog)
