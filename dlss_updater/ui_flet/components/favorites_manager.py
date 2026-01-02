"""
Favorites & Game Grouping Components
Provides UI components for favoriting games, creating tags, and filtering/sorting

Components:
- FavoriteButton: Animated star button for GameCard
- SortFilterBar: Dropdown for sorting + tag filter chips
- TagManagerDialog: Create/edit/delete tags with color picker
- AssignTagsDialog: Assign multiple tags to a game
"""

import asyncio
from typing import List, Optional, Callable, Dict
import flet as ft

from dlss_updater.database import db_manager
from dlss_updater.ui_flet.theme.colors import MD3Colors, Shadows
from dlss_updater.ui_flet.theme.md3_system import MD3Spacing, MD3Motion


# Predefined tag colors
TAG_COLORS = [
    "#E91E63",  # Pink
    "#9C27B0",  # Purple
    "#673AB7",  # Deep Purple
    "#3F51B5",  # Indigo
    "#2196F3",  # Blue
    "#03A9F4",  # Light Blue
    "#00BCD4",  # Cyan
    "#009688",  # Teal
    "#4CAF50",  # Green
    "#8BC34A",  # Light Green
    "#CDDC39",  # Lime
    "#FFEB3B",  # Yellow
    "#FFC107",  # Amber
    "#FF9800",  # Orange
    "#FF5722",  # Deep Orange
    "#795548",  # Brown
    "#9E9E9E",  # Grey
    "#607D8B",  # Blue Grey
]


class FavoriteButton(ft.Container):
    """Animated star button for favoriting games"""

    def __init__(
        self,
        game_id: int,
        is_favorite: bool = False,
        on_toggle: Optional[Callable[[int, bool], None]] = None,
    ):
        super().__init__()
        self.game_id = game_id
        self.is_favorite = is_favorite
        self.on_toggle_callback = on_toggle

        # Icon button
        self.icon_button = ft.IconButton(
            icon=ft.Icons.STAR if is_favorite else ft.Icons.STAR_BORDER,
            icon_color=MD3Colors.WARNING if is_favorite else MD3Colors.ON_SURFACE_VARIANT,
            icon_size=20,
            on_click=self._on_clicked,
            tooltip="Favorite" if not is_favorite else "Unfavorite",
            animate_rotation=ft.Animation(300, ft.AnimationCurve.BOUNCE_OUT),
            animate_scale=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )

        self.content = self.icon_button
        self.width = 32
        self.height = 32

    async def _on_clicked(self, e):
        """Toggle favorite state with animation"""
        # Toggle state
        self.is_favorite = not self.is_favorite

        # Animate icon change
        self.icon_button.scale = 1.3
        if self.page:
            self.page.update()

        await asyncio.sleep(0.1)

        # Update icon
        self.icon_button.icon = ft.Icons.STAR if self.is_favorite else ft.Icons.STAR_BORDER
        self.icon_button.icon_color = MD3Colors.WARNING if self.is_favorite else MD3Colors.ON_SURFACE_VARIANT
        self.icon_button.tooltip = "Unfavorite" if self.is_favorite else "Favorite"
        self.icon_button.scale = 1.0

        # Rotate animation for extra flair
        self.icon_button.rotate = (self.icon_button.rotate or 0) + 0.5

        if self.page:
            self.page.update()

        # Update database
        await db_manager.set_game_favorite(self.game_id, self.is_favorite)

        # Callback
        if self.on_toggle_callback:
            result = self.on_toggle_callback(self.game_id, self.is_favorite)
            if asyncio.iscoroutine(result):
                await result

    def set_favorite(self, is_favorite: bool):
        """Update favorite state without animation"""
        self.is_favorite = is_favorite
        self.icon_button.icon = ft.Icons.STAR if is_favorite else ft.Icons.STAR_BORDER
        self.icon_button.icon_color = MD3Colors.WARNING if is_favorite else MD3Colors.ON_SURFACE_VARIANT
        self.icon_button.tooltip = "Unfavorite" if is_favorite else "Favorite"
        if self.page:
            self.page.update()


