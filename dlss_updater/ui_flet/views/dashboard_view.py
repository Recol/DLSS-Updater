"""
DLL Version Dashboard View
Comprehensive dashboard showing DLL statistics, version matrix, update timeline, and technology distribution

Features:
- 4 stat cards (total games, DLLs updated, success rate, needs update count)
- Version matrix table (DLL types × installed versions)
- Timeline chart showing updates over time using Flet LineChart
- Pie chart showing technology distribution
- Recent updates list with expandable details
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import flet as ft

from dlss_updater.database import db_manager
from dlss_updater.ui_flet.theme.colors import MD3Colors, TechnologyColors, Shadows
from dlss_updater.ui_flet.theme.md3_system import MD3Spacing, MD3Motion, MD3Typography


class StatCard(ft.Container):
    """Single statistics card with icon, value, label, and trend indicator"""

    def __init__(
        self,
        title: str,
        value: str,
        icon: str,
        color: str,
        trend: Optional[str] = None,
        trend_up: bool = True,
    ):
        super().__init__()

        # Icon circle with glow
        icon_container = ft.Container(
            content=ft.Icon(icon, size=28, color=ft.Colors.WHITE),
            width=56,
            height=56,
            bgcolor=color,
            border_radius=28,
            alignment=ft.alignment.center,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=12,
                offset=ft.Offset(0, 4),
                color=f"{color}40",
            ),
        )

        # Value text (large, bold)
        value_text = ft.Text(
            value,
            size=36,
            weight=ft.FontWeight.BOLD,
            color=MD3Colors.ON_SURFACE,
        )

        # Title text
        title_text = ft.Text(
            title,
            size=14,
            color=MD3Colors.ON_SURFACE_VARIANT,
            weight=ft.FontWeight.W_500,
        )

        # Trend indicator (optional)
        trend_controls = []
        if trend:
            trend_icon = ft.Icons.TRENDING_UP if trend_up else ft.Icons.TRENDING_DOWN
            trend_color = MD3Colors.SUCCESS if trend_up else MD3Colors.ERROR
            trend_controls = [
                ft.Container(height=4),
                ft.Row(
                    controls=[
                        ft.Icon(trend_icon, size=16, color=trend_color),
                        ft.Text(trend, size=12, color=trend_color),
                    ],
                    spacing=4,
                    tight=True,
                ),
            ]

        # Layout
        card_content = ft.Row(
            controls=[
                icon_container,
                ft.Container(width=16),
                ft.Column(
                    controls=[
                        value_text,
                        title_text,
                        *trend_controls,
                    ],
                    spacing=2,
                    tight=True,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Container styling
        self.content = card_content
        self.bgcolor = MD3Colors.SURFACE_VARIANT
        self.border_radius = 12
        self.padding = 16
        self.shadow = Shadows.LEVEL_2
        self.animate = ft.Animation(200, ft.AnimationCurve.EASE_OUT)
        self.animate_scale = ft.Animation(200, ft.AnimationCurve.EASE_OUT)

        # Hover effect
        self.on_hover = self._on_hover

    def _on_hover(self, e):
        if e.data == "true":
            self.scale = 1.02
            self.shadow = Shadows.LEVEL_3
        else:
            self.scale = 1.0
            self.shadow = Shadows.LEVEL_2
        self.update()


class UpdateTimelineChart(ft.Container):
    """Line chart showing DLL updates over time (last 30 days)"""

    def __init__(self, timeline_data: List[Tuple[datetime, int]]):
        """
        Args:
            timeline_data: [(date, update_count), ...] - all 30 days filled
        """
        super().__init__()

        # Prepare data points for LineChart
        if not timeline_data:
            # Empty state
            self.content = ft.Column(
                controls=[
                    ft.Text(
                        "Update Timeline",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=MD3Colors.ON_SURFACE,
                    ),
                    ft.Container(height=16),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.SHOW_CHART, size=48, color=MD3Colors.ON_SURFACE_VARIANT),
                                ft.Text("No update history yet", color=MD3Colors.ON_SURFACE_VARIANT),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=8,
                        ),
                        height=200,
                        alignment=ft.alignment.center,
                    ),
                ],
            )
            self.padding = 16
            self.bgcolor = MD3Colors.SURFACE_VARIANT
            self.border_radius = 12
            self.shadow = Shadows.LEVEL_2
            return

        # Create line chart data points with proper date tooltips
        data_points = []
        max_count = max(count for _, count in timeline_data) if timeline_data else 1
        max_count = max(max_count, 1)  # Ensure at least 1 for proper axis scaling

        for i, (date, count) in enumerate(timeline_data):
            data_points.append(
                ft.LineChartDataPoint(
                    x=i,
                    y=count,
                    tooltip=f"{date.strftime('%b %d')}: {count} update{'s' if count != 1 else ''}",
                )
            )

        # Create X-axis labels showing dates (every 5 days to avoid crowding)
        bottom_axis_labels = []
        for i, (date, _) in enumerate(timeline_data):
            # Show label every 5 days, plus first and last
            if i % 5 == 0 or i == len(timeline_data) - 1:
                bottom_axis_labels.append(
                    ft.ChartAxisLabel(
                        value=i,
                        label=ft.Text(
                            date.strftime("%b %d"),
                            size=10,
                            color=MD3Colors.ON_SURFACE_VARIANT,
                        ),
                    )
                )

        # Create line chart
        chart = ft.LineChart(
            data_series=[
                ft.LineChartData(
                    data_points=data_points,
                    stroke_width=3,
                    color=MD3Colors.PRIMARY,
                    curved=True,
                    stroke_cap_round=True,
                    below_line_bgcolor=f"{MD3Colors.PRIMARY}20",
                )
            ],
            border=ft.Border(
                bottom=ft.BorderSide(1, MD3Colors.OUTLINE_VARIANT),
                left=ft.BorderSide(1, MD3Colors.OUTLINE_VARIANT),
            ),
            horizontal_grid_lines=ft.ChartGridLines(
                interval=max(1, max_count / 5),
                color=MD3Colors.OUTLINE_VARIANT,
                width=1,
            ),
            left_axis=ft.ChartAxis(
                labels_size=40,
                title=ft.Text("Updates", size=12, color=MD3Colors.ON_SURFACE_VARIANT),
            ),
            bottom_axis=ft.ChartAxis(
                labels=bottom_axis_labels,
                labels_size=40,
            ),
            tooltip_bgcolor=MD3Colors.SURFACE_DIM,
            min_y=0,
            max_y=max_count + 2,
            min_x=0,
            max_x=len(timeline_data) - 1 if timeline_data else 1,
        )

        self.content = ft.Column(
            controls=[
                ft.Text(
                    "Update Timeline (Last 30 Days)",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=MD3Colors.ON_SURFACE,
                ),
                ft.Container(height=8),
                ft.Container(
                    content=chart,
                    height=200,
                    padding=8,
                ),
            ],
            spacing=0,
        )
        self.padding = 16
        self.bgcolor = MD3Colors.SURFACE_VARIANT
        self.border_radius = 12
        self.shadow = Shadows.LEVEL_2


class TechnologyDistributionChart(ft.Container):
    """Pie chart showing DLL technology distribution by game"""

    def __init__(self, distribution_data: Dict[str, int]):
        """
        Args:
            distribution_data: {dll_type: count, ...}
        """
        super().__init__()

        if not distribution_data:
            # Empty state
            self.content = ft.Column(
                controls=[
                    ft.Text(
                        "Game Distribution",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=MD3Colors.ON_SURFACE,
                    ),
                    ft.Container(height=16),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.PIE_CHART, size=48, color=MD3Colors.ON_SURFACE_VARIANT),
                                ft.Text("No DLL data yet", color=MD3Colors.ON_SURFACE_VARIANT),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=8,
                        ),
                        height=250,
                        alignment=ft.alignment.center,
                    ),
                ],
            )
            self.padding = 16
            self.bgcolor = MD3Colors.SURFACE_VARIANT
            self.border_radius = 12
            self.shadow = Shadows.LEVEL_2
            return

        # Create pie chart sections
        total = sum(distribution_data.values())
        sections = []

        for dll_type, count in sorted(distribution_data.items(), key=lambda x: x[1], reverse=True):
            color = TechnologyColors.get_color(dll_type)
            percentage = (count / total) * 100

            sections.append(
                ft.PieChartSection(
                    value=count,
                    title=f"{percentage:.1f}%",
                    title_style=ft.TextStyle(
                        size=12,
                        color=ft.Colors.WHITE,
                        weight=ft.FontWeight.BOLD,
                    ),
                    color=color,
                    radius=80,
                )
            )

        # Create pie chart
        pie_chart = ft.PieChart(
            sections=sections,
            sections_space=2,
            center_space_radius=40,
            expand=False,
        )

        # Legend items (placed at top right)
        legend_items = []
        for dll_type, count in sorted(distribution_data.items(), key=lambda x: x[1], reverse=True):
            color = TechnologyColors.get_color(dll_type)
            percentage = (count / total) * 100

            legend_items.append(
                ft.Row(
                    controls=[
                        ft.Container(width=10, height=10, bgcolor=color, border_radius=2),
                        ft.Text(f"{dll_type}: {count} ({percentage:.1f}%)", size=11),
                    ],
                    spacing=6,
                    tight=True,
                )
            )

        # Header row with title on left, legend on right
        header_row = ft.Row(
            controls=[
                ft.Text(
                    "Game Distribution",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=MD3Colors.ON_SURFACE,
                ),
                ft.Container(expand=True),
                ft.Column(
                    controls=legend_items,
                    spacing=4,
                    alignment=ft.MainAxisAlignment.START,
                    horizontal_alignment=ft.CrossAxisAlignment.END,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        self.content = ft.Column(
            controls=[
                header_row,
                ft.Container(height=8),
                ft.Container(
                    content=pie_chart,
                    alignment=ft.alignment.center,
                    expand=True,
                ),
            ],
            spacing=0,
        )
        self.padding = 16
        self.bgcolor = MD3Colors.SURFACE_VARIANT
        self.border_radius = 12
        self.shadow = Shadows.LEVEL_2


class RecentUpdatesList(ft.Container):
    """List of recent DLL updates with expandable details"""

    def __init__(self, recent_updates: List[Dict]):
        """
        Args:
            recent_updates: [{game_name, dll_type, old_version, new_version, timestamp}, ...]
        """
        super().__init__()

        if not recent_updates:
            # Empty state
            self.content = ft.Column(
                controls=[
                    ft.Text(
                        "Recent Updates",
                        size=18,
                        weight=ft.FontWeight.BOLD,
                        color=MD3Colors.ON_SURFACE,
                    ),
                    ft.Container(height=16),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.HISTORY, size=48, color=MD3Colors.ON_SURFACE_VARIANT),
                                ft.Text("No recent updates", color=MD3Colors.ON_SURFACE_VARIANT),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=8,
                        ),
                        height=150,
                        alignment=ft.alignment.center,
                    ),
                ],
            )
            self.padding = 16
            self.bgcolor = MD3Colors.SURFACE_VARIANT
            self.border_radius = 12
            self.shadow = Shadows.LEVEL_2
            return

        # Create list tiles
        tiles = []
        for update in recent_updates[:10]:  # Show last 10
            color = TechnologyColors.get_color(update['dll_type'])

            # Time ago calculation
            timestamp = update['timestamp']
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp)
            time_ago = datetime.now() - timestamp
            if time_ago.days > 0:
                time_str = f"{time_ago.days}d ago"
            elif time_ago.seconds >= 3600:
                time_str = f"{time_ago.seconds // 3600}h ago"
            else:
                time_str = f"{time_ago.seconds // 60}m ago"

            tile = ft.ListTile(
                leading=ft.Icon(ft.Icons.UPDATE, color=color, size=24),
                title=ft.Text(update['game_name'], weight=ft.FontWeight.W_500, size=14),
                subtitle=ft.Row(
                    controls=[
                        ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
                        ft.Text(
                            f"{update['dll_type']}: {update['old_version'][:8]} → {update['new_version'][:8]}",
                            size=12,
                            color=MD3Colors.ON_SURFACE_VARIANT,
                        ),
                    ],
                    spacing=6,
                    tight=True,
                ),
                trailing=ft.Text(time_str, size=11, color=MD3Colors.ON_SURFACE_VARIANT),
                dense=True,
            )
            tiles.append(tile)

        self.content = ft.Column(
            controls=[
                ft.Text(
                    "Recent Updates",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=MD3Colors.ON_SURFACE,
                ),
                ft.Container(height=8),
                ft.Container(
                    content=ft.Column(
                        controls=tiles,
                        spacing=4,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    height=300,
                    bgcolor=MD3Colors.SURFACE,
                    border_radius=8,
                ),
            ],
            spacing=0,
        )
        self.padding = 16
        self.bgcolor = MD3Colors.SURFACE_VARIANT
        self.border_radius = 12
        self.shadow = Shadows.LEVEL_2


class DashboardView(ft.Column):
    """Main dashboard view with comprehensive DLL statistics and visualizations"""

    def __init__(self, page: ft.Page, logger):
        super().__init__()
        self.page = page
        self.logger = logger
        self.expand = True
        self.spacing = 0

        # State
        self.is_loading = False

        # Build UI
        self._build_ui()

    def _build_ui(self):
        """Build dashboard UI with loading state"""
        # Header
        header = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(
                        "Dashboard",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.WHITE,
                    ),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.REFRESH,
                        tooltip="Refresh Dashboard",
                        on_click=self._on_refresh_clicked,
                        animate_rotation=ft.Animation(400, ft.AnimationCurve.EASE_IN_OUT),
                    ),
                ],
            ),
            padding=16,
            bgcolor=MD3Colors.SURFACE_VARIANT,
        )

        # Loading indicator
        self.loading_indicator = ft.Container(
            content=ft.Column(
                controls=[
                    ft.ProgressRing(color=MD3Colors.PRIMARY),
                    ft.Text("Loading dashboard data...", color=ft.Colors.WHITE),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            ),
            alignment=ft.alignment.center,
            expand=True,
            visible=False,
        )

        # Content container (will be populated with data)
        self.content_container = ft.Column(
            controls=[],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # Wrapper with padding
        content_wrapper = ft.Container(
            content=self.content_container,
            padding=16,
            expand=True,
        )

        # Assemble
        self.controls = [
            header,
            ft.Divider(height=1, color=MD3Colors.OUTLINE),
            ft.Stack(
                controls=[
                    content_wrapper,
                    self.loading_indicator,
                ],
                expand=True,
            ),
        ]

    async def load_dashboard_data(self):
        """Load all dashboard data from database"""
        if self.is_loading:
            return

        self.is_loading = True
        self.loading_indicator.visible = True
        if self.page:
            self.page.update()

        try:
            self.logger.info("Loading dashboard data...")

            # Load all data in parallel
            tasks = [
                self._get_stat_card_data(),
                self._get_version_matrix_data(),
                self._get_timeline_data(),
                self._get_distribution_data(),
                self._get_recent_updates(),
            ]

            results = await asyncio.gather(*tasks)
            stats, version_matrix, timeline, distribution, recent_updates = results

            # Build dashboard content
            await self._build_dashboard_content(stats, version_matrix, timeline, distribution, recent_updates)

            self.loading_indicator.visible = False
            self.logger.info("Dashboard data loaded successfully")

        except Exception as e:
            self.logger.error(f"Error loading dashboard: {e}", exc_info=True)
            self.loading_indicator.visible = False
            # Show error state
            self.content_container.controls = [
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.ERROR_OUTLINE, size=64, color=MD3Colors.ERROR),
                            ft.Text("Failed to load dashboard data", size=18, color=MD3Colors.ERROR),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=16,
                    ),
                    alignment=ft.alignment.center,
                    expand=True,
                )
            ]

        finally:
            self.is_loading = False
            if self.page:
                self.page.update()

    async def _get_stat_card_data(self) -> Dict:
        """Get data for stat cards"""
        # Query database for stats
        total_games = await db_manager.get_total_games_count()
        total_dlls = await db_manager.get_total_dlls_count()
        updated_dlls = await db_manager.get_updated_dlls_count()
        needs_update = await db_manager.get_dlls_needing_update_count()

        # Calculate success rate
        success_rate = (updated_dlls / total_dlls * 100) if total_dlls > 0 else 0

        return {
            'total_games': total_games,
            'updated_dlls': updated_dlls,
            'success_rate': success_rate,
            'needs_update': needs_update,
        }

    async def _get_version_matrix_data(self) -> Dict[str, Dict[str, int]]:
        """Get version distribution by DLL type"""
        return await db_manager.get_version_distribution()

    async def _get_timeline_data(self) -> List[Tuple[datetime, int]]:
        """Get update timeline for last 30 days"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        return await db_manager.get_update_timeline(start_date, end_date)

    async def _get_distribution_data(self) -> Dict[str, int]:
        """Get DLL technology distribution"""
        return await db_manager.get_technology_distribution()

    async def _get_recent_updates(self) -> List[Dict]:
        """Get recent update history"""
        return await db_manager.get_recent_updates(limit=10)

    async def _build_dashboard_content(
        self,
        stats: Dict,
        version_matrix: Dict,  # Kept for API compatibility, not used
        timeline: List,
        distribution: Dict,
        recent_updates: List,
    ):
        """Build dashboard UI with loaded data"""
        # Stat cards row - 3 cards: Total Games, DLLs Updated, Needs Update
        stat_cards = ft.ResponsiveRow(
            controls=[
                ft.Container(
                    content=StatCard(
                        title="Total Games",
                        value=str(stats['total_games']),
                        icon=ft.Icons.VIDEOGAME_ASSET,
                        color=MD3Colors.PRIMARY,
                    ),
                    col={"xs": 12, "sm": 6, "md": 4},
                ),
                ft.Container(
                    content=StatCard(
                        title="DLLs Updated",
                        value=str(stats['updated_dlls']),
                        icon=ft.Icons.CHECK_CIRCLE,
                        color=MD3Colors.SUCCESS,
                    ),
                    col={"xs": 12, "sm": 6, "md": 4},
                ),
                ft.Container(
                    content=StatCard(
                        title="Pending Updates",
                        value=str(stats['needs_update']),
                        icon=ft.Icons.PENDING_ACTIONS,
                        color=MD3Colors.WARNING,
                    ),
                    col={"xs": 12, "sm": 6, "md": 4},
                ),
            ],
            spacing=16,
            run_spacing=16,
        )

        # Charts row
        charts_row = ft.ResponsiveRow(
            controls=[
                ft.Container(
                    content=UpdateTimelineChart(timeline),
                    col={"xs": 12, "sm": 12, "md": 8},
                ),
                ft.Container(
                    content=TechnologyDistributionChart(distribution),
                    col={"xs": 12, "sm": 12, "md": 4},
                ),
            ],
            spacing=16,
            run_spacing=16,
        )

        # Bottom row - just recent updates (full width)
        bottom_row = ft.ResponsiveRow(
            controls=[
                ft.Container(
                    content=RecentUpdatesList(recent_updates),
                    col={"xs": 12, "sm": 12, "md": 12},
                ),
            ],
            spacing=16,
            run_spacing=16,
        )

        # Update content
        self.content_container.controls = [
            stat_cards,
            charts_row,
            bottom_row,
        ]

        if self.page:
            self.page.update()

    async def _on_refresh_clicked(self, e):
        """Handle refresh button click"""
        # Rotate button
        e.control.rotate = (e.control.rotate or 0) + 6.28  # 360 degrees in radians
        if self.page:
            self.page.update()

        await self.load_dashboard_data()
