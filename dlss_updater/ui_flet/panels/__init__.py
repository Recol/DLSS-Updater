"""
UI Panels - Slide-out side panels for application settings and information
"""

from .preferences_panel import PreferencesPanel
from .release_notes_panel import ReleaseNotesPanel
from .blacklist_panel import BlacklistPanel
from .ui_preferences_panel import UIPreferencesPanel
from .proton_upscaler_panel import ProtonUpscalerPanel
from .dlss_settings_panel import WindowsDLSSPresetsPanel
from .ignore_list_panel import IgnoreListPanel

__all__ = [
    "PreferencesPanel",
    "ReleaseNotesPanel",
    "BlacklistPanel",
    "UIPreferencesPanel",
    "ProtonUpscalerPanel",
    "WindowsDLSSPresetsPanel",
    "IgnoreListPanel",
]
