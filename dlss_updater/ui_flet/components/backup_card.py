"""
Backup Card Component
Individual backup entry card with restore and delete actions
"""

from datetime import datetime
import flet as ft

from dlss_updater.database import DLLBackup
from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


class BackupCard(ThemeAwareMixin, ft.Card):
    """Individual backup card with metadata and actions"""

    def __init__(self, backup: DLLBackup, page: ft.Page, logger, on_restore=None, on_delete=None):
        super().__init__()
        self.backup = backup
        self._page_ref = page
        self.logger = logger
        self.on_restore_callback = on_restore
        self.on_delete_callback = on_delete

        # Get theme registry and state
        self._registry = get_theme_registry()
        self._theme_priority = 25  # Cards are mid-priority
        is_dark = self._registry.is_dark

        # Card styling
        self.elevation = 1
        self.margin = ft.margin.symmetric(horizontal=8, vertical=4)
        self.shadow = Shadows.LEVEL_1
        self.on_hover = self._on_hover_shadow

        # Build content
        self._build_card_content(is_dark)

        # Register for theme updates
        self._register_theme_aware()

    def _on_hover_shadow(self, e):
        """Handle hover state for shadow effects"""
        if e.data == "true":
            self.shadow = Shadows.LEVEL_2
        else:
            self.shadow = Shadows.LEVEL_1
        self.update()

    def _build_card_content(self, is_dark: bool):
        """Build card content layout"""
        # Header with game name and DLL
        self._header_icon = ft.Icon(ft.Icons.RESTORE, size=24, color=MD3Colors.get_primary(is_dark))
        self._header_text = ft.Text(
            f"{self.backup.game_name} - {self.backup.dll_filename}",
            size=16,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.get_text_primary(is_dark),
            expand=True,
            no_wrap=True,
        )
        header = ft.Row(
            controls=[
                self._header_icon,
                self._header_text,
            ],
            spacing=12,
            tight=True,
        )

        # Metadata
        metadata = ft.Column(
            controls=[
                self._create_metadata_row("Original Version:", self.backup.original_version or "Unknown", is_dark),
                self._create_metadata_row("Backup Date:", self._format_date(self.backup.backup_created_at), is_dark),
                self._create_metadata_row("Size:", self._format_size(self.backup.backup_size), is_dark),
                self._create_path_row("Location:", self.backup.backup_path, is_dark),
            ],
            spacing=4,
            tight=True,
        )
        self._metadata_column = metadata

        # Action buttons
        self._restore_btn = ft.ElevatedButton(
            "Restore",
            icon=ft.Icons.RESTORE,
            on_click=self._on_restore_clicked,
            style=ft.ButtonStyle(
                bgcolor=MD3Colors.get_primary(is_dark),
                color=MD3Colors.ON_PRIMARY,
            ),
            animate_scale=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
            elevation=2,
        )
        self._delete_btn = ft.TextButton(
            "Delete Backup",
            icon=ft.Icons.DELETE_OUTLINE,
            on_click=self._on_delete_clicked,
            style=ft.ButtonStyle(
                color=MD3Colors.get_error(is_dark),
                overlay_color="rgba(255, 0, 0, 0.08)",
            ),
            animate_scale=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
        )
        action_buttons = ft.Row(
            controls=[
                self._restore_btn,
                self._delete_btn,
            ],
            spacing=12,
            tight=True,
        )

        # Divider
        self._divider = ft.Divider(height=1, color=MD3Colors.get_outline(is_dark))

        # Card content - fixed height like GameCard for consistent GridView sizing
        self.content = ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    self._divider,
                    metadata,
                    action_buttons,
                ],
                spacing=6,  # Compact spacing
                tight=True,
            ),
            padding=10,
            height=220,  # Same as GameCard - fits all content including buttons
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

    def _create_metadata_row(self, label: str, value: str, is_dark: bool) -> ft.Row:
        """Create a metadata row with label and value"""
        label_text = ft.Text(
            label,
            size=11,  # Slightly smaller for compactness
            color=MD3Colors.get_text_secondary(is_dark),
            width=95,  # Reduced from 120 to fit better
            no_wrap=True,
        )
        value_text = ft.Text(
            value,
            size=11,  # Slightly smaller for compactness
            color=MD3Colors.get_text_primary(is_dark),
            expand=True,
            no_wrap=True,
        )
        # Store references for theme updates
        if not hasattr(self, '_metadata_labels'):
            self._metadata_labels = []
            self._metadata_values = []
        self._metadata_labels.append(label_text)
        self._metadata_values.append(value_text)

        return ft.Row(
            controls=[label_text, value_text],
            spacing=8,
            tight=True,
        )

    def _create_path_row(self, label: str, full_path: str, is_dark: bool) -> ft.Row:
        """Create a metadata row for paths with tooltip and copy functionality"""
        self._path_label = ft.Text(
            label,
            size=11,  # Match metadata row size
            color=MD3Colors.get_text_secondary(is_dark),
            width=95,  # Match metadata row width
            no_wrap=True,
        )
        # PERF: tooltip on Text directly instead of Container wrapper (-1 control)
        self._path_value = ft.Text(
            self._truncate_path(full_path),
            size=11,  # Match metadata row size
            color=MD3Colors.get_text_primary(is_dark),
            no_wrap=True,
            tooltip=full_path,
            expand=True,
        )
        self._copy_btn = ft.IconButton(
            icon=ft.Icons.CONTENT_COPY,
            icon_size=14,
            icon_color=MD3Colors.get_text_secondary(is_dark),
            tooltip="Copy path",
            on_click=lambda e, p=full_path: self._on_copy_path_clicked(e, p),
            width=24,
            height=24,
        )
        return ft.Row(
            controls=[
                self._path_label,
                self._path_value,
                self._copy_btn,
            ],
            spacing=8,
            tight=True,
        )

    async def _on_copy_path_clicked(self, e, path: str):
        """Copy path to clipboard with snackbar confirmation"""
        is_dark = self._registry.is_dark
        try:
            await ft.Clipboard().set(path)
            self._page_ref.show_dialog(ft.SnackBar(
                content=ft.Text("Path copied to clipboard"),
                bgcolor=MD3Colors.get_primary(is_dark),
            ))
        except Exception as ex:
            from dlss_updater.logger import setup_logger
            logger = setup_logger("BackupCard")
            logger.warning(f"Clipboard operation failed: {ex}")
            self._page_ref.show_dialog(ft.SnackBar(
                content=ft.Text("Failed to copy to clipboard"),
                bgcolor=MD3Colors.get_error(is_dark),
            ))

    def _format_date(self, dt: datetime) -> str:
        """Format datetime for display"""
        return dt.strftime("%Y-%m-%d %I:%M %p")

    def _format_size(self, size_bytes: int) -> str:
        """Format file size for display"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    def _truncate_path(self, path: str, max_length: int = 60) -> str:
        """Truncate long paths for display"""
        if len(path) <= max_length:
            return path
        return "..." + path[-(max_length - 3):]

    def _on_restore_clicked(self, e):
        """Handle restore button click"""
        if self.on_restore_callback:
            self.on_restore_callback(self.backup)

    def _on_delete_clicked(self, e):
        """Handle delete button click"""
        if self.on_delete_callback:
            self.on_delete_callback(self.backup)

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for cascade updates"""
        return {
            "_header_icon.color": MD3Colors.get_themed_pair("primary"),
            "_header_text.color": MD3Colors.get_themed_pair("text_primary"),
            "_divider.color": MD3Colors.get_themed_pair("outline"),
            "_path_label.color": MD3Colors.get_themed_pair("text_secondary"),
            "_path_value.color": MD3Colors.get_themed_pair("text_primary"),
            "_copy_btn.icon_color": MD3Colors.get_themed_pair("text_secondary"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay - extended for complex updates"""
        import asyncio
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        try:
            # Apply base properties
            properties = self.get_themed_properties()
            for prop_path, (dark_val, light_val) in properties.items():
                value = dark_val if is_dark else light_val
                self._set_nested_property(prop_path, value)

            # Update metadata labels and values
            if hasattr(self, '_metadata_labels'):
                for label in self._metadata_labels:
                    label.color = MD3Colors.get_text_secondary(is_dark)
            if hasattr(self, '_metadata_values'):
                for value in self._metadata_values:
                    value.color = MD3Colors.get_text_primary(is_dark)

            # Update button styles
            if hasattr(self, '_restore_btn'):
                self._restore_btn.style = ft.ButtonStyle(
                    bgcolor=MD3Colors.get_primary(is_dark),
                    color=MD3Colors.ON_PRIMARY,
                )
            if hasattr(self, '_delete_btn'):
                self._delete_btn.style = ft.ButtonStyle(
                    color=MD3Colors.get_error(is_dark),
                    overlay_color="rgba(255, 0, 0, 0.08)",
                )

            if hasattr(self, 'update'):
                self.update()
        except Exception:
            pass  # Silent fail - component may have been garbage collected
