"""Where the client fetches each DLL binary from.

Most DLLs live in the DLL repo's ``dlls/`` directory and are served by
raw.githubusercontent.com. That breaks down at 100 MiB, GitHub's hard
per-file push limit: nvngx_dlssnr.dll (DLSS 5 Neural Rendering) is ~158 MiB
and cannot be committed at all. Such DLLs ship as GitHub *release assets*
instead, and the manifest entry carries a "url" pointing at them.

The manifest stays the source of truth either way — the client just has to
honour the override, and refuse a URL that would take it off the DLL repo.
"""

import pytest

from dlss_updater import dll_repository
from dlss_updater.constants import DLL_GROUPS, DLL_TYPE_MAP

DLSSNR = "nvngx_dlssnr.dll"
RELEASE_URL = (
    "https://github.com/Recol/DLSS-Updater-DLLs/releases/download/"
    "nvngx_dlssnr-310.8.0.0/nvngx_dlssnr.dll"
)


class TestResolveDownloadUrl:
    def test_falls_back_to_the_raw_dlls_directory(self):
        """A manifest entry with no "url" keeps the historical behaviour."""
        url = dll_repository.resolve_download_url(
            "nvngx_dlss.dll", {"version": "310.9.0.0"}
        )
        assert url == f"{dll_repository.GITHUB_RAW_BASE}/dlls/nvngx_dlss.dll"

    def test_honours_a_manifest_url_override(self):
        url = dll_repository.resolve_download_url(DLSSNR, {"url": RELEASE_URL})
        assert url == RELEASE_URL

    def test_missing_entry_falls_back(self):
        """No entry at all is not a crash — callers already checked membership."""
        url = dll_repository.resolve_download_url(DLSSNR, None)
        assert url == f"{dll_repository.GITHUB_RAW_BASE}/dlls/{DLSSNR}"

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://github.com/Recol/DLSS-Updater-DLLs/releases/download/x/y.dll",
            "https://evil.example.com/nvngx_dlssnr.dll",
            "https://github.com.evil.example/Recol/x.dll",
            "https://github.com/SomeoneElse/Other-Repo/releases/download/x/y.dll",
            "file:///C:/Windows/System32/evil.dll",
            "not a url at all",
            "",
        ],
    )
    def test_rejects_a_url_outside_the_dll_repo(self, bad_url):
        """These DLLs get written into game directories. A manifest that has
        been tampered with must not be able to redirect that download."""
        url = dll_repository.resolve_download_url(DLSSNR, {"url": bad_url})
        assert url == f"{dll_repository.GITHUB_RAW_BASE}/dlls/{DLSSNR}"


class TestNeuralRenderingIsTracked:
    def test_has_a_dll_type(self):
        assert DLL_TYPE_MAP[DLSSNR] == "DLSS Neural Rendering DLL"

    def test_belongs_to_the_dlss_group(self):
        """Covered by the existing "DLSS" toggle - not a group of its own."""
        assert DLSSNR in DLL_GROUPS["DLSS"]

    def test_is_allowed_by_a_dlss_scope(self):
        from dlss_updater import update_scope

        assert update_scope.allows(frozenset({"DLSS"}), DLSSNR)

    def test_is_not_allowed_without_dlss(self):
        from dlss_updater import update_scope

        assert not update_scope.allows(frozenset({"FSR", "XeSS"}), DLSSNR)

    def test_is_not_a_preview_optin(self):
        """DLSS 5 shipped 2026-09-03; it is not gated like FSR Radiance Cache."""
        from dlss_updater.constants import PREVIEW_DLL_PREFERENCE, PREVIEW_DLLS

        assert DLSSNR not in PREVIEW_DLLS
        assert DLSSNR not in PREVIEW_DLL_PREFERENCE
