"""
Slide Panel Component Package

Provides a reusable slide-in panel system for DLSS Updater.

Components:
    - SlidePanel: Core panel component with animations
    - PanelManager: Singleton manager for panel lifecycle
    - PanelContentBase: Abstract base class for panel content

Usage Example:
    ```python
    from dlss_updater.ui_flet.components.slide_panel import (
        PanelContentBase,
        PanelManager,
    )

    # Define panel content
    class MyPanel(PanelContentBase):
        @property
        def title(self) -> str:
            return "My Settings"

        @property
        def width(self) -> int:
            return 500

        def build(self) -> ft.Control:
            return ft.Column([
                ft.Text("Panel content here"),
            ])

        async def on_save(self) -> bool:
            # Save logic
            return True

    # Show panel
    manager = PanelManager.get_instance(page, logger)
    content = MyPanel(page, logger)
    await manager.show_content(content)
    ```
"""

from .panel_content_base import PanelContentBase
from .panel_manager import PanelManager
from .slide_panel import SlidePanel

__all__ = [
    "PanelContentBase",
    "PanelManager",
    "SlidePanel",
]
