"""Progress ticks must update only their own control, never the whole page.

In Flet 1.0 page.update() serialises the entire attached tree and every
update() call is sent immediately, so a scan/update run's progress callback
calling page.update() per tick re-sends the whole page hundreds of times.
LoadingOverlay, DLLCacheProgressSnackbar and ShutdownProgressDialog now update
their own subtree, drop ticks that change nothing on screen, and throttle
non-final ticks (ProgressFlushThrottle), always flushing the final tick.

The page and the narrow update() are stubs; nothing touches a Flet client.
"""

import asyncio
from types import SimpleNamespace

import pytest

from dlss_updater.ui_flet.components.dll_cache_snackbar import (
    DLLCacheProgressSnackbar,
    NotificationState,
)
from dlss_updater.ui_flet.components.loading_overlay import (
    LoadingOverlay,
    ProgressFlushThrottle,
)
from dlss_updater.ui_flet.dialogs.shutdown_progress_dialog import ShutdownProgressDialog


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _page():
    calls = {"page": 0}

    def update(*_a):
        calls["page"] += 1

    page = SimpleNamespace(
        overlay=[],
        update=update,
        show_dialog=lambda d: None,
        pop_dialog=lambda: None,
    )
    return page, calls


# ==================== ProgressFlushThrottle ====================


def test_throttle_drops_identical_ticks():
    clock = _Clock()
    th = ProgressFlushThrottle(clock=clock)
    flushed = []

    assert th.submit((1, "a"), lambda: flushed.append(1)) is True
    clock.t += 10
    assert th.submit((1, "a"), lambda: flushed.append(2)) is False
    assert flushed == [1]


def test_throttle_defers_close_ticks_but_always_flushes_final():
    clock = _Clock()
    th = ProgressFlushThrottle(clock=clock)
    flushed = []

    th.submit((1, "a"), lambda: flushed.append("first"))
    clock.t += 0.01
    assert th.submit((2, "a"), lambda: flushed.append("close")) is False
    clock.t += 0.01
    assert th.submit((100, "a"), lambda: flushed.append("final"), final=True) is True
    assert flushed == ["first", "final"]


def test_throttle_trailing_flush_sends_latest_deferred_state():
    async def run():
        th = ProgressFlushThrottle(min_interval=0.02)
        flushed = []
        th.submit(1, lambda: flushed.append(1))
        th.submit(2, lambda: flushed.append(2))  # deferred
        th.submit(3, lambda: flushed.append(3))  # replaces the deferred one
        assert flushed == [1]
        await asyncio.sleep(0.08)
        return flushed

    assert asyncio.run(run()) == [1, 3]


def test_throttle_reset_cancels_pending_trailing_flush():
    async def run():
        th = ProgressFlushThrottle(min_interval=0.02)
        flushed = []
        th.submit(1, lambda: flushed.append(1))
        th.submit(2, lambda: flushed.append(2))
        th.reset()
        await asyncio.sleep(0.08)
        return flushed

    assert asyncio.run(run()) == [1]


# ==================== LoadingOverlay ====================


@pytest.fixture
def overlay(monkeypatch):
    page, calls = _page()
    ov = LoadingOverlay(page)
    ov.show(page, "Scanning...")
    assert calls["page"] == 1  # show() attaches via one page.update()
    calls["page"] = 0
    calls["narrow"] = 0

    def narrow():
        calls["narrow"] += 1

    monkeypatch.setattr(ov, "update", narrow)
    clock = _Clock()
    ov._flush_throttle._clock = clock
    return ov, page, calls, clock


def test_overlay_changed_tick_uses_narrow_update_not_page(overlay):
    ov, page, calls, clock = overlay

    asyncio.run(ov.set_progress_async(10, page, "Scanning Steam..."))

    assert calls == {"page": 0, "narrow": 1}
    assert ov.progress_text.value == "10%"
    assert ov.status_text.value == "Scanning Steam..."


