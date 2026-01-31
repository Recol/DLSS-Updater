"""
LinuxDLSSPresetsPanel - Linux DLSS SR Presets configuration panel
Allows users to generate Steam launch options for DXVK-NVAPI environment variables
"""

import logging
import flet as ft
from dlss_updater.ui_flet.components.slide_panel import PanelContentBase
from dlss_updater.config import config_manager
from dlss_updater.models import LinuxDLSSConfig, DLSSPreset
from dlss_updater.linux_dlss_utils import (
    generate_steam_launch_options,
    get_preset_description,
    get_all_presets,
)
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors


class LinuxDLSSPresetsPanel(ThemeAwareMixin, PanelContentBase):
    """
    Panel for configuring Linux DLSS SR presets.

    Features:
    - Preset dropdown (Default/K/L/M)
    - Debug overlay toggle
    - Wayland support toggle
    - HDR support toggle
    - Live command preview
    - Copy to clipboard button
    - Material Design 3 styling with consistent theme colors
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        """
        Initialize Linux DLSS presets panel.

        Args:
            page: Flet Page instance
            logger: Logger instance for diagnostics
        """
        super().__init__(page, logger)

        # Theme support
        self._registry = get_theme_registry()
        self._theme_priority = 60  # Panels animate later in cascade

        # Store themed element references
        self._info_container: ft.Container | None = None
        self._info_text: ft.Text | None = None
        self._preset_label: ft.Text | None = None
        self._preset_desc: ft.Text | None = None
        self._options_label: ft.Text | None = None
        self._command_label: ft.Text | None = None
        self._command_field: ft.TextField | None = None
        self._dividers: list[ft.Divider] = []

        self._load_config()
        self._build_controls()

        # Register for theme updates
        self._register_theme_aware()

    @property
    def title(self) -> str:
        """Panel title."""
        return "Linux DLSS SR Presets"

    @property
    def subtitle(self) -> str | None:
        """Panel subtitle."""
        return "Configure Proton/Wine DLSS settings"

    @property
    def width(self) -> int:
        """Panel width in pixels."""
        return 500

    def _load_config(self):
        """Load current configuration from config."""
        self._config = config_manager.get_linux_dlss_config()

    def _build_controls(self):
        """Build all controls for the panel."""
        is_dark = self._registry.is_dark
        primary_color = MD3Colors.get_primary(is_dark)

        # Preset dropdown options
        preset_options = [
            ft.DropdownOption(key=value, text=display_name)
            for value, display_name, _ in get_all_presets()
        ]

        # Preset dropdown
        self._preset_dropdown = ft.Dropdown(
            options=preset_options,
            value=self._config.selected_preset,
            label="SR Preset Override",
            border_color=MD3Colors.get_outline(is_dark),
            focused_border_color=primary_color,
            on_select=self._on_preset_changed,
            expand=True,
        )

        # Overlay switch
        self._overlay_switch = ft.Switch(
            value=self._config.overlay_enabled,
            active_color=primary_color,
            on_change=self._on_setting_changed,
        )

        # Wayland switch
        self._wayland_switch = ft.Switch(
            value=self._config.wayland_enabled,
            active_color=primary_color,
            on_change=self._on_setting_changed,
        )

        # HDR switch
        self._hdr_switch = ft.Switch(
            value=self._config.hdr_enabled,
            active_color=primary_color,
            on_change=self._on_setting_changed,
        )

    def _on_preset_changed(self, e):
        """Handle preset selection change."""
        self._update_preset_description()
        self._update_command_preview()

    def _on_setting_changed(self, e):
        """Handle switch toggle."""
        self._update_command_preview()

    def _update_preset_description(self):
        """Update preset description text based on current selection."""
        if self._preset_desc and self._preset_dropdown.value:
            desc = get_preset_description(self._preset_dropdown.value)
            self._preset_desc.value = desc
            try:
                self._preset_desc.update()
            except Exception:
                pass

    def _update_command_preview(self):
        """Update the command preview based on current settings."""
        if not self._command_field:
            return

        # Build config from current UI state
        current_config = LinuxDLSSConfig(
            selected_preset=self._preset_dropdown.value or "default",
            overlay_enabled=self._overlay_switch.value,
            wayland_enabled=self._wayland_switch.value,
            hdr_enabled=self._hdr_switch.value,
        )

        # Generate command
        command = generate_steam_launch_options(current_config)
        self._command_field.value = command
        try:
            self._command_field.update()
        except Exception:
            pass

    async def _copy_to_clipboard(self, e):
        """Copy the current command to clipboard."""
        if self._command_field and self._command_field.value:
            try:
                await self._page_ref.set_clipboard_async(self._command_field.value)
                self._show_snackbar("Copied to clipboard!", "#4CAF50")
            except Exception as ex:
                self.logger.warning(f"Failed to copy to clipboard: {ex}")
                self._show_snackbar("Failed to copy to clipboard", "#F44336")

    def build(self) -> ft.Control:
        """
        Build the Linux DLSS presets panel content.

        Returns:
            Column containing all preference controls
        """
        is_dark = self._registry.is_dark
        primary_color = MD3Colors.get_primary(is_dark)

        # Info container
        self._info_text = ft.Text(
            "These settings generate Steam launch options for DXVK-NVAPI. "
            "Copy and paste into Steam's \"Set Launch Options\" for each game.",
            size=13,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )
        self._info_container = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=20, color=primary_color),
                    ft.Container(content=self._info_text, expand=True),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=ft.padding.all(16),
            border_radius=12,
            bgcolor=MD3Colors.get_surface_container(is_dark),
        )

        # Preset section
        self._preset_label = ft.Text(
            "SR Preset Override",
            weight=ft.FontWeight.BOLD,
            size=15,
            color=MD3Colors.get_text_primary(is_dark),
        )

        self._preset_desc = ft.Text(
            get_preset_description(self._config.selected_preset),
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            italic=True,
        )

        # Divider 1
        divider1 = ft.Divider(height=20, color=MD3Colors.get_divider(is_dark))
        self._dividers.append(divider1)

        # Additional options section
        self._options_label = ft.Text(
            "Additional Options",
            weight=ft.FontWeight.BOLD,
            size=15,
            color=MD3Colors.get_text_primary(is_dark),
        )

        # Option tiles
        overlay_tile = ft.ListTile(
            title=ft.Text("DLSS Debug Overlay", weight=ft.FontWeight.W_500),
            subtitle=ft.Text("Show DLSS indicator in games", size=12),
            trailing=self._overlay_switch,
        )

        wayland_tile = ft.ListTile(
            title=ft.Text("Wayland Support", weight=ft.FontWeight.W_500),
            subtitle=ft.Text("Enable Proton Wayland mode", size=12),
            trailing=self._wayland_switch,
        )

        hdr_tile = ft.ListTile(
            title=ft.Text("HDR Support", weight=ft.FontWeight.W_500),
            subtitle=ft.Text("Enable HDR for Proton/Wine games", size=12),
            trailing=self._hdr_switch,
        )

        # Divider 2
        divider2 = ft.Divider(height=20, color=MD3Colors.get_divider(is_dark))
        self._dividers.append(divider2)

        # Command preview section
        self._command_label = ft.Text(
            "Steam Launch Options",
            weight=ft.FontWeight.BOLD,
            size=15,
            color=MD3Colors.get_text_primary(is_dark),
        )

        # Generate initial command
        initial_command = generate_steam_launch_options(self._config)

        self._command_field = ft.TextField(
            value=initial_command,
            read_only=True,
            multiline=True,
            min_lines=2,
            max_lines=4,
            border_color=MD3Colors.get_outline(is_dark),
            text_size=12,
        )

        copy_button = ft.IconButton(
            icon=ft.Icons.CONTENT_COPY,
            tooltip="Copy to clipboard",
            icon_color=primary_color,
            on_click=self._copy_to_clipboard,
        )

        command_row = ft.Row(
            controls=[
                ft.Container(content=self._command_field, expand=True),
                copy_button,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        return ft.Column(
            controls=[
                self._info_container,
                ft.Container(height=8),
                self._preset_label,
                self._preset_dropdown,
                self._preset_desc,
                divider1,
                self._options_label,
                overlay_tile,
                wayland_tile,
                hdr_tile,
                divider2,
                self._command_label,
                command_row,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """
        Return themed property mappings for cascade animation.

        Returns:
            Dict mapping property paths to (dark_value, light_value) tuples.
        """
        props = {}

        # Labels
        if self._preset_label:
            props["_preset_label.color"] = MD3Colors.get_themed_pair("text_primary")
        if self._options_label:
            props["_options_label.color"] = MD3Colors.get_themed_pair("text_primary")
        if self._command_label:
            props["_command_label.color"] = MD3Colors.get_themed_pair("text_primary")

        # Info text
        if self._info_text:
            props["_info_text.color"] = MD3Colors.get_themed_pair("on_surface_variant")
        if self._preset_desc:
            props["_preset_desc.color"] = MD3Colors.get_themed_pair("on_surface_variant")

        # Containers
        if self._info_container:
            props["_info_container.bgcolor"] = MD3Colors.get_themed_pair("surface_container")

        # Dividers
        for i, divider in enumerate(self._dividers):
            props[f"_dividers[{i}].color"] = MD3Colors.get_themed_pair("divider")

        # Switches - active color
        props["_overlay_switch.active_color"] = MD3Colors.get_themed_pair("primary")
        props["_wayland_switch.active_color"] = MD3Colors.get_themed_pair("primary")
        props["_hdr_switch.active_color"] = MD3Colors.get_themed_pair("primary")

        return props

    def validate(self) -> tuple[bool, str | None]:
        """
        Validate configuration before saving.

        Returns:
            Tuple of (is_valid, error_message)
        """
        # No validation needed - all configurations are valid
        return True, None

    async def on_save(self) -> bool:
        """
        Save configuration to config.

        Returns:
            True if save succeeded, False otherwise
        """
        # Build config from current UI state
        new_config = LinuxDLSSConfig(
            selected_preset=self._preset_dropdown.value or "default",
            overlay_enabled=self._overlay_switch.value,
            wayland_enabled=self._wayland_switch.value,
            hdr_enabled=self._hdr_switch.value,
        )

        # Save to config
        config_manager.save_linux_dlss_config(new_config)

        self.logger.info(
            f"Linux DLSS config saved: preset={new_config.selected_preset}, "
            f"overlay={new_config.overlay_enabled}, wayland={new_config.wayland_enabled}, "
            f"hdr={new_config.hdr_enabled}"
        )

        # Show success feedback
        self._show_snackbar("Settings saved successfully")

        return True

    def on_cancel(self):
        """
        Called when panel is cancelled.

        Reloads configuration from config to discard unsaved changes.
        """
        self.logger.debug("Linux DLSS presets panel cancelled, discarding changes")
        self._load_config()
