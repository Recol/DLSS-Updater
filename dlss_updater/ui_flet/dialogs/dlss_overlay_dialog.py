"""
DLSS Debug Overlay Dialog
Toggle NVIDIA DLSS debug overlay.
- Windows: via registry settings
- Linux: via DXVK-NVAPI environment variables (copy to clipboard)
"""

import logging
import flet as ft

from dlss_updater.registry_utils import get_dlss_overlay_state, set_dlss_overlay_state
from dlss_updater.platform_utils import FEATURES, IS_LINUX, IS_WINDOWS


class DLSSOverlayDialog:
    """
    Dialog for enabling/disabling the DLSS debug overlay.
    - Windows: Reads state directly from registry and applies changes immediately.
    - Linux: Shows environment variable to copy for Steam launch options.
    """

    # Linux DXVK-NVAPI environment variable for debug overlay
    LINUX_OVERLAY_ENV = "DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS=DLSSIndicator=1024"

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger
        self.overlay_switch: ft.Switch = None
        self.status_text: ft.Text = None
        self.loading_ring: ft.ProgressRing = None
        self.error_container: ft.Container = None
        self.dialog: ft.AlertDialog = None
        self.is_available = FEATURES.dlss_overlay
        # Linux-specific
        self.launch_options_field: ft.TextField = None

    async def _load_current_state(self):
        """Load current overlay state from registry"""
        self.loading_ring.visible = True
        self.overlay_switch.disabled = True
        self.page.update()

        is_enabled, error = await get_dlss_overlay_state()

        self.loading_ring.visible = False
        self.overlay_switch.disabled = False

        if error:
            self.logger.error(f"Failed to read DLSS overlay state: {error}")
            self._show_error(error)
            self.overlay_switch.value = False
        else:
            self.overlay_switch.value = is_enabled
            self._update_status_text(is_enabled)
            self._hide_error()

        self.page.update()

    def _update_status_text(self, is_enabled: bool):
        """Update the status text based on current state"""
        if is_enabled:
            self.status_text.value = "Overlay is ENABLED - shows DLSS indicator in games"
            self.status_text.color = "#4CAF50"  # Green
        else:
            self.status_text.value = "Overlay is DISABLED"
            self.status_text.color = ft.Colors.GREY

    def _show_error(self, message: str):
        """Display error message in dialog"""
        self.error_container.content.value = message
        self.error_container.visible = True

    def _hide_error(self):
        """Hide error message"""
        self.error_container.visible = False

    async def _on_toggle_changed(self, e):
        """Handle toggle switch change - apply immediately"""
        new_state = e.control.value
        self.logger.info(f"DLSS overlay toggle changed to: {new_state}")

        # Show loading state
        self.loading_ring.visible = True
        self.overlay_switch.disabled = True
        self._hide_error()
        self.page.update()

        # Apply change
        success, error = await set_dlss_overlay_state(new_state)

        self.loading_ring.visible = False
        self.overlay_switch.disabled = False

        if success:
            self._update_status_text(new_state)
            self._hide_error()

            # Show success snackbar
            action_text = "enabled" if new_state else "disabled"
            snackbar = ft.SnackBar(
                content=ft.Text(
                    f"DLSS debug overlay {action_text}. Restart games to see changes."
                ),
                bgcolor="#4CAF50" if new_state else "#2D6E88",
                duration=3000,
            )
            self.page.overlay.append(snackbar)
            snackbar.open = True
        else:
            # Revert switch state on failure
            self.overlay_switch.value = not new_state
            self._show_error(error)
            self.logger.error(f"Failed to set DLSS overlay state: {error}")

        self.page.update()

    async def _show_unavailable_dialog(self):
        """Show dialog explaining feature is Windows-only"""
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE, color="#2D6E88"),
                    ft.Text("Feature Not Available"),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(
                            "DLSS Debug Overlay is only available on Windows.",
                            size=14,
                        ),
                        ft.Container(height=8),
                        ft.Text(
                            "This feature requires direct access to the Windows "
                            "registry to enable NVIDIA's debug overlay indicator.",
                            size=12,
                            color=ft.Colors.GREY,
                        ),
                    ],
                    spacing=4,
                ),
                width=400,
            ),
            actions=[
                ft.FilledButton("OK", on_click=lambda e: self.page.close(dialog)),
            ],
        )
        self.page.open(dialog)

    async def _on_copy_clicked(self, e):
        """Copy launch options to clipboard (Linux)"""
        launch_opts = f"{self.LINUX_OVERLAY_ENV} %command%"
        self.page.set_clipboard(launch_opts)
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Launch options copied to clipboard!"),
            bgcolor="#4CAF50",
        )
        self.page.snack_bar.open = True
        self.page.update()
        self.logger.info("Linux DLSS overlay launch options copied to clipboard")

    async def _show_linux_dialog(self):
        """Show Linux-specific dialog with environment variable instructions"""
        launch_opts = f"{self.LINUX_OVERLAY_ENV} %command%"

        self.launch_options_field = ft.TextField(
            value=launch_opts,
            read_only=True,
            multiline=True,
            min_lines=1,
            max_lines=2,
            text_size=11,
            bgcolor="#2C2C2C",
        )

        copy_button = ft.FilledButton(
            "Copy to Clipboard",
            icon=ft.Icons.COPY,
            on_click=self._on_copy_clicked,
        )

        # Info container
        info_container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "On Linux, the DLSS debug overlay is enabled via DXVK-NVAPI "
                        "environment variables. Add the launch options below to Steam's "
                        "'Set Launch Options' for each game.",
                        size=12,
                        color=ft.Colors.GREY,
                    ),
                    ft.Container(height=4),
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=ft.Colors.AMBER),
                            ft.Text(
                                "Works with Proton/Wine games using DXVK-NVAPI.",
                                size=11,
                                color=ft.Colors.AMBER,
                                italic=True,
                            ),
                        ],
                        spacing=4,
                    ),
                ],
                spacing=4,
            ),
            bgcolor="#3C3C3C",
            padding=ft.padding.all(12),
            border_radius=4,
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.BUG_REPORT, color="#4CAF50"),
                    ft.Text("DLSS Debug Overlay"),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        info_container,
                        ft.Divider(),
                        ft.Text("Steam Launch Options:", weight=ft.FontWeight.BOLD, size=14),
                        self.launch_options_field,
                        ft.Row(
                            controls=[copy_button],
                            alignment=ft.MainAxisAlignment.END,
                        ),
                    ],
                    spacing=12,
                    tight=True,
                ),
                width=500,
            ),
            actions=[
                ft.FilledButton(
                    "Close", on_click=lambda e: self.page.close(self.dialog)
                ),
            ],
        )

        self.page.open(self.dialog)

    async def show(self):
        """Show the DLSS overlay settings dialog"""
        # Check if feature is available on this platform
        if not self.is_available:
            await self._show_unavailable_dialog()
            return

        # Linux: Show environment variable instructions
        if IS_LINUX:
            await self._show_linux_dialog()
            return

        # Create switch
        self.overlay_switch = ft.Switch(
            value=False,
            active_color="#4CAF50",  # Green when enabled
            on_change=self._on_toggle_changed,
        )

        # Status text
        self.status_text = ft.Text(
            "Loading...",
            size=12,
            color=ft.Colors.GREY,
        )

        # Loading indicator
        self.loading_ring = ft.ProgressRing(
            width=16,
            height=16,
            stroke_width=2,
            color="#2D6E88",
            visible=True,
        )

        # Error container
        self.error_container = ft.Container(
            content=ft.Text("", color=ft.Colors.RED, size=12),
            bgcolor="#4A1515",
            padding=ft.padding.all(8),
            border_radius=4,
            visible=False,
        )

        # Main toggle tile
        toggle_tile = ft.ListTile(
            leading=ft.Icon(ft.Icons.BUG_REPORT, color="#4CAF50"),
            title=ft.Text("DLSS Debug Overlay", weight=ft.FontWeight.BOLD),
            subtitle=ft.Text("Shows DLSS/DLAA indicator in-game"),
            trailing=ft.Row(
                controls=[self.loading_ring, self.overlay_switch],
                spacing=8,
                tight=True,
            ),
        )

        # Info container
        info_text = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "When enabled, games using DLSS will display a debug indicator "
                        "showing which upscaling mode is active.",
                        size=12,
                        color=ft.Colors.GREY,
                    ),
                    ft.Container(height=4),
                    ft.Text(
                        "Note: Changes require game restart to take effect.",
                        size=11,
                        color=ft.Colors.AMBER,
                        italic=True,
                    ),
                ],
                spacing=4,
            ),
            bgcolor="#3C3C3C",
            padding=ft.padding.all(12),
            border_radius=4,
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS_DISPLAY, color="#2D6E88"),
                    ft.Text("DLSS Debug Settings"),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        info_text,
                        ft.Divider(),
                        toggle_tile,
                        self.status_text,
                        self.error_container,
                    ],
                    spacing=12,
                    tight=True,
                ),
                width=500,
            ),
            actions=[
                ft.FilledButton(
                    "Close", on_click=lambda e: self.page.close(self.dialog)
                ),
            ],
        )

        self.page.open(self.dialog)

        # Load current state after dialog is visible
        await self._load_current_state()
