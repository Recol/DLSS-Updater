"""
NvAPI Driver Settings (DRS) module.

Provides async-safe control of the global NVIDIA DLSS preset overrides via the
NvAPI Driver Settings system - the same mechanism the NVIDIA App uses for its
global "DLSS Override" feature. Covers all three DLSS components:

    SR  - Super Resolution      (upscaling)
    RR  - Ray Reconstruction    (denoising)
    FG  - Frame Generation       (frame interpolation)

How it works
------------
The DLSS runtime queries the NVIDIA driver's *base profile* for a render-preset
override at game launch. By writing to that base profile we can force every
DLSS title to use a specific preset (or always pull the latest model),
regardless of what the game itself requests.

Call chain (per write):
    nvapi_QueryInterface(<func id>) -> function pointer
    NvAPI_Initialize()
    NvAPI_DRS_CreateSession(&hSession)
    NvAPI_DRS_LoadSettings(hSession)
    NvAPI_DRS_GetBaseProfile(hSession, &hProfile)   # global, not per-game
    NvAPI_DRS_SetSetting(hSession, hProfile, &setting)   # or DeleteProfileSetting
    NvAPI_DRS_SaveSettings(hSession)
    NvAPI_DRS_DestroySession(hSession)

Setting IDs and preset enum values are taken from NVIDIA's public
``NvApiDriverSettings.h`` (NVIDIA/nvapi on GitHub) and were verified live on
hardware (read-back of NVIDIA-App-written values + non-destructive round-trip).

Notes
-----
- Windows-only. On any other platform the public functions return a friendly
  "not available" error tuple, mirroring ``registry_utils.py``.
- Writing to the driver profile database may require administrator privileges
  depending on system configuration (the application already elevates on
  Windows); OS errors are surfaced gracefully.
- All blocking ctypes work runs in a thread via ``asyncio.to_thread`` so the
  Flet event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging

from dlss_updater.platform_utils import IS_WINDOWS

logger = logging.getLogger("DLSSUpdater")

# =============================================================================
# NvAPI constants (from NVIDIA/nvapi public SDK headers)
# =============================================================================

# Function interface IDs - resolved at runtime via nvapi_QueryInterface
_FUNC = {
    "NvAPI_Initialize": 0x0150E828,
    "NvAPI_Unload": 0xD22BDD7E,
    "NvAPI_DRS_CreateSession": 0x0694D52E,
    "NvAPI_DRS_DestroySession": 0xDAD9CFF8,
    "NvAPI_DRS_LoadSettings": 0x375DBD6B,
    "NvAPI_DRS_SaveSettings": 0xFCBC7E14,
    "NvAPI_DRS_GetBaseProfile": 0xDA8466A0,
    "NvAPI_DRS_SetSetting": 0x577DD202,
    "NvAPI_DRS_GetSetting": 0x73BF8338,
    "NvAPI_DRS_DeleteProfileSetting": 0xE4A26362,
}

# DRS setting IDs (NvApiDriverSettings.h) - (override_enable_id, preset_select_id)
# Verified live: names resolve to
#   "Enable DLSS-SR override" / "Override DLSS-SR presets", etc.
_FEATURE_IDS: dict[str, tuple[int, int]] = {
    "sr": (0x10E41E01, 0x10E41DF3),
    "rr": (0x10E41E02, 0x10E41DF7),
    "fg": (0x10E41E03, 0x10E41DF1),
}

# Special preset enum values shared across features
_PRESET_OFF = 0x00000000
_PRESET_DEFAULT = 0x00FFFFFE  # FG only ("Default" model)
_PRESET_LATEST = 0x00FFFFFF

# Per-feature mapping: config string -> driver u32 preset value.
# "default" is handled specially (clears the override entirely).
_PRESET_VALUES: dict[str, dict[str, int]] = {
    "sr": {
        "default": _PRESET_OFF,
        "latest": _PRESET_LATEST,
        "preset_j": 0x0A,  # 10
        "preset_k": 0x0B,  # 11
        "preset_l": 0x0C,  # 12
        "preset_m": 0x0D,  # 13
    },
    "rr": {
        "default": _PRESET_OFF,
        "latest": _PRESET_LATEST,
    },
    "fg": {
        "default": _PRESET_OFF,
        "latest": _PRESET_LATEST,
        "preset_a": 0x01,
        "preset_b": 0x02,
    },
}

# NVDRS_SETTING_TYPE
_NVDRS_DWORD_TYPE = 0

# NvAPI status codes we care about
_NVAPI_OK = 0
_NVAPI_SETTING_NOT_FOUND = -165

_NVAPI_PATH = r"C:\Windows\System32\nvapi64.dll"

VALID_FEATURES = ("sr", "rr", "fg")


# =============================================================================
# Value <-> label helpers
# =============================================================================

def preset_key_to_value(feature: str, preset: str) -> int | None:
    """Map a config preset key to the driver u32 value (None if unknown)."""
    return _PRESET_VALUES.get(feature, {}).get(preset)


def describe_preset_value(value: int | None) -> str:
    """
    Human description of any raw driver preset value (feature-agnostic).

    Handles the well-known special values plus the A-Z letter range, so the
    live readout is accurate even for values set by the NVIDIA App that aren't
    offered in our dropdowns (e.g. FG Preset B).
    """
    if value is None:
        return "Not set (no override)"
    if value == _PRESET_OFF:
        return "Off (no override)"
    if value == _PRESET_LATEST:
        return "Latest (newest model)"
    if value == _PRESET_DEFAULT:
        return "Default model"
    if 1 <= value <= 26:
        return f"Preset {chr(64 + value)}"
    return f"0x{value:08X}"


# =============================================================================
# ctypes plumbing (lazy - only touched on Windows)
# =============================================================================

def _build_nvapi():
    """
    Load nvapi64.dll and construct the ctypes structures + QueryInterface shim.

    Returns a dict of helpers, or raises RuntimeError on failure. Kept lazy so
    importing this module never touches ctypes on non-Windows platforms.
    """
    import ctypes
    from ctypes import (
        c_void_p, c_uint32, c_uint16, c_uint8, c_int,
        byref, POINTER, WINFUNCTYPE, Structure, Union,
    )

    NvAPI_UnicodeString = c_uint16 * 2048

    class NVDRS_BINARY(Structure):
        _fields_ = [("valueLength", c_uint32), ("valueData", c_uint8 * 4096)]

    class NVDRS_VALUE(Union):
        _fields_ = [
            ("u32Value", c_uint32),
            ("binaryValue", NVDRS_BINARY),
            ("wszValue", NvAPI_UnicodeString),
        ]

    class NVDRS_SETTING(Structure):
        _fields_ = [
            ("version", c_uint32),
            ("settingName", NvAPI_UnicodeString),
            ("settingId", c_uint32),
            ("settingType", c_int),
            ("settingLocation", c_int),
            ("isCurrentPredefined", c_uint32),
            ("isPredefinedValid", c_uint32),
            ("predefinedValue", NVDRS_VALUE),
            ("currentValue", NVDRS_VALUE),
        ]

    setting_version = ctypes.sizeof(NVDRS_SETTING) | (1 << 16)

    dll = ctypes.WinDLL(_NVAPI_PATH)
    query = dll.nvapi_QueryInterface
    query.restype = c_void_p
    query.argtypes = [c_uint32]

    def fn(name, *argtypes):
        addr = query(_FUNC[name])
        if not addr:
            raise RuntimeError(f"nvapi_QueryInterface returned NULL for {name}")
        return WINFUNCTYPE(c_int, *argtypes)(addr)

    return {
        "ctypes": ctypes,
        "byref": byref,
        "c_void_p": c_void_p,
        "POINTER": POINTER,
        "NVDRS_SETTING": NVDRS_SETTING,
        "NVDRS_SETTING_VER": setting_version,
        "Initialize": fn("NvAPI_Initialize"),
        "Unload": fn("NvAPI_Unload"),
        "CreateSession": fn("NvAPI_DRS_CreateSession", POINTER(c_void_p)),
        "DestroySession": fn("NvAPI_DRS_DestroySession", c_void_p),
        "LoadSettings": fn("NvAPI_DRS_LoadSettings", c_void_p),
        "SaveSettings": fn("NvAPI_DRS_SaveSettings", c_void_p),
        "GetBaseProfile": fn("NvAPI_DRS_GetBaseProfile", c_void_p, POINTER(c_void_p)),
        "SetSetting": fn("NvAPI_DRS_SetSetting", c_void_p, c_void_p, POINTER(NVDRS_SETTING)),
        "GetSetting": fn("NvAPI_DRS_GetSetting", c_void_p, c_void_p, c_uint32, POINTER(NVDRS_SETTING)),
        "DeleteProfileSetting": fn("NvAPI_DRS_DeleteProfileSetting", c_void_p, c_void_p, c_uint32),
    }


class _DrsSession:
    """Context manager handling NvAPI init/session lifecycle and base profile."""

    def __init__(self, api: dict):
        self._api = api
        self._session = None  # c_void_p handle
        self._initialized = False
        self.profile = None  # base profile handle

    def __enter__(self):
        api = self._api

        r = api["Initialize"]()
        if r != _NVAPI_OK:
            raise RuntimeError(f"NvAPI_Initialize failed ({r})")
        self._initialized = True

        session = api["c_void_p"]()
        r = api["CreateSession"](api["byref"](session))
        if r != _NVAPI_OK:
            raise RuntimeError(f"NvAPI_DRS_CreateSession failed ({r})")
        self._session = session

        r = api["LoadSettings"](session)
        if r != _NVAPI_OK:
            raise RuntimeError(f"NvAPI_DRS_LoadSettings failed ({r})")

        profile = api["c_void_p"]()
        r = api["GetBaseProfile"](session, api["byref"](profile))
        if r != _NVAPI_OK:
            raise RuntimeError(f"NvAPI_DRS_GetBaseProfile failed ({r})")
        self.profile = profile
        return self

    @property
    def session(self):
        return self._session

    def save(self):
        r = self._api["SaveSettings"](self._session)
        if r != _NVAPI_OK:
            raise RuntimeError(f"NvAPI_DRS_SaveSettings failed ({r})")

    def __exit__(self, exc_type, exc, tb):
        api = self._api
        try:
            if self._session is not None:
                api["DestroySession"](self._session)
        finally:
            if self._initialized:
                api["Unload"]()
        return False


def _set_dword(api: dict, drs: _DrsSession, setting_id: int, value: int) -> None:
    setting = api["NVDRS_SETTING"]()
    setting.version = api["NVDRS_SETTING_VER"]
    setting.settingId = setting_id
    setting.settingType = _NVDRS_DWORD_TYPE
    setting.currentValue.u32Value = value & 0xFFFFFFFF
    r = api["SetSetting"](drs.session, drs.profile, api["byref"](setting))
    if r != _NVAPI_OK:
        raise RuntimeError(f"NvAPI_DRS_SetSetting(0x{setting_id:08X}) failed ({r})")


def _delete_setting(api: dict, drs: _DrsSession, setting_id: int) -> None:
    r = api["DeleteProfileSetting"](drs.session, drs.profile, setting_id)
    if r not in (_NVAPI_OK, _NVAPI_SETTING_NOT_FOUND):
        raise RuntimeError(
            f"NvAPI_DRS_DeleteProfileSetting(0x{setting_id:08X}) failed ({r})"
        )


def _read_dword(api: dict, drs: _DrsSession, setting_id: int) -> int | None:
    setting = api["NVDRS_SETTING"]()
    setting.version = api["NVDRS_SETTING_VER"]
    r = api["GetSetting"](drs.session, drs.profile, setting_id, api["byref"](setting))
    if r != _NVAPI_OK:
        return None  # not set in base profile
    return int(setting.currentValue.u32Value)


# =============================================================================
# Blocking implementations (run in a thread)
# =============================================================================

def _read_all_blocking() -> tuple[dict[str, int | None], str | None]:
    """Read current raw preset values for all features. Returns (values, error)."""
    try:
        api = _build_nvapi()
    except Exception as e:
        return ({}, f"NvAPI unavailable: {e}")

    try:
        out: dict[str, int | None] = {}
        with _DrsSession(api) as drs:
            for feat, (_override_id, preset_id) in _FEATURE_IDS.items():
                out[feat] = _read_dword(api, drs, preset_id)
        return (out, None)
    except Exception as e:
        logger.error(f"Failed to read DLSS presets: {e}", exc_info=True)
        return ({}, str(e))


def _apply_blocking(selections: dict[str, str]) -> tuple[bool, str | None]:
    """
    Apply preset selections for one or more features in a single session.

    Args:
        selections: {feature: preset_key}. "default" clears that feature's
                    override; any other key enables the override and sets it.
    """
    # Validate first
    for feat, preset in selections.items():
        if feat not in _FEATURE_IDS:
            return (False, f"Unknown feature: {feat}")
        if preset not in _PRESET_VALUES[feat]:
            return (False, f"Unknown {feat.upper()} preset: {preset}")

    try:
        api = _build_nvapi()
    except Exception as e:
        return (False, f"NvAPI unavailable: {e}")

    try:
        with _DrsSession(api) as drs:
            for feat, preset in selections.items():
                override_id, preset_id = _FEATURE_IDS[feat]
                if preset == "default":
                    _delete_setting(api, drs, preset_id)
                    _set_dword(api, drs, override_id, 0)
                else:
                    _set_dword(api, drs, override_id, 1)
                    _set_dword(api, drs, preset_id, _PRESET_VALUES[feat][preset])
            drs.save()
        applied = ", ".join(f"{f.upper()}={p}" for f, p in selections.items())
        logger.info(f"DLSS global preset override applied: {applied}")
        return (True, None)
    except OSError as e:
        logger.error(f"OS error writing DLSS presets: {e}")
        return (False, f"Failed to write driver settings (admin required?): {e}")
    except Exception as e:
        logger.error(f"Failed to write DLSS presets: {e}", exc_info=True)
        return (False, str(e))


# =============================================================================
# Public async API
# =============================================================================

def is_available() -> bool:
    """True if the NvAPI DRS preset feature can run on this system."""
    if not IS_WINDOWS:
        return False
    import os
    return os.path.exists(_NVAPI_PATH)


async def get_current_presets() -> tuple[dict[str, int | None], str | None]:
    """
    Read the current global preset override values for SR, RR and FG.

    Returns:
        ({"sr": raw|None, "rr": raw|None, "fg": raw|None}, error_message).
        Use describe_preset_value() to render each raw value for display.
    """
    if not IS_WINDOWS:
        return ({}, "DLSS preset control is only available on Windows")
    return await asyncio.to_thread(_read_all_blocking)


async def apply_presets(selections: dict[str, str]) -> tuple[bool, str | None]:
    """
    Apply preset selections for one or more DLSS features in one driver write.

    Args:
        selections: {feature: preset_key} where feature is "sr"/"rr"/"fg" and
                    preset_key is a key from the feature's value map (e.g.
                    "default", "latest", "preset_k"). "default" clears that
                    feature's override.

    Returns:
        (success, error_message).
    """
    if not IS_WINDOWS:
        return (False, "DLSS preset control is only available on Windows")
    if not selections:
        return (True, None)
    return await asyncio.to_thread(_apply_blocking, selections)
