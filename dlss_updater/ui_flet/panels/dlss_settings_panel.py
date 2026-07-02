"""
WindowsDLSSPresetsPanel - Global DLSS preset control (Windows).

Writes the chosen presets to the NVIDIA driver base profile via NvAPI DRS - the
same global override the NVIDIA App uses - for all three DLSS components:

    SR  Super Resolution    (named presets J/K/L/M)
    RR  Ray Reconstruction  (Default / Latest model)
    FG  Frame Generation     (Default / Latest model)

Applies to every DLSS title on the system at next launch.

The shared Material 3 panel chrome (info banner, per-feature section blocks,
dividers, caution banner, footnote, themed properties) lives in
:class:`_DLSSPresetPanelBase`; this panel only supplies the global dropdowns and
the global save/cancel behaviour.
"""

import logging

import flet as ft

from dlss_updater.config import config_manager
from dlss_updater.models import (
    WindowsDLSSConfig,
    WindowsDLSSPreset,
    WindowsDLSSModelPreset,
    WindowsDLSSFGPreset,
)
from dlss_updater import nvapi_drs
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.panels.dlss_preset_panel_base import _DLSSPresetPanelBase


class WindowsDLSSPresetsPanel(_DLSSPresetPanelBase):
    """
    Panel for configuring the global Windows DLSS preset overrides (SR/RR/FG).

    Each feature has a dropdown plus a live readout of the value currently in
    the driver base profile (read on open). Save writes all three in a single
    NvAPI DRS transaction.
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        super().__init__(page, logger)

        # Per-feature dropdowns + SR description
        self._sr_dropdown: ft.Dropdown | None = None
        self._rr_dropdown: ft.Dropdown | None = None
        self._fg_dropdown: ft.Dropdown | None = None
        self._sr_desc: ft.Text | None = None

        self._load_config()
        self._build_controls()
        self._register_theme_aware()

    @property
    def title(self) -> str:
        return "DLSS Settings (Experimental)"

    @property
    def subtitle(self) -> str | None:
        return "Global preset overrides (SR / RR / FG)"

    @property
    def width(self) -> int:
        return 520

    def _load_config(self):
        self._config = config_manager.get_windows_dlss_config()

    def _build_controls(self):
        is_dark = self._registry.is_dark
        primary = MD3Colors.get_primary(is_dark)
        outline = MD3Colors.get_outline(is_dark)

        # SR dropdown: full named presets
        self._sr_dropdown = ft.Dropdown(
            options=[
                ft.DropdownOption(key=p.value, text=p.display_name)
                for p in WindowsDLSSPreset
            ],
            value=self._config.selected_preset,
            label="Super Resolution (SR) Preset",
            border_color=outline,
            focused_border_color=primary,
            on_select=self._on_sr_changed,
            expand=True,
        )

        # RR dropdown: Default / Latest model
        self._rr_dropdown = ft.Dropdown(
            options=[
                ft.DropdownOption(key=p.value, text=p.display_name)
                for p in WindowsDLSSModelPreset
            ],
            value=self._config.rr_preset,
            label="Ray Reconstruction (RR) Model",
            border_color=outline,
            focused_border_color=primary,
            expand=True,
        )
        # FG dropdown: Default / Latest / Preset A / Preset B
        self._fg_dropdown = ft.Dropdown(
            options=[
                ft.DropdownOption(key=p.value, text=p.display_name)
                for p in WindowsDLSSFGPreset
            ],
            value=self._config.fg_preset,
            label="Frame Generation (FG) Preset",
            border_color=outline,
            focused_border_color=primary,
            expand=True,
        )

    def _on_sr_changed(self, e):
        if self._sr_desc:
            self._sr_desc.value = self._current_sr().description
            try:
                self._sr_desc.update()
            except Exception:
                pass

    def _current_sr(self) -> WindowsDLSSPreset:
        try:
            return WindowsDLSSPreset(self._sr_dropdown.value or "default")
        except ValueError:
            return WindowsDLSSPreset.DEFAULT

    async def on_open(self):
        """Read live driver state for all three features once shown."""
        if not nvapi_drs.is_available():
            for feat in ("sr", "rr", "fg"):
                self._set_status(feat, "NVIDIA driver not detected", warning=True)
            return

        values, error = await nvapi_drs.get_current_presets()
        if error:
            self.logger.warning(f"Could not read current DLSS presets: {error}")
            for feat in ("sr", "rr", "fg"):
                self._set_status(feat, "Could not read driver state", warning=True)
            return

        for feat in ("sr", "rr", "fg"):
            desc = nvapi_drs.describe_preset_value(values.get(feat))
            self._set_status(feat, f"Currently applied: {desc}")

    def build(self) -> ft.Control:
        is_dark = self._registry.is_dark

        # Info banner (shared chrome)
        info_container = self._info_banner(
            "Force global DLSS presets for every game on this system. These write "
            "to the NVIDIA driver profile (the same settings the NVIDIA App uses) "
            "and apply the next time each game launches."
        )

        # SR section (with live description)
        self._sr_desc = ft.Text(
            self._current_sr().description,
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            italic=True,
        )

        controls: list[ft.Control] = [
            info_container,
            ft.Container(height=8),
            self.section_label("Super Resolution (Upscaling)"),
            *self._feature_block("sr", self._sr_dropdown, is_dark, extra=self._sr_desc),
            self.divider(),
            self.section_label("Ray Reconstruction (Denoising)"),
            *self._feature_block("rr", self._rr_dropdown, is_dark),
            self.divider(),
            self.section_label("Frame Generation"),
            *self._feature_block("fg", self._fg_dropdown, is_dark),
            self.divider(),
        ]

        # NVIDIA App interaction caution - the App writes PER-GAME overrides that
        # win over this global setting. Verified live: the NVIDIA App stamps its
        # DLSS override into hundreds of individual game profiles, not just the
        # global base profile this panel writes.
        controls.append(
            self._caution_banner(
                "Using the NVIDIA App's DLSS override? It writes per-game "
                "overrides that take priority over this global setting for "
                "those games. To let this control them, set the DLSS override "
                "to Default/Off in the NVIDIA App."
            )
        )

        controls.append(
            self._footnote(
                "'Default' clears that global override. RR exposes Default/Latest and FG "
                "adds Presets A/B (higher lettered model variants are undocumented and "
                "hidden); the status line shows whatever is currently applied. Restart "
                "games to apply changes."
            )
        )

        return ft.Column(controls=controls, spacing=8, scroll=ft.ScrollMode.AUTO)

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        props = super().get_themed_properties()
        if self._sr_desc:
            props["_sr_desc.color"] = MD3Colors.get_themed_pair("on_surface_variant")
        return props

    def validate(self) -> tuple[bool, str | None]:
        return True, None

    def _selections(self) -> dict[str, str]:
        return {
            "sr": self._sr_dropdown.value or "default",
            "rr": self._rr_dropdown.value or "default",
            "fg": self._fg_dropdown.value or "default",
        }

    async def on_save(self) -> bool:
        """Apply all three presets to the driver and persist the choices."""
        if not nvapi_drs.is_available():
            self._show_error_dialog(
                "Not Available",
                "DLSS preset control requires an NVIDIA driver on Windows.",
            )
            return False

        selections = self._selections()
        success, error = await nvapi_drs.apply_presets(selections)
        if not success:
            self.logger.error(f"Failed to apply DLSS presets: {error}")
            self._show_error_dialog(
                "Could Not Apply Presets",
                error or "Unknown error writing NVIDIA driver settings.",
            )
            return False

        config_manager.save_windows_dlss_config(
            WindowsDLSSConfig(
                selected_preset=selections["sr"],
                rr_preset=selections["rr"],
                fg_preset=selections["fg"],
            )
        )
        self.logger.info(f"Windows DLSS global presets applied: {selections}")
        self._show_snackbar(
            "DLSS presets applied. Restart games to see changes.",
            bgcolor="#4CAF50",
        )
        return True

    def on_cancel(self):
        self.logger.debug("Windows DLSS presets panel cancelled, discarding changes")
        self._load_config()
