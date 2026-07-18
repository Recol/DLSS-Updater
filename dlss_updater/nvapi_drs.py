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

import logging
import threading

import anyio

from dlss_updater.concurrency_limiters import thread_io
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
    # Per-application profile management (NVIDIA/nvapi public SDK headers).
    # Verified live on hardware via tools/nvapi_perapp_probe.py (all ids resolve;
    # full create/set/read-back/delete round-trip + predefined-profile resolve).
    "NvAPI_DRS_CreateProfile": 0xCC176068,
    "NvAPI_DRS_DeleteProfile": 0x17093206,
    "NvAPI_DRS_FindProfileByName": 0x7E4A9A0B,
    "NvAPI_DRS_GetProfileInfo": 0x61CD6FD6,
    "NvAPI_DRS_CreateApplication": 0x4347A9DE,
    "NvAPI_DRS_DeleteApplicationEx": 0xC5EA85A1,
    "NvAPI_DRS_EnumApplications": 0x7FA2173A,
    "NvAPI_DRS_FindApplicationByName": 0xEEE566B2,
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
_NVAPI_PROFILE_NOT_FOUND = -163
_NVAPI_SETTING_NOT_FOUND = -165
# Returned by DeleteProfileSetting on a user-created profile when there is no
# user-level override for the id (only an inherited/predefined value). Benign.
_NVAPI_SETTING_ALREADY_DEFAULT = -160
# FindApplicationByName returns this for a fully-qualified exe that maps to no
# profile (verified live: an unknown exe yields -166). NOT -161, which is
# INCOMPATIBLE_STRUCT_VERSION and must never be treated as "not found".
_NVAPI_EXECUTABLE_NOT_FOUND = -166

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

    # --- per-application profile structures -------------------------------
    class NVDRS_PROFILE(Structure):
        _fields_ = [
            ("version", c_uint32),
            ("profileName", NvAPI_UnicodeString),
            ("gpuSupport", c_uint32),    # NVDRS_GPU_SUPPORT bitfield; bit0 = geforce
            ("isPredefined", c_uint32),
            ("numOfApps", c_uint32),
            ("numOfSettings", c_uint32),
        ]

    class NVDRS_APPLICATION(Structure):  # V4 (current)
        _fields_ = [
            ("version", c_uint32),
            ("isPredefined", c_uint32),
            ("appName", NvAPI_UnicodeString),
            ("userFriendlyName", NvAPI_UnicodeString),
            ("launcher", NvAPI_UnicodeString),
            ("fileInFolder", NvAPI_UnicodeString),
            ("flags", c_uint32),         # bitfields: isMetro:1, isCommandLine:1
            ("commandLine", NvAPI_UnicodeString),
        ]

    profile_version = ctypes.sizeof(NVDRS_PROFILE) | (1 << 16)
    application_version = ctypes.sizeof(NVDRS_APPLICATION) | (4 << 16)

    def set_ustr(arr, text: str) -> None:
        """Write a Python str into an NvAPI_UnicodeString (u16[2048]) buffer."""
        ctypes.memset(arr, 0, ctypes.sizeof(arr))
        for i, ch in enumerate(text[:2047]):
            arr[i] = ord(ch)

    def read_ustr(arr) -> str:
        out = []
        for code in arr:
            if code == 0:
                break
            out.append(chr(code))
        return "".join(out)

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
        # Pin the loaded module so the resolved function pointers below stay
        # valid for the lifetime of the (now cached) table - prevents FreeLibrary
        # from unloading nvapi64.dll when local refs are dropped.
        "_dll": dll,
        "ctypes": ctypes,
        "byref": byref,
        "c_void_p": c_void_p,
        "c_uint32": c_uint32,
        "POINTER": POINTER,
        "NvAPI_UnicodeString": NvAPI_UnicodeString,
        "NVDRS_SETTING": NVDRS_SETTING,
        "NVDRS_SETTING_VER": setting_version,
        "NVDRS_PROFILE": NVDRS_PROFILE,
        "NVDRS_PROFILE_VER": profile_version,
        "NVDRS_APPLICATION": NVDRS_APPLICATION,
        "NVDRS_APPLICATION_VER": application_version,
        "set_ustr": set_ustr,
        "read_ustr": read_ustr,
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
        # per-application profile management
        "CreateProfile": fn("NvAPI_DRS_CreateProfile", c_void_p, POINTER(NVDRS_PROFILE), POINTER(c_void_p)),
        "DeleteProfile": fn("NvAPI_DRS_DeleteProfile", c_void_p, c_void_p),
        "FindProfileByName": fn("NvAPI_DRS_FindProfileByName", c_void_p, NvAPI_UnicodeString, POINTER(c_void_p)),
        "GetProfileInfo": fn("NvAPI_DRS_GetProfileInfo", c_void_p, c_void_p, POINTER(NVDRS_PROFILE)),
        "CreateApplication": fn("NvAPI_DRS_CreateApplication", c_void_p, c_void_p, POINTER(NVDRS_APPLICATION)),
        "FindApplicationByName": fn(
            "NvAPI_DRS_FindApplicationByName",
            c_void_p, NvAPI_UnicodeString, POINTER(c_void_p), POINTER(NVDRS_APPLICATION),
        ),
    }