class TagChip(ft.Container):
    """Individual tag chip with color and close button"""

    def __init__(
        self,
        tag_name: str,
        tag_color: str,
        on_remove: Optional[Callable[[str], None]] = None,
        removable: bool = True,
    ):
        super().__init__()
        self.tag_name = tag_name
        self.tag_color = tag_color
        self.on_remove_callback = on_remove

        # Chip content
        controls = [
            ft.Text(
                tag_name,
                size=12,
                color=ft.Colors.WHITE,
                weight=ft.FontWeight.W_500,
            ),
        ]

        if removable and on_remove:
            controls.append(
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    icon_size=14,
                    icon_color=ft.Colors.WHITE,
                    on_click=lambda e: on_remove(tag_name),
                    width=18,
                    height=18,
                    style=ft.ButtonStyle(padding=ft.padding.all(0)),
                )
            )

        self.content = ft.Row(
            controls=controls,
            spacing=4,
            tight=True,
        )
        self.bgcolor = tag_color
        self.border_radius = 12
        self.padding = ft.padding.symmetric(horizontal=8, vertical=4)
        self.animate = ft.Animation(150, ft.AnimationCurve.EASE_OUT)

        # Hover effect
        self.on_hover = self._on_hover

    def _on_hover(self, e):
        if e.data == "true":
            self.opacity = 0.85
        else:
            self.opacity = 1.0
        self.update()


