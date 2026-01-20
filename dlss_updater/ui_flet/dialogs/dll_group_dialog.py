"""
DLL Group Dialog
Displays DLLs grouped by technology with collapsible panels for batch updates/restores.

Features:
- Groups DLLs by technology (DLSS, XeSS, FSR, Streamline, DirectStorage)
- Shows combined status per group (e.g., "2/3 up to date")
- Batch update/restore per group
- Expandable panels to view individual DLLs
"""

import logging
from dataclasses import dataclass
from typing import Callable

import flet as ft

from dlss_updater.constants import DLL_GROUPS, DLL_TYPE_MAP
from dlss_updater.database import GameDLL
from dlss_updater.models import Game, MergedGame
from dlss_updater.ui_flet.theme.colors import TechnologyColors, MD3Colors


@dataclass
class GroupStatus:
    """Status summary for a DLL group"""
    group_name: str
    dll_count: int
    updates_available: int
    has_backups: bool
    dlls: list[GameDLL]
    oldest_version: str | None = None
    newest_version: str | None = None
    latest_available_version: str | None = None


class DLLGroupDialog:
    """
    Dialog showing DLLs grouped by technology with collapsible ExpansionPanels.

    Features:
    - Technology groups (DLSS, XeSS, FSR, Streamline, DirectStorage)
    - Combined status per group
    - Update/Restore buttons per group
    - Individual DLL details when expanded
    """

    def __init__(
        self,
        page: ft.Page,
        logger: logging.Logger,
        game: Game | MergedGame,
        dlls: list[GameDLL],
        backup_groups: dict[str, list] | None,
        on_update: Callable[[Game | MergedGame, str], None],
        on_restore: Callable[[Game | MergedGame, str], None],
    ):
        self.page = page
        self.logger = logger

        # Handle MergedGame
        if isinstance(game, MergedGame):
            self.game = game.primary_game
            self.merged_game = game
        else:
            self.game = game
            self.merged_game = None

        self.dlls = dlls
        self.backup_groups = backup_groups or {}
        self.on_update_callback = on_update
        self.on_restore_callback = on_restore

        # Track dialog for close handler
        self.dialog: ft.AlertDialog | None = None

    async def show(self):
        """Build and display the dialog"""
        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        # Group DLLs by technology
        grouped_dlls = self._group_dlls()

        if not grouped_dlls:
            # No DLLs found - show empty state
            self.logger.warning(f"No DLLs to display for {self.game.name}")
            self._show_empty_dialog()
            return

        # Build expansion panels for each group
        panels = []
        for group_name in ["DLSS", "Streamline", "XeSS", "FSR", "DirectStorage"]:
            if group_name not in grouped_dlls:
                continue

            group_dlls = grouped_dlls[group_name]
            status = self._calculate_group_status(group_name, group_dlls)
            panel = self._build_expansion_panel(group_name, group_dlls, status)
            panels.append(panel)

        # Add "Other" group if any ungrouped DLLs
        if "Other" in grouped_dlls:
            group_dlls = grouped_dlls["Other"]
            status = self._calculate_group_status("Other", group_dlls)
            panel = self._build_expansion_panel("Other", group_dlls, status)
            panels.append(panel)

        # Create expansion panel list
        expansion_list = ft.ExpansionPanelList(
            controls=panels,
            elevation=0,
            expand_icon_color=MD3Colors.PRIMARY,
        )

        # Calculate total stats for header
        total_updates = sum(
            self._calculate_group_status(g, dlls).updates_available
            for g, dlls in grouped_dlls.items()
        )
        total_dlls = len(self.dlls)

        # Status summary
        if total_updates > 0:
            status_text = f"{total_updates} update{'s' if total_updates != 1 else ''} available"
            status_color = MD3Colors.WARNING
            status_icon = ft.Icons.ARROW_UPWARD
        else:
            status_text = "All DLLs up to date"
            status_color = MD3Colors.SUCCESS
            status_icon = ft.Icons.CHECK_CIRCLE

        # Dialog content
        content = ft.Container(
            content=ft.Column(
                controls=[
                    # Game info header
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=24, color=MD3Colors.PRIMARY),
                                ft.Column(
                                    controls=[
                                        ft.Text(
                                            self.game.name,
                                            size=18,
                                            weight=ft.FontWeight.BOLD,
                                        ),
                                        ft.Row(
                                            controls=[
                                                ft.Text(
                                                    f"{total_dlls} DLL{'s' if total_dlls != 1 else ''}",
                                                    size=12,
                                                    color=ft.Colors.GREY,
                                                ),
                                                ft.Container(width=8),
                                                ft.Icon(status_icon, size=14, color=status_color),
                                                ft.Text(status_text, size=12, color=status_color),
                                            ],
                                            spacing=4,
                                        ),
                                    ],
                                    spacing=2,
                                    tight=True,
                                ),
                            ],
                            spacing=12,
                        ),
                        padding=ft.padding.only(bottom=12),
                    ),
                    ft.Divider(height=1),
                    # Scrollable expansion panels
                    ft.Container(
                        content=ft.Column(
                            controls=[expansion_list],
                            scroll=ft.ScrollMode.AUTO,
                        ),
                        height=450,
                        padding=ft.padding.only(top=8),
                    ),
                ],
                spacing=0,
            ),
            width=700,
            padding=16,
        )

        # Create dialog
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("DLL Details"),
            content=content,
            actions=[
                ft.TextButton("Close", on_click=lambda e: self.page.close(self.dialog)),
            ],
        )

        self.page.open(self.dialog)

    def _show_empty_dialog(self):
        """Show empty state dialog when no DLLs found"""
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("DLL Details"),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Icon(ft.Icons.INFO_OUTLINE, size=48, color=ft.Colors.GREY),
                        ft.Text("No DLLs found for this game", color=ft.Colors.GREY),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                width=400,
                height=200,
            ),
            actions=[
                ft.TextButton("Close", on_click=lambda e: self.page.close(self.dialog)),
            ],
        )
        self.page.open(self.dialog)

    def _group_dlls(self) -> dict[str, list[GameDLL]]:
        """Group DLLs by technology using DLL_GROUPS constant"""
        groups: dict[str, list[GameDLL]] = {}
        ungrouped: list[GameDLL] = []

        for dll in self.dlls:
            dll_filename = dll.dll_filename.lower() if dll.dll_filename else ""

            # Find which group this DLL belongs to
            matched = False
            for group_name, group_dll_names in DLL_GROUPS.items():
                if dll_filename in [d.lower() for d in group_dll_names]:
                    if group_name not in groups:
                        groups[group_name] = []
                    groups[group_name].append(dll)
                    matched = True
                    break

            if not matched:
                ungrouped.append(dll)

        # Add ungrouped DLLs to "Other" group if any exist
        if ungrouped:
            groups["Other"] = ungrouped

        return groups

    def _calculate_group_status(
        self,
        group_name: str,
        group_dlls: list[GameDLL]
    ) -> GroupStatus:
        """Calculate status for a DLL group"""
        from dlss_updater.config import LATEST_DLL_VERSIONS
        from dlss_updater.updater import parse_version

        updates_available = 0
        versions: list[str] = []
        latest_versions: list[str] = []

        for dll in group_dlls:
            if dll.current_version:
                versions.append(dll.current_version)

            # Check for update
            if dll.dll_filename:
                latest = LATEST_DLL_VERSIONS.get(dll.dll_filename.lower())
                if latest:
                    latest_versions.append(latest)
                    if dll.current_version:
                        try:
                            if parse_version(dll.current_version) < parse_version(latest):
                                updates_available += 1
                        except Exception as e:
                            self.logger.warning(f"Version parse error: {e}")

        # Check for backups - map group name to backup keys
        has_backups = False
        for backup_key in self.backup_groups.keys():
            # Backup keys are dll_type (e.g., "DLSS DLL"), not group name
            # Check if any backup key contains the group name
            if group_name.upper() in backup_key.upper() or backup_key in DLL_TYPE_MAP.values():
                # Check if this dll_type belongs to this group
                for dll in group_dlls:
                    if dll.dll_type == backup_key:
                        has_backups = True
                        break
            if has_backups:
                break

        return GroupStatus(
            group_name=group_name,
            dll_count=len(group_dlls),
            updates_available=updates_available,
            has_backups=has_backups or group_name in self.backup_groups,
            dlls=group_dlls,
            oldest_version=min(versions) if versions else None,
            newest_version=max(versions) if versions else None,
            latest_available_version=max(latest_versions) if latest_versions else None,
        )

    def _build_expansion_panel(
        self,
        group_name: str,
        group_dlls: list[GameDLL],
        status: GroupStatus
    ) -> ft.ExpansionPanel:
        """Build collapsible panel for a technology group"""
        return ft.ExpansionPanel(
            header=self._build_group_header(group_name, status),
            content=self._build_group_content(group_name, group_dlls, status),
            can_tap_header=True,
            expanded=status.updates_available > 0,  # Auto-expand groups with updates
        )

    def _build_group_header(
        self,
        group_name: str,
        status: GroupStatus
    ) -> ft.Container:
        """Build group header with status and action buttons"""
        tech_color = TechnologyColors.get_color(group_name)

        # Status badge
        if status.updates_available > 0:
            status_badge = ft.Container(
                content=ft.Text(
                    f"{status.updates_available} update{'s' if status.updates_available != 1 else ''}",
                    size=11,
                    color=ft.Colors.WHITE,
                    weight=ft.FontWeight.BOLD,
                ),
                bgcolor=MD3Colors.WARNING,
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                border_radius=12,
            )
        else:
            status_badge = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.CHECK_CIRCLE, size=14, color=MD3Colors.SUCCESS),
                        ft.Text("Up to date", size=11, color=MD3Colors.SUCCESS),
                    ],
                    spacing=4,
                    tight=True,
                ),
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
            )

        # Update button
        update_button = ft.ElevatedButton(
            "Update",
            icon=ft.Icons.UPDATE,
            on_click=lambda e: self._on_update_clicked(group_name),
            disabled=status.updates_available == 0,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: tech_color if status.updates_available > 0 else ft.Colors.GREY_700,
                    ft.ControlState.DISABLED: ft.Colors.GREY_700,
                },
                color=ft.Colors.WHITE,
                padding=ft.padding.symmetric(horizontal=12, vertical=8),
            ),
            height=32,
        )

        # Restore button
        restore_button = ft.ElevatedButton(
            "Restore",
            icon=ft.Icons.RESTORE,
            on_click=lambda e: self._on_restore_clicked(group_name),
            disabled=not status.has_backups,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: MD3Colors.SUCCESS if status.has_backups else ft.Colors.GREY_700,
                    ft.ControlState.DISABLED: ft.Colors.GREY_700,
                },
                color=ft.Colors.WHITE,
                padding=ft.padding.symmetric(horizontal=12, vertical=8),
            ),
            height=32,
        )

        return ft.Container(
            content=ft.Row(
                controls=[
                    # Technology color indicator bar
                    ft.Container(
                        width=4,
                        height=40,
                        bgcolor=tech_color,
                        border_radius=2,
                    ),
                    ft.Container(width=8),
                    # Group name and count
                    ft.Column(
                        controls=[
                            ft.Text(
                                group_name,
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.WHITE,
                            ),
                            ft.Text(
                                f"{status.dll_count} DLL{'s' if status.dll_count != 1 else ''}",
                                size=12,
                                color=ft.Colors.GREY,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                    ft.Container(expand=True),  # Spacer
                    status_badge,
                    ft.Container(width=12),
                    update_button,
                    ft.Container(width=8),
                    restore_button,
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
        )

    def _build_group_content(
        self,
        group_name: str,
        group_dlls: list[GameDLL],
        status: GroupStatus
    ) -> ft.Container:
        """Build group content with DLL rows"""
        from dlss_updater.config import LATEST_DLL_VERSIONS

        dll_rows = []

        for dll in sorted(group_dlls, key=lambda d: d.dll_type):
            latest_version = LATEST_DLL_VERSIONS.get(dll.dll_filename.lower()) if dll.dll_filename else None
            row = self._build_dll_row(dll, latest_version)
            dll_rows.append(row)

        return ft.Container(
            content=ft.Column(
                controls=dll_rows,
                spacing=4,
            ),
            padding=ft.padding.only(left=32, right=16, top=8, bottom=16),
            bgcolor=MD3Colors.SURFACE_DIM,
        )

    def _build_dll_row(
        self,
        dll: GameDLL,
        latest_version: str | None
    ) -> ft.Container:
        """Build a row for an individual DLL"""
        from dlss_updater.updater import parse_version

        # Check if update available
        update_available = False
        if dll.current_version and latest_version:
            try:
                update_available = parse_version(dll.current_version) < parse_version(latest_version)
            except Exception:
                pass

        # Version display (truncate long versions)
        current_ver_text = dll.current_version[:12] if dll.current_version else "Unknown"
        latest_ver_text = latest_version[:12] if latest_version else "N/A"

        # Status icon
        if update_available:
            status_icon = ft.Icon(ft.Icons.ARROW_UPWARD, size=16, color=MD3Colors.WARNING)
        else:
            status_icon = ft.Icon(ft.Icons.CHECK_CIRCLE, size=16, color=MD3Colors.SUCCESS)

        return ft.Container(
            content=ft.Row(
                controls=[
                    status_icon,
                    ft.Container(width=8),
                    ft.Column(
                        controls=[
                            ft.Text(
                                dll.dll_type,
                                size=14,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.WHITE,
                            ),
                            ft.Text(
                                dll.dll_filename or "Unknown",
                                size=11,
                                color=ft.Colors.GREY,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                    ft.Container(expand=True),  # Spacer
                    ft.Column(
                        controls=[
                            ft.Text(
                                f"Current: {current_ver_text}",
                                size=12,
                                color=ft.Colors.GREY,
                            ),
                            ft.Text(
                                f"Latest: {latest_ver_text}",
                                size=12,
                                color=MD3Colors.WARNING if update_available else ft.Colors.GREY,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.padding.all(12),
            bgcolor=MD3Colors.SURFACE,
            border_radius=8,
        )

    def _on_update_clicked(self, group_name: str):
        """Handle Update button click"""
        self.logger.info(f"Update clicked for group: {group_name}, game: {self.game.name}")

        # Close dialog
        if self.dialog:
            self.page.close(self.dialog)

        # Invoke callback with merged game if available, otherwise primary game
        game_to_update = self.merged_game if self.merged_game else self.game
        if self.on_update_callback:
            self.on_update_callback(game_to_update, group_name)

    def _on_restore_clicked(self, group_name: str):
        """Handle Restore button click"""
        self.logger.info(f"Restore clicked for group: {group_name}, game: {self.game.name}")

        # Close dialog
        if self.dialog:
            self.page.close(self.dialog)

        # Invoke callback with merged game if available, otherwise primary game
        game_to_restore = self.merged_game if self.merged_game else self.game
        if self.on_restore_callback:
            self.on_restore_callback(game_to_restore, group_name)
