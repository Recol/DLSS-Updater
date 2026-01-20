"""
Logger Panel Component - Material Design 3
Displays application logs with color-coded severity levels, animations, and filtering
Thread-safe implementation using page.run_task()
"""

import logging
import asyncio
import threading
from typing import Callable, Any
import flet as ft
from dlss_updater.ui_flet.theme.colors import MD3Colors, Animations, Shadows
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


class FletLoggerHandler(logging.Handler):
    """
    Custom logging handler for Flet UI with MD3 styling
    Emits logs to a Flet Column component with async updates
    """

    def __init__(self, log_column: ft.Column, page: ft.Page, logger_panel: 'LoggerPanel'):
        super().__init__()
        self.log_column = log_column
        self.page = page
        self.logger_panel = logger_panel
        self.max_lines = 1000  # Limit to prevent memory issues
        self._registry = get_theme_registry()

        # Store main thread ID for defensive checks
        self.main_thread_id = threading.current_thread().ident

        # Icon mapping for log levels
        self.icons = {
            "DEBUG": ft.Icons.CODE,
            "INFO": ft.Icons.INFO_OUTLINED,
            "WARNING": ft.Icons.WARNING_AMBER_ROUNDED,
            "ERROR": ft.Icons.ERROR_OUTLINE_ROUNDED,
            "CRITICAL": ft.Icons.DANGEROUS_ROUNDED,
        }

    def _get_level_color(self, level: str) -> str:
        """Get themed color for log level"""
        is_dark = self._registry.is_dark
        colors = {
            "DEBUG": MD3Colors.get_on_surface_variant(is_dark),
            "INFO": MD3Colors.get_info(is_dark),
            "WARNING": MD3Colors.get_warning(is_dark),
            "ERROR": MD3Colors.get_error(is_dark),
            "CRITICAL": MD3Colors.get_error(is_dark),
        }
        return colors.get(level, MD3Colors.get_on_surface(is_dark))

    def emit(self, record: logging.LogRecord):
        """
        Emit a log record to the UI (thread-safe)
        Uses page.run_task() to safely schedule UI updates from any thread
        """
        try:
            msg = self.format(record)
            color = self._get_level_color(record.levelname)
            icon = self.icons.get(record.levelname, ft.Icons.INFO_OUTLINED)

            # Use page.run_task() for thread-safe UI updates
            async def add_entry():
                await self._add_log_entry_async(msg, color, icon, record.levelname)

            try:
                # page.run_task() safely schedules the async function on the main event loop
                self.page.run_task(add_entry)
            except Exception as e:
                # Defensive: If run_task fails, at least log to console
                print(f"Failed to add log entry to UI: {e}")
                print(f"Log message: {msg}")

        except Exception:
            self.handleError(record)

    async def _add_log_entry_async(self, msg: str, color: str, icon: str, level: str):
        """
        Add log entry asynchronously with fade-in animation and hover effects
        """
        log_entry = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(icon, color=color, size=16),
                    ft.Text(
                        msg,
                        color=color,
                        size=12,
                        selectable=True,
                        expand=True,
                        weight=ft.FontWeight.W_400,
                    ),
                ],
                spacing=8,
            ),
            padding=ft.padding.symmetric(vertical=4, horizontal=8),
            border_radius=6,
            opacity=0,
            animate_opacity=Animations.FADE,
            data=level,  # Store level for filtering
            # Hover effect
            bgcolor=ft.Colors.TRANSPARENT,
            on_hover=lambda e: self._on_log_hover(e, log_entry),
        )

        self.log_column.controls.append(log_entry)

        # Limit lines to prevent memory issues
        if len(self.log_column.controls) > self.max_lines:
            self.log_column.controls.pop(0)

        # Update log count badge
        self.logger_panel.update_log_count()

        # Auto-expand on errors
        if level in ["ERROR", "CRITICAL"]:
            self.logger_panel.expand_on_error()

        # Trigger fade-in animation
        self.page.update()
        await asyncio.sleep(0.05)
        log_entry.opacity = 1
        self.page.update()

    def _on_log_hover(self, e: ft.HoverEvent, container: ft.Container):
        """Handle hover effect on log entries"""
        is_dark = self._registry.is_dark
        if e.data == "true":
            container.bgcolor = MD3Colors.HOVER_OVERLAY if is_dark else "rgba(0, 0, 0, 0.04)"
        else:
            container.bgcolor = ft.Colors.TRANSPARENT
        self.page.update()


