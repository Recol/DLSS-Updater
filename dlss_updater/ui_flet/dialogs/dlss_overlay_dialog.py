"""
DLSS Debug Overlay Dialog
Toggle NVIDIA DLSS debug overlay.
- Windows: via registry settings
- Linux: via DXVK-NVAPI environment variables (copy to clipboard)
- Theme-aware: responds to light/dark mode changes
"""

import logging
import flet as ft

from dlss_updater.registry_utils import get_dlss_overlay_state, set_dlss_overlay_state
from dlss_updater.platform_utils import FEATURES, IS_LINUX, IS_WINDOWS
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors


class DLSSOverlayDialog(ThemeAwareMixin):
    """
    Dialog for enabling/disabling the DLSS debug overlay.
    - Windows: Reads state directly from registry and applies changes immediately.
    - Linux: Shows environment variable to copy for Steam launch options.
    - Theme-aware: responds to light/dark mode changes.
    """

    # Linux DXVK-NVAPI environment variable for debug overlay
    LINUX_OVERLAY_ENV = "DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS=DLSSIndicator=1024"

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self._page_ref = page
        self.logger = logger

        # Theme registry setup
        self._registry = get_theme_registry()
        self._theme_priority = 70  # Dialogs are low priority (animate last)

        self.overlay_switch: ft.Switch = None
        self.status_text: ft.Text = None
        self.loading_ring: ft.ProgressRing = None
        self.error_container: ft.Container = None
        self.dialog: ft.AlertDialog = None
        self.is_available = FEATURES.dlss_overlay
        # Linux-specific
        self.launch_options_field: ft.TextField = None

        # Themed element references
        self._themed_elements: dict[str, ft.Control] = {}

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for theme-aware updates."""
        return {}  # Dialog rebuilds on show, individual elements handle themes

    def _close_dialog(self, e=None):
        """Close dialog and unregister from theme system."""
        self._unregister_theme_aware()
        if self.dialog:
            self._page_ref.pop_dialog()

    async def _load_current_state(self):
        """Load current overlay state from registry"""
        self.loading_ring.visible = True
        self.overlay_switch.disabled = True
        self._page_ref.update()

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

        self._page_ref.update()

    def _update_status_text(self, is_enabled: bool):
        """Update the status text based on current state"""
        is_dark = self._registry.is_dark
        if is_enabled:
            self.status_text.value = "Overlay is ENABLED - shows DLSS indicator in games"
            self.status_text.color = MD3Colors.get_success(is_dark)
        else:
            self.status_text.value = "Overlay is DISABLED"
            self.status_text.color = MD3Colors.get_text_secondary(is_dark)

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
        is_dark = self._registry.is_dark

        # Show loading state
        self.loading_ring.visible = True
        self.overlay_switch.disabled = True
        self._hide_error()
        self._page_ref.update()

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
                bgcolor=MD3Colors.get_success(is_dark) if new_state else MD3Colors.get_primary(is_dark),
                duration=3000,
            )
            self._page_ref.overlay.append(snackbar)
            snackbar.open = True
        else:
            # Revert switch state on failure
            self.overlay_switch.value = not new_state
            self._show_error(error)
            self.logger.error(f"Failed to set DLSS overlay state: {error}")

        self._page_ref.update()

    async def _show_unavailable_dialog(self):
        """Show dialog explaining feature is Windows-only"""
        is_dark = self._registry.is_dark
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE, color=MD3Colors.get_primary(is_dark)),
                    ft.Text("Feature Not Available", color=MD3Colors.get_text_primary(is_dark)),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(
                            "DLSS Debug Overlay is only available on Windows.",
                            size=14,
                            color=MD3Colors.get_text_primary(is_dark),
                        ),
                        ft.Container(height=8),
                        ft.Text(
                            "This feature requires direct access to the Windows "
                            "registry to enable NVIDIA's debug overlay indicator.",
                            size=12,
                            color=MD3Colors.get_text_secondary(is_dark),
                        ),
                    ],
                    spacing=4,
                ),
                width=400,
            ),
            bgcolor=MD3Colors.get_surface(is_dark),
            actions=[
                ft.FilledButton("OK", on_click=lambda e: self._page_ref.pop_dialog()),
            ],
        )
        self._page_ref.show_dialog(dialog)

    async def _on_copy_clicked(self, e):
        """Copy launch options to clipboard (Linux)"""
        is_dark = self._registry.is_dark
        launch_opts = f"{self.LINUX_OVERLAY_ENV} %command%"
        try:
            await ft.Clipboard().set(launch_opts)
            self._page_ref.snack_bar = ft.SnackBar(
                content=ft.Text("Launch options copied to clipboard!"),
                bgcolor=MD3Colors.get_success(is_dark),
            )
            self._page_ref.snack_bar.open = True
            self._page_ref.update()
            self.logger.info("Linux DLSS overlay launch options copied to clipboard")
        except Exception as ex:
            self.logger.warning(f"Clipboard operation failed: {ex}")
            self._page_ref.snack_bar = ft.SnackBar(
                content=ft.Text("Failed to copy to clipboard"),
                bgcolor=MD3Colors.get_error(is_dark),
            )
            self._page_ref.snack_bar.open = True
            self._page_ref.update()

    async def _show_linux_dialog(self):
        """Show Linux-specific dialog with environment variable instructions"""
        # Register for theme updates
        self._register_theme_aware()
        is_dark = self._registry.is_dark

        launch_opts = f"{self.LINUX_OVERLAY_ENV} %command%"

        self.launch_options_field = ft.TextField(
            value=launch_opts,
            read_only=True,
            multiline=True,
            min_lines=1,
            max_lines=2,
            text_size=11,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
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
                        color=MD3Colors.get_text_secondary(is_dark),
                    ),
                    ft.Container(height=4),
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=MD3Colors.get_warning(is_dark)),
                            ft.Text(
                                "Works with Proton/Wine games using DXVK-NVAPI.",
                                size=11,
                                color=MD3Colors.get_warning(is_dark),
                                italic=True,
                            ),
                        ],
                        spacing=4,
                    ),
                ],
                spacing=4,
            ),
            bgcolor=MD3Colors.get_surface_container(is_dark),
            padding=ft.padding.all(12),
            border_radius=4,
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.BUG_REPORT, color=MD3Colors.get_success(is_dark)),
                    ft.Text("DLSS Debug Overlay", color=MD3Colors.get_text_primary(is_dark)),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        info_container,
                        ft.Divider(color=MD3Colors.get_divider(is_dark)),
                        ft.Text("Steam Launch Options:", weight=ft.FontWeight.BOLD, size=14, color=MD3Colors.get_text_primary(is_dark)),
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
            bgcolor=MD3Colors.get_surface(is_dark),
            actions=[
                ft.FilledButton(
                    "Close", on_click=self._close_dialog
                ),
            ],
        )

        self._page_ref.show_dialog(self.dialog)

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

        # Register for theme updates
        self._register_theme_aware()
        is_dark = self._registry.is_dark

        # Create switch
        self.overlay_switch = ft.Switch(
            value=False,
            active_color=MD3Colors.get_success(is_dark),
            on_change=self._on_toggle_changed,
        )

        # Status text
        self.status_text = ft.Text(
            "Loading...",
            size=12,
            color=MD3Colors.get_text_secondary(is_dark),
        )

        # Loading indicator
        self.loading_ring = ft.ProgressRing(
            width=16,
            height=16,
            stroke_width=2,
            color=MD3Colors.get_primary(is_dark),
            visible=True,
        )

        # Error container
        self.error_container = ft.Container(
            content=ft.Text("", color=MD3Colors.get_error(is_dark), size=12),
            bgcolor=MD3Colors.ERROR_CONTAINER if not is_dark else "#4A1515",
            padding=ft.padding.all(8),
            border_radius=4,
            visible=False,
        )

        # Main toggle tile
        toggle_tile = ft.ListTile(
            leading=ft.Icon(ft.Icons.BUG_REPORT, color=MD3Colors.get_success(is_dark)),
            title=ft.Text("DLSS Debug Overlay", weight=ft.FontWeight.BOLD, color=MD3Colors.get_text_primary(is_dark)),
            subtitle=ft.Text("Shows DLSS/DLAA indicator in-game", color=MD3Colors.get_text_secondary(is_dark)),
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
                        color=MD3Colors.get_text_secondary(is_dark),
                    ),
                    ft.Container(height=4),
                    ft.Text(
                        "Note: Changes require game restart to take effect.",
                        size=11,
                        color=MD3Colors.get_warning(is_dark),
                        italic=True,
                    ),
                ],
                spacing=4,
            ),
            bgcolor=MD3Colors.get_surface_container(is_dark),
            padding=ft.padding.all(12),
            border_radius=4,
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS_DISPLAY, color=MD3Colors.get_primary(is_dark)),
                    ft.Text("DLSS Debug Settings", color=MD3Colors.get_text_primary(is_dark)),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        info_text,
                        ft.Divider(color=MD3Colors.get_divider(is_dark)),
                        toggle_tile,
                        self.status_text,
                        self.error_container,
                    ],
                    spacing=12,
                    tight=True,
                ),
                width=500,
            ),
            bgcolor=MD3Colors.get_surface(is_dark),
            actions=[
                ft.FilledButton(
                    "Close", on_click=self._close_dialog
                ),
            ],
        )

        self._page_ref.show_dialog(self.dialog)

        # Load current state after dialog is visible
        await self._load_current_state()
