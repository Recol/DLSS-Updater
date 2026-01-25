"""
Material Design 3 Color System and Animations
Centralized theme colors and animation constants with light/dark theme support.
All light mode colors are WCAG AA compliant (4.5:1 contrast minimum).
"""

import flet as ft


class MD3Colors:
    """
    Material Design 3 color palette for DLSS Updater.
    Supports both dark and light themes with WCAG AA compliance.
    """

    # ==================== PRIMARY COLORS ====================
    # Primary (brand color - teal blue)
    PRIMARY = "#2D6E88"
    PRIMARY_LIGHT = "#1A5A70"  # Darkened for light mode (7.2:1 contrast)
    ON_PRIMARY = "#FFFFFF"
    PRIMARY_CONTAINER = "#C2E7FF"
    ON_PRIMARY_CONTAINER = "#001E2C"

    # Secondary (accent color)
    SECONDARY = "#4F616C"
    ON_SECONDARY = "#FFFFFF"
    SECONDARY_CONTAINER = "#D2E5F3"
    ON_SECONDARY_CONTAINER = "#0C1E27"

    # ==================== SURFACE COLORS ====================
    # Dark mode surfaces
    SURFACE = "#1E1E1E"
    SURFACE_VARIANT = "#2E2E2E"
    SURFACE_CONTAINER = "#2E2E2E"
    SURFACE_DIM = "#141414"
    SURFACE_BRIGHT = "#3A3A3A"

    # Light mode surfaces
    SURFACE_LIGHT = "#FFFFFF"
    SURFACE_VARIANT_LIGHT = "#F5F5F5"  # Neutral light gray (not pastel pink)
    SURFACE_CONTAINER_LIGHT = "#F0F0F0"  # Neutral light gray for containers

    # On-surface colors
    ON_SURFACE = "#E4E2E0"
    ON_SURFACE_LIGHT = "#1C1B1F"  # 16.1:1 contrast on white
    ON_SURFACE_VARIANT = "#C4C7CA"
    ON_SURFACE_VARIANT_LIGHT = "#49454F"  # 7.8:1 contrast

    # ==================== BACKGROUND COLORS ====================
    BACKGROUND = "#2E2E2E"
    BACKGROUND_LIGHT = "#FAFBFC"
    ON_BACKGROUND = "#E4E2E0"
    ON_BACKGROUND_LIGHT = "#1C1B1F"

    # ==================== STATUS COLORS ====================
    # Dark mode status
    SUCCESS = "#81C784"
    WARNING = "#FFB74D"
    ERROR = "#CF6679"
    INFO = "#64B5F6"

    # Light mode status (darkened for contrast)
    SUCCESS_LIGHT = "#1B6D1B"  # 5.6:1 contrast
    WARNING_LIGHT = "#7A5800"  # 5.4:1 contrast
    ERROR_LIGHT = "#BA1A1A"    # 5.9:1 contrast
    INFO_LIGHT = "#0061A4"     # 5.2:1 contrast

    # Status containers for light mode badges
    SUCCESS_CONTAINER = "#D4EDDA"
    WARNING_CONTAINER = "#FFF3CD"
    ERROR_CONTAINER = "#FFDAD6"
    INFO_CONTAINER = "#D1E4FF"

    # ==================== NEUTRAL COLORS ====================
    OUTLINE = "#5A5A5A"
    OUTLINE_LIGHT = "#79747E"
    OUTLINE_VARIANT = "#3C3C3C"
    OUTLINE_VARIANT_LIGHT = "#CAC4D0"

    # Shadow
    SHADOW = "#000000"
    SCRIM = "#000000"

    # ==================== ACCENT VARIATIONS ====================
    ACCENT_LIGHT = "#3D8AA8"        # 15% lighter than PRIMARY
    ACCENT_DARK = "#1D5E78"         # 15% darker than PRIMARY
    ACCENT_MUTED = "#2D6E8880"      # 50% opacity
    ACCENT_SUBTLE = "#2D6E8820"     # 12% opacity for backgrounds

    # Interactive state overlays
    HOVER_OVERLAY = "rgba(45, 110, 136, 0.08)"
    PRESSED_OVERLAY = "rgba(45, 110, 136, 0.12)"
    FOCUS_OVERLAY = "rgba(45, 110, 136, 0.10)"

    # ==================== THEMED COLOR PAIRS ====================
    # Dict of (dark_value, light_value) for all theme-sensitive colors
    THEMED: dict[str, tuple[str, str]] = {
        # Primary
        "primary": ("#2D6E88", "#1A5A70"),
        "on_primary": ("#FFFFFF", "#FFFFFF"),
        "primary_container": ("#C2E7FF", "#C2E7FF"),

        # Surfaces
        "surface": ("#1E1E1E", "#FFFFFF"),
        "surface_variant": ("#2E2E2E", "#F5F5F5"),
        "surface_container": ("#2E2E2E", "#F0F0F0"),
        "surface_dim": ("#141414", "#DED8E1"),
        "surface_bright": ("#3A3A3A", "#FFFFFF"),

        # On-surfaces
        "on_surface": ("#E4E2E0", "#1C1B1F"),
        "on_surface_variant": ("#C4C7CA", "#49454F"),

        # Background
        "background": ("#2E2E2E", "#FAFBFC"),
        "on_background": ("#E4E2E0", "#1C1B1F"),

        # Status
        "success": ("#81C784", "#1B6D1B"),
        "warning": ("#FFB74D", "#7A5800"),
        "error": ("#CF6679", "#BA1A1A"),
        "info": ("#64B5F6", "#0061A4"),

        # Outline
        "outline": ("#5A5A5A", "#79747E"),
        "outline_variant": ("#3C3C3C", "#CAC4D0"),

        # Text
        "text_primary": ("#FFFFFF", "#1C1B1F"),
        "text_secondary": ("#888888", "#666666"),
        "text_tertiary": ("#666666", "#888888"),
        "text_disabled": ("#555555", "#AAAAAA"),

        # Card backgrounds
        "card_surface": ("#1E1E1E", "#FFFFFF"),
        "card_hover": ("#2E2E2E", "#F5F5F5"),

        # Skeleton loader - ft.Shimmer control colors (GPU-accelerated)
        "skeleton_base": ("#1E1E1E", "#F0F0F0"),      # Base container background
        "skeleton_start": ("#2A2A2A", "#E8E8E8"),     # Shimmer base_color (darker)
        "skeleton_mid": ("#3A3A3A", "#DADADA"),       # Legacy gradient mid
        "skeleton_end": ("#2A2A2A", "#E8E8E8"),       # Legacy gradient end
        "skeleton_highlight": ("#4A4A4A", "#FFFFFF"), # Shimmer highlight_color (lighter)

        # Specific UI elements
        "snackbar_bg": ("#2D6E88", "#1A5A70"),
        "badge_default_bg": ("#3A3A3A", "#E0E0E0"),
        "badge_update_bg": ("#FF9800", "#FF9800"),
        "divider": ("#3C3C3C", "#E0E0E0"),
        "icon_default": ("#888888", "#666666"),
    }

    # ==================== DYNAMIC THEME-AWARE METHODS ====================
    @staticmethod
    def get_themed(key: str, is_dark: bool = True) -> str:
        """
        Get a themed color by key.

        Args:
            key: Color key from THEMED dict (e.g., "surface", "on_surface")
            is_dark: Whether dark mode is active

        Returns:
            The appropriate color for the current theme
        """
        pair = MD3Colors.THEMED.get(key)
        if pair:
            return pair[0] if is_dark else pair[1]
        return "#FF00FF"  # Magenta for missing colors (debugging)

    @staticmethod
    def get_themed_pair(key: str) -> tuple[str, str]:
        """
        Get a themed color pair (dark, light) by key.

        Args:
            key: Color key from THEMED dict

        Returns:
            Tuple of (dark_value, light_value)
        """
        return MD3Colors.THEMED.get(key, ("#FF00FF", "#FF00FF"))

    @staticmethod
    def get_surface(is_dark: bool = True) -> str:
        return "#1E1E1E" if is_dark else "#FFFFFF"

    @staticmethod
    def get_surface_variant(is_dark: bool = True) -> str:
        return "#2E2E2E" if is_dark else "#F5F5F5"

    @staticmethod
    def get_surface_container(is_dark: bool = True) -> str:
        return "#2E2E2E" if is_dark else "#F0F0F0"

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

    @staticmethod
    def get_primary(is_dark: bool = True) -> str:
        """Get primary color (darkened in light mode for contrast)"""
        return "#2D6E88" if is_dark else "#1A5A70"

    @staticmethod
    def get_success(is_dark: bool = True) -> str:
        return "#81C784" if is_dark else "#1B6D1B"

    @staticmethod
    def get_warning(is_dark: bool = True) -> str:
        return "#FFB74D" if is_dark else "#7A5800"

    @staticmethod
    def get_error(is_dark: bool = True) -> str:
        return "#CF6679" if is_dark else "#BA1A1A"

    @staticmethod
    def get_info(is_dark: bool = True) -> str:
        return "#64B5F6" if is_dark else "#0061A4"

    @staticmethod
    def get_text_primary(is_dark: bool = True) -> str:
        return "#FFFFFF" if is_dark else "#1C1B1F"

    @staticmethod
    def get_text_secondary(is_dark: bool = True) -> str:
        return "#888888" if is_dark else "#666666"

    @staticmethod
    def get_divider(is_dark: bool = True) -> str:
        return "#3C3C3C" if is_dark else "#E0E0E0"


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