class SortFilterBar(ft.Container):
    """Sort dropdown + tag filter chips + smart group buttons"""

    def __init__(
        self,
        on_sort_changed: Optional[Callable[[str], None]] = None,
        on_tag_filter_changed: Optional[Callable[[List[str]], None]] = None,
        on_group_filter_changed: Optional[Callable[[str], None]] = None,
    ):
        super().__init__()
        self.on_sort_changed_callback = on_sort_changed
        self.on_tag_filter_callback = on_tag_filter_changed
        self.on_group_filter_callback = on_group_filter_changed

        # State
        self.available_tags: List[Dict] = []  # Will be loaded from database
        self.active_tag_filters: List[str] = []
        self.active_group_filter: Optional[str] = None

        # Build UI
        self._build_ui()

    def _build_ui(self):
        """Build sort/filter bar UI"""
        # Sort dropdown
        self.sort_dropdown = ft.Dropdown(
            label="Sort by",
            value="name",
            options=[
                ft.dropdown.Option("name", "Name (A-Z)"),
                ft.dropdown.Option("name_desc", "Name (Z-A)"),
                ft.dropdown.Option("recent", "Recently Updated"),
                ft.dropdown.Option("favorite", "Favorites First"),
                ft.dropdown.Option("needs_update", "Needs Update"),
            ],
            width=180,
            border_color=MD3Colors.OUTLINE,
            focused_border_color=MD3Colors.PRIMARY,
            on_change=self._on_sort_changed,
        )

        # Smart group chips
        self.group_chips_row = ft.Row(
            controls=[
                self._create_group_chip("All Games", None),
                self._create_group_chip("Favorites", "favorites"),
                self._create_group_chip("Needs Update", "needs_update"),
                self._create_group_chip("Recently Updated", "recent"),
            ],
            spacing=8,
            wrap=True,
            run_spacing=8,
        )

        # Tag filter chips container
        self.tag_chips_row = ft.Row(
            controls=[],
            spacing=8,
            wrap=True,
            run_spacing=8,
        )

        # Manage tags button
        manage_tags_btn = ft.TextButton(
            "Manage Tags",
            icon=ft.Icons.LOCAL_OFFER,
            style=ft.ButtonStyle(color=MD3Colors.PRIMARY),
            on_click=self._on_manage_tags_clicked,
        )

        # Layout
        self.content = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        self.sort_dropdown,
                        ft.Container(expand=True),
                        manage_tags_btn,
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(height=8),
                ft.Column(
                    controls=[
                        ft.Text("Filter by:", size=12, color=MD3Colors.ON_SURFACE_VARIANT),
                        self.group_chips_row,
                    ],
                    spacing=4,
                ),
                ft.Container(height=8),
                ft.Column(
                    controls=[
                        ft.Text("Tags:", size=12, color=MD3Colors.ON_SURFACE_VARIANT),
                        self.tag_chips_row,
                    ],
                    spacing=4,
                    visible=False,  # Will show when tags are loaded
                ),
            ],
            spacing=0,
        )
        self.padding = 16
        self.bgcolor = MD3Colors.SURFACE_VARIANT
        self.border_radius = 12

    def _create_group_chip(self, label: str, group_id: Optional[str]) -> ft.Container:
        """Create a smart group filter chip"""
        is_active = self.active_group_filter == group_id

        chip = ft.Container(
            content=ft.Text(
                label,
                size=13,
                color=ft.Colors.WHITE if is_active else MD3Colors.ON_SURFACE,
                weight=ft.FontWeight.W_500,
            ),
            bgcolor=MD3Colors.PRIMARY if is_active else MD3Colors.SURFACE,
            border_radius=16,
            padding=ft.padding.symmetric(horizontal=12, vertical=6),
            border=ft.border.all(1, MD3Colors.OUTLINE if not is_active else "transparent"),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            on_click=lambda e, g=group_id: self._on_group_chip_clicked(g),
        )

        # Hover effect
        def on_hover(e, c=chip):
            if e.data == "true":
                c.scale = 1.05
            else:
                c.scale = 1.0
            c.update()

        chip.on_hover = on_hover

        return chip

    async def _on_group_chip_clicked(self, group_id: Optional[str]):
        """Handle group chip click"""
        # Toggle selection (clicking active chip deselects it)
        if self.active_group_filter == group_id:
            self.active_group_filter = None
        else:
            self.active_group_filter = group_id

        # Rebuild chips
        self.group_chips_row.controls = [
            self._create_group_chip("All Games", None),
            self._create_group_chip("Favorites", "favorites"),
            self._create_group_chip("Needs Update", "needs_update"),
            self._create_group_chip("Recently Updated", "recent"),
        ]

        if self.page:
            self.page.update()

        # Callback
        if self.on_group_filter_callback:
            result = self.on_group_filter_callback(self.active_group_filter)
            if asyncio.iscoroutine(result):
                await result

    async def _on_sort_changed(self, e):
        """Handle sort dropdown change"""
        if self.on_sort_changed_callback:
            result = self.on_sort_changed_callback(e.control.value)
            if asyncio.iscoroutine(result):
                await result

    async def load_tags(self):
        """Load available tags from database"""
        self.available_tags = await db_manager.get_all_tags()

        # Build tag chips
        if self.available_tags:
            self.tag_chips_row.controls = [
                self._create_tag_filter_chip(tag)
                for tag in self.available_tags
            ]
            # Show tag section
            self.content.controls[3].visible = True
        else:
            self.tag_chips_row.controls = []
            self.content.controls[3].visible = False

        if self.page:
            self.page.update()

    def _create_tag_filter_chip(self, tag: Dict) -> ft.Container:
        """Create a tag filter chip"""
        is_active = tag['name'] in self.active_tag_filters

        chip = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        width=8,
                        height=8,
                        bgcolor=tag['color'],
                        border_radius=4,
                    ),
                    ft.Text(
                        tag['name'],
                        size=13,
                        color=MD3Colors.ON_SURFACE,
                        weight=ft.FontWeight.W_500,
                    ),
                    ft.Icon(
                        ft.Icons.CHECK,
                        size=14,
                        color=MD3Colors.PRIMARY,
                        visible=is_active,
                    ),
                ],
                spacing=6,
                tight=True,
            ),
            bgcolor=MD3Colors.SURFACE if not is_active else f"{MD3Colors.PRIMARY}20",
            border_radius=16,
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
            border=ft.border.all(1, MD3Colors.PRIMARY if is_active else MD3Colors.OUTLINE),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            on_click=lambda e, t=tag: self._on_tag_chip_clicked(t),
        )

        return chip

    async def _on_tag_chip_clicked(self, tag: Dict):
        """Handle tag filter chip click"""
        # Toggle tag filter
        if tag['name'] in self.active_tag_filters:
            self.active_tag_filters.remove(tag['name'])
        else:
            self.active_tag_filters.append(tag['name'])

        # Rebuild chips
        self.tag_chips_row.controls = [
            self._create_tag_filter_chip(t)
            for t in self.available_tags
        ]

        if self.page:
            self.page.update()

        # Callback
        if self.on_tag_filter_callback:
            result = self.on_tag_filter_callback(self.active_tag_filters)
            if asyncio.iscoroutine(result):
                await result

    async def _on_manage_tags_clicked(self, e):
        """Open tag manager dialog"""
        dialog = TagManagerDialog(self.page, None)
        await dialog.show()
        # Reload tags after dialog closes
        await self.load_tags()


