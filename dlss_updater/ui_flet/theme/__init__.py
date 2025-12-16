"""Theme module for DLSS Updater Flet UI"""

from .colors import MD3Colors, Animations, Shadows
from .md3_system import (
    MD3ColorSystem,
    MD3Typography,
    MD3Motion,
    MD3Spacing,
    MD3Shadows,
    create_md3_container,
    create_md3_card,
    create_md3_button,
    create_md3_text,
    create_md3_icon_button,
)

__all__ = [
    # Legacy exports (for compatibility)
    'MD3Colors',
    'Animations',
    'Shadows',
    # New MD3 system exports
    'MD3ColorSystem',
    'MD3Typography',
    'MD3Motion',
    'MD3Spacing',
    'MD3Shadows',
    # Helper functions
    'create_md3_container',
    'create_md3_card',
    'create_md3_button',
    'create_md3_text',
    'create_md3_icon_button',
]
