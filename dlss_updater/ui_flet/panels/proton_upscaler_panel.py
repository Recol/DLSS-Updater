"""
ProtonUpscalerPanel - Proton upscaler launch options panel (Linux)

Generates per-title Steam launch options covering DLSS (SR/RR presets, FG
override, DLL upgrade + indicators via DXVK-NVAPI and the community Proton
forks), FSR 4 upgrades (RDNA3/RDNA4 aware) and XeSS upgrades — gated by the
detected GPU vendor and validated against the Proton build each game actually
runs under (Steam's CompatToolMapping).
"""

import logging

import flet as ft

from dlss_updater.config import config_manager
from dlss_updater.gpu_detection import LinuxGPU, detect_linux_gpus, pick_primary_gpu
from dlss_updater.linux_dlss_utils import (
    generate_steam_launch_options,
    get_all_presets,
    get_preset_description,
    get_rr_presets,
)
from dlss_updater.models import LinuxDLSSConfig
from dlss_updater.proton_compat import (
    CAP_DLSS_INDICATOR,
    CAP_DLSS_UPGRADE,
    CAP_FSR4_INDICATOR,
    CAP_FSR4_UPGRADE,
    CAP_XESS_UPGRADE,
    ProtonToolInfo,
    classify_compat_tool,
    get_compat_tool_mapping,
    resolve_tool_for_app,
)
from dlss_updater.ui_flet.components.slide_panel import PanelContentBase
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry

# Dropdown key for the "no specific game" mode (no capability filtering)
_GENERIC_KEY = "generic"


