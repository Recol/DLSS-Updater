"""
NvAPI per-application DRS probe (standalone, reversible).

Validates the per-game DLSS preset-override mechanism on real hardware BEFORE any
of it is wired into the application. This mirrors how `dlss_updater/nvapi_drs.py`
was originally verified for the *global* base profile, but exercises the
*per-application profile* path instead (CreateProfile / CreateApplication /
FindApplicationByName / SetSetting / DeleteProfile).

What it does
------------
1. Loads nvapi64.dll and resolves every function id we need. A NULL pointer for
   any id means that id is wrong -> the probe self-verifies the new constants.
2. RESOLVE test (read-only, safe): if --exe is given, calls FindApplicationByName
   on that fully-qualified path and prints the profile the driver would apply plus
   that game's current SR / RR / FG preset override (if any).
3. ROUND-TRIP test (reversible): creates a throwaway profile + fake exe, writes an
   SR preset, saves, re-opens a fresh session, reads it back, then DELETES the
   throwaway profile and confirms it is gone. No real game/profile is modified.

Usage
-----
    # Round-trip self-test only (creates + deletes a throwaway profile):
    python tools/nvapi_perapp_probe.py

    # Also resolve a real game's profile + current preset (read-only):
    python tools/nvapi_perapp_probe.py --exe "D:\\Steam\\steamapps\\common\\Cyberpunk 2077\\bin\\x64\\Cyberpunk2077.exe"

Notes
-----
- Writing/saving DRS settings may require an ELEVATED terminal. Reads work without.
- Run from the repo root. Windows + NVIDIA only.
- The throwaway profile name and fake exe below are unique to this probe so they
  cannot collide with a real game profile.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from ctypes import (
    c_void_p, c_uint32, c_uint16, c_uint8, c_int,
    byref, POINTER, WINFUNCTYPE, Structure, Union,
)

NVAPI_PATH = r"C:\Windows\System32\nvapi64.dll"

# Throwaway identifiers (unique, cannot match a real game profile)
TEST_PROFILE_NAME = "DLSSUpdater_PerApp_Probe_DELETE_ME"
TEST_EXE_NAME = "dlssupdater_perapp_probe_DELETE_ME.exe"

# ---------------------------------------------------------------------------
# Function interface ids
#   - First block: already proven in dlss_updater/nvapi_drs.py (copied verbatim).
#   - Second block: NEW per-application ids, from NVIDIA/nvapi public headers.
#     The probe resolves each one; a NULL result flags a bad id immediately.
# ---------------------------------------------------------------------------
_FUNC = {
    # proven (global path)
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
    # NEW (per-application path) - to be verified live by this probe
    "NvAPI_DRS_CreateProfile": 0xCC176068,
    "NvAPI_DRS_DeleteProfile": 0x17093206,
    "NvAPI_DRS_FindProfileByName": 0x7E4A9A0B,
    "NvAPI_DRS_GetProfileInfo": 0x61CD6FD6,
    "NvAPI_DRS_CreateApplication": 0x4347A9DE,
    "NvAPI_DRS_DeleteApplicationEx": 0xC5EA85A1,
    "NvAPI_DRS_EnumApplications": 0x7FA2173A,
    "NvAPI_DRS_FindApplicationByName": 0xEEE566B2,
}

# SR feature setting ids (proven in nvapi_drs.py): (override_enable_id, preset_select_id)
_FEATURE_IDS = {
    "sr": (0x10E41E01, 0x10E41DF3),
    "rr": (0x10E41E02, 0x10E41DF7),
    "fg": (0x10E41E03, 0x10E41DF1),
}

_PRESET_OFF = 0x00000000
_PRESET_DEFAULT = 0x00FFFFFE
_PRESET_LATEST = 0x00FFFFFF
# SR preset K (transformer model) - a safe, well-known value for the write test
_SR_PRESET_K = 0x0B

_NVDRS_DWORD_TYPE = 0
_NVAPI_OK = 0
_NVAPI_SETTING_NOT_FOUND = -165
_NVAPI_PROFILE_NOT_FOUND = -163
_NVAPI_EXECUTABLE_NOT_FOUND = -161

NvAPI_UnicodeString = c_uint16 * 2048


def describe_preset(value):
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


def _set_ustr(arr, text: str) -> None:
    """Write a Python str into an NvAPI_UnicodeString (u16[2048]) buffer."""
    ctypes.memset(arr, 0, ctypes.sizeof(arr))
    for i, ch in enumerate(text[:2047]):
        arr[i] = ord(ch)


def _read_ustr(arr) -> str:
    out = []
    for code in arr:
        if code == 0:
            break
        out.append(chr(code))
    return "".join(out)


# ---------------------------------------------------------------------------
# ctypes structures
# ---------------------------------------------------------------------------
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


class NVDRS_PROFILE(Structure):
    _fields_ = [
        ("version", c_uint32),
        ("profileName", NvAPI_UnicodeString),
        ("gpuSupport", c_uint32),   # NVDRS_GPU_SUPPORT bitfield; bit0 = geforce
        ("isPredefined", c_uint32),
        ("numOfApps", c_uint32),
        ("numOfSettings", c_uint32),
    ]


class NVDRS_APPLICATION_V4(Structure):
    _fields_ = [
        ("version", c_uint32),
        ("isPredefined", c_uint32),
        ("appName", NvAPI_UnicodeString),
        ("userFriendlyName", NvAPI_UnicodeString),
        ("launcher", NvAPI_UnicodeString),
        ("fileInFolder", NvAPI_UnicodeString),
        ("flags", c_uint32),  # bitfields: isMetro:1, isCommandLine:1, reserved
        ("commandLine", NvAPI_UnicodeString),
    ]


SETTING_VER = ctypes.sizeof(NVDRS_SETTING) | (1 << 16)
PROFILE_VER = ctypes.sizeof(NVDRS_PROFILE) | (1 << 16)
APPLICATION_VER_V4 = ctypes.sizeof(NVDRS_APPLICATION_V4) | (4 << 16)


# ---------------------------------------------------------------------------
# Binding + tiny helpers
# ---------------------------------------------------------------------------
def build_api():
    dll = ctypes.WinDLL(NVAPI_PATH)
    query = dll.nvapi_QueryInterface
    query.restype = c_void_p
    query.argtypes = [c_uint32]

    api = {}
    print("Resolving NvAPI function ids...")
    bad = []
    for name, fid in _FUNC.items():
        addr = query(fid)
        flag = "OK " if addr else "NULL"
        if not addr:
            bad.append(name)
        marker = "       " if addr else "  <-- BAD"
        print(f"  [{flag}] 0x{fid:08X}  {name}{marker}")
        api[name] = addr
    if bad:
        raise RuntimeError(f"QueryInterface returned NULL for: {', '.join(bad)}")

    def fn(name, restype, *argtypes):
        return WINFUNCTYPE(restype, *argtypes)(api[name])

    api["_fn"] = fn
    return api


class Session:
    def __init__(self, api):
        self.api = api
        self.h = None
        self._init = False

    def __enter__(self):
        a, fn = self.api, self.api["_fn"]
        if fn("NvAPI_Initialize", c_int)() != _NVAPI_OK:
            raise RuntimeError("NvAPI_Initialize failed")
        self._init = True
        h = c_void_p()
        if fn("NvAPI_DRS_CreateSession", c_int, POINTER(c_void_p))(byref(h)) != _NVAPI_OK:
            raise RuntimeError("CreateSession failed")
        self.h = h
        if fn("NvAPI_DRS_LoadSettings", c_int, c_void_p)(h) != _NVAPI_OK:
            raise RuntimeError("LoadSettings failed")
        return self

    def save(self):
        r = self.api["_fn"]("NvAPI_DRS_SaveSettings", c_int, c_void_p)(self.h)
        if r != _NVAPI_OK:
            raise RuntimeError(f"SaveSettings failed ({r}) - elevated terminal required?")

    def __exit__(self, *exc):
        fn = self.api["_fn"]
        try:
            if self.h is not None:
                fn("NvAPI_DRS_DestroySession", c_int, c_void_p)(self.h)
        finally:
            if self._init:
                fn("NvAPI_Unload", c_int)()
        return False


def read_dword(api, sess, profile, setting_id):
    fn = api["_fn"]
    s = NVDRS_SETTING()
    s.version = SETTING_VER
    r = fn("NvAPI_DRS_GetSetting", c_int, c_void_p, c_void_p, c_uint32, POINTER(NVDRS_SETTING))(
        sess.h, profile, setting_id, byref(s)
    )
    if r != _NVAPI_OK:
        return None
    return int(s.currentValue.u32Value)


def set_dword(api, sess, profile, setting_id, value):
    fn = api["_fn"]
    s = NVDRS_SETTING()
    s.version = SETTING_VER
    s.settingId = setting_id
    s.settingType = _NVDRS_DWORD_TYPE
    s.currentValue.u32Value = value & 0xFFFFFFFF
    r = fn("NvAPI_DRS_SetSetting", c_int, c_void_p, c_void_p, POINTER(NVDRS_SETTING))(
        sess.h, profile, byref(s)
    )
    if r != _NVAPI_OK:
        raise RuntimeError(f"SetSetting(0x{setting_id:08X}) failed ({r})")


def read_all_presets(api, sess, profile):
    return {feat: read_dword(api, sess, profile, pid) for feat, (_o, pid) in _FEATURE_IDS.items()}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def resolve_test(api, exe_path: str) -> None:
    print("\n" + "=" * 70)
    print("RESOLVE TEST (read-only): FindApplicationByName")
    print("=" * 70)
    fn = api["_fn"]
    full = os.path.abspath(exe_path)
    print(f"  exe: {full}")
    if not os.path.exists(full):
        print("  WARNING: path does not exist on disk; driver lookup may still match by name.")

    with Session(api) as sess:
        name_buf = NvAPI_UnicodeString()
        _set_ustr(name_buf, full)
        profile = c_void_p()
        app = NVDRS_APPLICATION_V4()
        app.version = APPLICATION_VER_V4
        r = fn(
            "NvAPI_DRS_FindApplicationByName",
            c_int, c_void_p, NvAPI_UnicodeString, POINTER(c_void_p), POINTER(NVDRS_APPLICATION_V4),
        )(sess.h, name_buf, byref(profile), byref(app))

        if r == _NVAPI_EXECUTABLE_NOT_FOUND or r == _NVAPI_PROFILE_NOT_FOUND:
            print(f"  -> No driver profile maps this exe (status {r}).")
            print("     A per-game override would require creating a profile + adding this exe.")
            return
        if r != _NVAPI_OK:
            print(f"  -> FindApplicationByName returned status {r}")
            return

        # Inspect the resolved profile
        pinfo = NVDRS_PROFILE()
        pinfo.version = PROFILE_VER
        fn("NvAPI_DRS_GetProfileInfo", c_int, c_void_p, c_void_p, POINTER(NVDRS_PROFILE))(
            sess.h, profile, byref(pinfo)
        )
        print(f"  -> Resolved profile : '{_read_ustr(pinfo.profileName)}'")
        print(f"     isPredefined     : {bool(pinfo.isPredefined)}")
        print(f"     numOfApps        : {pinfo.numOfApps}   numOfSettings: {pinfo.numOfSettings}")
        print(f"     matched appName  : '{_read_ustr(app.appName)}'")
        presets = read_all_presets(api, sess, profile)
        print("     current per-game DLSS preset overrides on that profile:")
        for feat in ("sr", "rr", "fg"):
            print(f"       {feat.upper()}: {describe_preset(presets[feat])}")
    print("  RESOLVE TEST OK")


def roundtrip_test(api) -> bool:
    print("\n" + "=" * 70)
    print("ROUND-TRIP TEST (reversible): create -> set SR=K -> read back -> delete")
    print("=" * 70)
    fn = api["_fn"]
    sr_override_id, sr_preset_id = _FEATURE_IDS["sr"]

    # --- write phase ---
    with Session(api) as sess:
        # Clean any leftover from a previous aborted run
        existing = c_void_p()
        nb = NvAPI_UnicodeString()
        _set_ustr(nb, TEST_PROFILE_NAME)
        if fn("NvAPI_DRS_FindProfileByName", c_int, c_void_p, NvAPI_UnicodeString, POINTER(c_void_p))(
            sess.h, nb, byref(existing)
        ) == _NVAPI_OK:
            print("  (found leftover probe profile - deleting first)")
            fn("NvAPI_DRS_DeleteProfile", c_int, c_void_p, c_void_p)(sess.h, existing)
            sess.save()

        prof = NVDRS_PROFILE()
        prof.version = PROFILE_VER
        _set_ustr(prof.profileName, TEST_PROFILE_NAME)
        prof.gpuSupport = 1  # geforce bit
        hprof = c_void_p()
        r = fn("NvAPI_DRS_CreateProfile", c_int, c_void_p, POINTER(NVDRS_PROFILE), POINTER(c_void_p))(
            sess.h, byref(prof), byref(hprof)
        )
        if r != _NVAPI_OK:
            print(f"  FAIL: CreateProfile -> {r}")
            return False
        print(f"  CreateProfile OK ('{TEST_PROFILE_NAME}')")

        app = NVDRS_APPLICATION_V4()
        app.version = APPLICATION_VER_V4
        _set_ustr(app.appName, TEST_EXE_NAME)
        _set_ustr(app.userFriendlyName, "DLSS Updater per-app probe")
        r = fn("NvAPI_DRS_CreateApplication", c_int, c_void_p, c_void_p, POINTER(NVDRS_APPLICATION_V4))(
            sess.h, hprof, byref(app)
        )
        if r != _NVAPI_OK:
            print(f"  FAIL: CreateApplication -> {r}")
            fn("NvAPI_DRS_DeleteProfile", c_int, c_void_p, c_void_p)(sess.h, hprof)
            sess.save()
            return False
        print(f"  CreateApplication OK ('{TEST_EXE_NAME}')")

        set_dword(api, sess, hprof, sr_override_id, 1)
        set_dword(api, sess, hprof, sr_preset_id, _SR_PRESET_K)
        sess.save()
        print(f"  SetSetting SR override=1, preset=0x{_SR_PRESET_K:02X} (Preset K) + Save OK")

    # --- read-back phase (fresh session) ---
    with Session(api) as sess:
        hprof = c_void_p()
        nb = NvAPI_UnicodeString()
        _set_ustr(nb, TEST_PROFILE_NAME)
        if fn("NvAPI_DRS_FindProfileByName", c_int, c_void_p, NvAPI_UnicodeString, POINTER(c_void_p))(
            sess.h, nb, byref(hprof)
        ) != _NVAPI_OK:
            print("  FAIL: profile not found after save")
            return False
        val = read_dword(api, sess, hprof, sr_preset_id)
        ok = (val == _SR_PRESET_K)
        print(f"  Read back SR preset: {describe_preset(val)}  ({'MATCH' if ok else 'MISMATCH'})")

        # FindApplicationByName should also resolve our fake exe to this profile
        ab = NvAPI_UnicodeString()
        _set_ustr(ab, TEST_EXE_NAME)
        rp = c_void_p()
        rapp = NVDRS_APPLICATION_V4()
        rapp.version = APPLICATION_VER_V4
        fr = fn(
            "NvAPI_DRS_FindApplicationByName",
            c_int, c_void_p, NvAPI_UnicodeString, POINTER(c_void_p), POINTER(NVDRS_APPLICATION_V4),
        )(sess.h, ab, byref(rp), byref(rapp))
        print(f"  FindApplicationByName('{TEST_EXE_NAME}') -> status {fr} "
              f"({'resolved' if fr == _NVAPI_OK else 'not resolved'})")

    # --- cleanup phase ---
    with Session(api) as sess:
        hprof = c_void_p()
        nb = NvAPI_UnicodeString()
        _set_ustr(nb, TEST_PROFILE_NAME)
        if fn("NvAPI_DRS_FindProfileByName", c_int, c_void_p, NvAPI_UnicodeString, POINTER(c_void_p))(
            sess.h, nb, byref(hprof)
        ) == _NVAPI_OK:
            fn("NvAPI_DRS_DeleteProfile", c_int, c_void_p, c_void_p)(sess.h, hprof)
            sess.save()
            print("  DeleteProfile + Save OK")
        # confirm gone
        gone = fn("NvAPI_DRS_FindProfileByName", c_int, c_void_p, NvAPI_UnicodeString, POINTER(c_void_p))(
            sess.h, nb, byref(c_void_p())
        )
        print(f"  Confirm deleted: FindProfileByName -> status {gone} "
              f"({'gone' if gone != _NVAPI_OK else 'STILL PRESENT'})")

    print("  ROUND-TRIP TEST OK" if ok else "  ROUND-TRIP TEST FAILED (read-back mismatch)")
    return ok


def main():
    ap = argparse.ArgumentParser(description="NvAPI per-application DRS probe (reversible).")
    ap.add_argument("--exe", help="Fully-qualified path to a real game exe for the read-only resolve test.")
    ap.add_argument("--skip-roundtrip", action="store_true", help="Skip the create/delete round-trip test.")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("Windows only.")
        return 1
    if not os.path.exists(NVAPI_PATH):
        print(f"nvapi64.dll not found at {NVAPI_PATH}")
        return 1

    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        is_admin = False
    print(f"Elevated (admin): {is_admin}  (Save may fail without elevation)\n")

    api = build_api()

    if args.exe:
        resolve_test(api, args.exe)

    rc = 0
    if not args.skip_roundtrip:
        if not roundtrip_test(api):
            rc = 2

    print("\nDone.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
