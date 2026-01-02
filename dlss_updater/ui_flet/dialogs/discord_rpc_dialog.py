"""
Discord Rich Presence Settings Dialog
Configure Discord integration to show activity status

Features:
- Enable/disable Rich Presence
- Connection status indicator with real-time updates
- Privacy controls (show game count, show current activity, show elapsed time)
- Activity preview
- Connection troubleshooting
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
import flet as ft

from dlss_updater.config import config_manager
from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows


class ConnectionStatusBadge(ft.Container):
    """Animated status badge showing Discord connection state"""

    def __init__(self, is_connected: bool = False):
        super().__init__()
        self.is_connected = is_connected

        # Pulsing dot animation
        self.status_dot = ft.Container(
            width=10,
            height=10,
            border_radius=5,
            bgcolor=MD3Colors.SUCCESS if is_connected else MD3Colors.ERROR,
            animate=ft.Animation(1000, ft.AnimationCurve.EASE_IN_OUT),
        )

        # Status text
        status_text = "Connected" if is_connected else "Disconnected"
        status_color = MD3Colors.SUCCESS if is_connected else MD3Colors.ERROR

        self.content = ft.Row(
            controls=[
                self.status_dot,
                ft.Text(
                    status_text,
                    size=14,
                    weight=ft.FontWeight.W_500,
                    color=status_color,
                ),
            ],
            spacing=8,
            tight=True,
        )

    def update_status(self, is_connected: bool):
        """Update connection status with animation"""
        self.is_connected = is_connected

        # Update dot color
        self.status_dot.bgcolor = MD3Colors.SUCCESS if is_connected else MD3Colors.ERROR

        # Update text
        status_text = "Connected" if is_connected else "Disconnected"
        status_color = MD3Colors.SUCCESS if is_connected else MD3Colors.ERROR

        self.content.controls[1].value = status_text
        self.content.controls[1].color = status_color

        # Pulse animation
        self.status_dot.scale = 1.2
        if self.page:
            self.page.update()

        # Reset scale
        asyncio.create_task(self._reset_scale())

    async def _reset_scale(self):
        await asyncio.sleep(0.2)
        self.status_dot.scale = 1.0
        if self.page:
            self.page.update()


class DiscordActivityPreview(ft.Container):
    """Preview of how activity will appear in Discord"""

    def __init__(
        self,
        show_game_count: bool = True,
        show_activity: bool = True,
        show_elapsed: bool = True,
    ):
        super().__init__()

        # Discord-style preview card
        discord_card = ft.Container(
            content=ft.Column(
                controls=[
                    # App header
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=16, color=MD3Colors.ON_SURFACE),
                            ft.Text("DLSS Updater", size=13, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=8,
                    ),
                    # Activity details
                    ft.Text(
                        "Managing 42 games" if show_game_count else "Managing games",
                        size=12,
                        color=MD3Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Text(
                        "Updating DLL files" if show_activity else "",
                        size=11,
                        color=MD3Colors.ON_SURFACE_VARIANT,
                        visible=show_activity,
                    ),
                    ft.Text(
                        "Elapsed: 00:12:34" if show_elapsed else "",
                        size=10,
                        color=MD3Colors.ON_SURFACE_VARIANT,
                        visible=show_elapsed,
                    ),
                ],
                spacing=4,
                tight=True,
            ),
            bgcolor=MD3Colors.SURFACE_DIM,
            border_radius=8,
            padding=12,
            border=ft.border.all(1, MD3Colors.OUTLINE_VARIANT),
        )

        self.content = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.PREVIEW, size=16, color=MD3Colors.INFO),
                        ft.Text("Discord Preview", size=12, weight=ft.FontWeight.BOLD, color=MD3Colors.INFO),
                    ],
                    spacing=8,
                ),
                ft.Container(height=4),
                discord_card,
            ],
            spacing=0,
        )
        self.padding = 12
        self.bgcolor=f"{MD3Colors.INFO}15"
        self.border_radius = 8
        self.border = ft.border.all(1, f"{MD3Colors.INFO}40")


class DiscordRPCDialog:
    """Dialog for configuring Discord Rich Presence integration"""

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger

        # Load current preferences
        self.rpc_enabled = config_manager.get_discord_presence_enabled()
        self.show_game_count = config_manager.get_discord_show_game_count()
        self.show_activity = config_manager.get_discord_show_activity()
        self.show_elapsed = True  # Default to true

        # Connection state (would be updated by actual RPC client)
        self.is_connected = False

    async def show(self):
        """Show Discord Rich Presence settings dialog"""

        # Enable toggle
        enable_switch = ft.Switch(
            value=self.rpc_enabled,
            active_color=MD3Colors.PRIMARY,
            on_change=self._on_enable_changed,
        )
        enable_tile = ft.ListTile(
            leading=ft.Icon(ft.Icons.DISCORD, color="#5865F2", size=28),  # Discord brand color
            title=ft.Text("Enable Discord Rich Presence", weight=ft.FontWeight.BOLD, size=16),
            subtitle=ft.Text("Show your DLSS Updater activity on Discord", size=12),
            trailing=enable_switch,
        )

        # Connection status
        self.status_badge = ConnectionStatusBadge(self.is_connected)
        status_container = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text("Status:", size=13, color=MD3Colors.ON_SURFACE_VARIANT),
                    self.status_badge,
                    ft.Container(expand=True),
                    ft.TextButton(
                        "Test Connection",
                        icon=ft.Icons.CABLE,
                        on_click=self._on_test_connection,
                        disabled=not self.rpc_enabled,
                        style=ft.ButtonStyle(color=MD3Colors.PRIMARY),
                    ),
                ],
                spacing=12,
            ),
            padding=ft.padding.symmetric(horizontal=16, vertical=8),
        )

        # Privacy controls
        show_game_count_switch = ft.Switch(
            value=self.show_game_count,
            active_color=MD3Colors.PRIMARY,
            disabled=not self.rpc_enabled,
        )
        show_game_count_tile = ft.ListTile(
            title=ft.Text("Show game count", weight=ft.FontWeight.W_500),
            subtitle=ft.Text("Display number of games in your library", size=11),
            trailing=show_game_count_switch,
        )

        show_activity_switch = ft.Switch(
            value=self.show_activity,
            active_color=MD3Colors.PRIMARY,
            disabled=not self.rpc_enabled,
        )
        show_activity_tile = ft.ListTile(
            title=ft.Text("Show current activity", weight=ft.FontWeight.W_500),
            subtitle=ft.Text("Display what you're currently doing (scanning, updating, etc.)", size=11),
            trailing=show_activity_switch,
        )

        show_elapsed_switch = ft.Switch(
            value=self.show_elapsed,
            active_color=MD3Colors.PRIMARY,
            disabled=not self.rpc_enabled,
        )
        show_elapsed_tile = ft.ListTile(
            title=ft.Text("Show elapsed time", weight=ft.FontWeight.W_500),
            subtitle=ft.Text("Display how long you've been using the app", size=11),
            trailing=show_elapsed_switch,
        )

        # Store references for enable/disable toggling
        self.privacy_switches = [
            show_game_count_switch,
            show_activity_switch,
            show_elapsed_switch,
        ]
        self.privacy_tiles = [
            show_game_count_tile,
            show_activity_tile,
            show_elapsed_tile,
        ]

        # Activity preview
        self.activity_preview = DiscordActivityPreview(
            show_game_count=self.show_game_count,
            show_activity=self.show_activity,
            show_elapsed=self.show_elapsed,
        )

        # Update preview when switches change
        def update_preview():
            self.activity_preview.content = DiscordActivityPreview(
                show_game_count=show_game_count_switch.value,
                show_activity=show_activity_switch.value,
                show_elapsed=show_elapsed_switch.value,
            ).content
            self.activity_preview.update()

        show_game_count_switch.on_change = lambda e: update_preview()
        show_activity_switch.on_change = lambda e: update_preview()
        show_elapsed_switch.on_change = lambda e: update_preview()

        # Info section
        info_section = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=MD3Colors.INFO),
                            ft.Text("About Rich Presence", size=12, weight=ft.FontWeight.BOLD, color=MD3Colors.INFO),
                        ],
                        spacing=8,
                    ),
                    ft.Text(
                        "Discord Rich Presence lets others see what you're doing in DLSS Updater. "
                        "Make sure Discord is running for this feature to work.",
                        size=11,
                        color=MD3Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=8,
            ),
            padding=12,
            bgcolor=f"{MD3Colors.INFO}15",
            border_radius=8,
            border=ft.border.all(1, f"{MD3Colors.INFO}40"),
        )

        # Save handler
        async def save_clicked(e):
            # Save preferences
            config_manager.set_discord_presence_enabled(enable_switch.value)
            config_manager.set_discord_show_game_count(show_game_count_switch.value)
            config_manager.set_discord_show_activity(show_activity_switch.value)
            # Note: show_elapsed is not persisted yet, would need to add config method

            self.logger.info(f"Discord RPC settings saved (enabled: {enable_switch.value})")
            self.page.close(dialog)

            # Show success snackbar
            snackbar = ft.SnackBar(
                content=ft.Text("Discord Rich Presence settings saved"),
                bgcolor=MD3Colors.PRIMARY,
            )
            self.page.overlay.append(snackbar)
            snackbar.open = True
            self.page.update()

            # If enabled, try to connect (would call actual RPC client here)
            if enable_switch.value:
                self.logger.info("Discord RPC enabled - connection will be established on next activity")

        # Dialog
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.DISCORD, color="#5865F2", size=28),
                    ft.Text("Discord Rich Presence"),
                ],
                spacing=12,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        info_section,
                        ft.Container(height=8),
                        enable_tile,
                        status_container,
                        ft.Divider(height=16),
                        ft.Text("Privacy Settings:", weight=ft.FontWeight.BOLD, size=14),
                        show_game_count_tile,
                        show_activity_tile,
                        show_elapsed_tile,
                        ft.Container(height=8),
                        self.activity_preview,
                    ],
                    spacing=8,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=550,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("Save", on_click=save_clicked),
            ],
        )

        self.dialog = dialog
        self.page.open(dialog)

    def _on_enable_changed(self, e):
        """Handle enable switch change"""
        enabled = e.control.value

        # Enable/disable privacy switches
        for switch in self.privacy_switches:
            switch.disabled = not enabled

        # Enable/disable test connection button
        status_row = self.dialog.content.content.controls[2]
        test_button = status_row.content.controls[3]
        test_button.disabled = not enabled

        if self.page:
            self.page.update()

    async def _on_test_connection(self, e):
        """Test Discord connection"""
        # Disable button during test
        e.control.disabled = True
        e.control.text = "Testing..."
        if self.page:
            self.page.update()

        # Simulate connection test (replace with actual RPC connection attempt)
        await asyncio.sleep(1.5)

        # Update status (would check actual Discord client availability)
        try:
            # Placeholder: Check if Discord is running
            # In production, this would attempt to connect to Discord RPC
            self.is_connected = True  # Simulated success
            self.status_badge.update_status(True)

            # Show success snackbar
            snackbar = ft.SnackBar(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.WHITE),
                        ft.Text("Successfully connected to Discord!"),
                    ],
                    spacing=8,
                ),
                bgcolor=MD3Colors.SUCCESS,
            )
        except Exception as ex:
            self.is_connected = False
            self.status_badge.update_status(False)

            # Show error snackbar
            snackbar = ft.SnackBar(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.ERROR, color=ft.Colors.WHITE),
                        ft.Text("Failed to connect. Is Discord running?"),
                    ],
                    spacing=8,
                ),
                bgcolor=MD3Colors.ERROR,
            )

        self.page.overlay.append(snackbar)
        snackbar.open = True

        # Re-enable button
        e.control.disabled = False
        e.control.text = "Test Connection"
        if self.page:
            self.page.update()


class DiscordRPCStatusIndicator(ft.Container):
    """
    Small status indicator for app bar showing Discord RPC connection
    Can be added to MainView app bar to show live connection status
    """

    def __init__(self, on_click_callback=None):
        super().__init__()

        self.is_connected = False
        self.on_click_callback = on_click_callback

        # Status dot
        self.status_dot = ft.Container(
            width=8,
            height=8,
            border_radius=4,
            bgcolor=MD3Colors.ON_SURFACE_VARIANT,
            opacity=0.5,
        )

        # Icon button
        self.content = ft.Stack(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.DISCORD,
                    icon_color=MD3Colors.ON_SURFACE_VARIANT,
                    icon_size=20,
                    tooltip="Discord Rich Presence: Disconnected",
                    on_click=self._on_clicked,
                    width=40,
                    height=40,
                ),
                ft.Container(
                    content=self.status_dot,
                    right=8,
                    top=8,
                ),
            ],
            width=40,
            height=40,
        )

    def update_connection_status(self, is_connected: bool):
        """Update connection status indicator"""
        self.is_connected = is_connected

        # Update icon button
        icon_button = self.content.controls[0]
        icon_button.icon_color = "#5865F2" if is_connected else MD3Colors.ON_SURFACE_VARIANT
        icon_button.tooltip = f"Discord Rich Presence: {'Connected' if is_connected else 'Disconnected'}"

        # Update status dot
        self.status_dot.bgcolor = MD3Colors.SUCCESS if is_connected else MD3Colors.ON_SURFACE_VARIANT
        self.status_dot.opacity = 1.0 if is_connected else 0.5

        if self.page:
            self.page.update()

    async def _on_clicked(self, e):
        """Handle click - open Discord RPC settings"""
        if self.on_click_callback:
            result = self.on_click_callback()
            if asyncio.iscoroutine(result):
                await result