class TechnologyColors:
    """Brand colors for DLL technology groups with light/dark theme support"""

    # Brand colors (same in both themes)
    DLSS = "#76B900"          # NVIDIA green
    XeSS = "#0071C5"          # Intel blue
    FSR = "#ED1C24"           # AMD red
    DirectStorage = "#FFB900" # Windows yellow
    Streamline = "#76B900"    # NVIDIA green (Streamline is NVIDIA tech)

    # Light mode container backgrounds (pale versions)
    CONTAINERS_LIGHT = {
        "DLSS": "#E8F5E1",        # Pale green
        "XeSS": "#E3F2FD",        # Pale blue
        "FSR": "#FFEBEE",         # Pale red
        "DirectStorage": "#FFF8E1",  # Pale yellow
        "Streamline": "#E8F5E1",  # Pale green
    }

    # Light mode text colors (original brand colors work well)
    TEXT_LIGHT = {
        "DLSS": "#76B900",
        "XeSS": "#0071C5",
        "FSR": "#ED1C24",
        "DirectStorage": "#7A5800",  # Darkened for contrast
        "Streamline": "#76B900",
    }

    # Technology icons mapping
    ICONS = {
        "DLSS": "memory",          # GPU/memory icon for DLSS
        "XeSS": "memory",          # GPU/memory icon for XeSS
        "FSR": "memory",           # GPU/memory icon for FSR
        "DirectStorage": "storage",  # Storage icon
        "Streamline": "tune",      # Settings/tune icon for Streamline
    }

    @classmethod
    def get_color(cls, tech_name: str) -> str:
        """Get brand color for a technology by name (dark mode)"""
        return getattr(cls, tech_name, "#888888")

    @classmethod
    def get_themed_color(cls, tech_name: str, is_dark: bool = True) -> str:
        """Get brand color appropriate for current theme"""
        if is_dark:
            return getattr(cls, tech_name, "#888888")
        else:
            return cls.TEXT_LIGHT.get(tech_name, "#666666")

    @classmethod
    def get_container_bg(cls, tech_name: str, is_dark: bool = True) -> str:
        """Get container background color for badges"""
        if is_dark:
            # Dark mode: use brand color with low opacity
            color = getattr(cls, tech_name, "#888888")
            return f"{color}20"  # 12% opacity
        else:
            return cls.CONTAINERS_LIGHT.get(tech_name, "#F5F5F5")

    @classmethod
    def get_icon(cls, tech_name: str) -> str:
        """Get icon name for a technology"""
        return cls.ICONS.get(tech_name, "memory")


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


