"""
Discord Rich Presence Manager
Shows DLSS Updater activity status in Discord

Uses pypresence for Discord RPC integration.
Thread-safe singleton pattern for free-threaded Python 3.14+.
"""

import asyncio
import logging
import threading
import time
from typing import Optional, Dict, Any
from enum import Enum

# pypresence import - gracefully handle if not available
try:
    from pypresence import Presence, DiscordNotFound, DiscordError
    PYPRESENCE_AVAILABLE = True
except ImportError:
    PYPRESENCE_AVAILABLE = False
    Presence = None
    DiscordNotFound = Exception
    DiscordError = Exception

from dlss_updater.config import config_manager
from dlss_updater.logger import setup_logger

logger = setup_logger()


class ActivityState(Enum):
    """Current application activity state"""
    IDLE = "idle"
    SCANNING = "scanning"
    UPDATING = "updating"
    RESTORING = "restoring"


class DiscordPresenceManager:
    """
    Manages Discord Rich Presence integration.
    Thread-safe singleton for free-threaded Python 3.14+.
    """
    _instance: Optional['DiscordPresenceManager'] = None
    _lock = threading.Lock()

    # Discord Application Client ID (from Discord Developer Portal)
    DISCORD_CLIENT_ID = "1455864585771942023"

    # Rate limiting
    MIN_UPDATE_INTERVAL = 15.0  # Discord rate limit

    def __new__(cls) -> 'DiscordPresenceManager':
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
        self._rpc: Optional[Presence] = None
        self._connected = False
        self._last_update = 0.0
        self._start_time = int(time.time())

        # Current state
        self._game_count = 0
        self._dll_count = 0
        self._activity_state = ActivityState.IDLE
        self._activity_detail = ""

    @property
    def is_available(self) -> bool:
        """Check if pypresence is installed"""
        return PYPRESENCE_AVAILABLE

    @property
    def is_enabled(self) -> bool:
        """Check if Discord presence is enabled in config"""
        return config_manager.get_discord_presence_enabled()

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to Discord"""
        return self._connected

    async def connect(self) -> bool:
        """
        Connect to Discord RPC.
        Returns True if connected successfully.
        """
        if not PYPRESENCE_AVAILABLE:
            self.logger.warning("pypresence not installed - Discord presence disabled")
            return False

        if not self.is_enabled:
            self.logger.debug("Discord presence not enabled in config")
            return False

        if self._connected:
            return True

        try:
            self._rpc = Presence(self.DISCORD_CLIENT_ID)
            await asyncio.to_thread(self._rpc.connect)
            self._connected = True
            self._start_time = int(time.time())
            self.logger.info("Connected to Discord RPC")

            # Set initial presence
            await self.set_idle(self._game_count, self._dll_count)
            return True

        except DiscordNotFound:
            self.logger.debug("Discord not running - presence disabled")
            self._connected = False
            return False
        except DiscordError as e:
            self.logger.warning(f"Discord RPC error: {e}")
            self._connected = False
            return False
        except Exception as e:
            self.logger.error(f"Failed to connect to Discord: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from Discord RPC"""
        if self._rpc and self._connected:
            try:
                await asyncio.to_thread(self._rpc.close)
            except Exception as e:
                self.logger.debug(f"Error disconnecting from Discord: {e}")
            finally:
                self._connected = False
                self._rpc = None
                self.logger.info("Disconnected from Discord RPC")

    async def _update_presence(
        self,
        state: str,
        details: str = None,
        large_image: str = "dlss_logo",
        large_text: str = "DLSS Updater",
        small_image: str = None,
        small_text: str = None,
    ):
        """
        Update Discord presence with rate limiting.
        """
        if not self._connected or not self._rpc:
            return

        # Rate limiting
        now = time.time()
        if now - self._last_update < self.MIN_UPDATE_INTERVAL:
            self.logger.debug("Skipping presence update (rate limited)")
            return

        try:
            # Build presence data
            presence_data: Dict[str, Any] = {
                "state": state,
                "large_image": large_image,
                "large_text": large_text,
            }

            if details:
                presence_data["details"] = details

            if small_image:
                presence_data["small_image"] = small_image

            if small_text:
                presence_data["small_text"] = small_text

            # Show elapsed time if enabled
            if config_manager.get_discord_show_activity():
                presence_data["start"] = self._start_time

            await asyncio.to_thread(self._rpc.update, **presence_data)
            self._last_update = now
            self.logger.debug(f"Discord presence updated: {state}")

        except Exception as e:
            self.logger.warning(f"Failed to update Discord presence: {e}")
            self._connected = False

    async def set_idle(self, game_count: int = 0, dll_count: int = 0):
        """Set idle state showing game and DLL counts"""
        self._game_count = game_count
        self._dll_count = dll_count
        self._activity_state = ActivityState.IDLE

        if not config_manager.get_discord_show_game_count():
            state = "Managing games"
            details = "Idle"
        else:
            state = f"Managing {game_count} games"
            details = f"{dll_count} DLLs tracked"

        await self._update_presence(state, details)

    async def set_scanning(self, launcher: str = None):
        """Set scanning state"""
        self._activity_state = ActivityState.SCANNING

        if config_manager.get_discord_show_activity():
            state = f"Scanning {launcher}" if launcher else "Scanning for games"
            details = "Looking for DLLs..."
        else:
            state = "Managing games"
            details = None

        await self._update_presence(
            state,
            details,
            small_image="scanning",
            small_text="Scanning",
        )

    async def set_updating(self, dll_type: str = None, progress: int = None):
        """Set updating state"""
        self._activity_state = ActivityState.UPDATING

        if config_manager.get_discord_show_activity():
            if dll_type:
                state = f"Updating {dll_type}"
            else:
                state = "Updating DLLs"

            if progress is not None:
                details = f"{progress}% complete"
            else:
                details = "Processing..."
        else:
            state = "Managing games"
            details = None

        await self._update_presence(
            state,
            details,
            small_image="updating",
            small_text="Updating",
        )

    async def set_restoring(self, game_name: str = None):
        """Set restoring state"""
        self._activity_state = ActivityState.RESTORING

        if config_manager.get_discord_show_activity():
            state = f"Restoring {game_name}" if game_name else "Restoring backups"
            details = "Reverting DLLs..."
        else:
            state = "Managing games"
            details = None

        await self._update_presence(
            state,
            details,
            small_image="restoring",
            small_text="Restoring",
        )

    def update_stats(self, game_count: int = None, dll_count: int = None):
        """Update game/DLL counts (will be reflected in next presence update)"""
        if game_count is not None:
            self._game_count = game_count
        if dll_count is not None:
            self._dll_count = dll_count


# Singleton instance
discord_presence = DiscordPresenceManager()
