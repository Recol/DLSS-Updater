"""
Material Design 3 Theme System
Complete MD3 implementation with color system, typography, motion, spacing, and shadows.
Based on Material Design 3 specifications and DLSS Updater UX proposal.
"""

import flet as ft
from typing import Dict, List, Optional, Union


class MD3ColorSystem:
    """
    Complete Material Design 3 color palette
    Implements full MD3 color roles with semantic and brand colors
    """

    # ==================== PRIMARY COLORS ====================
    PRIMARY = "#2D6E88"
    PRIMARY_LIGHT = "#3D8AA8"
    PRIMARY_DARK = "#1D5E78"
    ON_PRIMARY = "#FFFFFF"
    PRIMARY_CONTAINER = "#C2E7FF"
    ON_PRIMARY_CONTAINER = "#001E2C"
    INVERSE_PRIMARY = "#7DD3FF"

    # ==================== SECONDARY COLORS ====================
    SECONDARY = "#4F616C"
    SECONDARY_LIGHT = "#6A7D89"
    SECONDARY_DARK = "#3B4D57"
    ON_SECONDARY = "#FFFFFF"
    SECONDARY_CONTAINER = "#D2E5F3"
    ON_SECONDARY_CONTAINER = "#0C1E27"

    # ==================== TERTIARY COLORS ====================
    TERTIARY = "#5E5C7E"
    TERTIARY_LIGHT = "#7A7899"
    TERTIARY_DARK = "#484665"
    ON_TERTIARY = "#FFFFFF"
    TERTIARY_CONTAINER = "#E4E0FF"
    ON_TERTIARY_CONTAINER = "#1A1736"

    # ==================== SURFACE COLORS ====================
    SURFACE = "#1E1E1E"
    SURFACE_DIM = "#141414"
    SURFACE_BRIGHT = "#3A3A3A"
    SURFACE_VARIANT = "#2E2E2E"
    ON_SURFACE = "#E4E2E0"
    ON_SURFACE_VARIANT = "#C4C7CA"
    INVERSE_SURFACE = "#E4E2E0"
    INVERSE_ON_SURFACE = "#313030"

    # ==================== SURFACE CONTAINERS ====================
    SURFACE_CONTAINER_LOWEST = "#0F0F0F"
    SURFACE_CONTAINER_LOW = "#1A1A1A"
    SURFACE_CONTAINER = "#1E1E1E"
    SURFACE_CONTAINER_HIGH = "#292929"
    SURFACE_CONTAINER_HIGHEST = "#343434"

    # ==================== BACKGROUND COLORS ====================
    BACKGROUND = "#2E2E2E"
    ON_BACKGROUND = "#E4E2E0"

    # ==================== SEMANTIC COLORS ====================
    # Error
    ERROR = "#CF6679"
    ERROR_LIGHT = "#E38898"
    ERROR_DARK = "#B24D5F"
    ON_ERROR = "#FFFFFF"
    ERROR_CONTAINER = "#4C1F24"
    ON_ERROR_CONTAINER = "#FFDAD5"

    # Warning
    WARNING = "#FFB74D"
    WARNING_LIGHT = "#FFCA7A"
    WARNING_DARK = "#E5A343"
    ON_WARNING = "#1F1300"
    WARNING_CONTAINER = "#4A3500"
    ON_WARNING_CONTAINER = "#FFEDCC"

    # Success
    SUCCESS = "#81C784"
    SUCCESS_LIGHT = "#9DD5A0"
    SUCCESS_DARK = "#6DB371"
    ON_SUCCESS = "#00210C"
    SUCCESS_CONTAINER = "#003919"
    ON_SUCCESS_CONTAINER = "#C6F3C4"

    # Info
    INFO = "#64B5F6"
    INFO_LIGHT = "#88C6F8"
    INFO_DARK = "#4A9FE3"
    ON_INFO = "#00233D"
    INFO_CONTAINER = "#003857"
    ON_INFO_CONTAINER = "#C8E6FF"

    # ==================== OUTLINE & BORDER COLORS ====================
    OUTLINE = "#5A5A5A"
    OUTLINE_VARIANT = "#3C3C3C"

    # ==================== SHADOW & SCRIM ====================
    SHADOW = "#000000"
    SCRIM = "#000000"

    # ==================== STATE LAYERS ====================
    # Hover overlays (8% opacity)
    HOVER_PRIMARY = "rgba(45, 110, 136, 0.08)"
    HOVER_SECONDARY = "rgba(79, 97, 108, 0.08)"
    HOVER_SURFACE = "rgba(228, 226, 224, 0.08)"
    HOVER_ERROR = "rgba(207, 102, 121, 0.08)"

    # Focus overlays (10% opacity)
    FOCUS_PRIMARY = "rgba(45, 110, 136, 0.10)"
    FOCUS_SECONDARY = "rgba(79, 97, 108, 0.10)"
    FOCUS_SURFACE = "rgba(228, 226, 224, 0.10)"
    FOCUS_ERROR = "rgba(207, 102, 121, 0.10)"

    # Pressed overlays (12% opacity)
    PRESSED_PRIMARY = "rgba(45, 110, 136, 0.12)"
    PRESSED_SECONDARY = "rgba(79, 97, 108, 0.12)"
    PRESSED_SURFACE = "rgba(228, 226, 224, 0.12)"
    PRESSED_ERROR = "rgba(207, 102, 121, 0.12)"

    # Drag overlays (16% opacity)
    DRAG_PRIMARY = "rgba(45, 110, 136, 0.16)"
    DRAG_SECONDARY = "rgba(79, 97, 108, 0.16)"

    # Selected state (subtle background)
    SELECTED_PRIMARY = "rgba(45, 110, 136, 0.20)"
    SELECTED_SECONDARY = "rgba(79, 97, 108, 0.20)"

    # Disabled state (38% opacity)
    DISABLED_CONTENT = "rgba(228, 226, 224, 0.38)"
    DISABLED_CONTAINER = "rgba(228, 226, 224, 0.12)"

    # ==================== DLL BRAND COLORS ====================
    # NVIDIA (Green with variations)
    NVIDIA_GREEN = "#76B900"
    NVIDIA_GREEN_LIGHT = "#8FD11E"
    NVIDIA_GREEN_DARK = "#5E9400"
    NVIDIA_GREEN_GLOW = "rgba(118, 185, 0, 0.15)"

    # Intel (Blue with variations)
    INTEL_BLUE = "#0071C5"
    INTEL_BLUE_LIGHT = "#1A8AD9"
    INTEL_BLUE_DARK = "#005A9E"
    INTEL_BLUE_GLOW = "rgba(0, 113, 197, 0.15)"

    # AMD (Red with variations)
    AMD_RED = "#ED1C24"
    AMD_RED_LIGHT = "#F44336"
    AMD_RED_DARK = "#C41C24"
    AMD_RED_GLOW = "rgba(237, 28, 36, 0.15)"

    # Generic/Unknown DLL
    GENERIC_DLL = "#9E9E9E"
    GENERIC_DLL_LIGHT = "#BDBDBD"
    GENERIC_DLL_DARK = "#757575"

    @classmethod
    def get_brand_color(cls, dll_name: str) -> str:
        """Get brand color based on DLL name/vendor"""
        dll_lower = dll_name.lower()
        if 'nvidia' in dll_lower or 'nvngx' in dll_lower:
            return cls.NVIDIA_GREEN
        elif 'intel' in dll_lower or 'xess' in dll_lower:
            return cls.INTEL_BLUE
        elif 'amd' in dll_lower or 'fsr' in dll_lower:
            return cls.AMD_RED
        return cls.GENERIC_DLL

    @classmethod
    def get_brand_glow(cls, dll_name: str) -> str:
        """Get brand glow color based on DLL name/vendor"""
        dll_lower = dll_name.lower()
        if 'nvidia' in dll_lower or 'nvngx' in dll_lower:
            return cls.NVIDIA_GREEN_GLOW
        elif 'intel' in dll_lower or 'xess' in dll_lower:
            return cls.INTEL_BLUE_GLOW
        elif 'amd' in dll_lower or 'fsr' in dll_lower:
            return cls.AMD_RED_GLOW
        return "rgba(158, 158, 158, 0.15)"

    @classmethod
    def with_opacity(cls, color: str, opacity: float) -> str:
        """Add opacity to hex color (returns rgba string)"""
        color = color.lstrip('#')
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        return f"rgba({r}, {g}, {b}, {opacity})"


