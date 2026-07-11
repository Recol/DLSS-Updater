"""
Windows AppUserModelID (AUMID) taskbar pinning fix.

Flet spawns `flet.exe` as a subprocess that owns the GUI window. Without an
explicit AUMID + RelaunchCommand set on that window, Windows pins `flet.exe`
(which when launched bare shows a white screen). We set four PKEY values on
flet.exe's window via SHGetPropertyStoreForWindow so the pinned shortcut
points back to DLSS_Updater.exe and the taskbar entry groups correctly.
"""

import sys

if sys.platform != "win32":
    raise ImportError("Windows only")

import anyio
import ctypes
import logging
import os
from ctypes import wintypes

import psutil

logger = logging.getLogger("DLSSUpdater")

AUMID = "io.github.recol.DLSSUpdater"
DISPLAY_NAME = "DLSS Updater"

_VT_LPWSTR = 31


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [
        ("fmtid", _GUID),
        ("pid", ctypes.c_uint32),
    ]


class _PROPVARIANT_VALUE(ctypes.Union):
    _fields_ = [
        ("pwszVal", ctypes.c_wchar_p),
        ("_pad", ctypes.c_uint64),
    ]


class _PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("_v",)
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("_v", _PROPVARIANT_VALUE),
    ]


_IID_IPropertyStore = _GUID(
    0x886D8EEB, 0x8CF2, 0x4446,
    (ctypes.c_ubyte * 8)(0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99),
)

_FMTID_AppUserModel = _GUID(
    0x9F4C2855, 0x9F79, 0x4B39,
    (ctypes.c_ubyte * 8)(0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3),
)


def _make_pkey(pid: int) -> _PROPERTYKEY:
    pkey = _PROPERTYKEY()
    pkey.fmtid = _FMTID_AppUserModel
    pkey.pid = pid
    return pkey


PKEY_AppUserModel_ID = _make_pkey(5)
PKEY_AppUserModel_RelaunchCommand = _make_pkey(2)
PKEY_AppUserModel_RelaunchDisplayNameResource = _make_pkey(4)
PKEY_AppUserModel_RelaunchIconResource = _make_pkey(3)


_shell32 = ctypes.windll.shell32
_user32 = ctypes.windll.user32
_ole32 = ctypes.windll.ole32

_SHGetPropertyStoreForWindow = _shell32.SHGetPropertyStoreForWindow
_SHGetPropertyStoreForWindow.argtypes = [
    wintypes.HWND, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)
]
_SHGetPropertyStoreForWindow.restype = ctypes.HRESULT

_GetWindowThreadProcessId = _user32.GetWindowThreadProcessId
_GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_GetWindowThreadProcessId.restype = wintypes.DWORD

_IsWindowVisible = _user32.IsWindowVisible
_IsWindowVisible.argtypes = [wintypes.HWND]
_IsWindowVisible.restype = wintypes.BOOL

_GetWindowTextLengthW = _user32.GetWindowTextLengthW
_GetWindowTextLengthW.argtypes = [wintypes.HWND]
_GetWindowTextLengthW.restype = ctypes.c_int

_GetWindowTextW = _user32.GetWindowTextW
_GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_GetWindowTextW.restype = ctypes.c_int

_GetAncestor = _user32.GetAncestor
_GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
_GetAncestor.restype = wintypes.HWND
_GA_ROOT = 2

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_EnumWindows = _user32.EnumWindows
_EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_EnumWindows.restype = wintypes.BOOL


