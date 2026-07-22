"""
Service layer for DLSS Updater.

UI code (``dlss_updater/ui_flet``) accesses data through these thin service
modules rather than importing ``db_manager`` / ``backup_manager`` directly, per
the project convention (see CLAUDE.md). Each service function is a thin wrapper
that delegates to the database / backup managers, keeping data-access policy in
one place and the UI decoupled from storage internals.

Submodules are imported explicitly by callers (e.g.
``from dlss_updater.services import backup_service``) so that adding or removing
a service module never risks a package-import failure cascade.
"""

__all__ = ["backup_service"]