class TagManagerDialog:
    """Dialog for creating, editing, and deleting tags"""

    def __init__(self, page: ft.Page, logger):
        self.page = page
        self.logger = logger
        self.tags: List[Dict] = []

    async def show(self):
        """Show tag manager dialog"""
        # Load existing tags
        self.tags = await db_manager.get_all_tags()

        # Build tag list
        tag_list_controls = []
        for tag in self.tags:
            tag_list_controls.append(self._create_tag_list_item(tag))

        # Create new tag section
        self.new_tag_name = ft.TextField(
            label="Tag Name",
            hint_text="e.g., Completed, Wishlist, Multiplayer",
            width=300,
            border_color=MD3Colors.OUTLINE,
            focused_border_color=MD3Colors.PRIMARY,
        )

        self.new_tag_color = TAG_COLORS[0]
        color_chips = []
        for color in TAG_COLORS:
            color_chips.append(
                ft.Container(
                    width=32,
                    height=32,
                    bgcolor=color,
                    border_radius=16,
                    border=ft.border.all(2, "transparent"),
                    on_click=lambda e, c=color: self._on_color_selected(c),
                )
            )

        self.color_selector_row = ft.Row(
            controls=color_chips,
            spacing=8,
            wrap=True,
            run_spacing=8,
        )

        # Dialog
        dialog = ft.AlertDialog(
            title=ft.Text("Manage Tags"),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text("Existing Tags:", weight=ft.FontWeight.BOLD, size=14),
                        ft.Container(
                            content=ft.Column(
                                controls=tag_list_controls if tag_list_controls else [
                                    ft.Container(
                                        content=ft.Text("No tags yet", color=MD3Colors.ON_SURFACE_VARIANT),
                                        alignment=ft.alignment.center,
                                        height=60,
                                    )
                                ],
                                spacing=8,
                                scroll=ft.ScrollMode.AUTO,
                            ),
                            height=200,
                            bgcolor=MD3Colors.SURFACE,
                            border_radius=8,
                            padding=8,
                        ),
                        ft.Divider(),
                        ft.Text("Create New Tag:", weight=ft.FontWeight.BOLD, size=14),
                        self.new_tag_name,
                        ft.Text("Color:", size=12, color=MD3Colors.ON_SURFACE_VARIANT),
                        self.color_selector_row,
                    ],
                    spacing=12,
                    tight=True,
                ),
                width=500,
            ),
            actions=[
                ft.TextButton("Close", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("Create Tag", on_click=lambda e: self._create_tag(dialog)),
            ],
        )

        self.page.open(dialog)

    def _create_tag_list_item(self, tag: Dict) -> ft.Container:
        """Create a tag list item with delete button"""
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(width=12, height=12, bgcolor=tag['color'], border_radius=6),
                    ft.Text(tag['name'], size=14, expand=True),
                    ft.Text(f"{tag.get('game_count', 0)} games", size=12, color=MD3Colors.ON_SURFACE_VARIANT),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_color=MD3Colors.ERROR,
                        icon_size=18,
                        on_click=lambda e, t=tag: self._delete_tag(t),
                        tooltip="Delete tag",
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=MD3Colors.SURFACE_VARIANT,
            border_radius=8,
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
        )

    def _on_color_selected(self, color: str):
        """Handle color selection"""
        self.new_tag_color = color

        # Update border on selected color
        for i, control in enumerate(self.color_selector_row.controls):
            if TAG_COLORS[i] == color:
                control.border = ft.border.all(2, MD3Colors.PRIMARY)
            else:
                control.border = ft.border.all(2, "transparent")

        if self.page:
            self.page.update()

    async def _create_tag(self, dialog):
        """Create a new tag"""
        tag_name = self.new_tag_name.value.strip()

        if not tag_name:
            # Show error
            self.new_tag_name.error_text = "Tag name is required"
            if self.page:
                self.page.update()
            return

        # Create tag in database
        await db_manager.create_tag(tag_name, self.new_tag_color)

        # Close and reopen dialog to refresh list
        self.page.close(dialog)
        await self.show()

    async def _delete_tag(self, tag: Dict):
        """Delete a tag"""
        # Confirm deletion
        confirm_dialog = ft.AlertDialog(
            title=ft.Text("Delete Tag?"),
            content=ft.Text(f"Are you sure you want to delete the tag '{tag['name']}'? This will remove it from all games."),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self.page.close(confirm_dialog)),
                ft.FilledButton(
                    "Delete",
                    on_click=lambda e: self._confirm_delete_tag(tag, confirm_dialog),
                    style=ft.ButtonStyle(bgcolor=MD3Colors.ERROR),
                ),
            ],
        )
        self.page.open(confirm_dialog)

    async def _confirm_delete_tag(self, tag: Dict, confirm_dialog):
        """Confirm tag deletion"""
        await db_manager.delete_tag(tag['id'])
        self.page.close(confirm_dialog)
        # Refresh tag manager
        await self.show()


