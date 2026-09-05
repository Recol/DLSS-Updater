"""Which DLL technologies a single update run may touch.

Scope is a frozenset of technology tokens. It is read from ThreadPoolExecutor
workers in the high-performance pipeline, so it must stay immutable.
"""

import pytest

from dlss_updater import update_scope
from dlss_updater.config import config_manager
from dlss_updater.constants import DLL_GROUPS, PREVIEW_DLL_PREFERENCE

RADIANCE = "amd_fidelityfx_radiancecache_dx12.dll"


@pytest.fixture
def prefs():
    """Restore whatever the user actually had set, whatever the test does."""
    tokens = list(DLL_GROUPS) + list(PREVIEW_DLL_PREFERENCE.values())
    saved = {t: config_manager.get_update_preference(t) for t in tokens}
    yield config_manager
    for token, value in saved.items():
        config_manager.set_update_preference(token, value)


class TestAllTechnologies:
    def test_is_every_group_on_this_platform(self):
        assert update_scope.all_technologies() == frozenset(DLL_GROUPS)

    def test_is_immutable(self):
        assert isinstance(update_scope.all_technologies(), frozenset)

    def test_excludes_preview_opt_ins(self):
        """'All technologies' must never silently enrol the user in a preview."""
        assert not (update_scope.all_technologies() & update_scope.PREVIEW_TOKENS)


class TestFromPreferences:
    def test_reflects_a_disabled_group(self, prefs):
        prefs.set_update_preference("XeSS", False)
        assert "XeSS" not in update_scope.from_preferences()

    def test_reflects_an_enabled_group(self, prefs):
        prefs.set_update_preference("XeSS", True)
        assert "XeSS" in update_scope.from_preferences()

    def test_carries_the_preview_opt_in(self, prefs):
        prefs.set_update_preference("FSR_RadianceCache", True)
        assert "FSR_RadianceCache" in update_scope.from_preferences()


class TestAllows:
    def test_dll_in_an_included_group(self):
        assert update_scope.allows(frozenset({"DLSS"}), "nvngx_dlss.dll") is True

    def test_dll_in_an_excluded_group(self):
        assert update_scope.allows(frozenset({"DLSS"}), "libxess.dll") is False

    def test_unknown_dll_is_never_allowed(self):
        assert update_scope.allows(update_scope.all_technologies(), "totally_made_up.dll") is False

    def test_is_case_insensitive(self):
        assert update_scope.allows(frozenset({"DLSS"}), "NVNGX_DLSS.DLL") is True

    def test_empty_scope_allows_nothing(self):
        assert update_scope.allows(frozenset(), "nvngx_dlss.dll") is False

    def test_preview_needs_its_own_token_on_top_of_fsr(self):
        """FSR in scope is not enough — the preview opt-in is layered on top."""
        assert update_scope.allows(frozenset({"FSR"}), RADIANCE) is False
        assert update_scope.allows(frozenset({"FSR", "FSR_RadianceCache"}), RADIANCE) is True

    def test_preview_token_alone_is_not_an_escape_hatch(self):
        assert update_scope.allows(frozenset({"FSR_RadianceCache"}), RADIANCE) is False

    def test_every_grouped_dll_is_allowed_by_its_own_group(self):
        """Guards the group mapping against drift in DLL_GROUPS."""
        for group, dlls in DLL_GROUPS.items():
            for dll in dlls:
                name = dll.lower()
                if name in PREVIEW_DLL_PREFERENCE:
                    continue  # needs a second token; covered above
                assert update_scope.allows(frozenset({group}), name) is True, f"{group}/{dll}"


class TestIsDllUpdateEnabledWithScope:
    """The scope keyword overrides config; None preserves the old behaviour."""

    def test_scope_overrides_enabled_preference(self, prefs):
        prefs.set_update_preference("XeSS", True)
        from dlss_updater.utils import is_dll_update_enabled

        assert is_dll_update_enabled("libxess.dll", scope=frozenset({"DLSS"})) is False

    def test_scope_overrides_disabled_preference(self, prefs):
        prefs.set_update_preference("XeSS", False)
        from dlss_updater.utils import is_dll_update_enabled

        assert is_dll_update_enabled("libxess.dll", scope=frozenset({"XeSS"})) is True

    def test_none_still_reads_config(self, prefs):
        prefs.set_update_preference("XeSS", False)
        from dlss_updater.utils import is_dll_update_enabled

        assert is_dll_update_enabled("libxess.dll") is False
        prefs.set_update_preference("XeSS", True)
        assert is_dll_update_enabled("libxess.dll") is True


