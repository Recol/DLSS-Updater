"""NavigationController keeps _nav_attached current so background work can
skip updates to views the user has left (see is_view_attached)."""

import flet as ft

from dlss_updater.ui_flet.navigation.navigation_controller import is_view_attached


def test_unmanaged_controls_count_as_attached():
    assert is_view_attached(ft.Column()) is True


def test_flag_is_honoured_on_real_controls():
    view = ft.Column()
    view._nav_attached = False
    assert is_view_attached(view) is False
    view._nav_attached = True
    assert is_view_attached(view) is True