# -----------------------------------------------------------------------------
# Cached NvAPI function table (free-threaded safe)
# -----------------------------------------------------------------------------
# _build_nvapi() loads nvapi64.dll and resolves ~20 function pointers via
# nvapi_QueryInterface. That work is identical on every call and is pure overhead
# when a single exe_resolver run probes up to _DRIVER_PROBE_LIMIT candidates
# (each triggering a full read/apply cycle). Cache the built table once.
#
# Both outcomes are cached:
#   - success: the table dict is reused for every subsequent call.
#   - failure (no NVIDIA driver / DLL missing): the raised exception is cached
#     and re-raised, so repeated probes don't retry the DLL load every call.
#     Callers already wrap _get_nvapi()/_build_nvapi() in try/except and downgrade
#     to an error tuple, so re-raising the cached exception preserves the exact
#     existing "NvAPI unavailable: ..." error semantics.
#
# The cached table holds only read-only artefacts (ctypes classes, pure helper
# closures, and immutable WINFUNCTYPE call wrappers). Callers instantiate their
# own structs and per-call _DrsSession, so the shared table is safe to reuse
# concurrently across worker threads on the free-threaded build.
_nvapi_lock = threading.Lock()
_nvapi_table: dict | None = None
_nvapi_error: Exception | None = None
_nvapi_built: bool = False


def _get_nvapi() -> dict:
    """Return the cached NvAPI function table, building it once on first use.

    Raises the (cached) build exception if nvapi64.dll cannot be loaded/resolved,
    mirroring a direct ``_build_nvapi()`` failure for callers.
    """
    global _nvapi_table, _nvapi_error, _nvapi_built

    # Fast path: no lock once the build outcome is known.
    if _nvapi_built:
        if _nvapi_error is not None:
            raise _nvapi_error
        return _nvapi_table  # type: ignore[return-value]

    with _nvapi_lock:
        # Double-check after acquiring the lock.
        if not _nvapi_built:
            try:
                _nvapi_table = _build_nvapi()
                _nvapi_error = None
            except Exception as e:
                _nvapi_table = None
                _nvapi_error = e
            finally:
                _nvapi_built = True
        if _nvapi_error is not None:
            raise _nvapi_error
        return _nvapi_table  # type: ignore[return-value]


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


def _set_dword(api: dict, drs: _DrsSession, profile, setting_id: int, value: int) -> None:
    setting = api["NVDRS_SETTING"]()
    setting.version = api["NVDRS_SETTING_VER"]
    setting.settingId = setting_id
    setting.settingType = _NVDRS_DWORD_TYPE
    setting.currentValue.u32Value = value & 0xFFFFFFFF
    r = api["SetSetting"](drs.session, profile, api["byref"](setting))
    if r != _NVAPI_OK:
        raise RuntimeError(f"NvAPI_DRS_SetSetting(0x{setting_id:08X}) failed ({r})")