class MD3Typography:
    """
    Material Design 3 typography system
    15-level type scale with display, headline, title, body, and label categories
    """

    # ==================== DISPLAY (Large, prominent text) ====================
    DISPLAY_LARGE = ft.TextStyle(
        size=57,
        weight=ft.FontWeight.W_400,
        letter_spacing=-0.25,
        height=64/57,  # line-height/font-size
    )

    DISPLAY_MEDIUM = ft.TextStyle(
        size=45,
        weight=ft.FontWeight.W_400,
        letter_spacing=0,
        height=52/45,
    )

    DISPLAY_SMALL = ft.TextStyle(
        size=36,
        weight=ft.FontWeight.W_400,
        letter_spacing=0,
        height=44/36,
    )

    # ==================== HEADLINE (High-emphasis text) ====================
    HEADLINE_LARGE = ft.TextStyle(
        size=32,
        weight=ft.FontWeight.W_400,
        letter_spacing=0,
        height=40/32,
    )

    HEADLINE_MEDIUM = ft.TextStyle(
        size=28,
        weight=ft.FontWeight.W_400,
        letter_spacing=0,
        height=36/28,
    )

    HEADLINE_SMALL = ft.TextStyle(
        size=24,
        weight=ft.FontWeight.W_400,
        letter_spacing=0,
        height=32/24,
    )

    # ==================== TITLE (Medium-emphasis text) ====================
    TITLE_LARGE = ft.TextStyle(
        size=22,
        weight=ft.FontWeight.W_400,
        letter_spacing=0,
        height=28/22,
    )

    TITLE_MEDIUM = ft.TextStyle(
        size=16,
        weight=ft.FontWeight.W_500,
        letter_spacing=0.15,
        height=24/16,
    )

    TITLE_SMALL = ft.TextStyle(
        size=14,
        weight=ft.FontWeight.W_500,
        letter_spacing=0.1,
        height=20/14,
    )

    # ==================== BODY (Regular text) ====================
    BODY_LARGE = ft.TextStyle(
        size=16,
        weight=ft.FontWeight.W_400,
        letter_spacing=0.5,
        height=24/16,
    )

    BODY_MEDIUM = ft.TextStyle(
        size=14,
        weight=ft.FontWeight.W_400,
        letter_spacing=0.25,
        height=20/14,
    )

    BODY_SMALL = ft.TextStyle(
        size=12,
        weight=ft.FontWeight.W_400,
        letter_spacing=0.4,
        height=16/12,
    )

    # ==================== LABEL (UI elements, buttons) ====================
    LABEL_LARGE = ft.TextStyle(
        size=14,
        weight=ft.FontWeight.W_500,
        letter_spacing=0.1,
        height=20/14,
    )

    LABEL_MEDIUM = ft.TextStyle(
        size=12,
        weight=ft.FontWeight.W_500,
        letter_spacing=0.5,
        height=16/12,
    )

    LABEL_SMALL = ft.TextStyle(
        size=11,
        weight=ft.FontWeight.W_500,
        letter_spacing=0.5,
        height=16/11,
    )

    @classmethod
    def get_text_style(cls, variant: str, color: Optional[str] = None) -> ft.TextStyle:
        """Get text style by variant name with optional color override"""
        style_map = {
            'display_large': cls.DISPLAY_LARGE,
            'display_medium': cls.DISPLAY_MEDIUM,
            'display_small': cls.DISPLAY_SMALL,
            'headline_large': cls.HEADLINE_LARGE,
            'headline_medium': cls.HEADLINE_MEDIUM,
            'headline_small': cls.HEADLINE_SMALL,
            'title_large': cls.TITLE_LARGE,
            'title_medium': cls.TITLE_MEDIUM,
            'title_small': cls.TITLE_SMALL,
            'body_large': cls.BODY_LARGE,
            'body_medium': cls.BODY_MEDIUM,
            'body_small': cls.BODY_SMALL,
            'label_large': cls.LABEL_LARGE,
            'label_medium': cls.LABEL_MEDIUM,
            'label_small': cls.LABEL_SMALL,
        }

        style = style_map.get(variant.lower(), cls.BODY_MEDIUM)
        if color:
            # Create a new TextStyle with the color override
            return ft.TextStyle(
                size=style.size,
                weight=style.weight,
                letter_spacing=style.letter_spacing,
                height=style.height,
                color=color,
            )
        return style


