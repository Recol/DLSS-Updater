"""
Steam API Configuration Card

Polished collapsible card for configuring Steam Web API credentials.
Styled to match the application's card-based design language with:
- Left accent bar in Steam brand blue
- Surface background with shadow elevation
- Status pill badge (green=connected, muted=unconfigured)
- Themed TextField and button styling
- Hover effects with shadow elevation changes

Uses ExpansionTile (native Flutter) for collapse/expand animation.
"""

import asyncio
import webbrowser

import flet as ft

from dlss_updater.logger import setup_logger
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry
from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows, Animations

logger = setup_logger()

STEAM_API_KEY_URL = "https://steamcommunity.com/dev/apikey"

# Steam brand colors for accent bar
STEAM_ACCENT_DARK = "#1b2838"
STEAM_ACCENT_LIGHT = "#2a475e"


class SteamAPICard(ThemeAwareMixin, ft.Container):
    """Collapsible Steam API configuration card for Games View header.

    Styled to match HubCard design language with left accent bar,
    surface background, shadow elevation, and status pill badge.

    Provides:
    - API key input with validation
    - Auto-detected Steam ID display
    - Re-resolution trigger with progress
    - Link to get a free API key
    """

    _theme_priority = 15  # Same tier as search bar and floating pill

    def __init__(
        self,
        page: ft.Page,
        on_reresolution_complete=None,
    ):
        super().__init__()
        self._page_ref = page
        self._on_reresolution_complete = on_reresolution_complete
        self._registry = get_theme_registry()

        # State
        self._is_validating = False
        self._is_reresolving = False
        self._api_key_valid: bool | None = None
        self._auto_steam_id: str | None = None
        self._games_to_improve: int = 0

        self._build_ui()
        self._register_theme_aware()

    def _get_is_dark(self) -> bool:
        """Get current theme state."""
        if self._page_ref and hasattr(self._page_ref, 'theme_mode'):
            return self._page_ref.theme_mode != ft.ThemeMode.LIGHT
        return True

    def _get_accent(self, is_dark: bool) -> str:
        """Get Steam accent color for current theme."""
        return STEAM_ACCENT_DARK if is_dark else STEAM_ACCENT_LIGHT

    def _safe_update(self):
        """Safely update control -- handles cases where control is not yet added."""
        try:
            self.update()
        except (RuntimeError, Exception):
            pass

    # ------------------------------------------------------------------ #
    #  UI Building
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        from dlss_updater.config import config_manager

        is_dark = self._get_is_dark()
        accent = self._get_accent(is_dark)
        existing_key = config_manager.get_steam_api_key()
        existing_id = config_manager.get_steam_id()

        # Determine initial status
        if existing_key:
            self._api_key_valid = True

        # -- Status pill badge ------------------------------------------------
        self.status_badge = self._create_status_badge(is_dark)

        # -- Title row for ExpansionTile --------------------------------------
        self._title_text = ft.Text(
            "Steam API",
            size=15,
            weight=ft.FontWeight.W_600,
            color=MD3Colors.get_on_surface(is_dark),
        )

        # -- Leading icon (Steam-branded circle) ------------------------------
        self._leading_icon = ft.Container(
            content=ft.Icon(
                ft.Icons.VPN_KEY_ROUNDED,
                size=20,
                color=ft.Colors.WHITE,
            ),
            width=36,
            height=36,
            border_radius=18,
            bgcolor=accent,
            alignment=ft.Alignment.CENTER,
        )

        # -- Description text -------------------------------------------------
        self._description_text = ft.Text(
            "Connect your Steam account for more accurate game image resolution. "
            "Uses your owned games list to match app IDs precisely. "
            "When registering for a key, the domain name field can be anything (e.g. localhost).",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )

        # -- API key link button ----------------------------------------------
        self._api_link_button = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.OPEN_IN_NEW,
                        size=14,
                        color=MD3Colors.get_primary(is_dark),
                    ),
                    ft.Text(
                        "Get a free API key",
                        size=12,
                        weight=ft.FontWeight.W_500,
                        color=MD3Colors.get_primary(is_dark),
                    ),
                ],
                spacing=6,
                tight=True,
            ),
            on_click=lambda _: webbrowser.open(STEAM_API_KEY_URL),
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.08, MD3Colors.get_primary(is_dark)),
            ink=True,
            tooltip="Opens steamcommunity.com/dev/apikey",
        )

        # -- API key text field -----------------------------------------------
        self.api_key_field = ft.TextField(
            label="Steam API Key",
            password=True,
            can_reveal_password=True,
            value=existing_key,
            hint_text="Paste your API key here",
            expand=True,
            text_size=13,
            height=48,
            border_radius=8,
            border_color=MD3Colors.get_outline(is_dark),
            focused_border_color=MD3Colors.get_primary(is_dark),
            bgcolor=MD3Colors.get_surface(is_dark),
            content_padding=ft.padding.symmetric(horizontal=14, vertical=8),
            text_style=ft.TextStyle(
                color=MD3Colors.get_on_surface(is_dark),
            ),
            hint_style=ft.TextStyle(
                color=MD3Colors.get_on_surface_variant(is_dark),
            ),
            label_style=ft.TextStyle(
                color=MD3Colors.get_on_surface_variant(is_dark),
                size=12,
            ),
        )

        # -- Save button (filled primary style) -------------------------------
        self.validate_button = ft.FilledButton(
            content="Save",
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
            on_click=self._on_validate_clicked,
            height=42,
            style=ft.ButtonStyle(
                bgcolor=MD3Colors.get_primary(is_dark),
                color=ft.Colors.WHITE,
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=20, vertical=0),
            ),
        )

        # -- Progress ring ----------------------------------------------------
        self.progress_ring = ft.ProgressRing(
            width=18,
            height=18,
            stroke_width=2,
            visible=False,
            color=MD3Colors.get_primary(is_dark),
        )

        # -- Steam ID display (inline, formatted) -----------------------------
        self._steam_id_icon = ft.Icon(
            ft.Icons.PERSON_OUTLINE,
            size=14,
            color=MD3Colors.get_on_surface_variant(is_dark),
        )
        self.steam_id_text = ft.Text(
            existing_id if existing_id else "Detecting...",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            weight=ft.FontWeight.W_500,
        )
        self._steam_id_label = ft.Text(
            "Steam ID",
            size=11,
            color=MD3Colors.get_on_surface_variant(is_dark),
            weight=ft.FontWeight.W_400,
        )
        self._steam_id_container = ft.Container(
            content=ft.Row(
                [
                    self._steam_id_icon,
                    self._steam_id_label,
                    ft.Container(
                        content=self.steam_id_text,
                        bgcolor=ft.Colors.with_opacity(0.08, MD3Colors.get_on_surface_variant(is_dark)),
                        padding=ft.padding.symmetric(horizontal=8, vertical=2),
                        border_radius=4,
                    ),
                ],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            visible=bool(existing_id),
        )

        # -- Re-resolve button (accent tonal style) ---------------------------
        self.reresolution_button = ft.FilledTonalButton(
            content="Re-resolve Games",
            icon=ft.Icons.AUTO_FIX_HIGH,
            on_click=self._on_reresolve_clicked,
            visible=bool(existing_key),
            height=38,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.with_opacity(0.12, MD3Colors.get_primary(is_dark)),
                color=MD3Colors.get_primary(is_dark),
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=16, vertical=0),
            ),
        )

        # -- Improvement status text ------------------------------------------
        self.improve_text = ft.Text(
            "",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            visible=False,
        )

        # ------------------------------------------------------------------ #
        #  Layout composition
        # ------------------------------------------------------------------ #

        # Input row: field + save button + progress ring
        input_row = ft.Row(
            [
                self.api_key_field,
                self.validate_button,
                self.progress_ring,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Actions row: re-resolve + improvement text
        actions_row = ft.Row(
            [
                self.reresolution_button,
                self.improve_text,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Divider between description area and input area
        self._divider = ft.Container(
            height=1,
            bgcolor=MD3Colors.get_themed("divider", is_dark),
        )

        # Main expanded content
        content_column = ft.Column(
            [
                self._description_text,
                ft.Container(height=2),
                self._api_link_button,
                ft.Container(height=8),
                self._divider,
                ft.Container(height=10),
                input_row,
                ft.Container(height=6),
                self._steam_id_container,
                ft.Container(height=6),
                actions_row,
            ],
            spacing=0,
        )

        # -- ExpansionTile wrapper -------------------------------------------
        self.expansion_tile = ft.ExpansionTile(
            title=self._title_text,
            leading=self._leading_icon,
            trailing=ft.Row(
                [self.status_badge],
                spacing=8,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            controls=[
                ft.Container(
                    content=content_column,
                    padding=ft.padding.only(left=16, right=16, bottom=14, top=4),
                ),
            ],
            expanded=False,
            maintain_state=True,
            bgcolor=ft.Colors.TRANSPARENT,
            collapsed_bgcolor=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=12),
            tile_padding=ft.padding.symmetric(horizontal=12, vertical=6),
        )

        # -- Outer container styling (matches HubCard pattern) ----------------
        self.content = self.expansion_tile
        self.padding = 0
        self.border_radius = 12
        self.bgcolor = MD3Colors.get_surface(is_dark)
        self.shadow = Shadows.LEVEL_1
        self.border = ft.border.only(left=ft.BorderSide(3, accent))
        self.animate = Animations.HOVER
        self.on_hover = self._on_hover

    def _create_status_badge(self, is_dark: bool) -> ft.Container:
        """Create the status pill badge (green=connected, muted=unconfigured)."""
        if self._api_key_valid:
            badge_text = "Connected"
            badge_icon = ft.Icons.CHECK_CIRCLE
            success_color = MD3Colors.get_success(is_dark)
            badge_bg = ft.Colors.with_opacity(0.12, success_color)
            badge_fg = success_color
        else:
            badge_text = "Not configured"
            badge_icon = ft.Icons.CIRCLE_OUTLINED
            variant_color = MD3Colors.get_on_surface_variant(is_dark)
            badge_bg = ft.Colors.with_opacity(0.08, variant_color)
            badge_fg = variant_color

        self._badge_icon = ft.Icon(badge_icon, size=12, color=badge_fg)
        self._badge_text = ft.Text(
            badge_text,
            size=11,
            weight=ft.FontWeight.W_500,
            color=badge_fg,
        )

        return ft.Container(
            content=ft.Row(
                [self._badge_icon, self._badge_text],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=badge_bg,
            padding=ft.padding.symmetric(horizontal=10, vertical=4),
            border_radius=12,
            height=26,
        )

    def _update_status_badge(self, is_dark: bool):
        """Update the status badge colors and text based on current state."""
        if self._api_key_valid:
            badge_text = "Connected"
            badge_icon = ft.Icons.CHECK_CIRCLE
            success_color = MD3Colors.get_success(is_dark)
            badge_bg = ft.Colors.with_opacity(0.12, success_color)
            badge_fg = success_color
        elif self._api_key_valid is False:
            badge_text = "Invalid key"
            badge_icon = ft.Icons.ERROR_OUTLINE
            error_color = MD3Colors.get_error(is_dark)
            badge_bg = ft.Colors.with_opacity(0.12, error_color)
            badge_fg = error_color
        else:
            badge_text = "Not configured"
            badge_icon = ft.Icons.CIRCLE_OUTLINED
            variant_color = MD3Colors.get_on_surface_variant(is_dark)
            badge_bg = ft.Colors.with_opacity(0.08, variant_color)
            badge_fg = variant_color

        self._badge_icon.name = badge_icon
        self._badge_icon.color = badge_fg
        self._badge_text.value = badge_text
        self._badge_text.color = badge_fg
        self.status_badge.bgcolor = badge_bg

    def _set_status_temporary(self, text: str, color: str, icon: str | None = None):
        """Set a temporary status message on the badge (e.g., 'Validating...')."""
        self._badge_text.value = text
        self._badge_text.color = color
        if icon:
            self._badge_icon.name = icon
        self._badge_icon.color = color
        self.status_badge.bgcolor = ft.Colors.with_opacity(0.08, color)

    # ------------------------------------------------------------------ #
    #  Hover effect
    # ------------------------------------------------------------------ #

    def _on_hover(self, e):
        """Handle hover -- elevate shadow and subtle border glow."""
        is_dark = self._get_is_dark()
        accent = self._get_accent(is_dark)

        if e.data == "true":
            self.shadow = Shadows.LEVEL_2
        else:
            self.shadow = Shadows.LEVEL_1

        self._safe_update()

    # ------------------------------------------------------------------ #
    #  Event Handlers (all existing logic preserved)
    # ------------------------------------------------------------------ #

    async def _on_validate_clicked(self, e):
        """Validate API key and save if valid."""
        from dlss_updater.config import config_manager

        api_key = self.api_key_field.value.strip() if self.api_key_field.value else ""
        if not api_key:
            is_dark = self._get_is_dark()
            self._set_status_temporary(
                "Enter a key", MD3Colors.get_error(is_dark), ft.Icons.WARNING_AMBER,
            )
            self._safe_update()
            return

        if self._is_validating:
            return
        self._is_validating = True

        is_dark = self._get_is_dark()
        self.progress_ring.visible = True
        self.validate_button.disabled = True
        self._set_status_temporary(
            "Validating...", MD3Colors.get_primary(is_dark), ft.Icons.HOURGLASS_TOP,
        )
        self._safe_update()

        try:
            from dlss_updater.steam_integration import validate_steam_api_key

            valid = await validate_steam_api_key(api_key)

            if valid:
                config_manager.set_steam_api_key(api_key)
                self._api_key_valid = True
                self._update_status_badge(is_dark)

                # Auto-detect Steam ID if not already set
                if not config_manager.get_steam_id():
                    await self._auto_detect_steam_id()

                # Check how many games could be improved
                from dlss_updater.reresolution import reresolution_needed

                count = await reresolution_needed()
                self._games_to_improve = count

                self.reresolution_button.visible = True
                if count > 0:
                    self.improve_text.value = f"{count} game(s) could be improved"
                else:
                    self.improve_text.value = "All games already have accurate IDs"
                self.improve_text.visible = True
            else:
                self._api_key_valid = False
                self._update_status_badge(is_dark)
                self.reresolution_button.visible = False
        except Exception as ex:
            self._set_status_temporary(
                f"Error", MD3Colors.get_error(is_dark), ft.Icons.ERROR_OUTLINE,
            )
            logger.error(f"API key validation error: {ex}", exc_info=True)
        finally:
            self.progress_ring.visible = False
            self.validate_button.disabled = False
            self._is_validating = False
            self._safe_update()

    async def _on_reresolve_clicked(self, e):
        """Trigger re-resolution of games."""
        if self._is_reresolving:
            return
        self._is_reresolving = True

        self.reresolution_button.disabled = True
        self.progress_ring.visible = True
        self.improve_text.value = "Re-resolving..."
        self.improve_text.visible = True
        self._safe_update()

        try:
            from dlss_updater.reresolution import run_reresolution

            async def progress_cb(current, total, msg):
                self.improve_text.value = msg
                self._safe_update()

            results = await run_reresolution(progress_callback=progress_cb, force=True)

            self.improve_text.value = (
                f"Done: {results['resolved']} improved, "
                f"{results['unchanged']} unchanged"
            )
            self.reresolution_button.visible = False

            # Notify parent to reload games view with updated images
            if self._on_reresolution_complete and results["resolved"] > 0:
                await self._on_reresolution_complete()

        except Exception as ex:
            self.improve_text.value = f"Error: {ex}"
            logger.error(f"Re-resolution error: {ex}", exc_info=True)
        finally:
            self.reresolution_button.disabled = False
            self.progress_ring.visible = False
            self._is_reresolving = False
            self._safe_update()

    async def _auto_detect_steam_id(self):
        """Auto-detect Steam ID from loginusers.vdf."""
        try:
            from dlss_updater.steam_integration import detect_steam_id
            from dlss_updater.config import config_manager

            steam_id = await detect_steam_id()
            if steam_id:
                self._auto_steam_id = steam_id
                config_manager.set_auto_detected_steam_id(steam_id)
                self.steam_id_text.value = steam_id
                self._steam_id_container.visible = True
                self._safe_update()
            else:
                self.steam_id_text.value = "Not detected"
                self._steam_id_container.visible = True
                self._safe_update()
        except Exception as e:
            logger.debug(f"Could not auto-detect Steam ID: {e}")
            self.steam_id_text.value = "Detection failed"
            self._steam_id_container.visible = True
            self._safe_update()

    async def initialize(self):
        """Initialize card state after construction (call from parent view)."""
        from dlss_updater.config import config_manager

        # Auto-detect Steam ID
        if not config_manager.get_steam_id():
            await self._auto_detect_steam_id()

        # If key already exists, check improvement count
        if config_manager.get_steam_api_key():
            try:
                from dlss_updater.reresolution import reresolution_needed

                count = await reresolution_needed()
                self._games_to_improve = count
                self.reresolution_button.visible = True
                if count > 0:
                    self.improve_text.value = f"{count} game(s) could be improved"
                else:
                    self.improve_text.value = "All games already have accurate IDs"
                self.improve_text.visible = True
                self._safe_update()
            except Exception as e:
                logger.debug(f"Error checking reresolution count: {e}")

    # ------------------------------------------------------------------ #
    #  Theme support
    # ------------------------------------------------------------------ #

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for efficient theme switching."""
        return {
            "bgcolor": MD3Colors.get_themed_pair("surface"),
            "_title_text.color": MD3Colors.get_themed_pair("on_surface"),
            "_description_text.color": MD3Colors.get_themed_pair("on_surface_variant"),
            "improve_text.color": MD3Colors.get_themed_pair("on_surface_variant"),
            "steam_id_text.color": MD3Colors.get_themed_pair("on_surface_variant"),
            "_steam_id_label.color": MD3Colors.get_themed_pair("on_surface_variant"),
            "_steam_id_icon.color": MD3Colors.get_themed_pair("on_surface_variant"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme colors to all sub-elements."""
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            accent = self._get_accent(is_dark)
            primary = MD3Colors.get_primary(is_dark)
            outline = MD3Colors.get_outline(is_dark)
            variant = MD3Colors.get_on_surface_variant(is_dark)

            # Apply standard themed properties (bgcolor, text colors)
            properties = self.get_themed_properties()
            for prop_path, (dark_val, light_val) in properties.items():
                value = dark_val if is_dark else light_val
                self._set_nested_property(prop_path, value)

            # Container styling (accent bar + border + shadow)
            self.border = ft.border.only(left=ft.BorderSide(3, accent))

            # Leading icon circle
            self._leading_icon.bgcolor = accent

            # Status badge
            self._update_status_badge(is_dark)

            # API link button
            self._api_link_button.bgcolor = ft.Colors.with_opacity(0.08, primary)
            link_row = self._api_link_button.content
            if link_row and hasattr(link_row, 'controls') and len(link_row.controls) >= 2:
                link_row.controls[0].color = primary  # Icon
                link_row.controls[1].color = primary  # Text

            # TextField styling
            self.api_key_field.border_color = outline
            self.api_key_field.focused_border_color = primary
            self.api_key_field.bgcolor = MD3Colors.get_surface(is_dark)
            self.api_key_field.text_style = ft.TextStyle(
                color=MD3Colors.get_on_surface(is_dark),
            )
            self.api_key_field.hint_style = ft.TextStyle(
                color=variant,
            )
            self.api_key_field.label_style = ft.TextStyle(
                color=variant,
                size=12,
            )

            # Save button
            self.validate_button.style = ft.ButtonStyle(
                bgcolor=primary,
                color=ft.Colors.WHITE,
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=20, vertical=0),
            )

            # Progress ring
            self.progress_ring.color = primary

            # Re-resolve button
            self.reresolution_button.style = ft.ButtonStyle(
                bgcolor=ft.Colors.with_opacity(0.12, primary),
                color=primary,
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=16, vertical=0),
            )

            # Steam ID container background
            if self._steam_id_container.content and hasattr(self._steam_id_container.content, 'controls'):
                id_row_controls = self._steam_id_container.content.controls
                if len(id_row_controls) >= 3:
                    # The third control is the badge-like container around the ID value
                    id_value_container = id_row_controls[2]
                    if hasattr(id_value_container, 'bgcolor'):
                        id_value_container.bgcolor = ft.Colors.with_opacity(0.08, variant)

            # Divider
            self._divider.bgcolor = MD3Colors.get_themed("divider", is_dark)

            self._safe_update()

        except Exception:
            # Silent fail -- component may have been garbage collected
            pass
