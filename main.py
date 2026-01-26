"""
DLSS Updater - Flet UI Entry Point
Async/await-based modern Material Design interface
"""

import sys
import os
import sysconfig

# Free-threaded Python 3.14 GIL configuration
# Note: Some C extensions (msgpack) may re-enable GIL when loaded
_is_free_threaded = bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
if _is_free_threaded:
    # Set environment variable for child processes and to indicate intent
    os.environ['PYTHON_GIL'] = '0'

# Install faster event loop based on platform (must be done before any asyncio usage)
if sys.platform == 'win32':
    try:
        import winloop
        winloop.install()
    except ImportError:
        pass  # winloop not installed, use default event loop
elif sys.platform == 'linux':
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass  # uvloop not installed, use default event loop

import asyncio
import logging
import flet as ft

# Core imports
from dlss_updater.logger import setup_logger
from dlss_updater.utils import check_dependencies, is_admin, run_as_admin  # Admin functions used on Windows only
from dlss_updater.platform_utils import IS_WINDOWS, IS_LINUX
from dlss_updater.ui_flet.views.main_view import MainView
from dlss_updater.task_registry import register_task


def ensure_flet_directories():
    """
    Ensure Flet framework directories exist before initialization.

    On Linux (especially Flatpak), Flet expects ~/.flet/bin to exist but may not
    be able to create it due to sandbox restrictions. We create it proactively
    to prevent FileNotFoundError during Flet initialization.

    See: https://github.com/Recol/DLSS-Updater/issues/122
         https://github.com/Recol/DLSS-Updater/issues/127
    """
    if IS_LINUX:
        from pathlib import Path
        flet_bin_dir = Path.home() / ".flet" / "bin"
        try:
            flet_bin_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # Log but don't fail - Flet might still work
            import logging
            logging.getLogger("DLSSUpdater").warning(
                f"Could not create Flet directory {flet_bin_dir}: {e}"
            )


