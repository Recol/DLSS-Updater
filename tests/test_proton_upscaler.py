"""
Unit tests for the Proton upscaler launch-option stack:
- linux_dlss_utils.generate_steam_launch_options (incl. capability gating and
  backward compatibility with pre-4.3 configs)
- proton_compat compat-tool classification and CompatToolMapping extraction
- gpu_detection PCI id classification (RDNA3/RDNA4)

All tests are pure (no sysfs / Steam install required) so they run on any OS.
"""

import pytest

from dlss_updater.gpu_detection import classify_pci_gpu, pick_primary_gpu
from dlss_updater.linux_dlss_utils import (
    generate_steam_launch_options,
    get_all_presets,
    get_rr_presets,
)
from dlss_updater.models import DLSSPreset, LinuxDLSSConfig
from dlss_updater.proton_compat import (
    ALL_UPGRADE_CAPS,
    CAP_FSR4_UPGRADE,
    classify_compat_tool,
    extract_compat_tool_mapping,
    resolve_tool_for_app,
)

# =============================================================================
# generate_steam_launch_options
# =============================================================================

class TestLaunchOptionGeneration:
    def test_default_config_is_bare_command(self):
        assert generate_steam_launch_options(LinuxDLSSConfig()) == "%command%"

    def test_legacy_fields_output_unchanged(self):
        """Configs saved by older versions must produce identical output."""
        config = LinuxDLSSConfig(
            selected_preset="preset_k",
            overlay_enabled=True,
            wayland_enabled=True,
            hdr_enabled=True,
        )
        assert generate_steam_launch_options(config) == (
            "DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION=render_preset_k "
            "DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS=DLSSIndicator=1024 "
            "PROTON_ENABLE_WAYLAND=1 "
            "PROTON_ENABLE_HDR=1 ENABLE_HDR_WSI=1 %command%"
        )

    def test_rr_preset_and_fg_override(self):
        config = LinuxDLSSConfig(rr_preset="latest", fg_override=True)
        command = generate_steam_launch_options(config)
        assert (
            "DXVK_NVAPI_DRS_NGX_DLSS_RR_OVERRIDE_RENDER_PRESET_SELECTION="
            "render_preset_latest" in command
        )
        assert "DXVK_NVAPI_DRS_NGX_DLSS_FG_OVERRIDE=on" in command
        assert command.endswith("%command%")

    def test_sr_latest_and_j_presets(self):
        for preset, env in (("latest", "render_preset_latest"), ("preset_j", "render_preset_j")):
            config = LinuxDLSSConfig(selected_preset=preset)
            assert (
                f"DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE_RENDER_PRESET_SELECTION={env}"
                in generate_steam_launch_options(config)
            )

    def test_invalid_preset_is_skipped(self):
        config = LinuxDLSSConfig(selected_preset="bogus", rr_preset="bogus")
        assert generate_steam_launch_options(config) == "%command%"

    def test_upgrade_vars_all_enabled(self):
        config = LinuxDLSSConfig(
            dlss_upgrade=True,
            dlss_indicator=True,
            fsr4_upgrade=True,
            fsr4_indicator=True,
            xess_upgrade=True,
        )
        command = generate_steam_launch_options(config)
        for var in (
            "PROTON_DLSS_UPGRADE=1",
            "PROTON_DLSS_INDICATOR=1",
            "PROTON_FSR4_UPGRADE=1",
            "PROTON_FSR4_INDICATOR=1",
            "PROTON_XESS_UPGRADE=1",
        ):
            assert var in command

    def test_fsr4_rdna3_mode_swaps_variable(self):
        config = LinuxDLSSConfig(fsr4_upgrade=True, fsr4_rdna3_mode=True)
        command = generate_steam_launch_options(config)
        assert "PROTON_FSR4_RDNA3_UPGRADE=1" in command
        assert "PROTON_FSR4_UPGRADE=1" not in command

    def test_capability_gating_filters_upgrade_vars(self):
        config = LinuxDLSSConfig(
            selected_preset="latest",
            dlss_upgrade=True,
            fsr4_upgrade=True,
            xess_upgrade=True,
        )
        # Proton-EM: FSR4 only
        command = generate_steam_launch_options(config, frozenset({CAP_FSR4_UPGRADE}))
        assert "PROTON_FSR4_UPGRADE=1" in command
        assert "PROTON_DLSS_UPGRADE" not in command
        assert "PROTON_XESS_UPGRADE" not in command
        # DXVK-NVAPI vars are never capability-filtered
        assert "SR_OVERRIDE_RENDER_PRESET_SELECTION=render_preset_latest" in command

    def test_capability_gating_valve_drops_all_upgrades(self):
        config = LinuxDLSSConfig(dlss_upgrade=True, fsr4_upgrade=True, xess_upgrade=True)
        command = generate_steam_launch_options(config, frozenset())
        assert command == "%command%"

    def test_none_capabilities_means_no_filtering(self):
        config = LinuxDLSSConfig(dlss_upgrade=True)
        assert "PROTON_DLSS_UPGRADE=1" in generate_steam_launch_options(config, None)


