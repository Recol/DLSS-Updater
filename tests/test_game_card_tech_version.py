"""Tests for GameCard._tech_version_label (the per-card DLSS/FSR/XeSS/RR chip
line) covering the two scenarios that must degrade gracefully rather than
break: a game with old/ancient DLLs, and a clean install with no cached DLL
manifest yet.

_tech_version_label() only reads self.dlls / self.dll_manifest /
self.dlss_presets, so it's called unbound on a plain stub instead of
constructing a real (Flet-backed) GameCard.
"""

from types import SimpleNamespace

from dlss_updater.models import GameDLL
from dlss_updater.ui_flet.components.game_card import GameCard

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
        "labels": [["310", "311", "RR 4.5"]],
    },
}


def _dll(dll_filename: str, current_version: str | None) -> GameDLL:
    return GameDLL(
        id=1,
        game_id=1,
        dll_type="DLSS",
        dll_filename=dll_filename,
        dll_path=f"C:/Game/{dll_filename}",
        current_version=current_version,
    )


def _label(dlls, manifest, presets=None) -> str:
    stub = SimpleNamespace(dlls=dlls, dll_manifest=manifest, dlss_presets=presets)
    return GameCard._tech_version_label(stub)


def test_clean_install_no_cached_manifest_hides_chip():
    """No manifest yet (get_cached_manifest() returned None) must not crash
    and must produce the empty string the caller uses to hide the chip line
    — never a raw/garbled label."""
    dlls = [_dll("nvngx_dlss.dll", "310.7.128.0")]
    assert _label(dlls, None) == ""


def test_ancient_dll_below_every_bucket_falls_back_to_raw_version():
    """A game with a genuinely old DLSS DLL (older than anything in the
    manifest's labeled ranges) shows its raw version instead of a
    wrong/missing marketing name."""
    dlls = [_dll("nvngx_dlss.dll", "1.0.13.0")]
    assert _label(dlls, MANIFEST) == "1.0.13.0"


def test_unparseable_version_does_not_raise():
    """A corrupt/garbage version string must not blow up label building for
    the rest of the game's DLLs. parse_version() never raises (it maps
    unparseable input to a low sentinel internally), so the garbage DLL still
    contributes its own bucket — via the same raw-version fallback as an old
    DLL — alongside the other DLL's real label."""
    dlls = [_dll("nvngx_dlss.dll", "garbage"), _dll("nvngx_dlssd.dll", "310.7.128.0")]
    assert _label(dlls, MANIFEST) == "garbage · RR 4.5"


def test_mixed_old_and_current_dlls_in_same_game():
    """Old SR DLL alongside a current RR DLL: each bucket resolves
    independently, old falls back to raw, current gets its marketing name."""
    dlls = [_dll("nvngx_dlss.dll", "2.5.10.0"), _dll("nvngx_dlssd.dll", "310.7.128.0")]
    assert _label(dlls, MANIFEST) == "DLSS 2 · RR 4.5"


def test_no_dlls_produces_empty_label():
    assert _label([], MANIFEST) == ""