class MD3Motion:
    """
    Material Design 3 motion system
    Duration constants and easing curves for consistent animations
    """

    # ==================== DURATION CONSTANTS (milliseconds) ====================
    # Short durations (simple transitions)
    SHORT1 = 50   # Micro-interactions
    SHORT2 = 100  # Simple fade
    SHORT3 = 150  # Icon transitions
    SHORT4 = 200  # Tooltip, small elements

    # Medium durations (standard transitions)
    MEDIUM1 = 250  # Standard hover/focus
    MEDIUM2 = 300  # Card expand/collapse
    MEDIUM3 = 350  # Navigation transitions
    MEDIUM4 = 400  # Complex state changes

    # Long durations (complex transitions)
    LONG1 = 450   # Screen transitions
    LONG2 = 500   # Large element animations
    LONG3 = 600   # Complex choreography
    LONG4 = 700   # Page-level transitions

    # Extra long (special cases)
    EXTRA_LONG1 = 800
    EXTRA_LONG2 = 900
    EXTRA_LONG3 = 1000

    # ==================== EASING CURVES ====================
    # Standard easing - Most common, balanced acceleration/deceleration
    STANDARD = ft.AnimationCurve.EASE_IN_OUT
    STANDARD_ACCELERATE = ft.AnimationCurve.EASE_IN
    STANDARD_DECELERATE = ft.AnimationCurve.EASE_OUT

    # Emphasized easing - More dramatic, for special emphasis
    EMPHASIZED = ft.AnimationCurve.FAST_OUT_SLOWIN
    EMPHASIZED_ACCELERATE = ft.AnimationCurve.FAST_LINEAR_TO_SLOW_EASE_IN
    EMPHASIZED_DECELERATE = ft.AnimationCurve.LINEAR_TO_EASE_OUT

    # Linear - No easing, constant speed
    LINEAR = ft.AnimationCurve.LINEAR

    # Legacy curves (for compatibility)
    LEGACY_ACCELERATE = ft.AnimationCurve.BOUNCE_IN
    LEGACY_DECELERATE = ft.AnimationCurve.BOUNCE_OUT

    # ==================== PRE-CONFIGURED ANIMATIONS ====================
    @classmethod
    def hover_animation(cls) -> ft.Animation:
        """Hover state transition (fast, emphasized)"""
        return ft.Animation(cls.SHORT4, cls.EMPHASIZED_DECELERATE)

    @classmethod
    def button_press_animation(cls) -> ft.Animation:
        """Button press feedback (very fast)"""
        return ft.Animation(cls.SHORT1, cls.STANDARD)

    @classmethod
    def expand_animation(cls) -> ft.Animation:
        """Expand/open transition (medium, emphasized)"""
        return ft.Animation(cls.MEDIUM2, cls.EMPHASIZED_DECELERATE)

    @classmethod
    def collapse_animation(cls) -> ft.Animation:
        """Collapse/close transition (medium, emphasized)"""
        return ft.Animation(cls.MEDIUM1, cls.EMPHASIZED_ACCELERATE)

    @classmethod
    def fade_in_animation(cls) -> ft.Animation:
        """Fade in transition (short, standard)"""
        return ft.Animation(cls.SHORT4, cls.STANDARD_DECELERATE)

    @classmethod
    def fade_out_animation(cls) -> ft.Animation:
        """Fade out transition (short, standard)"""
        return ft.Animation(cls.SHORT3, cls.STANDARD_ACCELERATE)

    @classmethod
    def scale_animation(cls) -> ft.Animation:
        """Scale transition (short, emphasized)"""
        return ft.Animation(cls.SHORT3, cls.EMPHASIZED)

    @classmethod
    def slide_animation(cls) -> ft.Animation:
        """Slide transition (medium, emphasized)"""
        return ft.Animation(cls.MEDIUM1, cls.EMPHASIZED_DECELERATE)

    @classmethod
    def navigation_animation(cls) -> ft.Animation:
        """Navigation/page transition (medium-long, emphasized)"""
        return ft.Animation(cls.MEDIUM3, cls.EMPHASIZED)

    @classmethod
    def loading_animation(cls) -> ft.Animation:
        """Loading indicator (medium, linear)"""
        return ft.Animation(cls.MEDIUM4, cls.LINEAR)

    @classmethod
    def dialog_open_animation(cls) -> ft.Animation:
        """Dialog/modal open (medium, emphasized)"""
        return ft.Animation(cls.MEDIUM2, cls.EMPHASIZED_DECELERATE)

    @classmethod
    def dialog_close_animation(cls) -> ft.Animation:
        """Dialog/modal close (short, emphasized)"""
        return ft.Animation(cls.SHORT4, cls.EMPHASIZED_ACCELERATE)


