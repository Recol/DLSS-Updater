"""
Backup Group Component
Groups backups by game using ExpansionTile for efficient control tree.

This component reduces control tree complexity by using a single ExpansionTile
per game instead of individual BackupCards for each DLL backup.

Performance characteristics:
- Collapsed: ~6 controls (header only)
- Expanded: ~8 controls per backup row
- Uses ExpansionTile (native Flutter component) for GPU-accelerated expand/collapse
"""

import math
import anyio
from datetime import datetime
from typing import Callable
import flet as ft

from dlss_updater.models import GameDLLBackup
from dlss_updater.ui_flet.theme.colors import MD3Colors
from dlss_updater.ui_flet.theme.theme_aware import ThemeAwareMixin, get_theme_registry


class BackupRow(ft.Container):
    """
    Lightweight backup row inside BackupGroup (~8 controls).

    Displays a single backup entry with:
    - DLL filename and icon
    - Version information
    - Backup date
    - File size
    - Delete action button (plus Restore, unless the row is orphaned)

    When ``is_orphan`` is True the backup's owning game is no longer in the
    library, so restore is impossible ("DLL information not found in
    database") — the restore affordance is omitted entirely and only delete
    is offered.
    """

    def __init__(
        self,
        backup: GameDLLBackup,
        is_dark: bool,
        on_restore: Callable[[GameDLLBackup], None] | None = None,
        on_delete: Callable[[GameDLLBackup], None] | None = None,
        is_orphan: bool = False,
    ):
        self.backup = backup
        self._on_restore = on_restore
        self._on_delete = on_delete
        self._is_dark = is_dark
        self._is_orphan = is_orphan

        # Row controls (data columns + spacer). Action buttons are appended
        # afterwards so the restore button can be conditionally omitted for
        # orphaned rows.
        row_controls = [
            ft.Icon(
                ft.Icons.DESCRIPTION,
                size=16,
                color=MD3Colors.get_text_secondary(is_dark),
            ),
            ft.Text(
                backup.dll_filename,
                size=12,
                weight=ft.FontWeight.W_500,
                color=MD3Colors.get_text_primary(is_dark),
                width=100,
                no_wrap=True,
                tooltip=backup.dll_filename,
            ),
            ft.Text(
                backup.original_version or "N/A",
                size=11,
                color=MD3Colors.get_text_secondary(is_dark),
                width=80,
                no_wrap=True,
            ),
            ft.Text(
                self._format_date(backup.backup_created_at),
                size=11,
                color=MD3Colors.get_text_secondary(is_dark),
                width=80,
                no_wrap=True,
            ),
            ft.Text(
                self._format_size(backup.backup_size),
                size=11,
                color=MD3Colors.get_text_secondary(is_dark),
                width=60,
                no_wrap=True,
            ),
            ft.Container(expand=True),  # Spacer
        ]

        # Restore is only meaningful for linked games — omit it for orphans.
        if not is_orphan:
            row_controls.append(
                ft.IconButton(
                    icon=ft.Icons.RESTORE,
                    icon_size=18,
                    icon_color=MD3Colors.get_primary(is_dark),
                    tooltip="Restore this backup",
                    on_click=self._handle_restore,
                    style=ft.ButtonStyle(padding=ft.Padding.all(4)),
                    width=32,
                    height=32,
                )
            )

        row_controls.append(
            ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_size=18,
                icon_color=MD3Colors.get_error(is_dark),
                tooltip="Delete backup",
                on_click=self._handle_delete,
                style=ft.ButtonStyle(padding=ft.Padding.all(4)),
                width=32,
                height=32,
            )
        )

        # Build row content
        super().__init__(
            content=ft.Row(
                controls=row_controls,
                spacing=8,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=6),
            border_radius=4,
            on_hover=self._on_hover,
        )

    def _format_date(self, dt: datetime) -> str:
        """Format datetime for compact display"""
        return dt.strftime("%Y-%m-%d")

    def _format_size(self, size_bytes: int) -> str:
        """Format file size for display"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.1f} MB"

    def _handle_restore(self, e):
        """Handle restore button click"""
        if self._on_restore:
            self._on_restore(self.backup)

    def _handle_delete(self, e):
        """Handle delete button click"""
        if self._on_delete:
            self._on_delete(self.backup)

    def _on_hover(self, e):
        """Handle hover state for visual feedback"""
        if e.data is True or e.data == "true":
            self.bgcolor = MD3Colors.get_surface_variant(self._is_dark)
        else:
            self.bgcolor = None
        self.update()


class BackupGroup(ThemeAwareMixin, ft.ExpansionTile):
    """
    Groups backups by game using ExpansionTile.

    Collapsed state shows:
    - Game icon
    - Game name
    - Most-recent backup date (subtitle)
    - Backup count badge + total size
    - Restore All button (linked groups only)
    - Expand chevron (rotates when expanded)

    Expanded state shows:
    - All BackupRow entries for this game

    When ``is_orphan`` is True the owning game is no longer in the library.
    Restore (per-row and Restore All) fails with "DLL information not found in
    database", so every restore affordance is omitted — only delete is offered.

    Performance: Uses is_isolated=True for independent updates.
    """

    def is_isolated(self):
        """Isolated controls are excluded from parent's update digest."""
        return True

    def __init__(
        self,
        game_name: str,
        game_id: int,
        backups: list[GameDLLBackup],
        page: ft.Page,
        logger,
        on_restore: Callable[[GameDLLBackup], None] | None = None,
        on_delete: Callable[[GameDLLBackup], None] | None = None,
        on_restore_all: Callable[[int, str], None] | None = None,
        art_path: str | None = None,
        is_orphan: bool = False,
    ):
        """
        Initialize BackupGroup.

        Args:
            game_name: Display name of the game
            game_id: Database ID of the game
            backups: List of GameDLLBackup entries for this game
            page: Flet page reference
            logger: Logger instance
            on_restore: Callback for restoring a single backup
            on_delete: Callback for deleting a single backup
            on_restore_all: Callback for restoring all backups (game_id, game_name)
            art_path: Optional local filesystem path to a cached Steam artwork
                WebP for this game (batch-resolved by the caller - no lookups
                happen here). When None, the header falls back to the generic
                folder icon.
            is_orphan: When True this group represents backups whose owning game
                is no longer in the library. Restore is impossible for these, so
                the Restore All button and per-row restore buttons are hidden;
                only delete remains available.
        """
        self.game_name = game_name
        self.game_id = game_id
        self.backups = backups
        self._page_ref = page
        self.logger = logger
        self._on_restore = on_restore
        self._on_delete = on_delete
        self._on_restore_all = on_restore_all
        self._art_path = art_path
        self._is_orphan = is_orphan

        # Get theme registry and state
        self._registry = get_theme_registry()
        self._theme_priority = 25  # Cards are mid-priority
        is_dark = self._registry.is_dark

        # Build backup rows (orphan rows omit their restore button)
        backup_rows = [
            BackupRow(backup, is_dark, on_restore, on_delete, is_orphan=is_orphan)
            for backup in backups
        ]
        self._backup_rows = backup_rows

        # Leading control: cached artwork thumbnail when available, otherwise
        # the generic folder icon (unchanged fallback behavior).
        self._leading_icon = None
        self._leading_thumbnail = None
        if art_path:
            self._leading_thumbnail = ft.Image(
                src=art_path,
                fit=ft.BoxFit.COVER,
                width=66,
                height=30,
                # Defensive: if the cached file has since been removed from
                # disk, degrade to the same fallback icon rather than a
                # broken-image glyph.
                error_content=ft.Icon(
                    ft.Icons.FOLDER_SPECIAL,
                    color=MD3Colors.get_primary(is_dark),
                    size=18,
                ),
            )
            leading_control = ft.Container(
                width=66,
                height=30,
                border_radius=6,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=self._leading_thumbnail,
            )
        else:
            self._leading_icon = ft.Icon(
                ft.Icons.FOLDER_SPECIAL,
                color=MD3Colors.get_primary(is_dark),
                size=24,
            )
            leading_control = self._leading_icon

        # Title text
        self._title_text = ft.Text(
            game_name,
            size=14,
            weight=ft.FontWeight.W_500,
            color=MD3Colors.get_text_primary(is_dark),
            no_wrap=True,
            expand=True,
        )

        # Subtitle: most-recent backup date (formatted like BackupRow / the
        # rest of the app: YYYY-MM-DD). Groups always contain >= 1 backup.
        most_recent = max(
            (b.backup_created_at for b in backups),
            default=None,
        )
        self._subtitle_text = ft.Text(
            f"Last backup · {most_recent.strftime('%Y-%m-%d')}" if most_recent else "",
            size=11,
            color=MD3Colors.get_text_secondary(is_dark),
        )

        # Backup count badge
        backup_count = len(backups)
        self._count_badge = ft.Container(
            content=ft.Text(
                f"{backup_count} backup{'s' if backup_count != 1 else ''}",
                size=11,
                color=MD3Colors.get_text_secondary(is_dark),
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            border_radius=12,
            bgcolor=MD3Colors.get_surface_variant(is_dark),
        )

        # Total size badge
        total_size = sum(b.backup_size for b in backups)
        self._size_text = ft.Text(
            self._format_total_size(total_size),
            size=11,
            color=MD3Colors.get_text_secondary(is_dark),
        )

        # Restore All button — only for linked groups. Orphan groups cannot be
        # restored, so the button is not created at all (self._restore_all_btn
        # stays None; apply_theme guards on it existing).
        self._restore_all_btn: ft.TextButton | None = None
        if not is_orphan:
            self._restore_all_btn = ft.TextButton(
                "Restore All",
                icon=ft.Icons.RESTORE,
                on_click=self._handle_restore_all,
                style=ft.ButtonStyle(
                    color=MD3Colors.get_primary(is_dark),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                ),
                height=32,
            )

        # Expand affordance: a chevron that rotates 180° when expanded. Because
        # a custom `trailing` replaces ExpansionTile's default rotating arrow
        # (show_trailing_icon only builds one when trailing is None), we supply
        # our own and drive its rotation from on_change.
        self._chevron = ft.Icon(
            ft.Icons.EXPAND_MORE,
            size=22,
            color=MD3Colors.get_text_secondary(is_dark),
            rotate=0,
            animate_rotation=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )

        # Trailing row: count badge, total size, (Restore All if linked), chevron
        trailing_controls = [self._count_badge, self._size_text]
        if self._restore_all_btn is not None:
            trailing_controls.append(self._restore_all_btn)
        trailing_controls.append(self._chevron)
        self._trailing_row = ft.Row(
            controls=trailing_controls,
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Initialize ExpansionTile with Material Design 3 styling
        super().__init__(
            leading=leading_control,
            title=self._title_text,
            subtitle=self._subtitle_text,
            trailing=self._trailing_row,
            controls=backup_rows,
            expanded=False,
            on_change=self._handle_expansion_change,
            bgcolor=ft.Colors.TRANSPARENT,
            collapsed_bgcolor=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=8),
            maintain_state=True,
            text_color=MD3Colors.get_on_surface(is_dark),
            icon_color=MD3Colors.get_primary(is_dark),
            collapsed_text_color=MD3Colors.get_on_surface(is_dark),
            collapsed_icon_color=MD3Colors.get_primary(is_dark),
            tile_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            controls_padding=ft.Padding.only(left=24, right=12, bottom=8),
            animate_opacity=ft.Animation(80, ft.AnimationCurve.EASE_OUT),
        )

        # Register for theme updates
        self._register_theme_aware()

    def _format_total_size(self, size_bytes: int) -> str:
        """Format total backup size for display"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _handle_restore_all(self, e):
        """Handle Restore All button click"""
        if self._on_restore_all:
            self._on_restore_all(self.game_id, self.game_name)

    def _handle_expansion_change(self, e):
        """Rotate the chevron to reflect the expand/collapse state.

        ExpansionTile.on_change delivers the post-change ``expanded`` state via
        ``e.data`` (a boolean, though the client may serialize it as the string
        "true"/"false"). A 180° rotation flips EXPAND_MORE into an up-chevron.
        """
        expanded = e.data is True or e.data == "true"
        if hasattr(self, "_chevron"):
            self._chevron.rotate = math.pi if expanded else 0
            try:
                self.update()
            except Exception:
                pass  # View may have detached mid-interaction

    def update_backups(self, backups: list[GameDLLBackup]) -> None:
        """
        Update the backup list without rebuilding the entire component.

        Args:
            backups: New list of backups for this game
        """
        self.backups = backups
        is_dark = self._registry.is_dark

        # Rebuild backup rows (preserve orphan restore-hiding)
        backup_rows = [
            BackupRow(
                backup, is_dark, self._on_restore, self._on_delete,
                is_orphan=getattr(self, '_is_orphan', False),
            )
            for backup in backups
        ]
        self._backup_rows = backup_rows
        self.controls = backup_rows

        # Update count badge
        backup_count = len(backups)
        if hasattr(self._count_badge, 'content') and self._count_badge.content:
            self._count_badge.content.value = f"{backup_count} backup{'s' if backup_count != 1 else ''}"

        # Update total size
        total_size = sum(b.backup_size for b in backups)
        self._size_text.value = self._format_total_size(total_size)

        if self._page_ref:
            self.update()

    def remove_backup(self, backup_id: int) -> bool:
        """
        Remove a single backup from the group.

        Args:
            backup_id: ID of the backup to remove

        Returns:
            True if backup was found and removed, False otherwise
        """
        for i, backup in enumerate(self.backups):
            if backup.id == backup_id:
                self.backups.pop(i)
                self.update_backups(self.backups)
                return True
        return False

    def get_themed_properties(self) -> dict[str, tuple[str, str]]:
        """Return themed property mappings for cascade updates"""
        return {
            "text_color": MD3Colors.get_themed_pair("on_surface"),
            "collapsed_text_color": MD3Colors.get_themed_pair("on_surface"),
            "icon_color": MD3Colors.get_themed_pair("primary"),
            "collapsed_icon_color": MD3Colors.get_themed_pair("primary"),
            "_leading_icon.color": MD3Colors.get_themed_pair("primary"),
            "_title_text.color": MD3Colors.get_themed_pair("text_primary"),
            "_subtitle_text.color": MD3Colors.get_themed_pair("text_secondary"),
            "_size_text.color": MD3Colors.get_themed_pair("text_secondary"),
            "_chevron.color": MD3Colors.get_themed_pair("text_secondary"),
        }

    async def apply_theme(self, is_dark: bool, delay_ms: int = 0) -> None:
        """Apply theme with optional cascade delay"""

        if delay_ms > 0:
            await anyio.sleep(delay_ms / 1000)

        try:
            # Apply base properties from get_themed_properties
            properties = self.get_themed_properties()
            for prop_path, (dark_val, light_val) in properties.items():
                value = dark_val if is_dark else light_val
                self._set_nested_property(prop_path, value)

            # Update count badge styling
            if hasattr(self, '_count_badge'):
                self._count_badge.bgcolor = MD3Colors.get_surface_variant(is_dark)
                if self._count_badge.content:
                    self._count_badge.content.color = MD3Colors.get_text_secondary(is_dark)

            # Update restore all button styling (linked groups only; orphan
            # groups leave _restore_all_btn as None)
            if getattr(self, '_restore_all_btn', None) is not None:
                self._restore_all_btn.style = ft.ButtonStyle(
                    color=MD3Colors.get_primary(is_dark),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                )

            # Rebuild backup rows with new theme (preserve orphan restore-hiding)
            if hasattr(self, '_backup_rows') and self.backups:
                backup_rows = [
                    BackupRow(
                        backup, is_dark, self._on_restore, self._on_delete,
                        is_orphan=getattr(self, '_is_orphan', False),
                    )
                    for backup in self.backups
                ]
                self._backup_rows = backup_rows
                self.controls = backup_rows

            if hasattr(self, 'update'):
                self.update()

        except Exception:
            pass  # Silent fail - component may have been garbage collected


# Export public API
__all__ = [
    'BackupGroup',
    'BackupRow',
]