class TestHighPerformanceTaskFiltering:
    """Regression for the technology filter the HP pipeline never had.

    The HP task builder gated only on LATEST_DLL_PATHS membership, so a
    technology disabled after a scan was still updated from cached scan
    results. These tests fail on the commit before this task.
    """

    @pytest.fixture(autouse=True)
    def resolvable_dlls(self, monkeypatch):
        """LATEST_DLL_PATHS is {} until initialize_dll_paths() runs (config.py:218).

        Without this the builder resolves nothing and every assertion below would
        pass for the wrong reason. build_dll_tasks re-imports the name at call
        time, so patching the module attribute takes effect.
        """
        monkeypatch.setattr(
            "dlss_updater.config.LATEST_DLL_PATHS",
            {
                # Values are never dereferenced — the builder only tests
                # membership and truthiness — so keep them separator-agnostic.
                "nvngx_dlss.dll": "C:/cache/nvngx_dlss.dll",
                "libxess.dll": "C:/cache/libxess.dll",
                "dstorage.dll": "C:/cache/dstorage.dll",
            },
        )

    def _build(self, dll_dict, scope):
        """Run only the task-building step, without touching the filesystem."""
        from dlss_updater.ui_flet.async_updater import build_dll_tasks

        return build_dll_tasks(dll_dict, scope)

    def test_out_of_scope_dlls_produce_no_tasks(self):
        dll_dict = {"Steam": [r"C:\games\a\nvngx_dlss.dll", r"C:\games\a\libxess.dll"]}
        tasks, skipped = self._build(dll_dict, frozenset({"DLSS"}))
        names = {t.source_dll_name for t in tasks}
        assert names == {"nvngx_dlss.dll"}
        assert skipped == [r"C:\games\a\libxess.dll"]

    def test_directstorage_excluded_by_scope(self):
        """The exact scenario from issue #121: DS disabled must mean DS untouched."""
        from dlss_updater.constants import DLL_GROUPS

        if "DirectStorage" not in DLL_GROUPS:
            pytest.skip("DirectStorage is Windows-only")
        dll_dict = {"Steam": [r"C:\games\a\dstorage.dll", r"C:\games\a\nvngx_dlss.dll"]}
        scope = frozenset({"DLSS"})
        tasks, skipped = self._build(dll_dict, scope)
        assert all(t.source_dll_name != "dstorage.dll" for t in tasks)
        assert r"C:\games\a\dstorage.dll" in skipped

    def test_empty_scope_yields_no_tasks_and_does_not_raise(self):
        dll_dict = {"Steam": [r"C:\games\a\nvngx_dlss.dll"]}
        tasks, skipped = self._build(dll_dict, frozenset())
        assert tasks == []
        assert skipped == [r"C:\games\a\nvngx_dlss.dll"]

    def test_full_scope_keeps_every_resolvable_dll(self):
        from dlss_updater.update_scope import all_technologies

        dll_dict = {"Steam": [r"C:\games\a\nvngx_dlss.dll"]}
        tasks, skipped = self._build(dll_dict, all_technologies())
        assert [t.source_dll_name for t in tasks] == ["nvngx_dlss.dll"]
        assert skipped == []


