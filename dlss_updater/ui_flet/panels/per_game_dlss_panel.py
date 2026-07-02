"""
PerGameDLSSPanel - per-game DLSS preset override (Windows).

Scoped variant of the global :class:`WindowsDLSSPresetsPanel`. Writes SR/RR/FG
preset selections to the NVIDIA driver's *per-application* profile for a single
game (via ``nvapi_drs.apply_presets_for_app``), which takes priority over the
global base-profile override for that game.

Flow:
    on_open  -> load saved db row -> resolve the game's exe (db cache preferred)
                -> if no exe, show the picker state; else show the exe row
                -> pre-select dropdowns from the db row
                -> read the live driver profile to populate "Currently applied"
    on_save  -> require an exe; apply to the driver FIRST; only persist to db on
                success
    reset    -> clear the driver overrides, delete the db row, reset dropdowns

Shares all Material 3 chrome with the global panel via
:class:`_DLSSPresetPanelBase`.
"""

import logging
import os

import flet as ft

from dlss_updater.models import (
    Game,
    WindowsDLSSPreset,
    WindowsDLSSModelPreset,
    WindowsDLSSFGPreset,
)
from dlss_updater import nvapi_drs
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.panels.dlss_preset_panel_base import _DLSSPresetPanelBase

# Backend module built in parallel; guard so this module always imports.
try:
    from dlss_updater import exe_resolver  # type: ignore
except Exception:  # pragma: no cover - backend not present yet
    exe_resolver = None  # type: ignore

# Sources that represent a best-effort guess rather than a cached/user choice.
_GUESS_SOURCES = frozenset({"heuristic", "driver", "steam_manifest"})