class ProtonUpscalerPanel(ThemeAwareMixin, PanelContentBase):
    """
    Panel for configuring Proton upscaler launch options.

    Features:
    - DLSS SR/RR preset overrides + FG override (DXVK-NVAPI, any Proton)
    - DLSS / FSR 4 / XeSS DLL upgrade toggles (GE-Proton, Proton-CachyOS, EM)
    - Per-game validation against Steam's CompatToolMapping
    - GPU-vendor section gating (NVIDIA / AMD / Intel via sysfs PCI ids)
    - Live command preview + copy to clipboard
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        super().__init__(page, logger)

        # Theme support
        self._registry = get_theme_registry()
        self._theme_priority = 60  # Panels animate later in cascade

        # Async-populated state (filled in on_open)
        self._compat_mapping: dict[str, str] = {}
        self._steam_games: list = []
        self._tool_info: ProtonToolInfo | None = None
        self._detected_gpu: LinuxGPU | None = None
        self._detected_vendors: set[str] = set()

        # Themed element references
        self._info_container: ft.Container | None = None
        self._info_text: ft.Text | None = None
        self._section_labels: list[ft.Text] = []
        self._preset_desc: ft.Text | None = None
        self._game_status: ft.Text | None = None
        self._gpu_status: ft.Text | None = None
        self._command_field: ft.TextField | None = None
        self._dividers: list[ft.Divider] = []
        self._root: ft.Column | None = None

        self._load_config()
        self._build_controls()

        # Register for theme updates
        self._register_theme_aware()

    @property
    def title(self) -> str:
        return "Proton Upscalers"

    @property
    def subtitle(self) -> str | None:
        return "DLSS, FSR 4 & XeSS launch options for Proton/Wine"

    @property
    def width(self) -> int:
        return 520

    def _load_config(self):
        """Load current configuration from config."""
        self._config = config_manager.get_linux_dlss_config()

    # =========================================================================
    # Control construction
    # =========================================================================

    def _build_controls(self):
        is_dark = self._registry.is_dark
        primary_color = MD3Colors.get_primary(is_dark)
        outline_color = MD3Colors.get_outline(is_dark)

        def switch(value: bool) -> ft.Switch:
            return ft.Switch(
                value=value,
                active_color=primary_color,
                on_change=self._on_setting_changed,
            )

        # --- Per-game validation ---
        self._game_dropdown = ft.Dropdown(
            options=[ft.DropdownOption(key=_GENERIC_KEY, text="Generic (any game)")],
            value=_GENERIC_KEY,
            label="Validate against game (Steam)",
            border_color=outline_color,
            focused_border_color=primary_color,
            on_select=self._on_game_changed,
            expand=True,
        )

        # --- NVIDIA / DLSS ---
        self._preset_dropdown = ft.Dropdown(
            options=[
                ft.DropdownOption(key=value, text=display_name)
                for value, display_name, _ in get_all_presets()
            ],
            value=self._config.selected_preset,
            label="SR Preset Override",
            border_color=outline_color,
            focused_border_color=primary_color,
            on_select=self._on_preset_changed,
            expand=True,
        )
        self._rr_dropdown = ft.Dropdown(
            options=[
                ft.DropdownOption(key=value, text=display_name)
                for value, display_name, _ in get_rr_presets()
            ],
            value=self._config.rr_preset,
            label="RR Preset Override (Ray Reconstruction)",
            border_color=outline_color,
            focused_border_color=primary_color,
            on_select=self._on_setting_changed,
            expand=True,
        )
        self._fg_switch = switch(self._config.fg_override)
        self._dlss_upgrade_switch = switch(self._config.dlss_upgrade)
        self._dlss_indicator_switch = switch(self._config.dlss_indicator)
        self._overlay_switch = switch(self._config.overlay_enabled)

        # --- AMD / FSR 4 ---
        self._fsr4_upgrade_switch = switch(self._config.fsr4_upgrade)
        self._rdna3_switch = switch(self._config.fsr4_rdna3_mode)
        self._fsr4_indicator_switch = switch(self._config.fsr4_indicator)

        # --- Intel / XeSS ---
        self._xess_upgrade_switch = switch(self._config.xess_upgrade)

        # --- General ---
        self._wayland_switch = switch(self._config.wayland_enabled)
        self._hdr_switch = switch(self._config.hdr_enabled)

        # Switches gated by the selected game's Proton capabilities
        self._capability_switches: list[tuple[ft.Switch, str]] = [
            (self._dlss_upgrade_switch, CAP_DLSS_UPGRADE),
            (self._dlss_indicator_switch, CAP_DLSS_INDICATOR),
            (self._fsr4_upgrade_switch, CAP_FSR4_UPGRADE),
            (self._rdna3_switch, CAP_FSR4_UPGRADE),
            (self._fsr4_indicator_switch, CAP_FSR4_INDICATOR),
            (self._xess_upgrade_switch, CAP_XESS_UPGRADE),
        ]

    # =========================================================================
    # Event handlers
    # =========================================================================

    def _on_preset_changed(self, e):
        self._update_preset_description()
        self._update_command_preview()

    def _on_setting_changed(self, e):
        self._update_command_preview()

    def _on_game_changed(self, e):
        key = self._game_dropdown.value
        if not key or key == _GENERIC_KEY:
            self._tool_info = None
        else:
            tool_name = resolve_tool_for_app(self._compat_mapping, key)
            self._tool_info = classify_compat_tool(tool_name)
        self._apply_capability_gating()
        self._update_game_status()
        self._update_command_preview()
        self._safe_update(self._root)

    # =========================================================================
    # State -> UI sync helpers
    # =========================================================================

    @staticmethod
    def _safe_update(control):
        if control is None:
            return
        try:
            control.update()
        except Exception:
            pass  # Panel may not be attached yet during open animation

    def _active_capabilities(self) -> frozenset[str] | None:
        """Capabilities of the selected game's Proton build (None = generic)."""
        return self._tool_info.capabilities if self._tool_info else None

    def _apply_capability_gating(self):
        """Disable upgrade switches the selected game's Proton doesn't support."""
        caps = self._active_capabilities()
        for sw, cap in self._capability_switches:
            supported = caps is None or cap in caps
            sw.disabled = not supported
            sw.tooltip = (
                None if supported
                else "Not supported by this game's Proton build - use GE-Proton or Proton-CachyOS"
            )

    def _update_game_status(self):
        if not self._game_status:
            return
        if self._tool_info is None:
            self._game_status.value = (
                "Generic mode - options are not validated against a specific "
                "Proton build. The DLL upgrade toggles need GE-Proton or "
                "Proton-CachyOS."
            )
        else:
            tool = self._tool_info
            if not tool.is_proton:
                support = "Native Linux runtime - Proton launch options do not apply."
            elif tool.capabilities:
                support = "Supports the DLL upgrade toggles."
            elif tool.family == "unknown":
                support = (
                    "Unrecognized Proton build - upgrade support unknown, "
                    "upgrade toggles disabled."
                )
            else:
                support = (
                    "No upscaler upgrade support - switch the game to GE-Proton "
                    "or Proton-CachyOS to use the upgrade toggles."
                )
            self._game_status.value = f"Runs under: {tool.display_name}. {support}"

    def _update_gpu_status(self):
        if not self._gpu_status:
            return
        gpu = self._detected_gpu
        if gpu is None:
            self._gpu_status.value = (
                "GPU not detected - showing all vendor sections."
            )
        elif gpu.vendor == "amd":
            gen = {"rdna4": "RDNA4", "rdna3": "RDNA3"}.get(gpu.amd_generation or "", "pre-RDNA3")
            extra = ""
            if gpu.amd_generation == "rdna3":
                extra = " RDNA3 mode is recommended for FSR 4."
            elif gpu.amd_generation == "other":
                extra = " FSR 4 upgrades need RDNA3 or newer."
            self._gpu_status.value = f"Detected GPU: AMD ({gen}).{extra}"
        else:
            self._gpu_status.value = f"Detected GPU: {gpu.vendor.upper()}."

    def _update_section_visibility(self):
        """Show vendor sections for detected GPUs (all when detection failed)."""
        vendors = self._detected_vendors
        show_all = not vendors
        self._nvidia_section.visible = show_all or "nvidia" in vendors
        self._amd_section.visible = show_all or "amd" in vendors
        self._intel_section.visible = show_all or "intel" in vendors

    def _update_preset_description(self):
        if self._preset_desc and self._preset_dropdown.value:
            self._preset_desc.value = get_preset_description(self._preset_dropdown.value)
            self._safe_update(self._preset_desc)

    def _config_from_ui(self) -> LinuxDLSSConfig:
        """Build a LinuxDLSSConfig from the current UI state."""
        return LinuxDLSSConfig(
            selected_preset=self._preset_dropdown.value or "default",
            overlay_enabled=self._overlay_switch.value,
            wayland_enabled=self._wayland_switch.value,
            hdr_enabled=self._hdr_switch.value,
            rr_preset=self._rr_dropdown.value or "default",
            fg_override=self._fg_switch.value,
            dlss_upgrade=self._dlss_upgrade_switch.value,
            dlss_indicator=self._dlss_indicator_switch.value,
            fsr4_upgrade=self._fsr4_upgrade_switch.value,
            fsr4_rdna3_mode=self._rdna3_switch.value,
            fsr4_indicator=self._fsr4_indicator_switch.value,
            xess_upgrade=self._xess_upgrade_switch.value,
        )

    def _update_command_preview(self):
        if not self._command_field:
            return
        command = generate_steam_launch_options(
            self._config_from_ui(), self._active_capabilities()
        )
        self._command_field.value = command
        self._safe_update(self._command_field)

    async def _copy_to_clipboard(self, e):
        if self._command_field and self._command_field.value:
            try:
                await ft.Clipboard().set(self._command_field.value)
                self._show_snackbar("Copied to clipboard!", "#4CAF50")
            except Exception as ex:
                self.logger.warning(f"Failed to copy to clipboard: {ex}")
                self._show_snackbar("Failed to copy to clipboard", "#F44336")

    # =========================================================================
    # Async initialization (runs concurrently with the open animation)
    # =========================================================================

    async def on_open(self):
        """Detect GPUs, load the compat mapping and the Steam games list."""
        try:
            gpus = await detect_linux_gpus()
            self._detected_gpu = pick_primary_gpu(gpus)
            self._detected_vendors = {
                g.vendor for g in gpus if g.vendor in ("nvidia", "amd", "intel")
            }
        except Exception as e:
            self.logger.warning(f"Linux GPU detection failed: {e}")

        try:
            self._compat_mapping = await get_compat_tool_mapping()
        except Exception as e:
            self.logger.warning(f"CompatToolMapping load failed: {e}")

        try:
            from dlss_updater.database import db_manager

            games_by_launcher = await db_manager.get_all_games_by_launcher()
            steam_games = [
                g for g in games_by_launcher.get("Steam", [])
                if g.effective_steam_app_id
            ]
            # Dedupe by app id (multi-path installs), sort for the dropdown
            by_app_id = {g.effective_steam_app_id: g for g in steam_games}
            self._steam_games = sorted(
                by_app_id.values(), key=lambda g: g.display_name.lower()
            )
        except Exception as e:
            self.logger.warning(f"Steam games load failed: {e}")

        # Push async results into the UI
        self._game_dropdown.options = [
            ft.DropdownOption(key=_GENERIC_KEY, text="Generic (any game)")
        ] + [
            ft.DropdownOption(
                key=str(g.effective_steam_app_id), text=g.display_name
            )
            for g in self._steam_games
        ]

        # Recommend RDNA3 mode when an RDNA3 card is the primary GPU and the
        # user hasn't configured FSR4 yet.
        if (
            self._detected_gpu is not None
            and self._detected_gpu.vendor == "amd"
            and self._detected_gpu.amd_generation == "rdna3"
            and not self._config.fsr4_upgrade
            and not self._config.fsr4_rdna3_mode
        ):
            self._rdna3_switch.value = True

        self._update_gpu_status()
        self._update_section_visibility()
        self._update_command_preview()
        self._safe_update(self._root)

    # =========================================================================
    # Build
    # =========================================================================

    def _section_label(self, text: str, is_dark: bool) -> ft.Text:
        label = ft.Text(
            text,
            weight=ft.FontWeight.BOLD,
            size=15,
            color=MD3Colors.get_text_primary(is_dark),
        )
        self._section_labels.append(label)
        return label

    def _divider(self, is_dark: bool) -> ft.Divider:
        divider = ft.Divider(height=20, color=MD3Colors.get_divider(is_dark))
        self._dividers.append(divider)
        return divider

    @staticmethod
    def _option_tile(title: str, subtitle: str, trailing: ft.Control) -> ft.ListTile:
        return ft.ListTile(
            title=ft.Text(title, weight=ft.FontWeight.W_500),
            subtitle=ft.Text(subtitle, size=12),
            trailing=trailing,
        )

    def build(self) -> ft.Control:
        is_dark = self._registry.is_dark
        primary_color = MD3Colors.get_primary(is_dark)
        on_surface_variant = MD3Colors.get_on_surface_variant(is_dark)

        # Info container
        self._info_text = ft.Text(
            "These settings generate launch options for Proton/Wine games. "
            "Paste into Steam's \"Set Launch Options\" per game - the same "
            "environment variables also work in Heroic and Lutris.",
            size=13,
            color=on_surface_variant,
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
            padding=ft.Padding.all(16),
            border_radius=12,
            bgcolor=MD3Colors.get_surface_container(is_dark),
        )

        # Per-game validation section
        self._game_status = ft.Text(
            "",
            size=12,
            color=on_surface_variant,
            italic=True,
        )
        self._gpu_status = ft.Text(
            "Detecting GPU...",
            size=12,
            color=on_surface_variant,
            italic=True,
        )
        self._update_game_status()

        # NVIDIA section
        self._preset_desc = ft.Text(
            get_preset_description(self._config.selected_preset),
            size=12,
            color=on_surface_variant,
            italic=True,
        )
        self._nvidia_section = ft.Container(
            content=ft.Column(
                controls=[
                    self._section_label("NVIDIA - DLSS", is_dark),
                    self._preset_dropdown,
                    self._preset_desc,
                    ft.Container(height=4),
                    self._rr_dropdown,
                    self._option_tile(
                        "Frame Generation Override",
                        "Force the DLSS FG override (DXVK-NVAPI)",
                        self._fg_switch,
                    ),
                    self._option_tile(
                        "DLSS DLL Upgrade",
                        "Auto-download latest DLSS DLLs (GE-Proton/CachyOS)",
                        self._dlss_upgrade_switch,
                    ),
                    self._option_tile(
                        "DLSS Indicator",
                        "Show the fork's DLSS HUD overlay",
                        self._dlss_indicator_switch,
                    ),
                    self._option_tile(
                        "DLSS Debug Overlay",
                        "DXVK-NVAPI DLSS indicator (any Proton)",
                        self._overlay_switch,
                    ),
                ],
                spacing=8,
            ),
        )

        # AMD section
        self._amd_section = ft.Container(
            content=ft.Column(
                controls=[
                    self._section_label("AMD - FSR 4", is_dark),
                    self._option_tile(
                        "FSR 4 Upgrade",
                        "Upgrade FSR 3.1 games to FSR 4 (GE-Proton/CachyOS/EM)",
                        self._fsr4_upgrade_switch,
                    ),
                    self._option_tile(
                        "RDNA3 Mode",
                        "Use the RDNA3 (RX 7000) FSR 4 path instead of RDNA4",
                        self._rdna3_switch,
                    ),
                    self._option_tile(
                        "FSR 4 Indicator",
                        "Show the FSR 4 watermark overlay",
                        self._fsr4_indicator_switch,
                    ),
                ],
                spacing=8,
            ),
        )

        # Intel section
        self._intel_section = ft.Container(
            content=ft.Column(
                controls=[
                    self._section_label("Intel - XeSS", is_dark),
                    self._option_tile(
                        "XeSS Upgrade",
                        "Auto-download latest XeSS DLLs (GE-Proton/CachyOS). "
                        "Note: Arc GPUs run XeSS in DP4a fallback mode on Linux",
                        self._xess_upgrade_switch,
                    ),
                ],
                spacing=8,
            ),
        )

        # General section
        general_section = ft.Column(
            controls=[
                self._section_label("General", is_dark),
                self._option_tile(
                    "Wayland Support",
                    "Enable Proton Wayland mode",
                    self._wayland_switch,
                ),
                self._option_tile(
                    "HDR Support",
                    "Enable HDR for Proton/Wine games",
                    self._hdr_switch,
                ),
            ],
            spacing=8,
        )

        # Command preview
        initial_command = generate_steam_launch_options(self._config)
        self._command_field = ft.TextField(
            value=initial_command,
            read_only=True,
            multiline=True,
            min_lines=2,
            max_lines=5,
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

        self._apply_capability_gating()
        self._update_section_visibility()

        self._root = ft.Column(
            controls=[
                self._info_container,
                ft.Container(height=8),
                self._section_label("Game", is_dark),
                self._game_dropdown,
                self._game_status,
                self._gpu_status,
                self._divider(is_dark),
                self._nvidia_section,
                self._divider(is_dark),
                self._amd_section,
                self._divider(is_dark),
                self._intel_section,
                self._divider(is_dark),
                general_section,
                self._divider(is_dark),
                self._section_label("Steam Launch Options", is_dark),
                command_row,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )
        return self._root

    # =========================================================================
    # Theme / persistence
    # =========================================================================

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        props = {}

        for i, _label in enumerate(self._section_labels):
            props[f"_section_labels[{i}].color"] = MD3Colors.get_themed_pair("text_primary")

        if self._info_text:
            props["_info_text.color"] = MD3Colors.get_themed_pair("on_surface_variant")
        if self._preset_desc:
            props["_preset_desc.color"] = MD3Colors.get_themed_pair("on_surface_variant")
        if self._game_status:
            props["_game_status.color"] = MD3Colors.get_themed_pair("on_surface_variant")
        if self._gpu_status:
            props["_gpu_status.color"] = MD3Colors.get_themed_pair("on_surface_variant")

        if self._info_container:
            props["_info_container.bgcolor"] = MD3Colors.get_themed_pair("surface_container")

        for i, _divider in enumerate(self._dividers):
            props[f"_dividers[{i}].color"] = MD3Colors.get_themed_pair("divider")

        for name in (
            "_fg_switch",
            "_dlss_upgrade_switch",
            "_dlss_indicator_switch",
            "_overlay_switch",
            "_fsr4_upgrade_switch",
            "_rdna3_switch",
            "_fsr4_indicator_switch",
            "_xess_upgrade_switch",
            "_wayland_switch",
            "_hdr_switch",
        ):
            props[f"{name}.active_color"] = MD3Colors.get_themed_pair("primary")

        return props

    def validate(self) -> tuple[bool, str | None]:
        return True, None

    async def on_save(self) -> bool:
        new_config = self._config_from_ui()
        config_manager.save_linux_dlss_config(new_config)

        self.logger.info(
            f"Proton upscaler config saved: sr={new_config.selected_preset}, "
            f"rr={new_config.rr_preset}, fg={new_config.fg_override}, "
            f"dlss_upgrade={new_config.dlss_upgrade}, "
            f"fsr4_upgrade={new_config.fsr4_upgrade} "
            f"(rdna3={new_config.fsr4_rdna3_mode}), "
            f"xess_upgrade={new_config.xess_upgrade}"
        )

        self._show_snackbar("Settings saved successfully")
        return True

    def on_cancel(self):
        """Reload configuration from config to discard unsaved changes."""
        self.logger.debug("Proton upscaler panel cancelled, discarding changes")
        self._load_config()