def _find_flet_pids(parent_pid: int) -> list[int]:
    pids: list[int] = []
    try:
        parent = psutil.Process(parent_pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return pids

    try:
        descendants = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return pids

    for child in descendants:
        try:
            name = child.name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "flet" in name:
            pids.append(child.pid)
    return pids


def _find_flet_hwnd(target_pids: list[int]) -> int | None:
    target_set = set(target_pids)
    found: list[int] = []

    def _callback(hwnd, _lparam):
        if not _IsWindowVisible(hwnd):
            return True
        # Only consider top-level windows (root of the window tree)
        if _GetAncestor(hwnd, _GA_ROOT) != hwnd:
            return True

        pid = wintypes.DWORD(0)
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in target_set:
            return True

        length = _GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        _GetWindowTextW(hwnd, buf, length + 1)
        if not buf.value.strip():
            return True

        found.append(hwnd)
        return True

    _EnumWindows(_WNDENUMPROC(_callback), 0)
    return found[0] if found else None


def _set_window_property(hwnd: int, pkey: _PROPERTYKEY, value: str) -> bool:
    pps = ctypes.c_void_p()
    hr = _SHGetPropertyStoreForWindow(
        hwnd, ctypes.byref(_IID_IPropertyStore), ctypes.byref(pps)
    )
    if hr != 0 or not pps.value:
        logger.debug(f"SHGetPropertyStoreForWindow failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        return False

    try:
        vtable_ptr = ctypes.cast(pps.value, ctypes.POINTER(ctypes.c_void_p))[0]
        vtable = ctypes.cast(vtable_ptr, ctypes.POINTER(ctypes.c_void_p))

        SetValue = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p,
            ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)
        )(vtable[6])
        Commit = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p
        )(vtable[7])
        Release = ctypes.WINFUNCTYPE(
            ctypes.c_ulong, ctypes.c_void_p
        )(vtable[2])

        # Keep buffer alive for the duration of the COM call; set pointer via
        # the _pad (uint64) union member to guarantee we store the raw address.
        buf = ctypes.create_unicode_buffer(value)
        pv = _PROPVARIANT()
        pv.vt = _VT_LPWSTR
        pv._pad = ctypes.addressof(buf)

        hr = SetValue(pps, ctypes.byref(pkey), ctypes.byref(pv))
        if hr != 0:
            logger.debug(f"IPropertyStore::SetValue failed (pid={pkey.pid}): HRESULT=0x{hr & 0xFFFFFFFF:08X}")
            Release(pps)
            return False

        hr = Commit(pps)
        Release(pps)
        if hr != 0:
            logger.debug(f"IPropertyStore::Commit failed (pid={pkey.pid}): HRESULT=0x{hr & 0xFFFFFFFF:08X}")
            return False
        return True
    except Exception as ex:
        logger.debug(f"_set_window_property exception: {ex}")
        try:
            Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
                ctypes.cast(
                    ctypes.cast(pps.value, ctypes.POINTER(ctypes.c_void_p))[0],
                    ctypes.POINTER(ctypes.c_void_p)
                )[2]
            )
            Release(pps)
        except Exception:
            pass
        return False


async def apply_taskbar_fix(exe_path: str | None = None) -> bool:
    if exe_path is None:
        exe_path = sys.executable

    exe_path = os.path.abspath(exe_path)
    relaunch_command = f'"{exe_path}"'
    relaunch_icon = f"{exe_path},0"

    parent_pid = os.getpid()

    for attempt in range(5):
        if attempt > 0:
            await anyio.sleep(0.5)

        flet_pids = _find_flet_pids(parent_pid)
        if not flet_pids:
            logger.debug(f"AUMID fix attempt {attempt + 1}: no flet child process found yet")
            continue

        hwnd = _find_flet_hwnd(flet_pids)
        if not hwnd:
            logger.debug(f"AUMID fix attempt {attempt + 1}: no visible flet window found yet (pids={flet_pids})")
            continue

        ok_id = _set_window_property(hwnd, PKEY_AppUserModel_ID, AUMID)
        ok_cmd = _set_window_property(hwnd, PKEY_AppUserModel_RelaunchCommand, relaunch_command)
        ok_name = _set_window_property(hwnd, PKEY_AppUserModel_RelaunchDisplayNameResource, DISPLAY_NAME)
        ok_icon = _set_window_property(hwnd, PKEY_AppUserModel_RelaunchIconResource, relaunch_icon)

        if ok_id and ok_cmd and ok_name and ok_icon:
            logger.info(
                f"Taskbar AUMID fix applied (hwnd=0x{hwnd:X}, pid={flet_pids[0]}, "
                f"relaunch={relaunch_command})"
            )
            return True

        logger.warning(
            f"AUMID fix attempt {attempt + 1}: partial failure "
            f"(id={ok_id}, cmd={ok_cmd}, name={ok_name}, icon={ok_icon})"
        )

    logger.warning("Failed to apply taskbar AUMID fix after 5 attempts; pinning may pin flet.exe instead")
    return False