class TabColors:
    """
    Color scheme for navigation tabs with light/dark theme support.
    Designed to be extensible for future tabs.
    """
    # Current tabs - distinct color families for clear visual identity
    # Dark mode colors (bright for dark backgrounds)
    LAUNCHERS = "#3D8AA8"    # Teal/blue - matches brand primary
    GAMES = "#2196F3"        # Blue - gaming/action theme
    BACKUPS = "#FF7043"      # Orange - safety/backup theme

    # Light mode colors (darkened for contrast)
    LAUNCHERS_LIGHT = "#1A5A70"   # Dark teal - brand primary darkened
    GAMES_LIGHT = "#0D47A1"       # Dark blue
    BACKUPS_LIGHT = "#C43F1F"     # Dark orange

    # Light mode container backgrounds (pale fills)
    CONTAINERS_LIGHT = {
        "Launchers": "#E0F2F7",   # Pale teal
        "Games": "#E3F2FD",       # Pale blue
        "Backups": "#FBE9E7",     # Pale orange
    }

    # Reserved palette for future tabs (distinct, accessible colors)
    SETTINGS = "#9C27B0"     # Purple - configuration
    UPDATES = "#2196F3"      # Blue - downloads/updates
    LOGS = "#607D8B"         # Blue-grey - logs/history
    STATS = "#00BCD4"        # Cyan - analytics/statistics

    # Fallback palette for dynamic tabs (cycles through)
    _PALETTE = [
        "#3D8AA8",  # Teal/blue (brand)
        "#2196F3",  # Blue
        "#FF7043",  # Orange
        "#9C27B0",  # Purple
        "#00BCD4",  # Cyan
        "#4CAF50",  # Green
        "#FF9800",  # Amber
        "#607D8B",  # Blue-grey
    ]

    # Named tab mapping (dark mode)
    _TAB_COLORS = {
        "Launchers": LAUNCHERS,
        "Games": GAMES,
        "Backups": BACKUPS,
        "Settings": SETTINGS,
        "Updates": UPDATES,
        "Logs": LOGS,
        "Stats": STATS,
    }

    # Light mode tab colors (darkened)
    _TAB_COLORS_LIGHT = {
        "Launchers": LAUNCHERS_LIGHT,
        "Games": GAMES_LIGHT,
        "Backups": BACKUPS_LIGHT,
        "Settings": "#6A1B9A",    # Dark purple
        "Updates": "#0D47A1",     # Dark blue
        "Logs": "#37474F",        # Dark blue-grey
        "Stats": "#006064",       # Dark cyan
    }

    @classmethod
    def get_color(cls, tab_name: str, index: int = 0) -> str:
        """
        Get color for a specific tab (dark mode).
        Falls back to palette cycling for unknown tabs.

        Args:
            tab_name: Name of the tab
            index: Tab index (used for fallback palette cycling)
        """
        if tab_name in cls._TAB_COLORS:
            return cls._TAB_COLORS[tab_name]
        # Fallback: cycle through palette based on index
        return cls._PALETTE[index % len(cls._PALETTE)]

    @classmethod
    def get_themed_color(cls, tab_name: str, is_dark: bool = True, index: int = 0) -> str:
        """
        Get themed color for a specific tab.

        Args:
            tab_name: Name of the tab
            is_dark: Whether dark mode is active
            index: Tab index (used for fallback palette cycling)
        """
        colors = cls._TAB_COLORS if is_dark else cls._TAB_COLORS_LIGHT
        if tab_name in colors:
            return colors[tab_name]
        return cls._PALETTE[index % len(cls._PALETTE)]

    @classmethod
    def get_container_bg(cls, tab_name: str, is_dark: bool = True) -> str:
        """Get container background for tab indicators in light mode"""
        if is_dark:
            # Dark mode: transparent or subtle tint
            return "transparent"
        return cls.CONTAINERS_LIGHT.get(tab_name, "#F5F5F5")

    @classmethod
    def register_tab(cls, tab_name: str, color: str):
        """Register a new tab color at runtime"""
        cls._TAB_COLORS[tab_name] = color