class MD3Spacing:
    """
    Material Design 3 spacing system
    Based on 4px grid system with semantic naming
    """

    # ==================== BASE GRID ====================
    BASE_UNIT = 4  # 4px base unit

    # ==================== SPACING SCALE ====================
    SPACE_0 = 0      # 0px
    SPACE_1 = 4      # 4px
    SPACE_2 = 8      # 8px
    SPACE_3 = 12     # 12px
    SPACE_4 = 16     # 16px
    SPACE_5 = 20     # 20px
    SPACE_6 = 24     # 24px
    SPACE_7 = 28     # 28px
    SPACE_8 = 32     # 32px
    SPACE_9 = 36     # 36px
    SPACE_10 = 40    # 40px
    SPACE_12 = 48    # 48px
    SPACE_14 = 56    # 56px
    SPACE_16 = 64    # 64px
    SPACE_18 = 72    # 72px
    SPACE_20 = 80    # 80px
    SPACE_24 = 96    # 96px
    SPACE_28 = 112   # 112px
    SPACE_32 = 128   # 128px

    # ==================== SEMANTIC SPACING ====================
    # Padding (internal spacing)
    PADDING_NONE = 0
    PADDING_TINY = 4      # Minimal padding
    PADDING_SMALL = 8     # Compact elements
    PADDING_MEDIUM = 12   # Standard elements
    PADDING_LARGE = 16    # Spacious elements
    PADDING_XL = 24       # Very spacious
    PADDING_XXL = 32      # Maximum padding

    # Gap (spacing between elements)
    GAP_NONE = 0
    GAP_TINY = 4          # Minimal gap
    GAP_SMALL = 8         # Related items
    GAP_MEDIUM = 12       # Standard gap
    GAP_LARGE = 16        # Separated items
    GAP_XL = 24           # Distinct sections
    GAP_XXL = 32          # Major sections

    # Margin (external spacing)
    MARGIN_NONE = 0
    MARGIN_SMALL = 8
    MARGIN_MEDIUM = 16
    MARGIN_LARGE = 24
    MARGIN_XL = 32
    MARGIN_XXL = 48

    # ==================== COMPONENT SIZES ====================
    # Icon sizes
    ICON_SIZE_SMALL = 16
    ICON_SIZE_MEDIUM = 20
    ICON_SIZE_LARGE = 24
    ICON_SIZE_XL = 32
    ICON_SIZE_XXL = 48

    # Button sizes
    BUTTON_HEIGHT_SMALL = 32
    BUTTON_HEIGHT_MEDIUM = 40
    BUTTON_HEIGHT_LARGE = 48
    BUTTON_MIN_WIDTH = 64

    # Icon button sizes
    ICON_BUTTON_SIZE_SMALL = 32
    ICON_BUTTON_SIZE_MEDIUM = 40
    ICON_BUTTON_SIZE_LARGE = 48

    # Card/Container
    CARD_MIN_HEIGHT = 80
    CARD_PADDING = 16
    CARD_BORDER_RADIUS = 12

    # Game Card specific sizes
    GAME_CARD_HEIGHT = 180          # Fixed card content height (increased for button visibility)
    GAME_CARD_IMAGE_SIZE = 140      # Image container dimensions
    GAME_CARD_DLL_ROW_HEIGHT = 36   # Fixed height for DLL badges row
    GAME_CARD_BUTTON_ROW_HEIGHT = 36  # Fixed height for action buttons row

    # Dialog
    DIALOG_MIN_WIDTH = 280
    DIALOG_MAX_WIDTH = 560
    DIALOG_PADDING = 24
    DIALOG_BORDER_RADIUS = 28

    # AppBar
    APPBAR_HEIGHT = 64
    APPBAR_PADDING = 16

    # List items
    LIST_ITEM_HEIGHT_ONE_LINE = 56
    LIST_ITEM_HEIGHT_TWO_LINE = 72
    LIST_ITEM_HEIGHT_THREE_LINE = 88

    # ==================== BORDER RADIUS ====================
    RADIUS_NONE = 0
    RADIUS_EXTRA_SMALL = 4
    RADIUS_SMALL = 8
    RADIUS_MEDIUM = 12
    RADIUS_LARGE = 16
    RADIUS_EXTRA_LARGE = 28
    RADIUS_FULL = 9999  # Circular

    # ==================== HELPER METHODS ====================
    @classmethod
    def spacing(cls, multiplier: Union[int, float]) -> int:
        """Get spacing based on base unit multiplier"""
        return int(cls.BASE_UNIT * multiplier)

    @classmethod
    def padding(cls, top: int = 0, right: int = 0, bottom: int = 0, left: int = 0) -> ft.Padding:
        """Create Flet Padding object"""
        return ft.padding.only(top=top, right=right, bottom=bottom, left=left)

    @classmethod
    def padding_all(cls, value: int) -> ft.Padding:
        """Create uniform padding"""
        return ft.padding.all(value)

    @classmethod
    def padding_symmetric(cls, vertical: int = 0, horizontal: int = 0) -> ft.Padding:
        """Create symmetric padding"""
        return ft.padding.symmetric(vertical=vertical, horizontal=horizontal)


