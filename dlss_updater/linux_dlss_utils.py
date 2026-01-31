"""
Linux DLSS SR Presets utility functions.

Generates Steam launch options for DXVK-NVAPI environment variables
to configure DLSS Super Resolution presets on Linux with Proton/Wine.

Environment Variables Reference:
- DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION: Preset override
- DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS: Debug overlay
- PROTON_ENABLE_WAYLAND: Wayland support
- PROTON_ENABLE_HDR / ENABLE_HDR_WSI: HDR support
"""

from dlss_updater.models import DLSSPreset, LinuxDLSSConfig


def generate_steam_launch_options(config: LinuxDLSSConfig) -> str:
    """
    Generate Steam launch options string from config.

    Args:
        config: LinuxDLSSConfig with preset and toggle settings

    Returns:
        Complete launch options string ending with %command%
    """
    parts: list[str] = []

    # SR Preset (only if not default)
    if config.selected_preset != DLSSPreset.DEFAULT:
        try:
            preset = DLSSPreset(config.selected_preset)
            if preset.env_value:
                parts.append(
                    f"DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION={preset.env_value}"
                )
        except ValueError:
            # Invalid preset value, skip
            pass

    # Debug overlay
    if config.overlay_enabled:
        parts.append("DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS=DLSSIndicator=1024")

    # Wayland
    if config.wayland_enabled:
        parts.append("PROTON_ENABLE_WAYLAND=1")

    # HDR (requires both variables)
    if config.hdr_enabled:
        parts.append("PROTON_ENABLE_HDR=1")
        parts.append("ENABLE_HDR_WSI=1")

    # Always end with %command%
    if parts:
        return " ".join(parts) + " %command%"
    return "%command%"


def get_preset_description(preset: DLSSPreset | str) -> str:
    """
    Get human-readable description for a preset.

    Args:
        preset: DLSSPreset enum value or string value

    Returns:
        Description string explaining the preset's characteristics
    """
    # Handle string input
    if isinstance(preset, str):
        preset_str = preset
    else:
        preset_str = preset.value

    descriptions = {
        DLSSPreset.DEFAULT: "Use game/driver default settings",
        DLSSPreset.PRESET_K: "Lighter preset - better performance, recommended for RTX 20/30 series",
        DLSSPreset.PRESET_L: "Balanced preset - good quality/performance balance",
        DLSSPreset.PRESET_M: "Heavier preset - higher quality, recommended for RTX 40/50 series",
        "default": "Use game/driver default settings",
        "preset_k": "Lighter preset - better performance, recommended for RTX 20/30 series",
        "preset_l": "Balanced preset - good quality/performance balance",
        "preset_m": "Heavier preset - higher quality, recommended for RTX 40/50 series",
    }
    return descriptions.get(preset_str, "Unknown preset")


def get_all_presets() -> list[tuple[str, str, str]]:
    """
    Get all available presets with their values, display names, and descriptions.

    Returns:
        List of tuples: (value, display_name, description)
    """
    return [
        (DLSSPreset.DEFAULT, DLSSPreset.DEFAULT.display_name, get_preset_description(DLSSPreset.DEFAULT)),
        (DLSSPreset.PRESET_K, DLSSPreset.PRESET_K.display_name, get_preset_description(DLSSPreset.PRESET_K)),
        (DLSSPreset.PRESET_L, DLSSPreset.PRESET_L.display_name, get_preset_description(DLSSPreset.PRESET_L)),
        (DLSSPreset.PRESET_M, DLSSPreset.PRESET_M.display_name, get_preset_description(DLSSPreset.PRESET_M)),
    ]
