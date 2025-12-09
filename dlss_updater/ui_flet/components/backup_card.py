"""
Backup Card Component
Individual backup entry card with restore and delete actions
"""

from datetime import datetime
import flet as ft

from dlss_updater.database import DLLBackup
from dlss_updater.ui_flet.theme.colors import Shadows


class BackupCard(ft.Card):
    """Individual backup card with metadata and actions"""

    def __init__(self, backup: DLLBackup, page: ft.Page, logger, on_restore=None, on_delete=None):
        super().__init__()
        self.backup = backup
        self.page = page
        self.logger = logger
        self.on_restore_callback = on_restore
        self.on_delete_callback = on_delete

        # Card styling
        self.elevation = 1
        self.margin = ft.margin.symmetric(horizontal=8, vertical=4)
        self.shadow = Shadows.LEVEL_1
        self.on_hover = self._on_hover_shadow

        # Build content
        self._build_card_content()

    def _on_hover_shadow(self, e):
        """Handle hover state for shadow effects"""
        if e.data == "true":
            self.shadow = Shadows.LEVEL_2
        else:
            self.shadow = Shadows.LEVEL_1
        self.update()

    def _build_card_content(self):
        """Build card content layout"""
        # Header with game name and DLL
        header = ft.Row(
            controls=[
                ft.Icon(ft.Icons.RESTORE, size=24, color="#2D6E88"),
                ft.Text(
                    f"{self.backup.game_name} - {self.backup.dll_filename}",
                    size=16,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.WHITE,
                    expand=True,
                    no_wrap=True,
                ),
            ],
            spacing=12,
            tight=True,
        )

        # Metadata
        metadata = ft.Column(
            controls=[
                self._create_metadata_row("Original Version:", self.backup.original_version or "Unknown"),
                self._create_metadata_row("Backup Date:", self._format_date(self.backup.backup_created_at)),
                self._create_metadata_row("Size:", self._format_size(self.backup.backup_size)),
                self._create_metadata_row("Location:", self._truncate_path(self.backup.backup_path)),
            ],
            spacing=4,
            tight=True,
        )

        # Action buttons
        action_buttons = ft.Row(
            controls=[
                ft.ElevatedButton(
                    "Restore",
                    icon=ft.Icons.RESTORE,
                    on_click=self._on_restore_clicked,
                    style=ft.ButtonStyle(
                        bgcolor="#2D6E88",
                        color=ft.Colors.WHITE,
                    ),
                    animate_scale=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
                    elevation=2,
                ),
                ft.TextButton(
                    "Delete Backup",
                    icon=ft.Icons.DELETE_OUTLINE,
                    on_click=self._on_delete_clicked,
                    style=ft.ButtonStyle(
                        color=ft.Colors.RED_400,
                        overlay_color="rgba(255, 0, 0, 0.08)",
                    ),
                    animate_scale=ft.Animation(100, ft.AnimationCurve.EASE_OUT),
                ),
            ],
            spacing=12,
            tight=True,
        )

        # Card content
        self.content = ft.Container(
            content=ft.Column(
                controls=[
                    header,
                    ft.Divider(height=1, color="#5A5A5A"),
                    metadata,
                    action_buttons,
                ],
                spacing=12,
                tight=True,
            ),
            padding=16,
        )

    def _create_metadata_row(self, label: str, value: str) -> ft.Row:
        """Create a metadata row with label and value"""
        return ft.Row(
            controls=[
                ft.Text(
                    label,
                    size=12,
                    color="#888888",
                    width=120,
                    no_wrap=True,
                ),
                ft.Text(
                    value,
                    size=12,
                    color=ft.Colors.WHITE,
                    expand=True,
                    no_wrap=True,
                ),
            ],
            spacing=8,
            tight=True,
        )

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