class LoggerPanel(ThemeAwareMixin, ft.Container):
    """
    Collapsible logger panel showing application logs with MD3 design
    Features:
    - Color-coded log levels with icons
    - Smooth expand/collapse animation (300ms)
    - Rotating arrow icon (180° rotation)
    - Log level filter chips
    - Log count badge
    - Header hover effect
    - Auto-expand on errors
    - Light/dark theme support
    """

    def __init__(self, page: ft.Page, logger: logging.Logger):
        super().__init__()

        self.page = page
        self.logger = logger
        self.expanded = False
        self.current_filter = "ALL"
        self._registry = get_theme_registry()
        self._theme_priority = 40  # Utility components are mid-low priority

        # Get current theme
        is_dark = self._registry.is_dark

        # Log column (scrollable)
        self.log_column = ft.Column(
            controls=[],
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # Create log count badge
        self.log_count_badge_text = ft.Text(
            "0",
            size=11,
            color=MD3Colors.ON_PRIMARY,
            weight=ft.FontWeight.BOLD,
        )
        self.log_count_badge = ft.Container(
            content=self.log_count_badge_text,
            bgcolor=MD3Colors.get_primary(is_dark),
            padding=ft.padding.symmetric(horizontal=6, vertical=2),
            border_radius=10,
            visible=False,
        )

        # Create filter chips
        self.filter_chips = self._create_filter_chips(is_dark)

        # Filter label
        self.filter_label = ft.Text(
            "Filter:",
            size=12,
            color=MD3Colors.get_on_surface_variant(is_dark),
            weight=ft.FontWeight.W_500,
        )

        # Filter row container
        self.filter_row = ft.Container(
            content=ft.Row(
                controls=[
                    self.filter_label,
                    *self.filter_chips,
                ],
                spacing=8,
            ),
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            opacity=0,
            animate_opacity=Animations.FADE,
        )

        # Create and attach Flet logger handler
        self.flet_handler = FletLoggerHandler(self.log_column, page, self)
        self.flet_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        logger.addHandler(self.flet_handler)

        # Toggle button with rotation animation
        self.toggle_button = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
            tooltip="Expand Logs",
            on_click=self.toggle_panel,
            icon_color=MD3Colors.get_primary(is_dark),
            icon_size=24,
            rotate=ft.Rotate(0, alignment=ft.alignment.center),
            animate_rotation=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
        )

        # Header icon
        self.header_icon = ft.Icon(ft.Icons.TERMINAL, size=20, color=MD3Colors.get_primary(is_dark))

        # Header title
        self.header_title = ft.Text(
            "Application Logs",
            size=14,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_on_surface(is_dark),
        )

        # Clear button
        self.clear_button = ft.TextButton(
            "Clear",
            icon=ft.Icons.CLEAR_ALL_ROUNDED,
            on_click=self.clear_logs,
            style=ft.ButtonStyle(
                color=MD3Colors.get_primary(is_dark),
            ),
        )

        # Header with gradient and hover effect
        self.header_container = ft.Container(
            content=ft.Row(
                controls=[
                    self.header_icon,
                    self.header_title,
                    self.log_count_badge,
                    ft.Container(expand=True),  # Spacer
                    self.clear_button,
                    self.toggle_button,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=ft.padding.all(12),
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=[MD3Colors.get_surface_variant(is_dark), MD3Colors.get_surface(is_dark)],
            ),
            on_hover=lambda e: self._on_header_hover(e),
            animate=Animations.HOVER,
        )

        # Top divider with gradient
        self.top_divider = ft.Container(
            height=2,
            gradient=ft.LinearGradient(
                begin=ft.alignment.center_left,
                end=ft.alignment.center_right,
                colors=[
                    ft.Colors.TRANSPARENT,
                    MD3Colors.get_primary(is_dark),
                    ft.Colors.TRANSPARENT,
                ],
            ),
        )

        # Log container (initially hidden)
        self.log_container = ft.Container(
            content=ft.Column(
                controls=[
                    self.filter_row,
                    ft.Container(
                        content=self.log_column,
                        expand=True,
                    ),
                ],
                spacing=0,
            ),
            bgcolor=MD3Colors.get_themed("surface_dim", is_dark),
            padding=ft.padding.all(8),
            height=0,
            visible=False,
            animate=Animations.EXPAND,
        )

        # Panel content
        self.content = ft.Column(
            controls=[
                self.top_divider,
                self.header_container,
                self.log_container,
            ],
            spacing=0,
        )

        # Container styling with MD3 shadow
        self.bgcolor = MD3Colors.get_surface(is_dark)
        self.shadow = Shadows.LEVEL_2
        self.border_radius = 0  # Bottom panel, no radius

        # Register for theme updates
        self._register_theme_aware()

    def _create_filter_chips(self, is_dark: bool) -> list[ft.Container]:
        """Create filter chips for log levels"""
        levels = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"]
        chips = []

        for level in levels:
            chip_text = ft.Text(
                level,
                size=11,
                color=MD3Colors.get_on_surface(is_dark) if level == "ALL" else MD3Colors.get_on_surface_variant(is_dark),
                weight=ft.FontWeight.BOLD if level == "ALL" else ft.FontWeight.W_500,
            )
            chip = ft.Container(
                content=chip_text,
                bgcolor=MD3Colors.get_primary(is_dark) if level == "ALL" else MD3Colors.get_surface_variant(is_dark),
                padding=ft.padding.symmetric(horizontal=12, vertical=6),
                border_radius=16,
                on_click=lambda e, lvl=level: self._apply_filter(lvl),
                data=level,
                animate=Animations.HOVER,
            )
            chips.append(chip)

        return chips

    def _apply_filter(self, level: str):
        """Apply log level filter"""
        self.current_filter = level
        is_dark = self._registry.is_dark

        # Update chip styles
        for chip in self.filter_chips:
            if chip.data == level:
                chip.bgcolor = MD3Colors.get_primary(is_dark)
                chip.content.color = MD3Colors.ON_PRIMARY
                chip.content.weight = ft.FontWeight.BOLD
            else:
                chip.bgcolor = MD3Colors.get_surface_variant(is_dark)
                chip.content.color = MD3Colors.get_on_surface_variant(is_dark)
                chip.content.weight = ft.FontWeight.W_500

        # Filter log entries
        for log_entry in self.log_column.controls:
            if level == "ALL":
                log_entry.visible = True
            else:
                log_entry.visible = log_entry.data == level

        self.page.update()

    def _on_header_hover(self, e: ft.HoverEvent):
        """Handle header hover effect with elevation change"""
        if e.data == "true":
            self.shadow = Shadows.LEVEL_3
        else:
            self.shadow = Shadows.LEVEL_2
        self.page.update()

    def toggle_panel(self, e=None):
        """Toggle panel expansion with smooth animation"""
        self.expanded = not self.expanded

        if self.expanded:
            self.log_container.height = 250
            self.log_container.visible = True
            self.toggle_button.icon = ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
            self.toggle_button.tooltip = "Collapse Logs"
            self.toggle_button.rotate = ft.Rotate(3.14159, alignment=ft.alignment.center)  # 180 degrees

            # Fade in filter row
            self.filter_row.opacity = 1
        else:
            self.log_container.height = 0
            self.toggle_button.icon = ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
            self.toggle_button.tooltip = "Expand Logs"
            self.toggle_button.rotate = ft.Rotate(0, alignment=ft.alignment.center)

            # Fade out filter row
            self.filter_row.opacity = 0

            # Delay hiding to allow animation
            async def hide_after_animation():
                await asyncio.sleep(0.3)
                self.log_container.visible = False
                self.page.update()

            self.page.run_task(hide_after_animation)

        self.page.update()

    def clear_logs(self, e=None):
        """Clear all log entries"""
        self.log_column.controls.clear()
        self.update_log_count()
        self.logger.info("Logs cleared")
        self.page.update()

    def update_log_count(self):
        """Update the log count badge"""
        count = len(self.log_column.controls)
        self.log_count_badge.content.value = str(count)
        self.log_count_badge.visible = count > 0
        self.page.update()

    def expand_on_error(self):
        """Auto-expand panel when error occurs"""
        if not self.expanded:
            self.toggle_panel()

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for the logger panel"""
        return {
            # Panel background
            "bgcolor": MD3Colors.get_themed_pair("surface"),
            # Header gradient - handled separately in apply_theme
            "header_title.color": MD3Colors.get_themed_pair("on_surface"),
            "header_icon.color": MD3Colors.get_themed_pair("primary"),
            "toggle_button.icon_color": MD3Colors.get_themed_pair("primary"),
            # Log container background
            "log_container.bgcolor": MD3Colors.get_themed_pair("surface_dim"),
            # Badge
            "log_count_badge.bgcolor": MD3Colors.get_themed_pair("primary"),
            # Filter label
            "filter_label.color": MD3Colors.get_themed_pair("on_surface_variant"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with cascade animation support"""
        import asyncio
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            # Apply basic properties via parent method
            properties = self.get_themed_properties()
            for prop_path, (dark_val, light_val) in properties.items():
                value = dark_val if is_dark else light_val
                self._set_nested_property(prop_path, value)

            # Update header gradient (cannot use simple property mapping)
            self.header_container.gradient = ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=[MD3Colors.get_surface_variant(is_dark), MD3Colors.get_surface(is_dark)],
            )

            # Update top divider gradient
            self.top_divider.gradient = ft.LinearGradient(
                begin=ft.alignment.center_left,
                end=ft.alignment.center_right,
                colors=[
                    ft.Colors.TRANSPARENT,
                    MD3Colors.get_primary(is_dark),
                    ft.Colors.TRANSPARENT,
                ],
            )

            # Update clear button style
            self.clear_button.style = ft.ButtonStyle(
                color=MD3Colors.get_primary(is_dark),
            )

            # Update filter chips
            for chip in self.filter_chips:
                if chip.data == self.current_filter:
                    chip.bgcolor = MD3Colors.get_primary(is_dark)
                    chip.content.color = MD3Colors.ON_PRIMARY
                else:
                    chip.bgcolor = MD3Colors.get_surface_variant(is_dark)
                    chip.content.color = MD3Colors.get_on_surface_variant(is_dark)

            if hasattr(self, 'update'):
                self.update()

        except Exception:
            pass  # Silent fail - component may have been garbage collected
