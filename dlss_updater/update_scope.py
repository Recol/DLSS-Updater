"""Which DLL technologies a single update run is allowed to touch.

An ``UpdateScope`` is a frozenset of technology tokens drawn from the same
vocabulary as ``config._UPDATE_PREF_FIELDS`` — one naming scheme across config,
scanner and scope.

Frozen deliberately. The high-performance pipeline reads scope from
ThreadPoolExecutor workers, and under free-threaded 3.14 an immutable value is
safe to share without a lock.

Preview components (``constants.PREVIEW_DLLS``) need their own token *on top of*
their technology group. This mirrors ``utils.is_dll_update_enabled`` so a
narrowed run can never enrol the user in replacing a DLL AMD ships as a preview.
"""

from __future__ import annotations

from dlss_updater.constants import DLL_GROUPS, PREVIEW_DLL_PREFERENCE

UpdateScope = frozenset[str]

#: Tokens that unlock pre-release components. NOT part of ``all_technologies()``.
PREVIEW_TOKENS: frozenset[str] = frozenset(PREVIEW_DLL_PREFERENCE.values())


def all_technologies() -> UpdateScope:
    """Every technology group available on this platform.

    Platform-conditional: ``DirectStorage`` is absent from ``DLL_GROUPS`` off
    Windows. Preview opt-ins are excluded — "everything" must never mean
    "including the preview".
    """
    return frozenset(DLL_GROUPS)


def from_preferences() -> UpdateScope:
    """The scope implied by the user's saved Update Preferences."""
    from dlss_updater.config import config_manager

    tokens = {g for g in DLL_GROUPS if config_manager.get_update_preference(g)}
    tokens |= {t for t in PREVIEW_TOKENS if config_manager.get_update_preference(t)}
    return frozenset(tokens)


def allows(scope: UpdateScope, dll_name: str) -> bool:
    """Whether ``dll_name`` may be updated under ``scope``."""
    from dlss_updater.utils import get_dll_technology_group

    name = dll_name.lower()
    group = get_dll_technology_group(name)
    if group is None or group not in scope:
        return False

    preview_token = PREVIEW_DLL_PREFERENCE.get(name)
    if preview_token is not None and preview_token not in scope:
        return False

    return True