def _delete_setting(api: dict, drs: _DrsSession, profile, setting_id: int) -> None:
    # DeleteProfileSetting removes only a *user-level* override. When the profile
    # has no user value for this id (only an inherited/predefined one, or nothing
    # at all) the driver returns a benign "nothing to delete" code that varies by
    # profile kind - seen live as -160 (user-created profile) and -165
    # (NVAPI_SETTING_NOT_FOUND). Both mean the override is already absent.
    r = api["DeleteProfileSetting"](drs.session, profile, setting_id)
    if r not in (_NVAPI_OK, _NVAPI_SETTING_NOT_FOUND, _NVAPI_SETTING_ALREADY_DEFAULT):
        raise RuntimeError(
            f"NvAPI_DRS_DeleteProfileSetting(0x{setting_id:08X}) failed ({r})"
        )


def _read_dword(api: dict, drs: _DrsSession, profile, setting_id: int) -> int | None:
    setting = api["NVDRS_SETTING"]()
    setting.version = api["NVDRS_SETTING_VER"]
    r = api["GetSetting"](drs.session, profile, setting_id, api["byref"](setting))
    if r != _NVAPI_OK:
        return None  # not set in this profile
    return int(setting.currentValue.u32Value)


def _read_dword_ex(api: dict, drs: _DrsSession, profile, setting_id: int) -> tuple[int | None, bool]:
    """Like _read_dword but also reports whether the value is the predefined one.

    Returns (value_or_None, is_predefined). On a driver-predefined profile this
    lets callers distinguish NVIDIA's shipped preset from a user override.
    """
    setting = api["NVDRS_SETTING"]()
    setting.version = api["NVDRS_SETTING_VER"]
    r = api["GetSetting"](drs.session, profile, setting_id, api["byref"](setting))
    if r != _NVAPI_OK:
        return (None, False)
    return (int(setting.currentValue.u32Value), bool(setting.isCurrentPredefined))


# =============================================================================
# Blocking implementations (run in a thread)
# =============================================================================

def _read_all_blocking() -> tuple[dict[str, int | None], str | None]:
    """Read current raw preset values for all features. Returns (values, error)."""
    try:
        api = _get_nvapi()
    except Exception as e:
        return ({}, f"NvAPI unavailable: {e}")

    try:
        out: dict[str, int | None] = {}
        with _DrsSession(api) as drs:
            for feat, (_override_id, preset_id) in _FEATURE_IDS.items():
                out[feat] = _read_dword(api, drs, drs.profile, preset_id)
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
        api = _get_nvapi()
    except Exception as e:
        return (False, f"NvAPI unavailable: {e}")

    try:
        with _DrsSession(api) as drs:
            for feat, preset in selections.items():
                override_id, preset_id = _FEATURE_IDS[feat]
                if preset == "default":
                    _delete_setting(api, drs, drs.profile, preset_id)
                    _set_dword(api, drs, drs.profile, override_id, 0)
                else:
                    _set_dword(api, drs, drs.profile, override_id, 1)
                    _set_dword(api, drs, drs.profile, preset_id, _PRESET_VALUES[feat][preset])
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
# Per-application profile layer (blocking - run in a thread)
# =============================================================================

