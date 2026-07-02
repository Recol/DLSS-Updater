"""
_DLSSPresetPanelBase - shared chrome for DLSS preset control panels.

Both the global :class:`WindowsDLSSPresetsPanel` and the per-game
:class:`PerGameDLSSPanel` render the same Material 3 panel grammar:

    - an info banner (icon + descriptive text)
    - one section per DLSS feature (SR / RR / FG): a bold section label, a
      dropdown, an optional italic description, and a live status line
    - dividers between sections
    - a caution banner slot + a footnote slot

This base extracts that shared chrome so each concrete panel only supplies the
feature dropdowns, the banner copy, and the save/reset behaviour. The global
panel's appearance and behaviour are unchanged after the refactor.
"""

import logging

import flet as ft

from dlss_updater.ui_flet.components.slide_panel import PanelContentBase
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors


class _DLSSPresetPanelBase(ThemeAwareMixin, PanelContentBase):
    """Shared base for DLSS preset panels (global + per-game).

    Subclasses are expected to:
      - build their feature dropdowns before calling the section/feature
        helpers in ``build()``
      - populate ``self._status`` via :meth:`_feature_block`
      - call :meth:`_info_banner` / :meth:`_caution_banner` / :meth:`_footnote`
        to construct the shared chrome and register the themed references
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        super().__init__(page, logger)

        self._registry = get_theme_registry()
        self._theme_priority = 60

        # Themed element references (shared chrome)
        self._info_container: ft.Container | None = None
        self._info_text: ft.Text | None = None
        self._note_text: ft.Text | None = None
        self._caution_container: ft.Container | None = None
        self._caution_text: ft.Text | None = None
        self._dividers: list[ft.Divider] = []
        self._section_labels: list[ft.Text] = []

        # Per-feature live status lines, keyed by feature ("sr"/"rr"/"fg"/...)
        self._status: dict[str, ft.Text] = {}

    # ------------------------------------------------------------------ #
    # Shared chrome builders
    # ------------------------------------------------------------------ #
    def _info_banner(self, text: str) -> ft.Container:
        """Build the top info banner (icon + descriptive text) and store refs."""
        is_dark = self._registry.is_dark
        primary = MD3Colors.get_primary(is_dark)

        self._info_text = ft.Text(
            text,
            size=13,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )
        self._info_container = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=20, color=primary),
                    ft.Container(content=self._info_text, expand=True),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=ft.Padding.all(16),
            border_radius=12,
            bgcolor=MD3Colors.get_surface_container(is_dark),
        )
        return self._info_container

    def section_label(self, text: str) -> ft.Text:
        """Build a bold section label and register it for theming."""
        is_dark = self._registry.is_dark
        lbl = ft.Text(
            text,
            weight=ft.FontWeight.BOLD,
            size=15,
            color=MD3Colors.get_text_primary(is_dark),
        )
        self._section_labels.append(lbl)
        return lbl

    def divider(self) -> ft.Divider:
        """Build a divider and register it for theming."""
        is_dark = self._registry.is_dark
        d = ft.Divider(height=20, color=MD3Colors.get_divider(is_dark))
        self._dividers.append(d)
        return d

    def _feature_block(
        self,
        feature: str,
        dropdown: ft.Dropdown,
        is_dark: bool,
        extra: ft.Control | None = None,
    ) -> list[ft.Control]:
        """Build the controls for one feature: dropdown, optional description,
        and a live status line (registered in ``self._status``)."""
        status = ft.Text(
            "Reading current state…",
            size=12,
            color=MD3Colors.get_text_secondary(is_dark),
        )
        self._status[feature] = status
        controls: list[ft.Control] = [dropdown]
        if extra is not None:
            controls.append(extra)
        controls.append(status)
        return controls

    def _caution_banner(self, text: str) -> ft.Container:
        """Build a warning caution banner (icon + text) and store refs."""
        is_dark = self._registry.is_dark
        self._caution_text = ft.Text(
            text,
            size=11,
            color=MD3Colors.get_text_secondary(is_dark),
        )
        self._caution_container = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.WARNING_AMBER,
                        size=18,
                        color=MD3Colors.get_warning(is_dark),
                    ),
                    ft.Container(content=self._caution_text, expand=True),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=ft.Padding.all(12),
            border_radius=8,
            bgcolor=MD3Colors.get_surface_container(is_dark),
        )
        return self._caution_container

    def _footnote(self, text: str) -> ft.Text:
        """Build the italic warning-colored footnote and store the ref."""
        is_dark = self._registry.is_dark
        self._note_text = ft.Text(
            text,
            size=11,
            color=MD3Colors.get_warning(is_dark),
            italic=True,
        )
        return self._note_text

    # ------------------------------------------------------------------ #
    # Status helper (with neutral style for "NVIDIA default")
    # ------------------------------------------------------------------ #
    def _set_status(
        self,
        feature: str,
        text: str,
        warning: bool = False,
        neutral: bool = False,
    ):
        """Update a feature's live status line.

        Styles:
          - default: success color (an override is applied / readable)
          - warning=True: warning color (could not read / unavailable)
          - neutral=True: muted secondary color (e.g. "(NVIDIA default)")
        """
        label = self._status.get(feature)
        if not label:
            return
        is_dark = self._registry.is_dark
        label.value = text
        if warning:
            label.color = MD3Colors.get_warning(is_dark)
        elif neutral:
            label.color = MD3Colors.get_text_secondary(is_dark)
        else:
            label.color = MD3Colors.get_success(is_dark)
        try:
            label.update()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Shared themed properties (concrete panels extend this)
    # ------------------------------------------------------------------ #
    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        props: dict[str, tuple[str, str]] = {}
        for i, lbl in enumerate(self._section_labels):
            props[f"_section_labels[{i}].color"] = MD3Colors.get_themed_pair("text_primary")
        if self._info_text:
            props["_info_text.color"] = MD3Colors.get_themed_pair("on_surface_variant")
        if self._info_container:
            props["_info_container.bgcolor"] = MD3Colors.get_themed_pair("surface_container")
        if self._caution_container:
            props["_caution_container.bgcolor"] = MD3Colors.get_themed_pair("surface_container")
        if self._caution_text:
            props["_caution_text.color"] = MD3Colors.get_themed_pair("text_secondary")
        for i, d in enumerate(self._dividers):
            props[f"_dividers[{i}].color"] = MD3Colors.get_themed_pair("divider")
        return props
