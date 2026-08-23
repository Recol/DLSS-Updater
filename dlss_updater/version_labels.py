"""Marketing-name labels for tracked upscaler / Ray Reconstruction DLL versions.

Raw DLL file versions don't line up 1:1 with vendor marketing names (NVIDIA
especially: the transformer model's file version jumped nvngx_dlss.dll into
the 310.x.x range, currently marketed "DLSS 4.5"). The mappings below only
cover generations we're confident about; anything outside a known range
returns None so callers can fall back to showing nothing (or the raw
version) instead of a guess.

ponytail: coarse major-version buckets, not every SDK point release
(3.5/3.7/3.8 all collapse to "DLSS 3") — when NVIDIA ships a new marketing
generation, update the top _DLSS_*_THRESHOLDS entry (name AND, if the raw
file-version major changes again, its threshold number) to match README.md's
"Bundled DLL Versions" table, which the maintainer already keeps current.
"""

# (minimum raw major version, label) pairs, checked highest-first. Top entry
# mirrors README.md's Bundled DLL Versions table (DLSS Super Resolution /
# DLSS FG-RR rows) — keep both in sync on the same release that bumps
# config.LATEST_DLL_VERSIONS's nvngx_dlss.dll / nvngx_dlssd.dll entries.
_DLSS_SR_THRESHOLDS = [(310, "DLSS 4.5"), (3, "DLSS 3"), (2, "DLSS 2"), (1, "DLSS 1")]
_DLSS_RR_THRESHOLDS = [(310, "RR 4.5"), (3, "RR 3.5")]  # RR didn't exist before DLSS 3.5

# DLL filenames whose raw major.minor IS the vendor's marketing version
# (Intel/AMD, unlike NVIDIA, don't renumber for a new generation).
_XESS_FILES = {"libxess.dll", "libxess_dx11.dll"}
# amd_fidelityfx_dx12.dll / _vk.dll are a frozen SDK 1.1.4 "loader" version,
# not the FSR feature version, so they're deliberately excluded here.
_FSR_FILES = {"amd_fidelityfx_upscaler_dx12.dll"}

DLSS_SR_FILES = {"nvngx_dlss.dll", "sl.dlss.dll"}
DLSS_RR_FILES = {"nvngx_dlssd.dll", "sl.dlss_d.dll"}

# A game can ship more than one DLL for the same technology (e.g. both the
# native nvngx_dlss.dll and the Streamline-wrapped sl.dlss.dll for DLSS SR),
# each independently versioned. kind_of() lets callers bucket by technology
# and pick ONE representative DLL per bucket (the highest version) instead of
# showing two possibly-contradictory generation labels for what a user sees
# as a single "DLSS version" on the card.
KIND_DLSS_SR = "dlss_sr"
KIND_DLSS_RR = "dlss_rr"
KIND_XESS = "xess"
KIND_FSR = "fsr"


def kind_of(dll_filename: str) -> str | None:
    """Technology bucket for a tracked DLL filename, or None if untracked."""
    if not dll_filename:
        return None
    name = dll_filename.lower()
    if name in DLSS_SR_FILES:
        return KIND_DLSS_SR
    if name in DLSS_RR_FILES:
        return KIND_DLSS_RR
    if name in _XESS_FILES:
        return KIND_XESS
    if name in _FSR_FILES:
        return KIND_FSR
    return None


def _major(raw_version: str) -> int | None:
    try:
        return int(raw_version.replace(",", ".").split(".")[0])
    except (ValueError, IndexError, AttributeError):
        return None


def _major_minor(raw_version: str) -> str | None:
    parts = raw_version.replace(",", ".").split(".")
    if len(parts) < 2:
        return None
    try:
        return f"{int(parts[0])}.{int(parts[1])}"
    except ValueError:
        return None


def marketing_version(dll_filename: str, raw_version: str) -> str | None:
    """Best-effort marketing label for a tracked DLL, or None if not confident."""
    if not dll_filename or not raw_version:
        return None
    name = dll_filename.lower()

    if name in DLSS_SR_FILES:
        major = _major(raw_version)
        if major is None:
            return None
        for threshold, label in _DLSS_SR_THRESHOLDS:
            if major >= threshold:
                return label
        return None

    if name in DLSS_RR_FILES:
        major = _major(raw_version)
        if major is None:
            return None
        for threshold, label in _DLSS_RR_THRESHOLDS:
            if major >= threshold:
                return label
        return None

    if name in _XESS_FILES:
        mm = _major_minor(raw_version)
        return f"XeSS {mm}" if mm else None

    if name in _FSR_FILES:
        mm = _major_minor(raw_version)
        return f"FSR {mm}" if mm else None

    return None


def is_ray_reconstruction(dll_filename: str) -> bool:
    return bool(dll_filename) and dll_filename.lower() in DLSS_RR_FILES


# Short chip labels for a per-game preset override (models.WindowsDLSSPreset /
# WindowsDLSSModelPreset keys). "default" deliberately has no entry — an
# unmodified game shows no suffix.
_PRESET_SHORT = {
    "latest": "Latest",
    "preset_j": "Preset J",
    "preset_k": "Preset K",
    "preset_l": "Preset L",
    "preset_m": "Preset M",
}


def preset_suffix(preset_key: str | None) -> str:
    """" (Preset K)" style suffix for a saved per-game override, or "" when
    unset/default. Windows-only feature (dlss_updater.nvapi_drs) — presets are
    never populated on other platforms."""
    if not preset_key:
        return ""
    short = _PRESET_SHORT.get(preset_key)
    return f" ({short})" if short else ""
