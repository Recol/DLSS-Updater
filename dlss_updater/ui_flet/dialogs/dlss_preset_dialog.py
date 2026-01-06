"""
DLSS Super Resolution Preset Override Dialog
Configure DLSS SR presets with GPU-based recommendations.

Windows: Applies system-wide via registry
Linux: Generates Steam launch options for clipboard
"""

import logging
from datetime import datetime
import flet as ft

from dlss_updater.config import config_manager
from dlss_updater.models import DLSSPreset, DLSSPresetConfig, GPUInfo
from dlss_updater.gpu_detection import detect_nvidia_gpu
from dlss_updater.dlss_preset_utils import (
    apply_preset,
    get_current_preset,
    generate_linux_env_vars,
    format_steam_launch_options,
)
from dlss_updater.platform_utils import IS_WINDOWS, IS_LINUX, FEATURES


class DLSSPresetDialog:
    """
    Dialog for configuring DLSS Super Resolution preset overrides.

    Shows GPU info, preset selection, and applies changes:
    - Windows: Registry-based system-wide override
    - Linux: Generates Steam launch options for manual copy
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        self.page = page
        self.logger = logger
        self.dialog: ft.AlertDialog | None = None
        self.gpu_info: GPUInfo | None = None
        self.current_preset: DLSSPreset = DLSSPreset.DEFAULT
        self.selected_preset: DLSSPreset = DLSSPreset.DEFAULT

        # UI components
        self.gpu_info_container: ft.Container | None = None
        self.loading_ring: ft.ProgressRing | None = None
        self.preset_radio_group: ft.RadioGroup | None = None
        self.linux_overlay_checkbox: ft.Checkbox | None = None
        self.launch_options_field: ft.TextField | None = None
        self.copy_button: ft.FilledButton | None = None
        self.apply_button: ft.FilledButton | None = None
        self.error_container: ft.Container | None = None
        self.status_text: ft.Text | None = None

    async def _detect_gpu(self):
        """Detect NVIDIA GPU and update UI"""
        self.loading_ring.visible = True
        self.page.update()

        try:
            self.gpu_info = await detect_nvidia_gpu()

            if self.gpu_info:
                self._update_gpu_info_display()
                self.logger.info(
                    f"GPU detected: {self.gpu_info.name} ({self.gpu_info.architecture})"
                )
            else:
                self._show_no_gpu_detected()
                self.logger.warning("No NVIDIA GPU detected")

        except Exception as e:
            self.logger.error(f"GPU detection failed: {e}", exc_info=True)
            self._show_error(f"GPU detection failed: {e}")

        finally:
            self.loading_ring.visible = False
            self.page.update()

    def _update_gpu_info_display(self):
        """Update GPU info container with detected GPU"""
        if not self.gpu_info:
            return

        # GPU name
        gpu_name = ft.Text(
            self.gpu_info.name,
            size=16,
            weight=ft.FontWeight.BOLD,
            color="#4CAF50",  # Green
        )

        # Architecture and VRAM
        arch_info = ft.Text(
            f"{self.gpu_info.architecture} | {self.gpu_info.vram_mb} MB VRAM | "
            f"SM {self.gpu_info.sm_version_major}.{self.gpu_info.sm_version_minor}",
            size=12,
            color=ft.Colors.GREY,
        )

        # Driver version
        driver_info = ft.Text(
            f"Driver: {self.gpu_info.driver_version}",
            size=11,
            color=ft.Colors.GREY,
        )

        # Recommended preset badge
        recommended = DLSSPreset(self.gpu_info.recommended_preset)
        recommended_badge = ft.Container(
            content=ft.Text(
                f"Recommended: {recommended.display_name}",
                size=11,
                color="#FFFFFF",
            ),
            bgcolor="#2D6E88",
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            border_radius=4,
        )

        self.gpu_info_container.content = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.MEMORY, color="#4CAF50", size=24),
                        gpu_name,
                    ],
                    spacing=8,
                ),
                arch_info,
                driver_info,
                ft.Container(height=4),
                recommended_badge,
            ],
            spacing=4,
        )
        self.gpu_info_container.bgcolor = "#2C2C2C"
        self.gpu_info_container.visible = True

    def _show_no_gpu_detected(self):
        """Show message when no NVIDIA GPU is detected"""
        self.gpu_info_container.content = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.WARNING, color=ft.Colors.AMBER),
                        ft.Text(
                            "No NVIDIA GPU Detected",
                            size=14,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.AMBER,
                        ),
                    ],
                    spacing=8,
                ),
                ft.Text(
                    "DLSS preset override requires an NVIDIA GPU with DLSS support.",
                    size=12,
                    color=ft.Colors.GREY,
                ),
            ],
            spacing=4,
        )
        self.gpu_info_container.bgcolor = "#3C3C3C"
        self.gpu_info_container.visible = True

        # Disable preset selection
        if self.preset_radio_group:
            self.preset_radio_group.disabled = True
        if self.apply_button:
            self.apply_button.disabled = True

    async def _load_current_preset(self):
        """Load current preset from config/registry"""
        # Load from config first
        config = config_manager.get_dlss_preset_config()
        self.selected_preset = DLSSPreset(config.selected_preset)

        # On Windows, also check current registry state
        if IS_WINDOWS:
            current, error, _ = await get_current_preset()
            if not error:
                self.current_preset = current
                # If config differs from registry, prefer registry as source of truth
                if self.current_preset != self.selected_preset:
                    self.selected_preset = self.current_preset

        # Update radio selection
        self.preset_radio_group.value = self.selected_preset.value
        self._update_status_text()

        # Update Linux launch options if applicable
        if IS_LINUX:
            self._update_launch_options()

        self.page.update()

    def _update_status_text(self):
        """Update status text based on current state"""
        if IS_WINDOWS:
            if self.current_preset == DLSSPreset.DEFAULT:
                self.status_text.value = "No preset override active (using driver default)"
                self.status_text.color = ft.Colors.GREY
            else:
                self.status_text.value = f"Current override: {self.current_preset.display_name}"
                self.status_text.color = "#4CAF50"
        else:
            self.status_text.value = "Copy launch options below to configure preset"
            self.status_text.color = ft.Colors.GREY

    def _on_preset_changed(self, e):
        """Handle preset radio button change"""
        self.selected_preset = DLSSPreset(e.control.value)
        self.logger.debug(f"Preset selection changed to: {self.selected_preset}")

        # Update Linux launch options if applicable
        if IS_LINUX:
            self._update_launch_options()
            self.page.update()

    def _update_launch_options(self):
        """Update launch options text field (Linux only)"""
        if not IS_LINUX or not self.launch_options_field:
            return

        include_overlay = (
            self.linux_overlay_checkbox.value
            if self.linux_overlay_checkbox
            else False
        )

        env_vars = generate_linux_env_vars(self.selected_preset, include_overlay)
        launch_opts = format_steam_launch_options(env_vars)

        self.launch_options_field.value = launch_opts
        self.copy_button.disabled = not env_vars  # Disable copy if nothing to copy

    def _on_linux_overlay_changed(self, e):
        """Handle Linux overlay checkbox change"""
        self._update_launch_options()
        self.page.update()

    async def _on_copy_clicked(self, e):
        """Copy launch options to clipboard"""
        if self.launch_options_field and self.launch_options_field.value:
            self.page.set_clipboard(self.launch_options_field.value)
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Launch options copied to clipboard!"),
                bgcolor="#4CAF50",
            )
            self.page.snack_bar.open = True
            self.page.update()
            self.logger.info("Linux launch options copied to clipboard")

    async def _on_apply_clicked(self, e):
        """Apply preset (Windows registry or save config)"""
        self.apply_button.disabled = True
        self.loading_ring.visible = True
        self._hide_error()
        self.page.update()

        try:
            success, error, extra_data = await apply_preset(self.selected_preset)

            if success:
                # Save to config
                config = DLSSPresetConfig(
                    selected_preset=self.selected_preset.value,
                    auto_detect_enabled=True,
                    detected_architecture=(
                        self.gpu_info.architecture if self.gpu_info else None
                    ),
                    last_detection_time=datetime.now().isoformat(),
                    linux_overlay_enabled=(
                        self.linux_overlay_checkbox.value
                        if self.linux_overlay_checkbox
                        else False
                    ),
                )
                config_manager.save_dlss_preset_config(config)

                self.current_preset = self.selected_preset
                self._update_status_text()

                # Show success
                action = "applied" if IS_WINDOWS else "saved"
                snackbar = ft.SnackBar(
                    content=ft.Text(
                        f"Preset {self.selected_preset.display_name} {action}. "
                        "Restart games to see changes."
                    ),
                    bgcolor="#4CAF50",
                    duration=3000,
                )
                self.page.overlay.append(snackbar)
                snackbar.open = True

                self.logger.info(
                    f"DLSS preset {self.selected_preset.value} applied successfully"
                )

                # Close dialog on success
                self.page.close(self.dialog)
                return
            else:
                self._show_error(error or "Failed to apply preset")
                self.logger.error(f"Failed to apply preset: {error}")

        except Exception as e:
            self._show_error(f"Error: {e}")
            self.logger.error(f"Error applying preset: {e}", exc_info=True)

        finally:
            self.apply_button.disabled = False
            self.loading_ring.visible = False
            self.page.update()

    def _show_error(self, message: str):
        """Display error message"""
        if self.error_container:
            self.error_container.content.value = message
            self.error_container.visible = True

    def _hide_error(self):
        """Hide error message"""
        if self.error_container:
            self.error_container.visible = False

    def _build_preset_radios(self) -> ft.RadioGroup:
        """Build radio buttons for preset selection"""
        recommended_preset = None
        if self.gpu_info:
            recommended_preset = self.gpu_info.recommended_preset

        def make_radio(preset: DLSSPreset) -> ft.Radio:
            label = preset.display_name
            if recommended_preset and preset.value == recommended_preset:
                label += " [Recommended]"
            return ft.Radio(value=preset.value, label=label)

        return ft.RadioGroup(
            content=ft.Column(
                controls=[
                    make_radio(DLSSPreset.DEFAULT),
                    make_radio(DLSSPreset.PRESET_K),
                    make_radio(DLSSPreset.PRESET_L),
                    make_radio(DLSSPreset.PRESET_M),
                ],
                spacing=8,
            ),
            value=self.selected_preset.value,
            on_change=self._on_preset_changed,
        )

    def _build_linux_section(self) -> list[ft.Control]:
        """Build Linux-specific UI elements"""
        if not IS_LINUX:
            return []

        self.linux_overlay_checkbox = ft.Checkbox(
            label="Include DLSS debug overlay",
            value=False,
            on_change=self._on_linux_overlay_changed,
        )

        self.launch_options_field = ft.TextField(
            value="%command%",
            read_only=True,
            multiline=True,
            min_lines=2,
            max_lines=3,
            text_size=11,
            bgcolor="#2C2C2C",
        )

        self.copy_button = ft.FilledButton(
            "Copy to Clipboard",
            icon=ft.Icons.COPY,
            on_click=self._on_copy_clicked,
            disabled=True,
        )

        return [
            ft.Divider(),
            ft.Text("Linux Launch Options:", weight=ft.FontWeight.BOLD, size=14),
            ft.Text(
                "Add these to Steam's 'Set Launch Options' or Lutris environment variables:",
                size=11,
                color=ft.Colors.GREY,
            ),
            self.linux_overlay_checkbox,
            self.launch_options_field,
            ft.Row(
                controls=[self.copy_button],
                alignment=ft.MainAxisAlignment.END,
            ),
        ]

    async def show(self):
        """Show the DLSS preset dialog"""
        # Check if NVIDIA GPU feature is available
        if not FEATURES.nvidia_gpu_detected:
            await self._show_unavailable_dialog()
            return

        # Loading ring
        self.loading_ring = ft.ProgressRing(
            width=16,
            height=16,
            stroke_width=2,
            color="#2D6E88",
            visible=True,
        )

        # GPU info container (populated after detection)
        self.gpu_info_container = ft.Container(
            content=ft.Row(
                controls=[
                    self.loading_ring,
                    ft.Text("Detecting GPU...", color=ft.Colors.GREY),
                ],
                spacing=8,
            ),
            bgcolor="#2C2C2C",
            padding=ft.padding.all(12),
            border_radius=8,
        )

        # Status text
        self.status_text = ft.Text(
            "Loading...",
            size=12,
            color=ft.Colors.GREY,
        )

        # Error container
        self.error_container = ft.Container(
            content=ft.Text("", color=ft.Colors.RED, size=12),
            bgcolor="#4A1515",
            padding=ft.padding.all(8),
            border_radius=4,
            visible=False,
        )

        # Preset radio group (will be rebuilt after GPU detection)
        self.preset_radio_group = self._build_preset_radios()

        # Info text
        info_container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Override the default DLSS Super Resolution preset for all games. "
                        "Preset K is recommended for RTX 20/30. For RTX 40/50, use Preset M or K. "
                        "Preset L is heavier and may reduce performance.",
                        size=12,
                        color=ft.Colors.GREY,
                    ),
                    ft.Container(height=4),
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=ft.Colors.AMBER),
                            ft.Text(
                                "Changes require game restart to take effect.",
                                size=11,
                                color=ft.Colors.AMBER,
                                italic=True,
                            ),
                        ],
                        spacing=4,
                    ),
                    ft.Container(height=2),
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.WARNING_AMBER, size=14, color=ft.Colors.ORANGE),
                            ft.Text(
                                "Driver bug: System-level overrides may not work. "
                                "If ineffective, use the NVIDIA App instead.",
                                size=11,
                                color=ft.Colors.ORANGE,
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

        # Multi-GPU note
        multi_gpu_note = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.WARNING_AMBER, size=14, color=ft.Colors.GREY),
                    ft.Text(
                        "Note: Multi-GPU configurations not supported (uses primary GPU)",
                        size=10,
                        color=ft.Colors.GREY,
                        italic=True,
                    ),
                ],
                spacing=4,
            ),
            padding=ft.padding.only(top=8),
        )

        # Build content column
        content_controls = [
            ft.Text("GPU Information", weight=ft.FontWeight.BOLD, size=14),
            self.gpu_info_container,
            ft.Divider(),
            ft.Text("Select Preset:", weight=ft.FontWeight.BOLD, size=14),
            self.preset_radio_group,
            self.status_text,
            self.error_container,
        ]

        # Add Linux-specific controls
        content_controls.extend(self._build_linux_section())

        # Add info and notes
        content_controls.extend([
            ft.Divider(),
            info_container,
            multi_gpu_note,
        ])

        # Apply button (Windows) or just Close (Linux)
        actions = []
        if IS_WINDOWS:
            self.apply_button = ft.FilledButton(
                "Apply",
                icon=ft.Icons.CHECK,
                on_click=self._on_apply_clicked,
            )
            actions = [
                ft.TextButton(
                    "Cancel",
                    on_click=lambda e: self.page.close(self.dialog),
                ),
                self.apply_button,
            ]
        else:
            # Linux: Just close button since changes are copied to clipboard
            actions = [
                ft.FilledButton(
                    "Close",
                    on_click=lambda e: self.page.close(self.dialog),
                ),
            ]

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.TUNE, color="#2D6E88"),
                    ft.Text("DLSS SR Preset Override"),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=content_controls,
                    spacing=12,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=520,
                height=500,
            ),
            actions=actions,
        )

        self.page.open(self.dialog)

        # Detect GPU and load current preset
        await self._detect_gpu()

        # Rebuild radio group with recommendations after GPU detection
        if self.gpu_info:
            new_radio_group = self._build_preset_radios()
            # Find and replace the radio group in content
            for i, ctrl in enumerate(content_controls):
                if ctrl == self.preset_radio_group:
                    content_controls[i] = new_radio_group
                    self.preset_radio_group = new_radio_group
                    break
            # Update dialog content
            self.dialog.content.content.controls = content_controls
            self.page.update()

        await self._load_current_preset()

    async def _show_unavailable_dialog(self):
        """Show dialog explaining feature requires NVIDIA GPU"""
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
                            "DLSS SR Preset Override requires an NVIDIA GPU.",
                            size=14,
                        ),
                        ft.Container(height=8),
                        ft.Text(
                            "This feature allows you to override the default DLSS "
                            "Super Resolution preset for optimal image quality based "
                            "on your GPU architecture.",
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