class PerGameDLSSPanel(_DLSSPresetPanelBase):
    """Per-game DLSS preset override panel for a single :class:`Game`."""

    def __init__(self, page: ft.Page, logger: logging.Logger, game: Game, db_manager):
        super().__init__(page, logger)

        self.game = game
        self.db_manager = db_manager

        # Resolved executable state
        self._exe_path: str | None = None
        self._exe_name: str | None = None
        self._exe_source: str | None = None
        self._exe_is_guess: bool = False
        self._profile_name: str | None = None

        # Per-feature dropdowns + SR description
        self._sr_dropdown: ft.Dropdown | None = None
        self._rr_dropdown: ft.Dropdown | None = None
        self._fg_dropdown: ft.Dropdown | None = None
        self._sr_desc: ft.Text | None = None

        # Exe-row UI references (rebuilt as resolution state changes)
        self._exe_row: ft.Container | None = None
        self._exe_path_text: ft.Text | None = None
        self._exe_status_icon: ft.Icon | None = None
        self._exe_auto_tag: ft.Container | None = None

        self._build_controls()
        self._register_theme_aware()

    # ------------------------------------------------------------------ #
    # PanelContentBase metadata
    # ------------------------------------------------------------------ #
    @property
    def title(self) -> str:
        return self.game.display_name

    @property
    def subtitle(self) -> str | None:
        return "Per-game DLSS preset override"

    @property
    def width(self) -> int:
        return 520

    # ------------------------------------------------------------------ #
    # Dropdowns
    # ------------------------------------------------------------------ #
    def _build_controls(self):
        is_dark = self._registry.is_dark
        primary = MD3Colors.get_primary(is_dark)
        outline = MD3Colors.get_outline(is_dark)

        self._sr_dropdown = ft.Dropdown(
            options=[
                ft.DropdownOption(key=p.value, text=p.display_name)
                for p in WindowsDLSSPreset
            ],
            value="default",
            label="Super Resolution (SR) Preset",
            border_color=outline,
            focused_border_color=primary,
            on_select=self._on_sr_changed,
            expand=True,
        )
        self._rr_dropdown = ft.Dropdown(
            options=[
                ft.DropdownOption(key=p.value, text=p.display_name)
                for p in WindowsDLSSModelPreset
            ],
            value="default",
            label="Ray Reconstruction (RR) Model",
            border_color=outline,
            focused_border_color=primary,
            expand=True,
        )
        self._fg_dropdown = ft.Dropdown(
            options=[
                ft.DropdownOption(key=p.value, text=p.display_name)
                for p in WindowsDLSSFGPreset
            ],
            value="default",
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

    # ------------------------------------------------------------------ #
    # Executable row
    # ------------------------------------------------------------------ #
    def _build_exe_row(self) -> ft.Container:
        """Build the detected-executable row (mutated in place by _render_exe_state)."""
        is_dark = self._registry.is_dark

        self._exe_status_icon = ft.Icon(
            ft.Icons.HELP_OUTLINE,
            size=20,
            color=MD3Colors.get_text_secondary(is_dark),
        )
        self._exe_path_text = ft.Text(
            "Resolving executable…",
            size=12,
            color=MD3Colors.get_text_secondary(is_dark),
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            selectable=True,
            expand=True,
        )
        # "(auto-detected)" tag - hidden until we know the source is a guess.
        self._exe_auto_tag = ft.Container(
            content=ft.Text(
                "auto-detected",
                size=10,
                color=ft.Colors.WHITE,
                weight=ft.FontWeight.W_500,
            ),
            bgcolor=MD3Colors.get_text_secondary(is_dark),
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            border_radius=10,
            visible=False,
        )
        self._change_exe_button = ft.TextButton(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.FOLDER_OPEN, size=16),
                    ft.Text("Change executable", size=12),
                ],
                spacing=6,
                tight=True,
            ),
            on_click=lambda e: self._page_ref.run_task(self._pick_exe),
        )

        self._exe_row = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self._exe_status_icon,
                            ft.Column(
                                controls=[
                                    ft.Row(
                                        controls=[
                                            ft.Text(
                                                "Executable",
                                                size=13,
                                                weight=ft.FontWeight.BOLD,
                                                color=MD3Colors.get_text_primary(is_dark),
                                            ),
                                            self._exe_auto_tag,
                                        ],
                                        spacing=8,
                                        tight=True,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    self._exe_path_text,
                                ],
                                spacing=2,
                                expand=True,
                                tight=True,
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    ft.Row(
                        controls=[self._change_exe_button],
                        alignment=ft.MainAxisAlignment.END,
                    ),
                ],
                spacing=4,
                tight=True,
            ),
            padding=ft.Padding.all(12),
            border_radius=12,
            bgcolor=MD3Colors.get_surface_container(is_dark),
        )
        return self._exe_row

    def _render_exe_state(self):
        """Update the exe row to reflect the current resolution state."""
        if not self._exe_path_text or not self._exe_status_icon:
            return
        is_dark = self._registry.is_dark

        if self._exe_path:
            self._exe_status_icon.name = ft.Icons.CHECK_CIRCLE
            self._exe_status_icon.color = MD3Colors.get_success(is_dark)
            self._exe_path_text.value = self._exe_path
            self._exe_path_text.color = MD3Colors.get_text_secondary(is_dark)
            self._exe_path_text.tooltip = self._exe_path
            if self._exe_auto_tag:
                self._exe_auto_tag.visible = self._exe_is_guess
        else:
            self._exe_status_icon.name = ft.Icons.ERROR_OUTLINE
            self._exe_status_icon.color = MD3Colors.get_warning(is_dark)
            self._exe_path_text.value = (
                "Couldn't detect the game's executable — choose it with "
                "“Change executable”."
            )
            self._exe_path_text.color = MD3Colors.get_warning(is_dark)
            self._exe_path_text.tooltip = None
            if self._exe_auto_tag:
                self._exe_auto_tag.visible = False

        for ctrl in (self._exe_row, self._exe_status_icon, self._exe_path_text, self._exe_auto_tag):
            try:
                if ctrl is not None:
                    ctrl.update()
            except Exception:
                pass

    async def _pick_exe(self):
        """Open an inline FilePicker to let the user choose the game's exe."""
        initial_dir = None
        try:
            if self.game.path and os.path.isdir(self.game.path):
                initial_dir = self.game.path
            elif self.game.path:
                initial_dir = os.path.dirname(self.game.path)
        except Exception:
            initial_dir = None

        try:
            files = await ft.FilePicker().pick_files(
                dialog_title=f"Select the executable for {self.game.display_name}",
                initial_directory=initial_dir,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["exe"],
                allow_multiple=False,
            )
        except Exception as ex:
            self.logger.warning(f"File picker failed: {ex}")
            return

        if not files:
            return
        picked = files[0]
        path = getattr(picked, "path", None)
        if not path:
            return

        self._exe_path = path
        self._exe_name = os.path.basename(path)
        self._exe_source = "user"
        self._exe_is_guess = False
        self._render_exe_state()

        # Re-read the live driver state for the newly chosen exe.
        await self._refresh_live_status()

    # ------------------------------------------------------------------ #
    # Open / load
    # ------------------------------------------------------------------ #
    async def on_open(self):
        """Load saved presets, resolve the exe, and read live driver state."""
        # 1. Load saved db row (pre-select dropdowns + cached exe).
        saved = None
        try:
            saved = await self.db_manager.get_game_dlss_presets(self.game.id)
        except Exception as ex:
            self.logger.warning(f"Could not load saved per-game presets: {ex}")

        if saved is not None:
            self._apply_saved_to_dropdowns(saved)
            self._profile_name = getattr(saved, "profile_name", None)

        # 2. Resolve the exe (resolver prefers the db cache).
        await self._resolve_exe(saved)

        # 3. Read live driver state for "Currently applied" lines.
        await self._refresh_live_status()

    def _apply_saved_to_dropdowns(self, saved):
        for dd, attr in (
            (self._sr_dropdown, "sr"),
            (self._rr_dropdown, "rr"),
            (self._fg_dropdown, "fg"),
        ):
            val = getattr(saved, attr, None)
            if dd is not None and val:
                dd.value = val
                try:
                    dd.update()
                except Exception:
                    pass
        if self._sr_desc:
            self._sr_desc.value = self._current_sr().description
            try:
                self._sr_desc.update()
            except Exception:
                pass

    async def _resolve_exe(self, saved):
        """Populate exe state via the resolver, falling back to the saved cache."""
        resolution = None
        if exe_resolver is not None:
            try:
                resolution = await exe_resolver.resolve_game_exe(self.game, self.db_manager)
            except Exception as ex:
                self.logger.warning(f"Exe resolution failed: {ex}")

        if resolution is not None and getattr(resolution, "exe_path", None):
            self._exe_path = resolution.exe_path
            self._exe_name = getattr(resolution, "exe_name", None) or (
                os.path.basename(resolution.exe_path) if resolution.exe_path else None
            )
            self._exe_source = getattr(resolution, "source", None)
            self._exe_is_guess = self._exe_source in _GUESS_SOURCES
        elif saved is not None and getattr(saved, "exe_path", None):
            # Resolver unavailable/empty but we have a cached exe from the db row.
            self._exe_path = saved.exe_path
            self._exe_name = getattr(saved, "exe_name", None) or os.path.basename(saved.exe_path)
            self._exe_source = "cache"
            self._exe_is_guess = False
        else:
            self._exe_path = None
            self._exe_name = None
            self._exe_source = "none"
            self._exe_is_guess = False

        self._render_exe_state()

    async def _refresh_live_status(self):
        """Read the NVIDIA driver profile for the resolved exe and update status."""
        if not nvapi_drs.is_available():
            for feat in ("sr", "rr", "fg"):
                self._set_status(feat, "NVIDIA driver not detected", warning=True)
            return

        if not self._exe_path:
            for feat in ("sr", "rr", "fg"):
                self._set_status(
                    feat, "Choose the executable to read driver state", neutral=True
                )
            return

        values, meta, error = await nvapi_drs.get_presets_for_app(self._exe_path)
        if error:
            self.logger.warning(f"Could not read per-game DLSS presets: {error}")
            for feat in ("sr", "rr", "fg"):
                self._set_status(feat, "Could not read driver state", warning=True)
            return

        self._profile_name = meta.get("profile_name") or self._profile_name

        if not meta.get("found", False):
            # No driver profile maps this exe yet — created on first save.
            for feat in ("sr", "rr", "fg"):
                self._set_status(
                    feat, "No driver profile yet (created on save)", neutral=True
                )
            return

        predefined = meta.get("predefined", {}) or {}
        for feat in ("sr", "rr", "fg"):
            desc = nvapi_drs.describe_preset_value(values.get(feat))
            if predefined.get(feat, False):
                # Value comes from NVIDIA's predefined profile, not our override.
                self._set_status(
                    feat, f"Currently applied: {desc} (NVIDIA default)", neutral=True
                )
            else:
                self._set_status(feat, f"Currently applied: {desc}")

    # ------------------------------------------------------------------ #
    # Build
    # ------------------------------------------------------------------ #
    def build(self) -> ft.Control:
        is_dark = self._registry.is_dark

        info_container = self._info_banner(
            f"Force DLSS presets just for {self.game.display_name}. These write to "
            "the NVIDIA driver's per-application profile for this game and apply the "
            "next time it launches."
        )

        exe_row = self._build_exe_row()

        self._sr_desc = ft.Text(
            self._current_sr().description,
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            italic=True,
        )

        controls: list[ft.Control] = [
            info_container,
            ft.Container(height=4),
            exe_row,
            ft.Container(height=4),
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

        # Priority note is INVERTED vs the global panel.
        controls.append(
            self._caution_banner(
                "This per-game override takes priority over your global DLSS "
                "Settings for this game. Note: if the NVIDIA App has its own DLSS "
                "override active for this game, it may still take precedence — set "
                "the App's override to Default/Off to let this control apply."
            )
        )

        controls.append(
            self._footnote(
                "'Default' reverts that feature to the profile's predefined value "
                "(or removes the override). The status line shows whatever is "
                "currently applied. Restart the game to apply changes."
            )
        )

        # Reset-to-default action.
        self._reset_button = ft.OutlinedButton(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.RESTART_ALT, size=16),
                    ft.Text("Reset to default", size=13),
                ],
                spacing=6,
                tight=True,
            ),
            on_click=lambda e: self._page_ref.run_task(self._on_reset),
        )
        controls.append(ft.Container(height=4))
        controls.append(
            ft.Row(controls=[self._reset_button], alignment=ft.MainAxisAlignment.START)
        )

        return ft.Column(controls=controls, spacing=8, scroll=ft.ScrollMode.AUTO)

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        props = super().get_themed_properties()
        if self._sr_desc:
            props["_sr_desc.color"] = MD3Colors.get_themed_pair("on_surface_variant")
        if self._exe_row:
            props["_exe_row.bgcolor"] = MD3Colors.get_themed_pair("surface_container")
        return props

    # ------------------------------------------------------------------ #
    # Save / reset
    # ------------------------------------------------------------------ #
    def validate(self) -> tuple[bool, str | None]:
        if not self._exe_path:
            return False, "Choose the game's executable before saving."
        return True, None

    def _selections(self) -> dict[str, str]:
        return {
            "sr": self._sr_dropdown.value or "default",
            "rr": self._rr_dropdown.value or "default",
            "fg": self._fg_dropdown.value or "default",
        }

    async def on_save(self) -> bool:
        """Apply to the driver FIRST, then persist to the db only on success."""
        if not nvapi_drs.is_available():
            self._show_error_dialog(
                "Not Available",
                "DLSS preset control requires an NVIDIA driver on Windows.",
            )
            return False

        if not self._exe_path:
            self._show_error_dialog(
                "No Executable Selected",
                "Choose the game's executable before saving per-game presets.",
            )
            return False

        selections = self._selections()

        # 1. Apply to the NVIDIA driver per-app profile first.
        success, error = await nvapi_drs.apply_presets_for_app(
            self._exe_path, selections, self.game.display_name
        )
        if not success:
            self.logger.error(f"Failed to apply per-game DLSS presets: {error}")
            self._show_error_dialog(
                "Could Not Apply Presets",
                error or "Unknown error writing NVIDIA driver settings.",
            )
            return False

        # 2. Persist only after a successful apply.
        try:
            await self.db_manager.save_game_dlss_presets(
                self.game.id,
                self._exe_name or os.path.basename(self._exe_path),
                self._exe_path,
                selections["sr"],
                selections["rr"],
                selections["fg"],
                self._profile_name,
            )
        except Exception as ex:
            self.logger.error(f"Driver applied but DB persist failed: {ex}")
            self._show_error_dialog(
                "Saved to Driver Only",
                "The presets were applied to the NVIDIA driver, but saving them "
                "for next time failed. They will still take effect now.",
            )
            return True

        self.logger.info(
            f"Per-game DLSS presets applied for {self.game.display_name}: {selections}"
        )
        self._show_snackbar(
            "Applied — restart the game to see changes.",
            bgcolor="#4CAF50",
        )
        return True

    async def _on_reset(self):
        """Clear driver overrides, delete the db row, and reset the dropdowns."""
        if not self._exe_path:
            # Nothing applied; just reset the UI selections.
            self._reset_dropdowns()
            return

        if nvapi_drs.is_available():
            success, error = await nvapi_drs.reset_app_presets(self._exe_path)
            if not success:
                self.logger.error(f"Failed to reset per-game DLSS presets: {error}")
                self._show_error_dialog(
                    "Could Not Reset",
                    error or "Unknown error clearing NVIDIA driver settings.",
                )
                return

        try:
            await self.db_manager.delete_game_dlss_presets(self.game.id)
        except Exception as ex:
            self.logger.warning(f"Could not delete saved per-game presets: {ex}")

        self._reset_dropdowns()
        self._profile_name = None
        self.logger.info(f"Per-game DLSS presets reset for {self.game.display_name}")
        self._show_snackbar(
            "Per-game override cleared. Restart the game to see changes.",
            bgcolor="#2D6E88",
        )

        # Re-read live status to reflect the cleared state.
        await self._refresh_live_status()

    def _reset_dropdowns(self):
        for dd in (self._sr_dropdown, self._rr_dropdown, self._fg_dropdown):
            if dd is not None:
                dd.value = "default"
                try:
                    dd.update()
                except Exception:
                    pass
        if self._sr_desc:
            self._sr_desc.value = self._current_sr().description
            try:
                self._sr_desc.update()
            except Exception:
                pass

    def on_cancel(self):
        self.logger.debug("Per-game DLSS presets panel cancelled, discarding changes")