def test_overlay_unchanged_tick_does_not_update(overlay):
    ov, page, calls, clock = overlay

    asyncio.run(ov.set_progress_async(10, page, "Scanning Steam..."))
    clock.t += 1
    asyncio.run(ov.set_progress_async(10, page, "Scanning Steam..."))
    ov.set_progress(10, page)  # sync path, no message: still nothing new

    assert calls == {"page": 0, "narrow": 1}


def test_overlay_final_tick_always_flushes(overlay):
    ov, page, calls, clock = overlay

    asyncio.run(ov.set_progress_async(10, page, "a"))
    clock.t += 0.001  # well inside the throttle window
    asyncio.run(ov.set_progress_async(100, page, "a"))

    assert calls == {"page": 0, "narrow": 2}
    assert ov.progress_text.value == "100%"


def test_overlay_ticks_after_hide_do_nothing(overlay):
    ov, page, calls, clock = overlay

    ov.hide(page)
    assert calls["page"] == 1  # hide() still flushes the overlay removal
    clock.t += 1
    asyncio.run(ov.set_progress_async(50, page, "late"))

    assert calls == {"page": 1, "narrow": 0}


def test_overlay_falls_back_to_page_update_when_not_yet_attached():
    page, calls = _page()
    ov = LoadingOverlay(page)
    ov.show(page, "x")
    calls["page"] = 0

    # Never really attached to a client page: update() raises RuntimeError.
    asyncio.run(ov.set_progress_async(5, page, "x"))

    assert calls["page"] == 1


# ==================== DLLCacheProgressSnackbar ====================


@pytest.fixture
def snackbar(monkeypatch):
    page, calls = _page()
    sb = DLLCacheProgressSnackbar(page)
    calls["narrow"] = 0

    def narrow():
        calls["narrow"] += 1

    monkeypatch.setattr(sb.wrapper, "update", narrow)
    sb._state = NotificationState.INITIALIZING
    sb._flush_throttle.reset()
    clock = _Clock()
    sb._flush_throttle._clock = clock
    return sb, calls, clock


def test_snackbar_changed_tick_updates_wrapper_not_page(snackbar):
    sb, calls, clock = snackbar

    asyncio.run(sb.update_progress(10, 100, "Checked 1/10 DLLs"))

    assert calls == {"page": 0, "narrow": 1}
    assert sb.progress_text.value == "10%"


def test_snackbar_unchanged_tick_does_not_update(snackbar):
    sb, calls, clock = snackbar

    asyncio.run(sb.update_progress(0, 100, "Fetching manifest..."))
    clock.t += 1
    asyncio.run(sb.update_progress(0, 100, "Fetching manifest..."))

    assert calls == {"page": 0, "narrow": 1}


def test_snackbar_final_tick_always_flushes(snackbar):
    sb, calls, clock = snackbar

    asyncio.run(sb.update_progress(10, 100, "a"))
    clock.t += 0.001
    asyncio.run(sb.update_progress(100, 100, "a"))

    assert calls == {"page": 0, "narrow": 2}
    assert sb.progress_text.value == "100%"


def test_snackbar_falls_back_to_page_update_when_wrapper_unattached():
    page, calls = _page()
    sb = DLLCacheProgressSnackbar(page)
    sb._state = NotificationState.INITIALIZING

    asyncio.run(sb.update_progress(50, 100, "x"))

    assert calls["page"] == 1


# ==================== ShutdownProgressDialog ====================


def test_shutdown_steps_update_dialog_content_not_page(monkeypatch):
    page, calls = _page()
    dlg = ShutdownProgressDialog(page)
    dlg.show()
    calls["narrow"] = 0

    def narrow():
        calls["narrow"] += 1

    monkeypatch.setattr(dlg._content, "update", narrow)

    dlg.update_step(3)
    dlg.show_complete()

    assert calls == {"page": 0, "narrow": 2}
    assert dlg._status_text.value == "Shutdown complete"
