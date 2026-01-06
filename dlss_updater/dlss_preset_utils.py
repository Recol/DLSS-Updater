"""
DLSS Preset Utilities Module
Provides cross-platform DLSS Super Resolution preset management.

Windows: Modifies NVIDIA DRS (Driver Runtime Settings) via registry
Linux: Generates environment variables for DXVK-NVAPI/Proton

Thread-safe for Python 3.14 free-threading.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from dlss_updater.platform_utils import IS_WINDOWS, IS_LINUX
from dlss_updater.models import DLSSPreset

if TYPE_CHECKING:
    pass

logger = logging.getLogger("DLSSUpdater")

# Windows Registry paths
NGXCORE_REGISTRY_PATH = r"SOFTWARE\NVIDIA Corporation\Global\NGXCore"
DRS_PRESET_KEY = "DlssSrOverrideRenderPreset"

# Linux environment variable names (for DXVK-NVAPI)
LINUX_PRESET_ENV_VAR = "DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION"
LINUX_DEBUG_OPTIONS_VAR = "DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS"


async def get_current_preset_windows() -> tuple[DLSSPreset, str | None]:
    """
    Read current DLSS SR preset override from Windows registry.

    Returns:
        tuple[DLSSPreset, str | None]: (current_preset, error_message)
        - If no override set, returns (DEFAULT, None)
        - On error, returns (DEFAULT, error_string)
    """
    if not IS_WINDOWS:
        return (DLSSPreset.DEFAULT, "Windows-only operation")

    def _read_registry() -> tuple[DLSSPreset, str | None]:
        import winreg

        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                NGXCORE_REGISTRY_PATH,
                0,
                winreg.KEY_READ
            )
            try:
                value, reg_type = winreg.QueryValueEx(key, DRS_PRESET_KEY)
                winreg.CloseKey(key)

                # Map registry value to preset
                value_to_preset = {
                    12: DLSSPreset.PRESET_L,
                    13: DLSSPreset.PRESET_M,
                }
                return (value_to_preset.get(value, DLSSPreset.DEFAULT), None)

            except FileNotFoundError:
                winreg.CloseKey(key)
                return (DLSSPreset.DEFAULT, None)  # No override = default

        except FileNotFoundError:
            return (DLSSPreset.DEFAULT, None)  # NGXCore path doesn't exist
        except PermissionError as e:
            return (DLSSPreset.DEFAULT, f"Permission denied: {e}")
        except OSError as e:
            return (DLSSPreset.DEFAULT, f"Registry error: {e}")

    return await asyncio.to_thread(_read_registry)


async def set_preset_windows(preset: DLSSPreset) -> tuple[bool, str | None]:
    """
    Set DLSS SR preset override in Windows registry.

    Args:
        preset: DLSSPreset to set (DEFAULT removes the override)

    Returns:
        tuple[bool, str | None]: (success, error_message)

    Requires administrator privileges.
    """
    if not IS_WINDOWS:
        return (False, "Windows-only operation")

    def _write_registry() -> tuple[bool, str | None]:
        import winreg

        try:
            # Create/open key with write access
            key = winreg.CreateKeyEx(
                winreg.HKEY_LOCAL_MACHINE,
                NGXCORE_REGISTRY_PATH,
                0,
                winreg.KEY_WRITE
            )

            if preset == DLSSPreset.DEFAULT:
                # Remove override to use default behavior
                try:
                    winreg.DeleteValue(key, DRS_PRESET_KEY)
                    logger.info("DLSS SR preset override removed (using default)")
                except FileNotFoundError:
                    logger.debug("DLSS SR preset key already absent")
            else:
                # Set the override value
                reg_value = preset.registry_value
                if reg_value is not None:
                    winreg.SetValueEx(
                        key,
                        DRS_PRESET_KEY,
                        0,
                        winreg.REG_DWORD,
                        reg_value
                    )
                    logger.info(
                        f"DLSS SR preset override set to {preset.name} "
                        f"(registry value={reg_value})"
                    )

            winreg.CloseKey(key)
            return (True, None)

        except PermissionError:
            return (
                False,
                "Administrator privileges required to modify NVIDIA driver settings"
            )
        except OSError as e:
            return (False, f"Registry operation failed: {e}")

    return await asyncio.to_thread(_write_registry)


def generate_linux_env_vars(
    preset: DLSSPreset,
    include_debug_overlay: bool = False
) -> dict[str, str]:
    """
    Generate Linux environment variables for DLSS preset override.

    Args:
        preset: DLSSPreset to configure
        include_debug_overlay: If True, include DLSS indicator env vars

    Returns:
        dict[str, str]: Environment variables to set for game launch

    These should be added to Steam launch options or game-specific configs.

    Example output:
        {
            "DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION": "render_preset_m",
            "DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS": "DLSSIndicator=1024"
        }
    """
    env_vars: dict[str, str] = {}

    # Preset override
    if preset != DLSSPreset.DEFAULT:
        env_value = preset.dxvk_env_value
        if env_value:
            env_vars[LINUX_PRESET_ENV_VAR] = env_value

    # Debug overlay (optional)
    if include_debug_overlay:
        # DLSSIndicator=1024 shows the DLSS overlay
        env_vars[LINUX_DEBUG_OPTIONS_VAR] = "DLSSIndicator=1024"

    return env_vars


def format_steam_launch_options(env_vars: dict[str, str]) -> str:
    """
    Format environment variables as Steam launch options string.

    Args:
        env_vars: Dictionary of environment variables

    Returns:
        String formatted for Steam's "Set Launch Options" dialog

    Example output:
        "DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION=render_preset_m %command%"
    """
    if not env_vars:
        return "%command%"

    parts = [f"{key}={value}" for key, value in env_vars.items()]
    return " ".join(parts) + " %command%"


async def apply_preset(preset: DLSSPreset) -> tuple[bool, str | None, dict | None]:
    """
    Apply DLSS SR preset override on the current platform.

    Args:
        preset: DLSSPreset to apply

    Returns:
        tuple[bool, str | None, dict | None]:
            - success: True if applied successfully
            - error_message: Error string on failure, None on success
            - extra_data: Platform-specific data (e.g., env vars for Linux)

    Windows: Writes to registry (requires admin)
    Linux: Returns environment variables for user to configure
    """
    if IS_WINDOWS:
        success, error = await set_preset_windows(preset)
        return (success, error, None)

    elif IS_LINUX:
        # On Linux, we can't auto-apply - return the env vars for the user
        env_vars = generate_linux_env_vars(preset)
        launch_options = format_steam_launch_options(env_vars)

        extra_data = {
            "env_vars": env_vars,
            "steam_launch_options": launch_options,
            "instructions": (
                "Add the following to your Steam launch options or game configuration:\n"
                f"{launch_options}\n\n"
                "For Lutris, add these as environment variables in the game's configuration."
            )
        }

        logger.info(f"Linux DLSS preset generated: {preset.name}")
        return (True, None, extra_data)

    else:
        return (False, "Unsupported platform", None)


async def get_current_preset() -> tuple[DLSSPreset, str | None, dict | None]:
    """
    Get current DLSS SR preset override on the current platform.

    Returns:
        tuple[DLSSPreset, str | None, dict | None]:
            - preset: Current preset (DEFAULT if none set)
            - error_message: Error string on failure
            - extra_data: Platform-specific additional info
    """
    if IS_WINDOWS:
        preset, error = await get_current_preset_windows()
        return (preset, error, None)

    elif IS_LINUX:
        # On Linux, we can't query current state - it depends on how user launches games
        return (
            DLSSPreset.DEFAULT,
            None,
            {"note": "Linux preset detection not available - depends on launch configuration"}
        )

    return (DLSSPreset.DEFAULT, "Unsupported platform", None)
