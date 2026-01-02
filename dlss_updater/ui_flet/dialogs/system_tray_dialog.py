"""
System Tray Settings Dialog
Configure minimize-to-tray behavior and notification preferences

Features:
- Toggle minimize-to-tray
- Toggle close-to-tray (minimize on close instead of exit)
- Notification preferences
"""

import logging
import flet as ft

from dlss_updater.config import config_manager
from dlss_updater.ui_flet.theme.colors import MD3Colors


class SystemTrayDialog:
    """Dialog for configuring system tray and notification settings"""

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger

        # Load current preferences
        self.minimize_to_tray = config_manager.get_minimize_to_tray()
        self.close_to_tray = config_manager.get_close_to_tray()
        self.show_notifications = config_manager.get_show_tray_notifications()

    async def show(self):
        """Show system tray settings dialog"""

        # Minimize to tray switch
        minimize_switch = ft.Switch(
            value=self.minimize_to_tray,
            active_color=MD3Colors.PRIMARY,
        )
        minimize_tile = ft.ListTile(
            leading=ft.Icon(ft.Icons.MINIMIZE, color=MD3Colors.PRIMARY, size=24),
            title=ft.Text("Minimize to System Tray", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Keep application running in background when minimized", size=12),
            trailing=minimize_switch,
        )

        # Close to tray switch (only enabled if minimize to tray is on)
        close_switch = ft.Switch(
            value=self.close_to_tray,
            active_color=MD3Colors.PRIMARY,
            disabled=not self.minimize_to_tray,
        )
        close_tile = ft.ListTile(
            leading=ft.Icon(ft.Icons.CLOSE, color=MD3Colors.SECONDARY, size=24),
            title=ft.Text("Close to Tray", weight=ft.FontWeight.W_500),
            subtitle=ft.Text("Minimize to tray instead of exiting when clicking X", size=12),
            trailing=close_switch,
        )

        # Update close switch state when minimize switch changes
        def on_minimize_switch_change(e):
            close_switch.disabled = not e.control.value
            if not e.control.value:
                close_switch.value = False
            close_switch.update()

        minimize_switch.on_change = on_minimize_switch_change

        # Notification switch
        notify_switch = ft.Switch(
            value=self.show_notifications,
            active_color=MD3Colors.PRIMARY,
        )
        notify_tile = ft.ListTile(
            leading=ft.Icon(ft.Icons.NOTIFICATIONS, color=MD3Colors.INFO, size=24),
            title=ft.Text("Tray Notifications", weight=ft.FontWeight.W_500),
            subtitle=ft.Text("Show balloon notifications from system tray", size=12),
            trailing=notify_switch,
        )

        # Info section about tray requirements
        info_section = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE, color=MD3Colors.INFO, size=20),
                    ft.Text(
                        "System tray requires the application to be running with administrator privileges.",
                        size=12,
                        color=MD3Colors.ON_SURFACE_VARIANT,
                        expand=True,
                    ),
                ],
                spacing=8,
            ),
            padding=ft.padding.all(12),
            bgcolor=f"{MD3Colors.INFO}15",
            border_radius=8,
        )

        # Save handler
        async def save_clicked(e):
            # Save preferences
            config_manager.set_minimize_to_tray(minimize_switch.value)
            config_manager.set_close_to_tray(close_switch.value)
            config_manager.set_show_tray_notifications(notify_switch.value)

            self.logger.info(f"System tray settings saved: minimize={minimize_switch.value}, close_to_tray={close_switch.value}")
            self.page.close(dialog)

            # Show success snackbar
            snackbar = ft.SnackBar(
                content=ft.Text("System tray settings saved. Restart app to apply changes."),
                bgcolor=MD3Colors.PRIMARY,
            )
            self.page.overlay.append(snackbar)
            snackbar.open = True
            self.page.update()

        # Dialog
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS_APPLICATIONS, color=MD3Colors.PRIMARY, size=24),
                    ft.Text("System Tray Settings"),
                ],
                spacing=12,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        minimize_tile,
                        close_tile,
                        ft.Divider(height=16),
                        notify_tile,
                        ft.Container(height=8),
                        info_section,
                    ],
                    spacing=8,
                    tight=True,
                ),
                width=450,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("Save", on_click=save_clicked),
            ],
        )

        self.page.open(dialog)
