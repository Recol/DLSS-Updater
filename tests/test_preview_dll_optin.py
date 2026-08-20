"""
Pre-release FidelityFX components must never be updated on the strength of the
FSR technology toggle alone.

AMD ships FSR Radiance Caching as "(Preview)" at version 0.9.0, for engine
developers integrating ahead of release — no shipping game, driver path or
benchmark uses it. Enabling "FSR" therefore must NOT enrol the user in replacing
it; a separate, explicitly-acknowledged opt-in is required.
"""

import pytest

from dlss_updater.config import config_manager
from dlss_updater.constants import (
    DLL_GROUPS,
    DLL_TYPE_MAP,
    PREVIEW_DLL_PREFERENCE,
    PREVIEW_DLLS,
)
from dlss_updater.utils import get_dll_technology_group, is_dll_update_enabled

RADIANCE = "amd_fidelityfx_radiancecache_dx12.dll"


@pytest.fixture
def prefs():
    """Restore whatever the user actually had set, whatever the test does."""
    saved = {
        "FSR": config_manager.get_update_preference("FSR"),
        "FSR_RadianceCache": config_manager.get_update_preference("FSR_RadianceCache"),
    }
    yield config_manager
    for token, value in saved.items():
        config_manager.set_update_preference(token, value)


class TestPreviewRegistration:
    def test_radiance_cache_is_marked_preview(self):
        assert RADIANCE in PREVIEW_DLLS
        assert PREVIEW_DLL_PREFERENCE[RADIANCE] == "FSR_RadianceCache"

    def test_groups_under_fsr_for_display(self):
        """In the FSR group so it isn't rendered as an unknown DLL if present."""
        assert RADIANCE in [d.lower() for d in DLL_GROUPS["FSR"]]
        assert get_dll_technology_group(RADIANCE) == "FSR"

    def test_has_a_type_label_naming_it_preview(self):
        assert "Preview" in DLL_TYPE_MAP[RADIANCE]

    def test_default_is_off(self):
        """The whole point: a fresh install must not touch a preview component."""
        from dlss_updater.models import UpdatePreferencesConfig

        assert UpdatePreferencesConfig().update_fsr_radiance_cache is False


class TestPreviewGating:
    def test_fsr_on_alone_does_not_enable_it(self, prefs):
        prefs.set_update_preference("FSR", True)
        prefs.set_update_preference("FSR_RadianceCache", False)
        assert is_dll_update_enabled(RADIANCE) is False
        # ...while ordinary FSR DLLs are unaffected
        assert is_dll_update_enabled("amd_fidelityfx_upscaler_dx12.dll") is True

    def test_opt_in_enables_it(self, prefs):
        prefs.set_update_preference("FSR", True)
        prefs.set_update_preference("FSR_RadianceCache", True)
        assert is_dll_update_enabled(RADIANCE) is True

    def test_fsr_off_overrides_opt_in(self, prefs):
        """The opt-in is layered on top of FSR, not an escape hatch around it."""
        prefs.set_update_preference("FSR", False)
        prefs.set_update_preference("FSR_RadianceCache", True)
        assert is_dll_update_enabled(RADIANCE) is False


class TestNoDllIsUnreachable:
    """Every known DLL must be resolvable, or it can never be updated.

    process_single_dll looks the cached file up in LATEST_DLL_PATHS. A DLL that
    is in DLL_GROUPS (so it is scanned for, grouped and shown in the UI) but
    missing from LATEST_DLL_PATHS fails *silently*: the scan finds it, the
    preference allows it, and then nothing happens. This is exactly how the
    Ray Regeneration and Radiance Caching DLLs were unreachable when first
    added, so guard the invariant rather than the two filenames.
    """

    def test_every_grouped_dll_is_in_the_type_map(self):
        from dlss_updater.constants import DLL_GROUPS, DLL_TYPE_MAP

        grouped = {d.lower() for dlls in DLL_GROUPS.values() for d in dlls}
        missing = sorted(grouped - set(DLL_TYPE_MAP))
        assert not missing, f"in DLL_GROUPS but absent from DLL_TYPE_MAP: {missing}"

    def test_dll_paths_are_derived_from_the_type_map(self):
        """LATEST_DLL_PATHS is built from DLL_TYPE_MAP, so the two cannot drift."""
        import inspect

        from dlss_updater.config import initialize_dll_paths

        source = inspect.getsource(initialize_dll_paths)
        assert "DLL_TYPE_MAP" in source, (
            "initialize_dll_paths must derive its keys from DLL_TYPE_MAP; "
            "hand-listing them lets new DLLs become silently un-updatable"
        )