class MD3Shadows:
    """
    Material Design 3 shadow system
    Multi-layer shadows with elevation levels
    """

    # ==================== ELEVATION LEVELS ====================
    NONE = None

    # Level 1 - Minimal elevation (resting cards)
    LEVEL_1 = ft.BoxShadow(
        spread_radius=0,
        blur_radius=2,
        offset=ft.Offset(0, 1),
        color="rgba(0, 0, 0, 0.12)",
        blur_style=ft.ShadowBlurStyle.NORMAL,
    )

    # Level 2 - Standard elevation (raised cards)
    LEVEL_2 = ft.BoxShadow(
        spread_radius=0,
        blur_radius=4,
        offset=ft.Offset(0, 2),
        color="rgba(0, 0, 0, 0.16)",
        blur_style=ft.ShadowBlurStyle.NORMAL,
    )

    # Level 3 - Hover elevation (multi-layer with accent glow)
    LEVEL_3 = [
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=8,
            offset=ft.Offset(0, 4),
            color="rgba(0, 0, 0, 0.20)",
            blur_style=ft.ShadowBlurStyle.NORMAL,
        ),
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=16,
            offset=ft.Offset(0, 2),
            color="rgba(45, 110, 136, 0.08)",  # Accent glow
            blur_style=ft.ShadowBlurStyle.OUTER,
        ),
    ]

    # Level 4 - Dialog/overlay elevation
    LEVEL_4 = [
        ft.BoxShadow(
            spread_radius=1,
            blur_radius=24,
            offset=ft.Offset(0, 8),
            color="rgba(0, 0, 0, 0.28)",
            blur_style=ft.ShadowBlurStyle.NORMAL,
        ),
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=12,
            offset=ft.Offset(0, 4),
            color="rgba(0, 0, 0, 0.16)",
            blur_style=ft.ShadowBlurStyle.NORMAL,
        ),
    ]

    # Level 5 - Modal elevation (highest)
    LEVEL_5 = [
        ft.BoxShadow(
            spread_radius=2,
            blur_radius=32,
            offset=ft.Offset(0, 12),
            color="rgba(0, 0, 0, 0.32)",
            blur_style=ft.ShadowBlurStyle.NORMAL,
        ),
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=16,
            offset=ft.Offset(0, 6),
            color="rgba(0, 0, 0, 0.20)",
            blur_style=ft.ShadowBlurStyle.NORMAL,
        ),
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=8,
            offset=ft.Offset(0, 3),
            color="rgba(0, 0, 0, 0.12)",
            blur_style=ft.ShadowBlurStyle.NORMAL,
        ),
    ]

    # ==================== BRAND-COLORED SHADOWS ====================
    @classmethod
    def brand_shadow(cls, color: str, elevation: int = 2) -> List[ft.BoxShadow]:
        """Create brand-colored shadow with specified elevation"""
        base_shadows = {
            1: [(0, 1, 2, 0.12)],
            2: [(0, 2, 4, 0.16)],
            3: [(0, 4, 8, 0.20), (0, 2, 16, 0.08)],
            4: [(0, 8, 24, 0.28), (0, 4, 12, 0.16)],
            5: [(0, 12, 32, 0.32), (0, 6, 16, 0.20), (0, 3, 8, 0.12)],
        }

        shadow_configs = base_shadows.get(elevation, base_shadows[2])
        shadows = []

        for offset_y, blur, _, opacity in shadow_configs[:-1]:
            shadows.append(ft.BoxShadow(
                spread_radius=0,
                blur_radius=blur,
                offset=ft.Offset(0, offset_y),
                color=f"rgba(0, 0, 0, {opacity})",
                blur_style=ft.ShadowBlurStyle.NORMAL,
            ))

        # Add brand-colored glow as last layer
        _, _, blur, glow_opacity = shadow_configs[-1]
        # Extract RGB from hex color
        color = color.lstrip('#')
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        shadows.append(ft.BoxShadow(
            spread_radius=0,
            blur_radius=blur,
            offset=ft.Offset(0, 2),
            color=f"rgba({r}, {g}, {b}, {glow_opacity})",
            blur_style=ft.ShadowBlurStyle.OUTER,
        ))

        return shadows


