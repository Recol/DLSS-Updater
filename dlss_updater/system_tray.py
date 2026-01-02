"""
System Tray Manager
Provides system tray icon with context menu for DLSS Updater

Uses pystray for Windows system tray integration.
Runs in a separate thread to not block the Flet event loop.
"""

import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional, Callable

# pystray import - gracefully handle if not available
try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False
    pystray = None
    item = None
    Image = None

from dlss_updater.config import config_manager, resource_path
from dlss_updater.logger import setup_logger

logger = setup_logger()


class SystemTrayManager:
    """
    Manages the system tray icon and menu.
    Thread-safe singleton for free-threaded Python 3.14+.
    """
    _instance: Optional['SystemTrayManager'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'SystemTrayManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self.logger = logger
        self.icon: Optional['pystray.Icon'] = None
        self.tray_thread: Optional[threading.Thread] = None
        self._running = False

        # Callbacks (set by main app)
        self.on_show_window: Optional[Callable] = None
        self.on_scan_games: Optional[Callable] = None
        self.on_update_all: Optional[Callable] = None
        self.on_exit: Optional[Callable] = None

        # State
        self._game_count = 0
        self._dll_count = 0

    @property
    def is_available(self) -> bool:
        """Check if system tray is available (pystray installed)"""
        return PYSTRAY_AVAILABLE

    @property
    def is_enabled(self) -> bool:
        """Check if system tray is enabled in config"""
        return config_manager.get_minimize_to_tray()

    @property
    def is_running(self) -> bool:
        """Check if tray icon is currently running"""
        return self._running and self.icon is not None

    def _load_icon(self) -> Optional['Image.Image']:
        """Load the application icon for the tray"""
        if not PYSTRAY_AVAILABLE:
            return None

        try:
            # Try to load from bundled resources
            icon_paths = [
                resource_path("icon.ico"),
                resource_path("assets/icon.ico"),
                resource_path("dlss_updater/assets/icon.ico"),
                Path(__file__).parent / "assets" / "icon.ico",
                Path(__file__).parent.parent / "assets" / "icon.ico",
            ]

            for path in icon_paths:
                path = Path(path)
                if path.exists():
                    return Image.open(path)

            # Create a simple default icon if no icon file found
            self.logger.warning("No icon file found, creating default tray icon")
            img = Image.new('RGBA', (64, 64), color=(45, 110, 136, 255))  # Primary color
            return img

        except Exception as e:
            self.logger.error(f"Failed to load tray icon: {e}")
            # Create a simple fallback icon
            try:
                img = Image.new('RGBA', (64, 64), color=(45, 110, 136, 255))
                return img
            except Exception:
                return None

    def _create_menu(self) -> 'pystray.Menu':
        """Create the system tray context menu"""
        if not PYSTRAY_AVAILABLE:
            return None

        return pystray.Menu(
            item('DLSS Updater', self._on_show_window, default=True),
            pystray.Menu.SEPARATOR,
            item('Scan for Games', self._on_scan_games),
            item('Update All', self._on_update_all),
            pystray.Menu.SEPARATOR,
            item('Exit', self._on_exit),
        )

    def _on_show_window(self, icon, item):
        """Handle show window menu click"""
        if self.on_show_window:
            # Run callback in asyncio event loop
            try:
                asyncio.get_running_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(self._run_callback_async(self.on_show_window))
                )
            except RuntimeError:
                # No running loop, just call directly
                self.on_show_window()

    def _on_scan_games(self, icon, item):
        """Handle scan games menu click"""
        if self.on_scan_games:
            try:
                asyncio.get_running_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(self._run_callback_async(self.on_scan_games))
                )
            except RuntimeError:
                self.on_scan_games()

    def _on_update_all(self, icon, item):
        """Handle update all menu click"""
        if self.on_update_all:
            try:
                asyncio.get_running_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(self._run_callback_async(self.on_update_all))
                )
            except RuntimeError:
                self.on_update_all()

    def _on_exit(self, icon, item):
        """Handle exit menu click"""
        self.stop()
        if self.on_exit:
            try:
                asyncio.get_running_loop().call_soon_threadsafe(
                    lambda: asyncio.create_task(self._run_callback_async(self.on_exit))
                )
            except RuntimeError:
                self.on_exit()

    async def _run_callback_async(self, callback):
        """Run callback as async if it's a coroutine function"""
        import inspect
        if inspect.iscoroutinefunction(callback):
            await callback()
        else:
            callback()

    def start(self) -> bool:
        """
        Start the system tray icon.
        Returns True if started successfully, False otherwise.
        """
        if not PYSTRAY_AVAILABLE:
            self.logger.warning("pystray not available - system tray disabled")
            return False

        if self._running:
            self.logger.debug("System tray already running")
            return True

        if not self.is_enabled:
            self.logger.debug("System tray not enabled in config")
            return False

        try:
            icon_image = self._load_icon()
            if icon_image is None:
                self.logger.error("Failed to load icon - system tray disabled")
                return False

            menu = self._create_menu()
            self.icon = pystray.Icon(
                "DLSS Updater",
                icon_image,
                "DLSS Updater",
                menu
            )

            # Run icon in separate thread
            self._running = True
            self.tray_thread = threading.Thread(target=self._run_icon, daemon=True)
            self.tray_thread.start()

            self.logger.info("System tray started")
            return True

        except Exception as e:
            self.logger.error(f"Failed to start system tray: {e}")
            self._running = False
            return False

    def _run_icon(self):
        """Run the pystray icon (blocking, runs in separate thread)"""
        try:
            if self.icon:
                self.icon.run()
        except Exception as e:
            self.logger.error(f"System tray error: {e}")
        finally:
            self._running = False

    def stop(self):
        """Stop the system tray icon"""
        if self.icon:
            try:
                self.icon.stop()
            except Exception as e:
                self.logger.debug(f"Error stopping tray icon: {e}")
        self._running = False
        self.icon = None
        self.logger.info("System tray stopped")

    def update_tooltip(self, game_count: int = None, dll_count: int = None):
        """Update the tray icon tooltip with stats"""
        if game_count is not None:
            self._game_count = game_count
        if dll_count is not None:
            self._dll_count = dll_count

        if self.icon:
            self.icon.title = f"DLSS Updater - {self._game_count} games, {self._dll_count} DLLs"

    def show_notification(self, title: str, message: str):
        """
        Show a balloon notification from the tray icon.
        Only shows if notifications are enabled in config.
        """
        if not config_manager.get_show_tray_notifications():
            return

        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception as e:
                self.logger.debug(f"Failed to show tray notification: {e}")


# Singleton instance
system_tray_manager = SystemTrayManager()