def _resolve_app_profile(
    api: dict,
    drs: _DrsSession,
    exe_path: str,
    *,
    create: bool,
    friendly_name: str | None = None,
):
    """Resolve the DRS profile that governs ``exe_path``.

    Strategy (matches NVIDIA's own resolution order):
      1. ``FindApplicationByName`` with the fully-qualified path returns the
         profile the driver would actually apply - including NVIDIA's predefined
         per-game profiles (e.g. "Battlefield 6"). We layer user overrides on top.
      2. If no profile maps the exe and ``create`` is True, create a dedicated
         profile and attach the exe to it.

    Returns ``(profile_handle | None, profile_name, is_predefined, created)``.
    When the exe is unknown and ``create`` is False, returns ``(None, None, False, False)``.
    """
    import os

    full = os.path.abspath(exe_path)
    exe_name = os.path.basename(full)

    name_buf = api["NvAPI_UnicodeString"]()
    api["set_ustr"](name_buf, full)
    profile = api["c_void_p"]()
    app = api["NVDRS_APPLICATION"]()
    app.version = api["NVDRS_APPLICATION_VER"]
    r = api["FindApplicationByName"](drs.session, name_buf, api["byref"](profile), api["byref"](app))

    if r == _NVAPI_OK:
        pinfo = api["NVDRS_PROFILE"]()
        pinfo.version = api["NVDRS_PROFILE_VER"]
        api["GetProfileInfo"](drs.session, profile, api["byref"](pinfo))
        return (profile, api["read_ustr"](pinfo.profileName), bool(pinfo.isPredefined), False)

    if r not in (_NVAPI_EXECUTABLE_NOT_FOUND, _NVAPI_PROFILE_NOT_FOUND):
        raise RuntimeError(f"NvAPI_DRS_FindApplicationByName failed ({r})")

    if not create:
        return (None, None, False, False)

    # Unknown exe: create (or reuse) a dedicated profile, then attach the exe.
    prof_name = (friendly_name or exe_name).strip() or exe_name
    name_buf2 = api["NvAPI_UnicodeString"]()
    api["set_ustr"](name_buf2, prof_name)
    hprof = api["c_void_p"]()
    existing = api["FindProfileByName"](drs.session, name_buf2, api["byref"](hprof))
    if existing != _NVAPI_OK:
        prof = api["NVDRS_PROFILE"]()
        prof.version = api["NVDRS_PROFILE_VER"]
        api["set_ustr"](prof.profileName, prof_name)
        prof.gpuSupport = 1  # geforce bit
        rc = api["CreateProfile"](drs.session, api["byref"](prof), api["byref"](hprof))
        if rc != _NVAPI_OK:
            raise RuntimeError(f"NvAPI_DRS_CreateProfile failed ({rc})")

    napp = api["NVDRS_APPLICATION"]()
    napp.version = api["NVDRS_APPLICATION_VER"]
    api["set_ustr"](napp.appName, exe_name)
    api["set_ustr"](napp.userFriendlyName, prof_name)
    rc = api["CreateApplication"](drs.session, hprof, api["byref"](napp))
    # Re-adding an exe already present in this profile is harmless.
    if rc not in (_NVAPI_OK,):
        raise RuntimeError(f"NvAPI_DRS_CreateApplication failed ({rc})")

    return (hprof, prof_name, False, True)


def _read_app_blocking(exe_path: str) -> tuple[dict[str, int | None], dict, str | None]:
    """Read per-game preset values for the profile governing ``exe_path``.

    Returns ``(values, meta, error)`` where ``values`` maps feature -> raw value
    (or None) and ``meta`` carries ``found``, ``profile_name``,
    ``is_predefined_profile`` and a per-feature ``predefined`` flag map.
    """
    try:
        api = _get_nvapi()
    except Exception as e:
        return ({}, {}, f"NvAPI unavailable: {e}")

    try:
        with _DrsSession(api) as drs:
            profile, pname, is_predef, _ = _resolve_app_profile(api, drs, exe_path, create=False)
            if profile is None:
                return ({}, {"found": False}, None)
            values: dict[str, int | None] = {}
            predefined: dict[str, bool] = {}
            for feat, (_override_id, preset_id) in _FEATURE_IDS.items():
                val, is_pre = _read_dword_ex(api, drs, profile, preset_id)
                values[feat] = val
                predefined[feat] = is_pre
            meta = {
                "found": True,
                "profile_name": pname,
                "is_predefined_profile": is_predef,
                "predefined": predefined,
            }
            return (values, meta, None)
    except Exception as e:
        logger.error(f"Failed to read per-game DLSS presets: {e}", exc_info=True)
        return ({}, {}, str(e))