class TestProcessSingleDllScope:
    """Regression for the standard-mode fallback path (taken when the HP
    pipeline raises): process_single_dll must forward an explicit scope to
    is_dll_update_enabled instead of always falling back to saved
    preferences. Without this, a per-run scope narrower than the user's
    saved preferences was silently ignored whenever the fallback ran.
    """

    @pytest.mark.anyio
    async def test_scope_is_forwarded_to_is_dll_update_enabled(self, monkeypatch):
        """Cheapest proof: is_dll_update_enabled is consulted WITH the scope,
        short-circuiting before any filesystem/whitelist work."""
        from pathlib import Path

        import dlss_updater.utils as utils_module

        calls = []

        def fake_is_dll_update_enabled(dll_name, scope=None):
            calls.append((dll_name, scope))
            return False

        monkeypatch.setattr(utils_module, "is_dll_update_enabled", fake_is_dll_update_enabled)

        scope = frozenset({"DLSS"})
        result = await utils_module.process_single_dll(
            Path(r"C:\games\a\libxess.dll"), "Steam", scope=scope
        )

        assert calls == [("libxess.dll", scope)]
        assert result.success is False

    @pytest.mark.anyio
    async def test_out_of_scope_dll_is_skipped_even_when_saved_prefs_allow_it(self, prefs):
        """The exact bug: saved prefs enable XeSS, but this run's explicit
        scope is DLSS-only — the explicit scope must win."""
        from pathlib import Path

        from dlss_updater.utils import process_single_dll

        prefs.set_update_preference("XeSS", True)

        result = await process_single_dll(
            Path(r"C:\games\a\libxess.dll"), "Steam", scope=frozenset({"DLSS"})
        )

        assert result.success is False

    @pytest.mark.anyio
    async def test_none_scope_still_reads_saved_preferences(self, prefs):
        """scope=None must preserve today's behaviour exactly."""
        from pathlib import Path

        from dlss_updater.utils import process_single_dll

        prefs.set_update_preference("XeSS", False)

        result = await process_single_dll(Path(r"C:\games\a\libxess.dll"), "Steam")

        assert result.success is False


class TestGameCardPopoverExclusionDisplay:
    """Regression for the Games view DLL popover dimming out-of-scope DLLs.

    Scanning is unconditional now, so the popover can list a technology the
    user has excluded from updates. It must dim/tooltip those rows rather
    than hide them, keyed on the DLL filename (not the display label).
    """

    def test_filename_keyed_check_is_correct_for_a_narrowed_scope(self):
        scope = frozenset({"DLSS"})
        assert update_scope.allows(scope, "libxess.dll") is False
        assert update_scope.allows(scope, "nvngx_dlss.dll") is True

    def test_dlss_display_labels_are_not_technology_tokens(self):
        """Guards the trap in game_card._build_dll_popover_items: passing
        dll.dll_type (a display label like 'DLSS-G'/'DLSS-D') instead of
        dll.dll_filename would silently fail to resolve to a group."""
        technologies = update_scope.all_technologies()
        assert "DLSS-G" not in technologies
        assert "DLSS-D" not in technologies

    def test_allows_conflates_excluded_technology_with_unrecognised_dll(self):
        """allows() returns False for two different reasons: a technology the
        user excluded, and a filename that belongs to no technology group at
        all. A caller cannot tell these apart from the return value alone —
        which is exactly why the popover needs its own
        get_dll_technology_group() check rather than trusting allows() to
        mean 'excluded from updates in preferences'."""
        assert "totally_made_up.dll" not in {
            d.lower() for dlls in DLL_GROUPS.values() for d in dlls
        }
        assert update_scope.allows(frozenset({"DLSS"}), "libxess.dll") is False
        assert update_scope.allows(update_scope.all_technologies(), "totally_made_up.dll") is False


class TestGroupDialogExclusionDisplay:
    """The DLL group dialog's technology loop tests group names directly
    against from_preferences() with no filename mapping, so every literal
    in the loop's group list must actually be a technology token."""

    def test_group_dialog_technologies_are_all_valid_tokens(self):
        technologies = update_scope.all_technologies()
        for group_name in ["DLSS", "Streamline", "XeSS", "FSR", "DirectStorage"]:
            assert group_name in technologies, (
                f"{group_name} is not a technology token in all_technologies() — "
                "the group dialog's direct membership test would be unsound"
            )


