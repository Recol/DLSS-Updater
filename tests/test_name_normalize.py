"""Tests for dlss_updater.name_normalize.prettify_display_name (display-only helper)."""

import pytest

from dlss_updater.name_normalize import prettify_display_name


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Run-together folder names get word boundaries inserted.
        ("BlackMythWukong", "Black Myth Wukong"),
        ("ForzaHorizon6", "Forza Horizon 6"),
        ("DuneAwakening", "Dune Awakening"),
        ("MarvelRivals", "Marvel Rivals"),
        ("DLSSUpdater", "DLSS Updater"),  # ALLCAPS run -> Titlecase word
        # Names that already contain whitespace are returned UNCHANGED.
        ("ARC Raiders", "ARC Raiders"),  # preserves the all-caps run
        ("No Man's Sky", "No Man's Sky"),
        ("Ghost of Tsushima DIRECTOR'S CUT", "Ghost of Tsushima DIRECTOR'S CUT"),
        ("Baldurs Gate 3", "Baldurs Gate 3"),
        ("Helldivers 2", "Helldivers 2"),
    ],
)
def test_prettify_display_name(raw, expected):
    assert prettify_display_name(raw) == expected


def test_prettify_display_name_empty():
    assert prettify_display_name("") == ""
