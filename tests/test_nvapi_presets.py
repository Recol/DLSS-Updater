"""
Tests for the NvAPI DLSS preset value maps (pure lookups - no driver calls).

The driver enum values are verified against NVIDIA's public NvApiDriverSettings.h
and against the driver's own NvAPI_DRS_EnumAvailableSettingValues enumeration for
"Override DLSS-RR preset" (0x10E41DF7), which publishes presets A-O plus Latest.
"""

from dlss_updater.models import WindowsDLSSModelPreset
from dlss_updater.nvapi_drs import describe_preset_value, preset_key_to_value


class TestRRPresetValues:
    def test_rr_preset_d_and_f_driver_values(self):
        # NGX_DLSS_RR_OVERRIDE_RENDER_PRESET_SELECTION_RENDER_PRESET_D / _F
        assert preset_key_to_value("rr", "preset_d") == 0x04
        assert preset_key_to_value("rr", "preset_f") == 0x06

    def test_rr_default_and_latest(self):
        assert preset_key_to_value("rr", "default") == 0x00000000
        assert preset_key_to_value("rr", "latest") == 0x00FFFFFF

    def test_unknown_rr_preset_is_none(self):
        assert preset_key_to_value("rr", "preset_zz") is None

    def test_every_enum_member_maps_to_a_driver_value(self):
        # Both RR dropdowns are built by iterating WindowsDLSSModelPreset, so an
        # unmapped member would render an option that cannot be applied.
        for preset in WindowsDLSSModelPreset:
            assert preset_key_to_value("rr", preset.value) is not None, preset.value

    def test_every_enum_member_has_a_display_name(self):
        for preset in WindowsDLSSModelPreset:
            assert preset.display_name != preset.value, preset.value


class TestDescribePresetValue:
    def test_letter_values(self):
        assert describe_preset_value(0x04) == "Preset D"
        assert describe_preset_value(0x06) == "Preset F"

    def test_special_values(self):
        assert describe_preset_value(None) == "Not set (no override)"
        assert describe_preset_value(0x00000000) == "Off (no override)"
        assert describe_preset_value(0x00FFFFFF) == "Latest (newest model)"
        # FG-only sentinel: NvApiDriverSettings.h names it RENDER_PRESET_Default
        assert describe_preset_value(0x00FFFFFE) == "Default model"