def _apply_app_blocking(
    exe_path: str, selections: dict[str, str], friendly_name: str | None
) -> tuple[bool, str | None]:
    """Apply per-game preset selections to the profile governing ``exe_path``.

    "default" deletes that feature's override so the profile falls back to its
    predefined value (for NVIDIA's known games) or to no-override (for games we
    created a profile for).
    """
    for feat, preset in selections.items():
        if feat not in _FEATURE_IDS:
            return (False, f"Unknown feature: {feat}")
        if preset not in _PRESET_VALUES[feat]:
            return (False, f"Unknown {feat.upper()} preset: {preset}")

    try:
        api = _get_nvapi()
    except Exception as e:
        return (False, f"NvAPI unavailable: {e}")

    try:
        with _DrsSession(api) as drs:
            profile, pname, _is_predef, _created = _resolve_app_profile(
                api, drs, exe_path, create=True, friendly_name=friendly_name
            )
            for feat, preset in selections.items():
                override_id, preset_id = _FEATURE_IDS[feat]
                if preset == "default":
                    _delete_setting(api, drs, profile, preset_id)
                    _delete_setting(api, drs, profile, override_id)
                else:
                    _set_dword(api, drs, profile, override_id, 1)
                    _set_dword(api, drs, profile, preset_id, _PRESET_VALUES[feat][preset])
            drs.save()
        applied = ", ".join(f"{f.upper()}={p}" for f, p in selections.items())
        logger.info(f"DLSS per-game preset override applied to '{pname}': {applied}")
        return (True, None)
    except OSError as e:
        logger.error(f"OS error writing per-game DLSS presets: {e}")
        return (False, f"Failed to write driver settings (admin required?): {e}")
    except Exception as e:
        logger.error(f"Failed to write per-game DLSS presets: {e}", exc_info=True)
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
    return await anyio.to_thread.run_sync(_read_all_blocking, limiter=thread_io)


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
    return await anyio.to_thread.run_sync(_apply_blocking, selections, limiter=thread_io)


async def get_presets_for_app(
    exe_path: str,
) -> tuple[dict[str, int | None], dict, str | None]:
    """Read the per-game preset overrides for the profile governing ``exe_path``.

    Returns ``(values, meta, error)``:
      - ``values``: {"sr"/"rr"/"fg": raw value | None}. Render with
        ``describe_preset_value()``.
      - ``meta``: {"found": bool, "profile_name": str, "is_predefined_profile":
        bool, "predefined": {feature: bool}}. ``found`` is False when no driver
        profile maps the exe yet (no override has ever been applied).
    """
    if not IS_WINDOWS:
        return ({}, {}, "DLSS preset control is only available on Windows")
    return await anyio.to_thread.run_sync(_read_app_blocking, exe_path, limiter=thread_io)


async def apply_presets_for_app(
    exe_path: str,
    selections: dict[str, str],
    friendly_name: str | None = None,
) -> tuple[bool, str | None]:
    """Apply per-game preset selections to the profile governing ``exe_path``.

    Resolves the driver profile for the exe (NVIDIA's predefined profile when the
    game is known, otherwise a profile we create and attach the exe to), then
    layers the selections on top. Per-game overrides take priority over the
    global base-profile override at game launch.

    Args:
        exe_path: Fully-qualified path to the game's executable.
        selections: {feature: preset_key}; "default" reverts that feature to the
            profile's predefined value (or removes the override entirely).
        friendly_name: Display name used when a new profile must be created.

    Returns:
        (success, error_message).
    """
    if not IS_WINDOWS:
        return (False, "DLSS preset control is only available on Windows")
    if not selections:
        return (True, None)
    return await anyio.to_thread.run_sync(_apply_app_blocking, exe_path, selections, friendly_name, limiter=thread_io)


async def reset_app_presets(exe_path: str) -> tuple[bool, str | None]:
    """Clear all per-game SR/RR/FG overrides for ``exe_path``.

    Reverts every feature to the profile's predefined value (known games) or to
    no-override (games we created a profile for).
    """
    return await apply_presets_for_app(exe_path, {f: "default" for f in VALID_FEATURES})