# ==================== HELPER FUNCTIONS ====================

def create_md3_container(
    content: ft.Control,
    bgcolor: Optional[str] = None,
    padding: Union[int, ft.Padding] = MD3Spacing.PADDING_MEDIUM,
    border_radius: int = MD3Spacing.RADIUS_MEDIUM,
    shadow: Optional[Union[ft.BoxShadow, List[ft.BoxShadow]]] = MD3Shadows.LEVEL_2,
    border: Optional[ft.Border] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    expand: Union[bool, int] = False,
    animate: Optional[ft.Animation] = None,
) -> ft.Container:
    """
    Create MD3-styled container with sensible defaults

    Args:
        content: Child control
        bgcolor: Background color (defaults to SURFACE)
        padding: Padding value or Padding object
        border_radius: Border radius
        shadow: Shadow configuration
        border: Border configuration
        width: Fixed width
        height: Fixed height
        expand: Expand behavior
        animate: Animation configuration

    Returns:
        Configured Container control
    """
    if bgcolor is None:
        bgcolor = MD3ColorSystem.SURFACE

    if isinstance(padding, int):
        padding = ft.padding.all(padding)

    return ft.Container(
        content=content,
        bgcolor=bgcolor,
        padding=padding,
        border_radius=ft.border_radius.all(border_radius),
        shadow=shadow,
        border=border,
        width=width,
        height=height,
        expand=expand,
        animate=animate,
    )


