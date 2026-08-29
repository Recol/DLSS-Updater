"""Tests for the Games grid density table.

The grid's cell geometry has one invariant, documented on GRID_DENSITIES: a
cell is exactly the flexible banner plus GameCard's FIXED 52 px footer. Get the
aspect ratio wrong and the footer is either clipped or trailed by a grey gap,
which is precisely the class of bug the original single-density comment in
games_view.py was written to prevent.

resolve_density() is a pure function over a table, so it is called directly
without constructing a Flet-backed GamesView.
"""

import pytest

from dlss_updater.ui_flet.components.game_card import FOOTER_HEIGHT, HERO_HEIGHT
from dlss_updater.ui_flet.views.games_view import (
    DEFAULT_DENSITY,
    GRID_DENSITIES,
    resolve_density,
)


def test_comfortable_preserves_the_existing_grid_geometry():
    """The default density must stay byte-identical to the old constant
    (320, 1.25, banner=HERO_HEIGHT) — anyone who never opens the new menu
    should see exactly the grid they had before."""
    max_extent, aspect, banner = resolve_density("comfortable")

    assert max_extent == 320
    assert banner == HERO_HEIGHT
    assert aspect == pytest.approx(1.25)


@pytest.mark.parametrize("name", sorted(GRID_DENSITIES))
def test_cell_height_is_exactly_banner_plus_fixed_footer(name):
    """The invariant: max_extent / aspect == banner + FOOTER_HEIGHT."""
    max_extent, aspect, banner = resolve_density(name)

    assert max_extent / aspect == pytest.approx(banner + FOOTER_HEIGHT)


def test_unknown_density_falls_back_to_the_default():
    assert resolve_density("enormous") == resolve_density(DEFAULT_DENSITY)


def test_missing_density_falls_back_to_the_default():
    """config_manager.get_grid_density() can return None on a fresh config."""
    assert resolve_density(None) == resolve_density(DEFAULT_DENSITY)


def test_densities_are_distinct_and_ordered_by_cell_width():
    widths = [resolve_density(n)[0] for n in ("compact", "comfortable", "large")]

    assert widths == sorted(widths)
    assert len(set(widths)) == 3


def test_default_density_is_a_real_entry():
    assert DEFAULT_DENSITY in GRID_DENSITIES
