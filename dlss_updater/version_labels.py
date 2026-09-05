"""Marketing-name labels for tracked upscaler / Ray Reconstruction DLLs.

Labels and technology bucketing come from the DLL manifest (manifest.json in
the DLL repo), not this client. A client-side version->name table goes stale
the moment a vendor renumbers (NVIDIA's DLSS marketing generation doesn't
track the raw PE version linearly) or the DLL repo adds a DLL the table
doesn't know about — and since the mismatched DLL is one this same app just
auto-installed, a wrong label is worse than a missing one. So the manifest
carries, per DLL, an optional "feature" key (which technology bucket it
belongs to — lets DLLs like nvngx_dlss.dll and the Streamline-wrapped
sl.dlss.dll that describe the same tech get grouped without a client-side
filename list) and an optional "labels" list of bounded
[min_version, max_version, name] ranges, e.g.:

    "nvngx_dlss.dll": {
        "version": "310.7.128.0",
        "feature": "dlss_sr",
        "labels": [["310.4", "311", "DLSS 4.5"], ["310", "310.4", "DLSS 4"]]
    }

No manifest, no entry, no "labels", or a version outside every range means
marketing_version() returns None — callers show the raw version instead of
guessing. Ranges are display-only lookups; they never feed into the actual
update version comparison (that's parse_version() in updater.py, against the
manifest's own "version" field).
"""

from .updater import parse_version

# The two feature keys the client special-cases for the per-game DLSS
# preset-override suffix (see game_card._tech_version_label). Any other
# feature key is still bucketed and displayed — it just never gets a
# "(Preset K)" suffix, since presets only apply to DLSS SR/RR.
FEATURE_DLSS_SR = "dlss_sr"
FEATURE_DLSS_RR = "dlss_rr"

# DLSS 5 Neural Rendering. A bucket of its own rather than part of dlss_sr: it
# runs alongside Super Resolution instead of replacing it, so a game can show
# both chips. Presets don't apply to it.
FEATURE_DLSS_NR = "dlss_nr"

# Canonical display order for the well-known buckets (matches the
# DLSS/FSR/XeSS/RR order the chip line has always used). A feature key the
# manifest introduces after this client shipped isn't in here — it still
# displays, just appended after these, alphabetically, in sort_kinds().
KNOWN_FEATURE_ORDER = (
    FEATURE_DLSS_SR,
    "fsr_sr",
    "xess_sr",
    FEATURE_DLSS_RR,
    FEATURE_DLSS_NR,
)


def _manifest_entry(dll_filename: str, manifest: dict | None) -> dict | None:
    if not dll_filename or not isinstance(manifest, dict):
        return None
    entry = manifest.get(dll_filename.lower())
    return entry if isinstance(entry, dict) else None


def kind_of(dll_filename: str, manifest: dict | None) -> str | None:
    """Technology bucket for a tracked DLL, from the manifest's "feature" key.

    None (untracked, no chip entry) if the manifest is unavailable or has no
    "feature" for this DLL.
    """
    entry = _manifest_entry(dll_filename, manifest)
    feature = entry.get("feature") if entry else None
    return feature if isinstance(feature, str) and feature else None


def marketing_version(dll_filename: str, raw_version: str, manifest: dict | None) -> str | None:
    """Marketing label for raw_version within the manifest's bounded ranges,
    or None if there's no entry/labels/match — callers should fall back to
    showing raw_version rather than guess."""
    entry = _manifest_entry(dll_filename, manifest)
    labels = entry.get("labels") if entry else None
    if not isinstance(labels, list) or not raw_version:
        return None

    v = parse_version(raw_version)
    for bucket in labels:
        if not isinstance(bucket, (list, tuple)) or len(bucket) != 3:
            continue
        lo, hi, label = bucket
        if not isinstance(label, str) or not label:
            continue
        if parse_version(lo) <= v < parse_version(hi):
            return label
    return None


def sort_kinds(kinds) -> list[str]:
    """Order feature-bucket keys: known techs in their fixed display order,
    then any unrecognised ones (new to the manifest) alphabetically after."""
    known = [k for k in KNOWN_FEATURE_ORDER if k in kinds]
    unknown = sorted(k for k in kinds if k not in KNOWN_FEATURE_ORDER)
    return known + unknown


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