def create_md3_card(
    content: ft.Control,
    on_click=None,
    on_hover=None,
    elevation: int = 2,
    interactive: bool = False,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> ft.Container:
    """
    Create MD3-styled interactive card

    Args:
        content: Card content
        on_click: Click handler
        on_hover: Hover handler
        elevation: Shadow elevation (1-5)
        interactive: Enable hover effects
        width: Fixed width
        height: Fixed height

    Returns:
        Configured card Container
    """
    shadow_map = {
        1: MD3Shadows.LEVEL_1,
        2: MD3Shadows.LEVEL_2,
        3: MD3Shadows.LEVEL_3,
        4: MD3Shadows.LEVEL_4,
        5: MD3Shadows.LEVEL_5,
    }

    return ft.Container(
        content=content,
        bgcolor=MD3ColorSystem.SURFACE,
        padding=ft.padding.all(MD3Spacing.CARD_PADDING),
        border_radius=ft.border_radius.all(MD3Spacing.CARD_BORDER_RADIUS),
        shadow=shadow_map.get(elevation, MD3Shadows.LEVEL_2),
        on_click=on_click,
        on_hover=on_hover,
        width=width,
        height=height,
        animate=MD3Motion.hover_animation() if interactive else None,
        animate_scale=ft.animation.Animation(MD3Motion.SHORT3, MD3Motion.EMPHASIZED) if interactive else None,
    )


def create_md3_button(
    text: str,
    on_click=None,
    style: str = "filled",  # filled, outlined, text, elevated, tonal
    icon: Optional[str] = None,
    disabled: bool = False,
    width: Optional[int] = None,
    height: int = MD3Spacing.BUTTON_HEIGHT_MEDIUM,
) -> ft.ElevatedButton:
    """
    Create MD3-styled button

    Args:
        text: Button text
        on_click: Click handler
        style: Button style variant
        icon: Optional icon name
        disabled: Disabled state
        width: Fixed width
        height: Button height

    Returns:
        Configured button control
    """
    style_config = {
        "filled": {
            "bgcolor": MD3ColorSystem.PRIMARY,
            "color": MD3ColorSystem.ON_PRIMARY,
            "elevation": 0,
        },
        "elevated": {
            "bgcolor": MD3ColorSystem.SURFACE_CONTAINER_LOW,
            "color": MD3ColorSystem.PRIMARY,
            "elevation": 1,
        },
        "tonal": {
            "bgcolor": MD3ColorSystem.SECONDARY_CONTAINER,
            "color": MD3ColorSystem.ON_SECONDARY_CONTAINER,
            "elevation": 0,
        },
        "outlined": {
            "bgcolor": ft.Colors.TRANSPARENT,
            "color": MD3ColorSystem.PRIMARY,
            "elevation": 0,
        },
        "text": {
            "bgcolor": ft.Colors.TRANSPARENT,
            "color": MD3ColorSystem.PRIMARY,
            "elevation": 0,
        },
    }

    config = style_config.get(style, style_config["filled"])

    return ft.ElevatedButton(
        text=text,
        icon=icon,
        on_click=on_click,
        disabled=disabled,
        width=width,
        height=height,
        bgcolor=config["bgcolor"],
        color=config["color"],
        elevation=config["elevation"],
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=MD3Spacing.RADIUS_LARGE),
            padding=ft.padding.symmetric(
                horizontal=MD3Spacing.PADDING_LARGE,
                vertical=MD3Spacing.PADDING_SMALL,
            ),
            animation_duration=MD3Motion.SHORT4,
        ),
    )


