"""
DLSS Updater - Flet UI Entry Point
Async/await-based modern Material Design interface
"""

import sys
import asyncio
import logging
import flet as ft

# Core imports
from dlss_updater.logger import setup_logger
from dlss_updater.utils import check_dependencies, is_admin, run_as_admin
from dlss_updater.ui_flet.views.main_view import MainView


async def main(page: ft.Page):
    """
    Main async entry point for the Flet application

    Args:
        page: The Flet page instance
    """
    # Configure page
    page.title = "DLSS Updater"
    page.window.width = 900
    page.window.height = 700
    page.window.min_width = 700
    page.window.min_height = 500
    page.padding = 0
    page.spacing = 0

    # Set theme to dark by default
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#2E2E2E"

    # Create Material 3 theme with custom colors
    page.theme = ft.Theme(
        color_scheme_seed="#2D6E88",  # Primary teal blue
        use_material3=True,
    )

    page.dark_theme = ft.Theme(
        color_scheme_seed="#2D6E88",
        use_material3=True,
    )

    # Get logger instance
    logger = logging.getLogger("DLSSUpdater")
    logger.info("DLSS Updater (Flet) starting...")

    # Initialize database
    try:
        from dlss_updater.database import db_manager
        logger.info("Initializing database...")
        await db_manager.initialize()
        logger.info("Database initialized successfully")

        # Clean up any duplicate backup entries (one-time cleanup)
        try:
            cleaned = await db_manager.cleanup_duplicate_backups()
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} duplicate backup entries on startup")
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup duplicate backups: {cleanup_error}")
            # Non-critical, continue

        # Clean up any duplicate game entries (one-time cleanup)
        try:
            cleaned = await db_manager.cleanup_duplicate_games()
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} duplicate game entries on startup")
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup duplicate games: {cleanup_error}")
            # Non-critical, continue

    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        # Continue without database - app should still work

    # Update Steam app list if needed (in background)
    try:
        from dlss_updater.steam_integration import update_steam_app_list_if_needed
        asyncio.create_task(update_steam_app_list_if_needed())
    except Exception as e:
        logger.warning(f"Failed to update Steam app list: {e}")
        # Non-critical, continue

    # Create and add main view
    main_view = MainView(page, logger)
    await main_view.initialize()

    # Add to page
    page.add(main_view)
    page.update()

    logger.info("UI initialized successfully")


def check_prerequisites():
    """Check dependencies and admin privileges before launching UI"""
    logger = setup_logger()

    # Check dependencies
    logger.info("Checking dependencies...")
    if not check_dependencies():
        logger.error("Dependency check failed")
        sys.exit(1)

    # Check admin privileges
    if not is_admin():
        logger.warning("Application requires administrator privileges")
        logger.info("Attempting to restart with admin rights...")
        run_as_admin()
        sys.exit(0)

    logger.info("Admin privileges confirmed")

    # Initialize DLL cache after admin check (avoid circular imports)
    try:
        from dlss_updater.dll_repository import initialize_dll_cache

        logger.info("Initializing DLL cache...")
        initialize_dll_cache()
        logger.info("DLL cache initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize DLL cache: {e}", exc_info=True)
        sys.exit(1)

    return logger


if __name__ == "__main__":
    # Check prerequisites before launching UI
    check_prerequisites()

    # Launch Flet app with async main
    ft.app(target=main)
