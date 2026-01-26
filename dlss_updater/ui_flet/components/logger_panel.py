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
    Emits logs to a Flet Column component with batched async updates
    """

    # Batching configuration
    BATCH_FLUSH_INTERVAL_MS = 200  # Flush every 200ms
    MAX_BATCH_SIZE = 50  # Force flush if batch exceeds this

    def __init__(self, log_column: ft.Column, page: ft.Page, logger_panel: 'LoggerPanel'):
        super().__init__()
        self.log_column = log_column
        self._page_ref = page
        self.logger_panel = logger_panel
        self.max_lines = 1000  # Limit to prevent memory issues
        self._registry = get_theme_registry()

        # Store main thread ID for defensive checks
        self.main_thread_id = threading.current_thread().ident

        # Batching state
        self._pending_entries: list[tuple[str, str, str, str]] = []  # (msg, color, icon, level)
        self._flush_scheduled = False
        self._batch_lock = threading.Lock()

        # Icon mapping for log levels
        self.icons = {
            "DEBUG": ft.Icons.CODE,
            "INFO": ft.Icons.INFO_OUTLINED,
            "WARNING": ft.Icons.WARNING_AMBER_ROUNDED,
            "ERROR": ft.Icons.ERROR_OUTLINE_ROUNDED,
            "CRITICAL": ft.Icons.DANGEROUS_ROUNDED,
        }

    def _safe_update(self):
        """Safely update page, handling destroyed session during shutdown"""
        try:
            if self._page_ref and hasattr(self._page_ref, 'session'):
                self._page_ref.update()
        except RuntimeError:
            # Session destroyed during shutdown - ignore
            pass

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
        Emit a log record to the UI (thread-safe) with batching.

        Uses batching to reduce UI updates:
        - Entries are collected in a buffer
        - Buffer is flushed every 200ms or when it reaches MAX_BATCH_SIZE
        - Single page.update() per flush (vs per-entry previously)
        """
        try:
            msg = self.format(record)
            color = self._get_level_color(record.levelname)
            icon = self.icons.get(record.levelname, ft.Icons.INFO_OUTLINED)

            # Add to batch with thread lock
            with self._batch_lock:
                self._pending_entries.append((msg, color, icon, record.levelname))
                should_flush_now = len(self._pending_entries) >= self.MAX_BATCH_SIZE
                needs_schedule = not self._flush_scheduled

            # Force immediate flush if batch is full
            if should_flush_now:
                try:
                    self._page_ref.run_task(self._flush_batch)
                except Exception:
                    pass
            elif needs_schedule:
                # Schedule a delayed flush
                with self._batch_lock:
                    self._flush_scheduled = True
                try:
                    self._page_ref.run_task(self._schedule_flush)
                except Exception as e:
                    # Defensive: If run_task fails, at least log to console
                    print(f"Failed to schedule log flush: {e}")
                    print(f"Log message: {msg}")

        except Exception:
            self.handleError(record)

    async def _schedule_flush(self):
        """Schedule a delayed flush after BATCH_FLUSH_INTERVAL_MS"""
        await asyncio.sleep(self.BATCH_FLUSH_INTERVAL_MS / 1000)
        await self._flush_batch()

    async def _flush_batch(self):
        """Flush all pending log entries with a single UI update"""
        # Get and clear pending entries atomically
        with self._batch_lock:
            entries = self._pending_entries.copy()
            self._pending_entries.clear()
            self._flush_scheduled = False

        if not entries:
            return

        has_error = False

        # Add all entries to the log column
        for msg, color, icon, level in entries:
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
                opacity=1,  # No fade animation - instant display for performance
                data=level,  # Store level for filtering
                bgcolor=ft.Colors.TRANSPARENT,
            )
            # Set hover handler after creation to avoid forward reference
            log_entry.on_hover = lambda e, entry=log_entry: self._on_log_hover(e, entry)

            self.log_column.controls.append(log_entry)

            if level in ["ERROR", "CRITICAL"]:
                has_error = True

        # Enforce max lines limit
        while len(self.log_column.controls) > self.max_lines:
            self.log_column.controls.pop(0)

        # Update log count badge
        self.logger_panel.update_log_count()

        # Auto-expand on errors
        if has_error:
            self.logger_panel.expand_on_error()

        # Single page update for entire batch
        self._safe_update()

    def _on_log_hover(self, e: ft.HoverEvent, container: ft.Container):
        """Handle hover effect on log entries using control-level update"""
        is_dark = self._registry.is_dark
        if e.data == "true":
            container.bgcolor = MD3Colors.HOVER_OVERLAY if is_dark else "rgba(0, 0, 0, 0.04)"
        else:
            container.bgcolor = ft.Colors.TRANSPARENT
        # Use control-level update instead of page.update() for better performance
        try:
            container.update()
        except RuntimeError:
            pass  # Control may not be attached yet


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

    Performance: Uses is_isolated=True to prevent parent update() from including
    this control's changes. Must call self.update() manually for changes.
    """

    def is_isolated(self):
        """Isolated controls are excluded from parent's update digest."""
        return True

    def __init__(self, page: ft.Page, logger: logging.Logger):
        super().__init__()

        self._page_ref = page
        self.logger = logger
        self.expanded = False
        self.current_filter = "ALL"
        self._registry = get_theme_registry()
        self._theme_priority = 40  # Utility components are mid-low priority
        self._shutdown = False  # Track shutdown state

        # Pre-allocated rotation objects (avoid allocation pressure during animations)
        import math
        self._rotate_0 = ft.Rotate(angle=0, alignment=ft.Alignment.CENTER)
        self._rotate_180 = ft.Rotate(angle=math.pi, alignment=ft.Alignment.CENTER)

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
        self.flet_handler = FletLoggerHandler(self.log_column, self._page_ref, self)
        self.flet_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        self.logger.addHandler(self.flet_handler)

        # Toggle button with rotation animation
        self.toggle_button = ft.IconButton(
            icon=ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
            tooltip="Expand Logs",
            on_click=self.toggle_panel,
            icon_color=MD3Colors.get_primary(is_dark),
            icon_size=24,
            rotate=ft.Rotate(0, alignment=ft.Alignment.CENTER),
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
                begin=ft.Alignment.TOP_LEFT,
                end=ft.Alignment.BOTTOM_RIGHT,
                colors=[MD3Colors.get_surface_variant(is_dark), MD3Colors.get_surface(is_dark)],
            ),
            on_hover=lambda e: self._on_header_hover(e),
            animate=Animations.HOVER,
        )

        # Top divider with gradient
        self.top_divider = ft.Container(
            height=2,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT,
                end=ft.Alignment.CENTER_RIGHT,
                colors=[
                    ft.Colors.TRANSPARENT,
                    MD3Colors.get_primary(is_dark),
                    ft.Colors.TRANSPARENT,
                ],
            ),
        )

        # Log container (initially hidden)
        # PERFORMANCE: Uses visible+opacity pattern instead of height animation
        # Height animation (0->250) causes full layout recalculation
        # Visibility toggle with fixed height avoids layout recalc (~50-100ms faster)
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
            height=250,  # Fixed height - visibility controls show/hide
            visible=False,
            opacity=0,
            animate_opacity=Animations.FADE,
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

    def _safe_update(self):
        """Safely update page, handling destroyed session during shutdown"""
        if self._shutdown:
            return
        try:
            if self._page_ref and hasattr(self._page_ref, 'session'):
                self._page_ref.update()
        except RuntimeError:
            # Session destroyed during shutdown - mark as shutdown
            self._shutdown = True

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

        self._safe_update()

    def _on_header_hover(self, e: ft.HoverEvent):
        """Handle header hover effect with elevation change"""
        if e.data == "true":
            self.shadow = Shadows.LEVEL_3
        else:
            self.shadow = Shadows.LEVEL_2
        self._safe_update()

    def toggle_panel(self, e=None):
        """Toggle panel expansion with smooth animation.

        PERFORMANCE: Uses visible+opacity pattern instead of height animation.
        Height animation causes full layout recalculation (~50-100ms overhead).
        Opacity animation is GPU-accelerated and doesn't trigger layout recalc.

        Pre-allocated rotation objects avoid allocation pressure during animation.
        """
        import time
        from dlss_updater.ui_flet.perf_monitor import perf_logger

        start_total = time.perf_counter()
        self.expanded = not self.expanded

        start_props = time.perf_counter()
        if self.expanded:
            # Show container first, then fade in
            self.log_container.visible = True
            self.log_container.opacity = 1
            self.toggle_button.icon = ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
            self.toggle_button.tooltip = "Collapse Logs"
            self.toggle_button.rotate = self._rotate_180

            # Fade in filter row
            self.filter_row.opacity = 1
        else:
            # Fade out first, then hide after animation completes
            self.log_container.opacity = 0
            self.toggle_button.icon = ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
            self.toggle_button.tooltip = "Expand Logs"
            self.toggle_button.rotate = self._rotate_0

            # Fade out filter row
            self.filter_row.opacity = 0

            # Delay hiding to allow opacity animation to complete
            async def hide_after_animation():
                await asyncio.sleep(0.3)
                self.log_container.visible = False
                self._safe_update()

            self._page_ref.run_task(hide_after_animation)

        props_ms = (time.perf_counter() - start_props) * 1000

        start_update = time.perf_counter()
        self._safe_update()
        update_ms = (time.perf_counter() - start_update) * 1000

        total_ms = (time.perf_counter() - start_total) * 1000
        perf_logger.debug(f"[PERF] logger_toggle: props={props_ms:.1f}ms, update={update_ms:.1f}ms, total={total_ms:.1f}ms")

    def clear_logs(self, e=None):
        """Clear all log entries"""
        self.log_column.controls.clear()
        self.update_log_count()
        self.logger.info("Logs cleared")
        self._safe_update()

    def update_log_count(self):
        """Update the log count badge (no page update - caller handles it)"""
        count = len(self.log_column.controls)
        self.log_count_badge.content.value = str(count)
        self.log_count_badge.visible = count > 0
        # Note: No _safe_update() here - called from _flush_batch which does a single update

    def expand_on_error(self):
        """Auto-expand panel when error occurs"""
        if not self.expanded:
            self.toggle_panel()

    def cleanup(self):
        """
        Cleanup logger panel resources during shutdown.

        Removes the Flet handler from the logger to:
        - Prevent dangling references to destroyed page/session
        - Allow proper garbage collection
        - Ensure clean process termination

        Must be called during application shutdown.
        """
        try:
            if hasattr(self, 'flet_handler') and self.flet_handler:
                # Flush any pending log entries
                with self.flet_handler._batch_lock:
                    self.flet_handler._pending_entries.clear()

                # Remove handler from logger
                self.logger.removeHandler(self.flet_handler)

                # Close the handler
                self.flet_handler.close()

                # Clear references
                self.flet_handler = None
        except Exception:
            pass  # Best effort cleanup

        # Unregister from theme system
        try:
            self._unregister_theme_aware()
        except Exception:
            pass

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
                begin=ft.Alignment.TOP_LEFT,
                end=ft.Alignment.BOTTOM_RIGHT,
                colors=[MD3Colors.get_surface_variant(is_dark), MD3Colors.get_surface(is_dark)],
            )

            # Update top divider gradient
            self.top_divider.gradient = ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT,
                end=ft.Alignment.CENTER_RIGHT,
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
