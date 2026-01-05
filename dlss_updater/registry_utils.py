"""
Registry Utilities Module
Provides async-safe Windows registry operations for NVIDIA DLSS settings.
On non-Windows platforms, returns appropriate fallback values.
"""

import asyncio
import logging

from dlss_updater.platform_utils import IS_WINDOWS

logger = logging.getLogger("DLSSUpdater")

# Registry constants
NGXCORE_REGISTRY_PATH = r"SOFTWARE\NVIDIA Corporation\Global\NGXCore"
DLSS_INDICATOR_KEY = "ShowDlssIndicator"
DLSS_INDICATOR_ENABLED_VALUE = 0x00000400  # 1024 decimal


async def get_dlss_overlay_state() -> tuple[bool, str | None]:
    """
    Read current DLSS debug overlay state from registry.

    Returns:
        tuple[bool, str | None]: (is_enabled, error_message)
        - is_enabled: True if overlay is enabled, False otherwise
        - error_message: None on success, error string on failure
        - On Linux: Returns (False, "DLSS overlay is only available on Windows")

    Runs blocking winreg operations in thread pool.
    """
    if not IS_WINDOWS:
        return (False, "DLSS overlay is only available on Windows")

    def _read_registry() -> tuple[bool, str | None]:
        import winreg

        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, NGXCORE_REGISTRY_PATH, 0, winreg.KEY_READ
            )
            try:
                value, reg_type = winreg.QueryValueEx(key, DLSS_INDICATOR_KEY)
                winreg.CloseKey(key)
                return (value == DLSS_INDICATOR_ENABLED_VALUE, None)
            except FileNotFoundError:
                # Key exists but value doesn't - overlay is disabled
                winreg.CloseKey(key)
                return (False, None)
        except FileNotFoundError:
            # NGXCore path doesn't exist - overlay is disabled
            return (False, None)
        except PermissionError as e:
            return (False, f"Permission denied reading registry: {e}")
        except OSError as e:
            return (False, f"Registry access error: {e}")

    return await asyncio.to_thread(_read_registry)


async def set_dlss_overlay_state(enabled: bool) -> tuple[bool, str | None]:
    """
    Set DLSS debug overlay state in registry.

    Args:
        enabled: True to enable overlay, False to disable

    Returns:
        tuple[bool, str | None]: (success, error_message)
        - success: True if operation succeeded
        - error_message: None on success, error string on failure
        - On Linux: Returns (False, "DLSS overlay is only available on Windows")

    Runs blocking winreg operations in thread pool.
    """
    if not IS_WINDOWS:
        return (False, "DLSS overlay is only available on Windows")

    def _write_registry() -> tuple[bool, str | None]:
        import winreg

        try:
            # Create key path if it doesn't exist
            key = winreg.CreateKeyEx(
                winreg.HKEY_LOCAL_MACHINE,
                NGXCORE_REGISTRY_PATH,
                0,
                winreg.KEY_WRITE,
            )

            if enabled:
                # Set value to enable overlay
                winreg.SetValueEx(
                    key,
                    DLSS_INDICATOR_KEY,
                    0,
                    winreg.REG_DWORD,
                    DLSS_INDICATOR_ENABLED_VALUE,
                )
                logger.info(
                    f"DLSS debug overlay enabled (set {DLSS_INDICATOR_KEY}=0x{DLSS_INDICATOR_ENABLED_VALUE:X})"
                )
            else:
                # Delete value to disable overlay
                try:
                    winreg.DeleteValue(key, DLSS_INDICATOR_KEY)
                    logger.info(
                        f"DLSS debug overlay disabled (deleted {DLSS_INDICATOR_KEY})"
                    )
                except FileNotFoundError:
                    # Value doesn't exist, already disabled
                    logger.debug("DLSS debug overlay already disabled")

            winreg.CloseKey(key)
            return (True, None)

        except PermissionError as e:
            error_msg = (
                "Administrator privileges required to modify NVIDIA registry settings"
            )
            logger.error(f"Permission denied writing registry: {e}")
            return (False, error_msg)
        except OSError as e:
            error_msg = f"Failed to access Windows registry: {e}"
            logger.error(f"Registry write error: {e}", exc_info=True)
            return (False, error_msg)

    return await asyncio.to_thread(_write_registry)