def create_md3_text(
    text: str,
    variant: str = "body_medium",
    color: Optional[str] = None,
    selectable: bool = False,
    text_align: ft.TextAlign = ft.TextAlign.LEFT,
    max_lines: Optional[int] = None,
    overflow: ft.TextOverflow = ft.TextOverflow.FADE,
) -> ft.Text:
    """
    Create MD3-styled text control

    Args:
        text: Text content
        variant: Typography variant (e.g., 'headline_medium', 'body_large')
        color: Text color (defaults to ON_SURFACE)
        selectable: Enable text selection
        text_align: Text alignment
        max_lines: Maximum lines before truncation
        overflow: Overflow behavior

    Returns:
        Configured Text control
    """
    if color is None:
        color = MD3ColorSystem.ON_SURFACE

    style = MD3Typography.get_text_style(variant, color)

    return ft.Text(
        text,
        style=style,
        selectable=selectable,
        text_align=text_align,
        max_lines=max_lines,
        overflow=overflow,
    )


def create_md3_icon_button(
    icon: str,
    on_click=None,
    icon_color: Optional[str] = None,
    bgcolor: Optional[str] = None,
    tooltip: Optional[str] = None,
    size: int = MD3Spacing.ICON_BUTTON_SIZE_MEDIUM,
    icon_size: int = MD3Spacing.ICON_SIZE_MEDIUM,
) -> ft.IconButton:
    """
    Create MD3-styled icon button

    Args:
        icon: Icon name
        on_click: Click handler
        icon_color: Icon color (defaults to ON_SURFACE_VARIANT)
        bgcolor: Background color (defaults to transparent)
        tooltip: Tooltip text
        size: Button size
        icon_size: Icon size

    Returns:
        Configured IconButton control
    """
    if icon_color is None:
        icon_color = MD3ColorSystem.ON_SURFACE_VARIANT

    return ft.IconButton(
        icon=icon,
        icon_color=icon_color,
        bgcolor=bgcolor or ft.Colors.TRANSPARENT,
        on_click=on_click,
        tooltip=tooltip,
        width=size,
        height=size,
        icon_size=icon_size,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=MD3Spacing.RADIUS_FULL),
            padding=ft.padding.all(MD3Spacing.PADDING_SMALL),
        ),
    )


# Export all classes and helpers
__all__ = [
    'MD3ColorSystem',
    'MD3Typography',
    'MD3Motion',
    'MD3Spacing',
    'MD3Shadows',
    'create_md3_container',
    'create_md3_card',
    'create_md3_button',
    'create_md3_text',
    'create_md3_icon_button',
]
