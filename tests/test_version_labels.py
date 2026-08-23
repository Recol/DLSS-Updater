"""Tests for dlss_updater.version_labels (DLL version -> marketing name)."""

import pytest

from dlss_updater.version_labels import (
    KIND_DLSS_RR,
    KIND_DLSS_SR,
    KIND_FSR,
    KIND_XESS,
    is_ray_reconstruction,
    kind_of,
    marketing_version,
    preset_suffix,
)


@pytest.mark.parametrize(
    "dll_filename, raw_version, expected",
    [
        ("nvngx_dlss.dll", "310.2.1.0", "DLSS 4.5"),
        ("sl.dlss.dll", "3.8.10.0", "DLSS 3"),
        ("nvngx_dlss.dll", "2.5.1.0", "DLSS 2"),
        ("nvngx_dlssd.dll", "310.2.1.0", "RR 4.5"),
        ("sl.dlss_d.dll", "3.5.0.0", "RR 3.5"),
        ("nvngx_dlssd.dll", "2.5.1.0", None),  # RR didn't exist pre-3.5
        ("libxess.dll", "2.0.2.68", "XeSS 2.0"),
        ("libxess_dx11.dll", "1.3.0.5", "XeSS 1.3"),
        ("amd_fidelityfx_upscaler_dx12.dll", "4.0.2.44888", "FSR 4.0"),
        ("amd_fidelityfx_dx12.dll", "1.0.1.41314", None),  # loader/SDK version, not FSR's
        ("nvngx_dlssg.dll", "310.2.1.0", None),  # frame gen: not tracked here
        ("unknown.dll", "1.0.0.0", None),
        ("nvngx_dlss.dll", "", None),
        ("nvngx_dlss.dll", "not-a-version", None),
    ],
)
def test_marketing_version(dll_filename, raw_version, expected):
    assert marketing_version(dll_filename, raw_version) == expected


def test_is_ray_reconstruction():
    assert is_ray_reconstruction("nvngx_dlssd.dll") is True
    assert is_ray_reconstruction("sl.dlss_d.dll") is True
    assert is_ray_reconstruction("nvngx_dlss.dll") is False
    assert is_ray_reconstruction("") is False


@pytest.mark.parametrize(
    "preset_key, expected",
    [
        (None, ""),
        ("", ""),
        ("default", ""),
        ("latest", " (Latest)"),
        ("preset_k", " (Preset K)"),
        ("bogus", ""),
    ],
)
def test_preset_suffix(preset_key, expected):
    assert preset_suffix(preset_key) == expected


@pytest.mark.parametrize(
    "dll_filename, expected_kind",
    [
        ("nvngx_dlss.dll", KIND_DLSS_SR),
        ("sl.dlss.dll", KIND_DLSS_SR),  # same bucket as nvngx_dlss.dll (regression:
        # a game shipping both must contribute ONE label, not two conflicting ones)
        ("nvngx_dlssd.dll", KIND_DLSS_RR),
        ("sl.dlss_d.dll", KIND_DLSS_RR),
        ("libxess.dll", KIND_XESS),
        ("libxess_dx11.dll", KIND_XESS),
        ("amd_fidelityfx_upscaler_dx12.dll", KIND_FSR),
        ("amd_fidelityfx_dx12.dll", None),
        ("nvngx_dlssg.dll", None),
        ("", None),
    ],
)
def test_kind_of(dll_filename, expected_kind):
    assert kind_of(dll_filename) == expected_kind