class AssignTagsDialog:
    """Dialog for assigning tags to a game"""

    def __init__(self, page: ft.Page, logger, game_id: int, game_name: str):
        self.page = page
        self.logger = logger
        self.game_id = game_id
        self.game_name = game_name

    async def show(self):
        """Show assign tags dialog"""
        # Load all tags
        all_tags = await db_manager.get_all_tags()

        # Load current tags for this game
        current_tags = await db_manager.get_game_tags(self.game_id)
        current_tag_names = [tag['name'] for tag in current_tags]

        # Build tag checkboxes
        tag_checkboxes = []
        for tag in all_tags:
            checkbox = ft.Checkbox(
                label=tag['name'],
                value=tag['name'] in current_tag_names,
                data=tag,
            )
            tag_row = ft.Row(
                controls=[
                    ft.Container(width=12, height=12, bgcolor=tag['color'], border_radius=6),
                    checkbox,
                ],
                spacing=8,
            )
            tag_checkboxes.append(tag_row)

        # Dialog
        dialog = ft.AlertDialog(
            title=ft.Text(f"Assign Tags - {self.game_name}"),
            content=ft.Container(
                content=ft.Column(
                    controls=tag_checkboxes if tag_checkboxes else [
                        ft.Text("No tags available. Create tags in the Tag Manager.", color=MD3Colors.ON_SURFACE_VARIANT)
                    ],
                    spacing=8,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=400,
                height=300,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self.page.close(dialog)),
                ft.FilledButton("Save", on_click=lambda e: self._save_tags(dialog, tag_checkboxes)),
            ],
        )

        self.page.open(dialog)

    async def _save_tags(self, dialog, tag_checkboxes):
        """Save tag assignments"""
        # Get selected tags
        selected_tag_ids = []
        for tag_row in tag_checkboxes:
            checkbox = tag_row.controls[1]
            if checkbox.value:
                selected_tag_ids.append(checkbox.data['id'])

        # Update database
        await db_manager.set_game_tags(self.game_id, selected_tag_ids)

        self.page.close(dialog)

        # Show success snackbar
        snackbar = ft.SnackBar(
            content=ft.Text("Tags updated successfully"),
            bgcolor=MD3Colors.PRIMARY,
        )
        self.page.overlay.append(snackbar)
        snackbar.open = True
        self.page.update()
