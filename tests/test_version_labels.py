"""Tests for dlss_updater.version_labels (manifest-driven DLL label lookup).

Labels/bucketing come from a manifest dict shaped like the DLL repo's
manifest.json (see version_labels.py's module docstring) — these tests build
that manifest inline rather than hitting the network.
"""

import pytest

from dlss_updater.version_labels import (
    FEATURE_DLSS_RR,
    FEATURE_DLSS_SR,
    kind_of,
    marketing_version,
    preset_suffix,
    sort_kinds,
)

# Mirrors the shape (and the exact bug scenarios) described for the real
# manifest: a bounded DLSS SR/RR generation table, sl.* DLLs sharing the same
# "feature" as their nvngx_* counterparts but with NO labels (raw-version
# fallback), and libxess.dll present but untagged (not yet migrated).
MANIFEST = {
    "nvngx_dlss.dll": {
        "version": "310.7.128.0",
        "feature": "dlss_sr",
        "labels": [
            ["310.4", "311", "DLSS 4.5"],
            ["310", "310.4", "DLSS 4"],
            ["3", "4", "DLSS 3"],
            ["2", "3", "DLSS 2"],
        ],
    },
    "nvngx_dlssd.dll": {
        "version": "310.7.128.0",
        "feature": "dlss_rr",
        "labels": [
            ["310", "311", "RR 4.5"],
            ["3.5", "4", "RR 3.5"],
        ],
    },
    "sl.dlss.dll": {"version": "2.12.128.0", "feature": "dlss_sr"},
    "sl.dlss_d.dll": {"version": "2.12.128.0", "feature": "dlss_rr"},
    "libxess.dll": {"version": "2.0.2.68"},  # no "feature" yet: untracked
    "malformed.dll": {"version": "1.0.0.0", "feature": "fsr_sr", "labels": "not-a-list"},
}


@pytest.mark.parametrize(
    "dll_filename, raw_version, expected",
    [
        # Bounded top range: covers the current shipped version.
        ("nvngx_dlss.dll", "310.7.128.0", "DLSS 4.5"),
        ("nvngx_dlss.dll", "310.2.1.0", "DLSS 4"),
        ("nvngx_dlss.dll", "3.8.10.0", "DLSS 3"),
        # A version above every bucket's upper bound (the vendor renumbered
        # again since this manifest snapshot) falls through to None, NOT the
        # top bucket's label — this is the whole point of bounding ranges.
        ("nvngx_dlss.dll", "311.0.0.0", None),
        # A version BELOW every bucket's lower bound — an ancient DLSS 1.x
        # DLL a game installed years before this manifest existed — falls
        # through to None the same way, so the caller shows the raw old
        # version instead of a wrong/missing label.
        ("nvngx_dlss.dll", "1.0.13.0", None),
        # Unparseable version string must not raise (parse_version() catches
        # internally and returns a low sentinel) — still resolves to None.
        ("nvngx_dlss.dll", "garbage", None),
        ("nvngx_dlssd.dll", "310.7.128.0", "RR 4.5"),
        ("nvngx_dlssd.dll", "3.5.0.0", "RR 3.5"),
        # Streamline DLLs: same "feature" as their nvngx counterpart, no
        # "labels" — always raw-version fallback (caller supplies the raw
        # string; this function itself has nothing to map).
        ("sl.dlss.dll", "2.12.128.0", None),
        ("sl.dlss_d.dll", "2.12.128.0", None),
        # Not in the manifest at all.
        ("unknown.dll", "1.0.0.0", None),
        ("nvngx_dlss.dll", "", None),
        # Malformed "labels" (wrong type) must not raise.
        ("malformed.dll", "1.0.0.0", None),
    ],
)
def test_marketing_version(dll_filename, raw_version, expected):
    assert marketing_version(dll_filename, raw_version, MANIFEST) == expected


def test_marketing_version_no_manifest():
    assert marketing_version("nvngx_dlss.dll", "310.7.128.0", None) is None


@pytest.mark.parametrize(
    "dll_filename, expected_kind",
    [
        ("nvngx_dlss.dll", "dlss_sr"),
        ("sl.dlss.dll", "dlss_sr"),  # same bucket as nvngx_dlss.dll
        ("nvngx_dlssd.dll", "dlss_rr"),
        ("sl.dlss_d.dll", "dlss_rr"),
        ("libxess.dll", None),  # present in manifest, but no "feature" yet
        ("unknown.dll", None),
    ],
)
def test_kind_of(dll_filename, expected_kind):
    assert kind_of(dll_filename, MANIFEST) == expected_kind


def test_kind_of_no_manifest():
    assert kind_of("nvngx_dlss.dll", None) is None


def test_sort_kinds_known_order_then_unknown_alphabetical():
    kinds = {FEATURE_DLSS_RR, "xess_sr", FEATURE_DLSS_SR, "zzz_new_tech", "aaa_new_tech"}
    assert sort_kinds(kinds) == [
        FEATURE_DLSS_SR,
        "xess_sr",
        FEATURE_DLSS_RR,
        "aaa_new_tech",
        "zzz_new_tech",
    ]


def test_preset_suffix():
    assert preset_suffix("preset_k") == " (Preset K)"
    assert preset_suffix(None) == ""
    assert preset_suffix("default") == ""