def _detect_initial_theme() -> bool:
    """
    Load theme preference from config or default to dark.

    Note: OS theme detection (darkdetect) was removed due to blocking calls
    that caused GUI freezes on Linux Flatpak/Wayland and Windows with AV software.
    See: https://github.com/Recol/DLSS-Updater/issues/XXX

    Returns:
        True if dark mode should be used, False for light mode
    """
    try:
        from dlss_updater.config import config_manager

        # Load saved theme preference (user override or previous selection)
        theme_pref = config_manager.get("Appearance", "theme", fallback="dark")
        return theme_pref == "dark"

    except Exception:
        return True  # Default to dark on any error


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

    # Detect initial theme (OS preference or user override)
    is_dark = _detect_initial_theme()

    # Set theme based on detection
    page.theme_mode = ft.ThemeMode.DARK if is_dark else ft.ThemeMode.LIGHT
    page.bgcolor = "#2E2E2E" if is_dark else "#FAFBFC"

    # Create Material 3 theme with custom colors
    page.theme = ft.Theme(
        color_scheme_seed="#2D6E88" if is_dark else "#1A5A70",
        use_material3=True,
    )

    page.dark_theme = ft.Theme(
        color_scheme_seed="#2D6E88",
        use_material3=True,
    )

    # Get logger instance
    logger = logging.getLogger("DLSSUpdater")
    logger.info("DLSS Updater (Flet) starting...")

    # Create startup loading overlay with theme-aware colors
    from dlss_updater.ui_flet.theme.colors import MD3Colors
    startup_overlay = ft.Container(
        content=ft.Column(
            controls=[
                ft.ProgressRing(color=MD3Colors.get_primary(is_dark), width=50, height=50),
                ft.Text("Loading...", size=16, color=MD3Colors.get_on_surface(is_dark)),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=20,
        ),
        expand=True,
        alignment=ft.Alignment.CENTER,
        bgcolor=MD3Colors.get_background(is_dark),
    )
    page.add(startup_overlay)
    page.update()

    # Initialize whitelist in background (non-blocking)
    async def init_whitelist():
        """Initialize whitelist asynchronously"""
        try:
            from dlss_updater.whitelist import initialize_whitelist
            await initialize_whitelist()
        except asyncio.CancelledError:
            logger.info("Whitelist initialization cancelled")
            raise
        except Exception as e:
            logger.warning(f"Failed to initialize whitelist: {e}")
            # Non-critical, continue without whitelist

    # Run database and DLL cache initialization in parallel
    async def init_database():
        """Initialize database asynchronously"""
        try:
            from dlss_updater.database import db_manager
            logger.info("Initializing database...")
            await db_manager.initialize()
            logger.info("Database initialized successfully")

            # Run cleanup operations in parallel
            cleanup_tasks = []
            cleanup_tasks.append(db_manager.cleanup_duplicate_backups())
            cleanup_tasks.append(db_manager.cleanup_duplicate_games())

            results = await asyncio.gather(*cleanup_tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"Cleanup task {i} failed: {result}")
                elif result and result > 0:
                    task_name = "backup" if i == 0 else "game"
                    logger.info(f"Cleaned up {result} duplicate {task_name} entries on startup")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}", exc_info=True)
            # Continue without database - app should still work

    async def init_dll_cache():
        """Initialize DLL cache asynchronously with progress notification"""
        snackbar = main_view.get_dll_cache_snackbar()

        try:
            from dlss_updater.dll_repository import initialize_dll_cache_async

            await snackbar.show_initializing()
            logger.info("Initializing DLL cache (async)...")

            async def on_progress(current, total, message):
                await snackbar.update_progress(current, total, message)

            await initialize_dll_cache_async(progress_callback=on_progress)

            logger.info("DLL cache initialized successfully")
            await snackbar.show_complete()

        except asyncio.CancelledError:
            logger.info("DLL cache initialization cancelled")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize DLL cache: {e}", exc_info=True)
            await snackbar.show_error(f"Cache init failed: {str(e)[:40]}")

    async def update_steam_list():
        """Update Steam app list in background"""
        try:
            from dlss_updater.steam_integration import update_steam_app_list_if_needed
            await update_steam_app_list_if_needed()
        except asyncio.CancelledError:
            logger.info("Steam list update cancelled")
            raise
        except Exception as e:
            logger.warning(f"Failed to update Steam app list: {e}")
            # Non-critical, continue

    # Initialize database first (fast operation, needed before UI)
    await init_database()

    # Clear startup overlay and show main UI
    page.controls.clear()
    page.update()

    # Create and add main view (show UI immediately)
    main_view = MainView(page, logger)
    await main_view.initialize()

    # Add to page
    page.add(main_view)
    page.update()

    # Register shutdown handler for graceful cleanup
    # IMPORTANT: Use prevent_close + on_event + destroy pattern for proper process termination
    # page.on_close alone does NOT terminate the Flet/Flutter subprocess, leaving the app running
    # See: https://flet.dev/docs/controls/page/#app-exit-confirmation
    _shutdown_in_progress = False

    async def handle_window_event(e: ft.WindowEvent):
        """Handle window events, particularly close requests."""
        nonlocal _shutdown_in_progress
        if e.type == ft.WindowEventType.CLOSE:
            if _shutdown_in_progress:
                return  # Already shutting down, avoid double-shutdown
            _shutdown_in_progress = True

            try:
                # Run cleanup first
                await main_view.shutdown()
            except Exception as ex:
                logger.error(f"Shutdown error: {ex}")
            finally:
                # CRITICAL: Destroy window to terminate the Flet/Flutter process
                try:
                    await page.window.destroy()
                except Exception:
                    # Force exit if destroy fails
                    import os
                    os._exit(0)

    page.window.prevent_close = True
    page.window.on_event = handle_window_event

    # Debounced resize handler for high-DPI display optimization
    # Prevents excessive layout recalculations during rapid window resizing
    # while maintaining responsive behavior for different screen sizes
    _resize_task: asyncio.Task | None = None
    _resize_debounce_ms = 100  # Wait 100ms after last resize before updating

    async def _debounced_resize_update():
        """Perform actual resize update after debounce delay."""
        await asyncio.sleep(_resize_debounce_ms / 1000)
        # ResponsiveRow handles layout automatically, just trigger update
        if page and hasattr(page, 'update'):
            try:
                page.update()
            except Exception:
                pass  # Page may be closing

    def on_page_resized(e):
        """Debounced resize handler - prevents excessive updates during rapid resizing."""
        nonlocal _resize_task
        # Cancel any pending resize update
        if _resize_task and not _resize_task.done():
            _resize_task.cancel()
        # Schedule new debounced update - register for proper shutdown
        _resize_task = register_task(
            asyncio.create_task(_debounced_resize_update()),
            "resize_debounce"
        )

    page.on_resized = on_page_resized

    logger.info("UI initialized successfully")

    # Now initialize DLL cache, whitelist, and Steam list in background (after UI is visible)
    # This allows the user to see the app immediately while heavy init happens
    # Register tasks for graceful shutdown cancellation
    register_task(asyncio.create_task(init_whitelist()), "init_whitelist")
    register_task(asyncio.create_task(init_dll_cache()), "init_dll_cache")
    register_task(asyncio.create_task(update_steam_list()), "update_steam_list")


def check_prerequisites():
    """Check dependencies and admin privileges before launching UI"""
    logger = setup_logger()

    # Check dependencies
    logger.info("Checking dependencies...")
    if not check_dependencies():
        logger.error("Dependency check failed")
        sys.exit(1)

    # Check admin privileges - Windows requires elevation for writing to game directories
    if IS_WINDOWS:
        if not is_admin():
            logger.warning("Application requires administrator privileges")
            logger.info("Attempting to restart with admin rights...")
            run_as_admin()
            sys.exit(0)
        logger.info("Admin privileges confirmed")
    # Linux: No elevation needed - Flatpak sandboxing handles filesystem permissions

    # DLL cache initialization is now done asynchronously after UI loads
    # to avoid blocking the window from appearing
    logger.info("DLL cache will be initialized after UI loads...")

    return logger


if __name__ == "__main__":
    # Check prerequisites before launching UI
    check_prerequisites()

    # Ensure Flet directories exist (Linux Flatpak fix for Issues #122 & #127)
    ensure_flet_directories()

    # Workaround for Flet bug: is_linux_server() only checks DISPLAY, not WAYLAND_DISPLAY
    # On Wayland-only sessions (e.g., Fedora/Nobara), DISPLAY is not set, causing Flet
    # to incorrectly detect a "headless server" and force web server mode on port 8000
    # See: https://github.com/Recol/DLSS-Updater/issues/122
    import os
    if sys.platform == 'linux':
        if os.environ.get('WAYLAND_DISPLAY') and not os.environ.get('DISPLAY'):
            os.environ['DISPLAY'] = ':0'  # Prevent Flet's web server fallback

    # Launch Flet app with async main - uses ft.run() for Flet 0.80.4+
    ft.run(main)
