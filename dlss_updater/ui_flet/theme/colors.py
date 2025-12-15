"""
Material Design 3 Color System and Animations
Centralized theme colors and animation constants
"""

import flet as ft


class MD3Colors:
    """Material Design 3 color palette for DLSS Updater"""

    # Primary (brand color - teal blue)
    PRIMARY = "#2D6E88"
    ON_PRIMARY = "#FFFFFF"
    PRIMARY_CONTAINER = "#C2E7FF"
    ON_PRIMARY_CONTAINER = "#001E2C"

    # Secondary (accent color)
    SECONDARY = "#4F616C"
    ON_SECONDARY = "#FFFFFF"
    SECONDARY_CONTAINER = "#D2E5F3"
    ON_SECONDARY_CONTAINER = "#0C1E27"

    # Surface colors
    SURFACE = "#1E1E1E"
    ON_SURFACE = "#E4E2E0"
    SURFACE_VARIANT = "#2E2E2E"
    ON_SURFACE_VARIANT = "#C4C7CA"
    SURFACE_DIM = "#141414"
    SURFACE_BRIGHT = "#3A3A3A"

    # Background
    BACKGROUND = "#2E2E2E"
    ON_BACKGROUND = "#E4E2E0"

    # Status colors
    SUCCESS = "#81C784"
    WARNING = "#FFB74D"
    ERROR = "#CF6679"
    INFO = "#64B5F6"

    # Neutral colors
    OUTLINE = "#5A5A5A"
    OUTLINE_VARIANT = "#3C3C3C"

    # Shadow
    SHADOW = "#000000"
    SCRIM = "#000000"

    # Accent variations
    ACCENT_LIGHT = "#3D8AA8"        # 15% lighter than PRIMARY
    ACCENT_DARK = "#1D5E78"         # 15% darker than PRIMARY
    ACCENT_MUTED = "#2D6E8880"      # 50% opacity
    ACCENT_SUBTLE = "#2D6E8820"     # 12% opacity for backgrounds

    # Interactive state overlays
    HOVER_OVERLAY = "rgba(45, 110, 136, 0.08)"
    PRESSED_OVERLAY = "rgba(45, 110, 136, 0.12)"
    FOCUS_OVERLAY = "rgba(45, 110, 136, 0.10)"

    # Dynamic theme-aware color methods
    @staticmethod
    def get_surface(is_dark: bool = True) -> str:
        return "#1E1E1E" if is_dark else "#FFFFFF"

    @staticmethod
    def get_surface_variant(is_dark: bool = True) -> str:
        return "#2E2E2E" if is_dark else "#F5F5F5"

    @staticmethod
    def get_on_surface(is_dark: bool = True) -> str:
        return "#E4E2E0" if is_dark else "#1C1B1F"

    @staticmethod
    def get_on_surface_variant(is_dark: bool = True) -> str:
        return "#C4C7CA" if is_dark else "#49454F"

    @staticmethod
    def get_outline(is_dark: bool = True) -> str:
        return "#5A5A5A" if is_dark else "#79747E"

    @staticmethod
    def get_background(is_dark: bool = True) -> str:
        return "#2E2E2E" if is_dark else "#FAFBFC"


class Animations:
    """Animation constants following Material Design motion guidelines"""

    # Duration constants (in milliseconds)
    FAST = ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT)
    NORMAL = ft.Animation(250, ft.AnimationCurve.EASE_OUT)
    SLOW = ft.Animation(400, ft.AnimationCurve.DECELERATE)

    # Specific use cases
    HOVER = ft.Animation(200, ft.AnimationCurve.EASE_OUT)
    EXPAND = ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT)
    FADE = ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT)
    SCALE = ft.Animation(150, ft.AnimationCurve.EASE_OUT)

    # Easing curves for manual use
    STANDARD_EASING = ft.AnimationCurve.EASE_IN_OUT
    DECELERATE_EASING = ft.AnimationCurve.EASE_OUT
    ACCELERATE_EASING = ft.AnimationCurve.EASE_IN
    SHARP_EASING = ft.AnimationCurve.LINEAR


class Shadows:
    """Material Design 3 shadow system with multi-level elevation"""

    # Elevation 0 - Flat (no shadow)
    NONE = None

    # Elevation 1 - Resting cards (minimal shadow)
    LEVEL_1 = ft.BoxShadow(
        spread_radius=0,
        blur_radius=2,
        offset=ft.Offset(0, 1),
        color="rgba(0, 0, 0, 0.12)",
    )

    # Elevation 2 - Raised cards (default state)
    LEVEL_2 = ft.BoxShadow(
        spread_radius=0,
        blur_radius=4,
        offset=ft.Offset(0, 2),
        color="rgba(0, 0, 0, 0.16)",
    )

    # Elevation 3 - Hover state (multi-layer with accent glow)
    LEVEL_3 = [
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=8,
            offset=ft.Offset(0, 4),
            color="rgba(0, 0, 0, 0.20)",
        ),
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=16,
            offset=ft.Offset(0, 2),
            color="rgba(45, 110, 136, 0.08)",  # Accent glow
        ),
    ]

    # Elevation 4 - Dialogs and overlays
    LEVEL_4 = ft.BoxShadow(
        spread_radius=1,
        blur_radius=24,
        offset=ft.Offset(0, 8),
        color="rgba(0, 0, 0, 0.28)",
    )

    # Elevation 5 - Modals and top-level overlays (multi-layer)
    LEVEL_5 = [
        ft.BoxShadow(
            spread_radius=2,
            blur_radius=32,
            offset=ft.Offset(0, 12),
            color="rgba(0, 0, 0, 0.32)",
        ),
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=8,
            offset=ft.Offset(0, 4),
            color="rgba(0, 0, 0, 0.16)",
        ),
    ]


class LauncherColors:
    """Brand colors for each game launcher"""

    # Launcher brand colors
    STEAM = "#1b2838"           # Steam dark blue
    EA = "#FF4500"              # EA orange
    EPIC = "#2F2D2E"            # Epic dark gray
    UBISOFT = "#0070FF"         # Ubisoft blue
    GOG = "#86328A"             # GOG purple
    BATTLENET = "#00AEFF"       # Battle.net blue
    XBOX = "#107C10"            # Xbox green
    CUSTOM = "#2D6E88"          # Primary teal for custom folders

    # Mapping for enum lookup
    _MAPPING = {
        "STEAM": STEAM,
        "EA": EA,
        "EPIC": EPIC,
        "UBISOFT": UBISOFT,
        "GOG": GOG,
        "BATTLENET": BATTLENET,
        "XBOX": XBOX,
        "CUSTOM1": CUSTOM,
        "CUSTOM2": CUSTOM,
        "CUSTOM3": CUSTOM,
        "CUSTOM4": CUSTOM,
    }

    @classmethod
    def get_color(cls, launcher_name: str) -> str:
        """Get brand color for a launcher by name"""
        return cls._MAPPING.get(launcher_name.upper(), cls.CUSTOM)
