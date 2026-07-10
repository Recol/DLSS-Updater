"""
Linux Proton upscaler utility functions.

Generates Steam launch options covering:
- DXVK-NVAPI DLSS DRS overrides (SR/RR render presets, FG override, debug
  overlay) — work on any Proton that bundles dxvk-nvapi, including Valve's.
- Community Proton fork upscaler upgrades (GE-Proton / Proton-CachyOS / EM):
  PROTON_DLSS_UPGRADE, PROTON_FSR4_UPGRADE (+RDNA3 variant), PROTON_XESS_UPGRADE
  and their on-screen indicators.
- Proton misc toggles (Wayland, HDR).

The output is a plain ``ENV=value ... %command%`` prefix: paste into Steam's
launch options as-is, or use the env-var portion in Heroic/Lutris environment
settings — the format is intentionally launcher-agnostic.

Environment Variables Reference:
- DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION: SR preset
- DXVK_NVAPI_DRS_NGX_DLSS_RR_OVERRIDE_RENDER_PRESET_SELECTION: RR preset
- DXVK_NVAPI_DRS_NGX_DLSS_FG_OVERRIDE: Frame Generation override (on)
- DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS: Debug overlay
- PROTON_DLSS_UPGRADE / PROTON_DLSS_INDICATOR: DLSS DLL upgrade + HUD (forks)
- PROTON_FSR4_UPGRADE / PROTON_FSR4_RDNA3_UPGRADE / PROTON_FSR4_INDICATOR
- PROTON_XESS_UPGRADE
- PROTON_ENABLE_WAYLAND: Wayland support
- PROTON_ENABLE_HDR / ENABLE_HDR_WSI: HDR support
"""

from dlss_updater.models import DLSSPreset, LinuxDLSSConfig
from dlss_updater.proton_compat import (
    CAP_DLSS_INDICATOR,
    CAP_DLSS_UPGRADE,
    CAP_FSR4_INDICATOR,
    CAP_FSR4_UPGRADE,
    CAP_XESS_UPGRADE,
)


def _preset_env_value(preset_key: str) -> str:
    """Resolve a stored preset key to its DXVK-NVAPI env value ("" if none)."""
    try:
        return DLSSPreset(preset_key).env_value
    except ValueError:
        return ""


def generate_steam_launch_options(
    config: LinuxDLSSConfig,
    capabilities: frozenset[str] | None = None,
) -> str:
    """
    Generate Steam launch options string from config.

    Args:
        config: LinuxDLSSConfig with preset and toggle settings
        capabilities: Optional set of supported PROTON_* upgrade capabilities
            (from proton_compat.classify_compat_tool) — when provided, upgrade
            variables the target Proton build doesn't support are omitted.
            DXVK-NVAPI variables are never filtered (they work on any Proton).

    Returns:
        Complete launch options string ending with %command%
    """
    parts: list[str] = []

    def cap_ok(cap: str) -> bool:
        return capabilities is None or cap in capabilities

    # SR Preset (only if not default)
    if config.selected_preset != DLSSPreset.DEFAULT:
        env_value = _preset_env_value(config.selected_preset)
        if env_value:
            parts.append(
                f"DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION={env_value}"
            )

    # RR Preset (only if not default)
    if config.rr_preset != DLSSPreset.DEFAULT:
        env_value = _preset_env_value(config.rr_preset)
        if env_value:
            parts.append(
                f"DXVK_NVAPI_DRS_NGX_DLSS_RR_OVERRIDE_RENDER_PRESET_SELECTION={env_value}"
            )

    # Frame Generation override
    if config.fg_override:
        parts.append("DXVK_NVAPI_DRS_NGX_DLSS_FG_OVERRIDE=on")

    # Debug overlay
    if config.overlay_enabled:
        parts.append("DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS=DLSSIndicator=1024")

    # Fork upscaler upgrades (capability-gated)
    if config.dlss_upgrade and cap_ok(CAP_DLSS_UPGRADE):
        parts.append("PROTON_DLSS_UPGRADE=1")
    if config.dlss_indicator and cap_ok(CAP_DLSS_INDICATOR):
        parts.append("PROTON_DLSS_INDICATOR=1")
    if config.fsr4_upgrade and cap_ok(CAP_FSR4_UPGRADE):
        parts.append(
            "PROTON_FSR4_RDNA3_UPGRADE=1" if config.fsr4_rdna3_mode
            else "PROTON_FSR4_UPGRADE=1"
        )
    if config.fsr4_indicator and cap_ok(CAP_FSR4_INDICATOR):
        parts.append("PROTON_FSR4_INDICATOR=1")
    if config.xess_upgrade and cap_ok(CAP_XESS_UPGRADE):
        parts.append("PROTON_XESS_UPGRADE=1")

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
    preset_str = preset if isinstance(preset, str) else preset.value

    descriptions = {
        "default": "Use game/driver default settings",
        "latest": "Always use the newest preset the DLL provides",
        "preset_j": "Transformer model - slightly less ghosting than K",
        "preset_k": "Lighter preset - better performance, recommended for RTX 20/30 series",
        "preset_l": "Balanced preset - good quality/performance balance",
        "preset_m": "Heavier preset - higher quality, recommended for RTX 40/50 series",
    }
    return descriptions.get(preset_str, "Unknown preset")


_SR_PRESETS = (
    DLSSPreset.DEFAULT,
    DLSSPreset.LATEST,
    DLSSPreset.PRESET_J,
    DLSSPreset.PRESET_K,
    DLSSPreset.PRESET_L,
    DLSSPreset.PRESET_M,
)

# RR mirrors the Windows per-game dialog: Default or Latest only
_RR_PRESETS = (
    DLSSPreset.DEFAULT,
    DLSSPreset.LATEST,
)


def _preset_tuples(presets: tuple[DLSSPreset, ...]) -> list[tuple[str, str, str]]:
    return [
        (p.value, p.display_name, get_preset_description(p))
        for p in presets
    ]


def get_all_presets() -> list[tuple[str, str, str]]:
    """
    Get all available SR presets with their values, display names, and
    descriptions.

    Returns:
        List of tuples: (value, display_name, description)
    """
    return _preset_tuples(_SR_PRESETS)


def get_rr_presets() -> list[tuple[str, str, str]]:
    """
    Get available Ray Reconstruction presets (Default/Latest, mirroring the
    Windows per-game DLSS dialog).

    Returns:
        List of tuples: (value, display_name, description)
    """
    return _preset_tuples(_RR_PRESETS)