class TestScanningIsUnconditional:
    """The scan finds everything; scope is applied at update time.

    Keeping the filter at scan time meant widening scope silently did
    nothing, because the cached results never contained the new technology.
    """

    def test_technology_groups_are_unconditional(self, prefs):
        """With technology preferences disabled, all non-preview DLLs are still scanned."""
        from dlss_updater.scanner import dll_names_to_scan

        # Disable all technology preferences
        prefs.set_update_preference("DLSS", False)
        prefs.set_update_preference("XeSS", False)
        prefs.set_update_preference("Streamline", False)
        prefs.set_update_preference("FSR", False)
        if "DirectStorage" in DLL_GROUPS:
            prefs.set_update_preference("DirectStorage", False)

        # Get the list of DLLs to scan
        scan_list = dll_names_to_scan()

        # Every non-preview DLL should still be in the list
        for group, dlls in DLL_GROUPS.items():
            for dll in dlls:
                is_preview = dll.lower() in PREVIEW_DLL_PREFERENCE
                if is_preview:
                    continue  # Preview DLLs handled separately
                assert dll in scan_list, (
                    f"{dll} from {group} should be in scan list even with preferences disabled"
                )

    def test_preview_excluded_by_default(self, prefs):
        """With FSR_RadianceCache opt-in disabled, the preview DLL is not scanned."""
        from dlss_updater.scanner import dll_names_to_scan

        # Ensure FSR is enabled but the preview opt-in is disabled
        prefs.set_update_preference("FSR", True)
        prefs.set_update_preference("FSR_RadianceCache", False)

        scan_list = dll_names_to_scan()

        # The preview DLL should NOT be in the scan list
        assert RADIANCE not in scan_list, (
            "Preview DLL must be excluded from scan when opt-in is disabled"
        )

    def test_preview_included_on_optin(self, prefs):
        """With FSR_RadianceCache opt-in enabled, the preview DLL is scanned."""
        from dlss_updater.scanner import dll_names_to_scan

        # Enable both FSR and the preview opt-in
        prefs.set_update_preference("FSR", True)
        prefs.set_update_preference("FSR_RadianceCache", True)

        scan_list = dll_names_to_scan()

        # The preview DLL SHOULD be in the scan list
        assert RADIANCE in scan_list, (
            "Preview DLL must be included in scan when opt-in is enabled"
        )


class TestUpdateSummaryDialogScopeBanner:
    """The update summary dialog banners a narrowed run so a deliberately
    narrowed run and a broken run don't look identical (issue: fewer games
    touched than expected with no explanation).

    _build_scope_banner() is exercised directly rather than through show(),
    which needs a live ft.Page.
    """

    def _dialog(self, scope=None, out_of_scope=None):
        from dlss_updater.ui_flet.dialogs.update_summary_dialog import UpdateSummaryDialog

        return UpdateSummaryDialog(
            page=None,
            logger=None,
            result=None,
            scope=scope,
            out_of_scope=out_of_scope,
        )

    def _message(self, banner):
        """Extract the banner's text out of the Container(Row(Icon, Text)) tree."""
        return banner.content.controls[1].value

    def test_no_scope_means_no_banner(self):
        """scope=None: the caller passed nothing, so no banner."""
        dialog = self._dialog(scope=None)
        assert dialog._build_scope_banner(is_dark=True) is None

    def test_full_scope_means_no_banner(self):
        """scope=all_technologies(): nothing was excluded, so no banner."""
        from dlss_updater.update_scope import all_technologies

        dialog = self._dialog(scope=all_technologies())
        assert dialog._build_scope_banner(is_dark=True) is None

    def test_narrowed_scope_names_included_and_excluded_with_count(self):
        dialog = self._dialog(
            scope=frozenset({"DLSS"}),
            out_of_scope=["nvngx_dlss.dll", "libxess.dll"],
        )
        banner = dialog._build_scope_banner(is_dark=True)
        assert banner is not None
        message = self._message(banner)
        assert "DLSS" in message
        for excluded in sorted(update_scope.all_technologies() - {"DLSS"}):
            assert excluded in message
        assert "2 DLLs skipped" in message

    def test_narrowed_scope_with_no_out_of_scope_omits_skipped_count(self):
        """The HP path populates last_out_of_scope; the standard-mode fallback
        (filtering per-DLL inside is_dll_update_enabled) leaves it empty.
        '0 DLLs skipped' next to a narrowed-scope banner would be actively
        misleading, so the count must be omitted entirely, not printed as 0."""
        dialog = self._dialog(scope=frozenset({"DLSS"}), out_of_scope=[])
        banner = dialog._build_scope_banner(is_dark=True)
        assert banner is not None
        message = self._message(banner)
        assert "DLSS" in message
        assert "skipped" not in message
        assert "0" not in message