class TestPresetLists:
    def test_sr_presets_include_latest_and_j(self):
        values = [v for v, _, _ in get_all_presets()]
        assert values == ["default", "latest", "preset_j", "preset_k", "preset_l", "preset_m"]

    def test_rr_presets_mirror_windows_dialog(self):
        values = [v for v, _, _ in get_rr_presets()]
        assert values == ["default", "latest"]

    def test_env_values(self):
        assert DLSSPreset.LATEST.env_value == "render_preset_latest"
        assert DLSSPreset.PRESET_J.env_value == "render_preset_j"
        assert DLSSPreset.DEFAULT.env_value == ""


# =============================================================================
# proton_compat
# =============================================================================

class TestCompatToolClassification:
    @pytest.mark.parametrize("name", ["GE-Proton10-26", "GE-Proton9-20", "Proton-GE-8-32"])
    def test_ge_proton_full_caps(self, name):
        info = classify_compat_tool(name)
        assert info.family == "ge"
        assert info.capabilities == ALL_UPGRADE_CAPS

    def test_cachyos_full_caps(self):
        info = classify_compat_tool("proton-cachyos")
        assert info.family == "cachyos"
        assert info.capabilities == ALL_UPGRADE_CAPS

    def test_proton_em_fsr4_only(self):
        info = classify_compat_tool("Proton-EM-10.0-2d")
        assert info.family == "em"
        assert info.capabilities == frozenset({CAP_FSR4_UPGRADE})

    @pytest.mark.parametrize("name", ["proton_experimental", "proton_hotfix", "proton_9"])
    def test_valve_no_upgrade_caps(self, name):
        info = classify_compat_tool(name)
        assert info.family == "valve"
        assert info.is_proton
        assert not info.capabilities

    def test_default_none_is_valve(self):
        info = classify_compat_tool(None)
        assert info.family == "valve"
        assert not info.capabilities

    def test_native_runtime_not_proton(self):
        info = classify_compat_tool("SteamLinuxRuntime_sniper")
        assert not info.is_proton
        assert not info.capabilities

    def test_unknown_fork_conservative(self):
        info = classify_compat_tool("SomeCustomProtonFork")
        assert info.family == "unknown"
        assert info.is_proton
        assert not info.capabilities


class TestCompatToolMapping:
    _VDF = {
        "InstallConfigStore": {
            "Software": {
                "valve": {  # lowercase on some installs
                    "Steam": {
                        "CompatToolMapping": {
                            "0": {"name": "GE-Proton10-26", "config": "", "priority": "75"},
                            "1091500": {"name": "proton_experimental", "config": "", "priority": "250"},
                            "620": {"name": "", "config": "", "priority": "250"},
                        }
                    }
                }
            }
        }
    }

    def test_extract_mapping_case_insensitive(self):
        mapping = extract_compat_tool_mapping(self._VDF)
        assert mapping == {"0": "GE-Proton10-26", "1091500": "proton_experimental"}

    def test_extract_mapping_missing_section(self):
        assert extract_compat_tool_mapping({}) == {}
        assert extract_compat_tool_mapping({"InstallConfigStore": {}}) == {}

    def test_resolve_per_app_wins_over_default(self):
        mapping = extract_compat_tool_mapping(self._VDF)
        assert resolve_tool_for_app(mapping, 1091500) == "proton_experimental"
        assert resolve_tool_for_app(mapping, "1091500") == "proton_experimental"

    def test_resolve_falls_back_to_global_default(self):
        mapping = extract_compat_tool_mapping(self._VDF)
        assert resolve_tool_for_app(mapping, 999999) == "GE-Proton10-26"

    def test_resolve_no_entries(self):
        assert resolve_tool_for_app({}, 42) is None


# =============================================================================
# gpu_detection PCI classification
# =============================================================================

class TestGPUClassification:
    def test_nvidia(self):
        gpu = classify_pci_gpu(0x10DE, 0x2684)  # RTX 4090
        assert gpu.vendor == "nvidia"
        assert gpu.amd_generation is None

    def test_amd_rdna4_range(self):
        assert classify_pci_gpu(0x1002, 0x7550).amd_generation == "rdna4"

    def test_amd_rdna3_range_and_igpu(self):
        assert classify_pci_gpu(0x1002, 0x744C).amd_generation == "rdna3"  # Navi 31
        assert classify_pci_gpu(0x1002, 0x15BF).amd_generation == "rdna3"  # Phoenix iGPU

    def test_amd_older(self):
        assert classify_pci_gpu(0x1002, 0x73BF).amd_generation == "other"  # Navi 21 (RDNA2)

    def test_intel(self):
        assert classify_pci_gpu(0x8086, 0x56A0).vendor == "intel"  # Arc A770

    def test_unknown_vendor(self):
        assert classify_pci_gpu(0x1234, 0x0001).vendor == "unknown"

    def test_pick_primary_prefers_dgpu(self):
        intel = classify_pci_gpu(0x8086, 0x46A6)   # iGPU
        nvidia = classify_pci_gpu(0x10DE, 0x2684)
        assert pick_primary_gpu([intel, nvidia]).vendor == "nvidia"

        rdna3_igpu = classify_pci_gpu(0x1002, 0x15BF)
        rdna4 = classify_pci_gpu(0x1002, 0x7550)
        assert pick_primary_gpu([rdna3_igpu, rdna4]).amd_generation == "rdna4"

    def test_pick_primary_empty(self):
        assert pick_primary_gpu([]) is None
