"""
msgspec-based data models for type-safe, high-performance serialization.
All application data structures use msgspec.Struct for validation and speed.

This module provides:
- Custom datetime encoder/decoder hooks for msgspec
- All data models as msgspec.Struct definitions
- Convenience functions for JSON encoding/decoding
- Type-safe, validated data structures throughout the application

Performance benefits:
- 5-10x faster JSON serialization vs standard library
- Runtime validation of all data structures
- Better IDE support with type hints
- Reduced memory usage for large datasets
"""

import msgspec
from datetime import datetime
from typing import Optional, List, Dict


# =============================================================================
# Custom Encoder/Decoder Hooks
# =============================================================================

def datetime_enc_hook(obj):
    """
    Custom encoder hook for datetime objects.
    Converts datetime to ISO 8601 string format.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise NotImplementedError(f"Cannot encode {type(obj)}")


def datetime_dec_hook(type, obj):
    """
    Custom decoder hook for datetime objects.
    Converts ISO 8601 string back to datetime.
    """
    if type is datetime:
        return datetime.fromisoformat(obj)
    raise NotImplementedError(f"Cannot decode {type}")


# =============================================================================
# Global Encoders/Decoders
# =============================================================================

# Create encoders/decoders with custom hooks for datetime handling
json_encoder = msgspec.json.Encoder(enc_hook=datetime_enc_hook)
json_decoder = msgspec.json.Decoder(dec_hook=datetime_dec_hook)


# =============================================================================
# Convenience Functions
# =============================================================================

def encode_json(obj) -> bytes:
    """
    Encode object to JSON bytes using msgspec.

    Args:
        obj: Any msgspec.Struct or serializable object

    Returns:
        JSON as bytes

    Example:
        >>> cache = ScanCacheData(scan_results={}, timestamp="2025-01-01T12:00:00")
        >>> json_bytes = encode_json(cache)
    """
    return json_encoder.encode(obj)


def decode_json(data: bytes, type=None):
    """
    Decode JSON bytes to object using msgspec.

    Args:
        data: JSON as bytes
        type: Optional msgspec.Struct type for validation

    Returns:
        Decoded object (validated if type provided)

    Example:
        >>> data = b'{"scan_results": {}, "timestamp": "2025-01-01T12:00:00"}'
        >>> cache = decode_json(data, type=ScanCacheData)
    """
    if type:
        decoder = msgspec.json.Decoder(type, dec_hook=datetime_dec_hook)
        return decoder.decode(data)
    return json_decoder.decode(data)


def format_json(data: bytes, indent: int = 2) -> bytes:
    """
    Format JSON with indentation for pretty-printing.

    Args:
        data: JSON as bytes
        indent: Number of spaces per indentation level

    Returns:
        Formatted JSON as bytes

    Example:
        >>> formatted = format_json(encode_json(cache), indent=2)
    """
    return msgspec.json.format(data, indent=indent)


# =============================================================================
# Cache & Runtime Structures (Phase 2)
# =============================================================================

class ScanCacheData(msgspec.Struct):
    """
    Scan results cache for persistence across app restarts.

    Stores the mapping of launcher names to lists of DLL paths found during scan.
    Includes timestamp to track when the scan was performed.
    """
    scan_results: Dict[str, List[str]]  # launcher -> DLL paths
    timestamp: str  # ISO format datetime string

    def __post_init__(self):
        """Validate timestamp format on instantiation"""
        try:
            datetime.fromisoformat(self.timestamp)
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format: {self.timestamp}") from e


class UpdateProgress(msgspec.Struct):
    """
    Progress information for update operations.

    Used for UI progress updates during scan/update operations.
    """
    current: int
    total: int
    message: str
    percentage: int

    def __post_init__(self):
        """Validate percentage range"""
        if not 0 <= self.percentage <= 100:
            raise ValueError(f"Percentage must be 0-100, got {self.percentage}")


class UpdateResult(msgspec.Struct):
    """
    Results from update operation.

    Contains summary of what was updated, skipped, and any errors encountered.
    """
    updated_games: List[str]
    skipped_games: List[str]
    errors: List[Dict[str, str]]
    backup_created: bool
    total_processed: int


# =============================================================================
# Database Models (Phase 3)
# =============================================================================

class Game(msgspec.Struct):
    """
    Game database model.

    Represents a game discovered during scanning with metadata.
    """
    id: int
    name: str
    path: str
    launcher: str
    steam_app_id: Optional[int] = None
    last_scanned: datetime = msgspec.field(default_factory=datetime.now)
    created_at: datetime = msgspec.field(default_factory=datetime.now)


class GameDLL(msgspec.Struct):
    """
    Game DLL database model.

    Represents a specific DLL file found in a game.
    """
    id: int
    game_id: int
    dll_type: str
    dll_filename: str
    dll_path: str
    current_version: Optional[str] = None
    detected_at: datetime = msgspec.field(default_factory=datetime.now)


class DLLBackup(msgspec.Struct):
    """
    DLL backup database model.

    Represents a backup of a DLL file for restore capability.
    """
    id: int
    game_dll_id: int
    game_name: str
    dll_filename: str
    backup_path: str
    backup_size: int
    original_version: Optional[str] = None
    backup_created_at: datetime = msgspec.field(default_factory=datetime.now)
    is_active: bool = True


class UpdateHistory(msgspec.Struct):
    """
    Update history database model.

    Tracks the history of DLL updates for analytics.
    """
    id: int
    game_dll_id: int
    success: bool
    from_version: Optional[str] = None
    to_version: Optional[str] = None
    updated_at: datetime = msgspec.field(default_factory=datetime.now)


class SteamImage(msgspec.Struct):
    """
    Steam image cache database model.

    Caches Steam CDN image URLs and local paths for game headers.
    """
    steam_app_id: int
    image_url: str
    local_path: Optional[str] = None
    cached_at: datetime = msgspec.field(default_factory=datetime.now)
    fetch_failed: bool = False


# =============================================================================
# Configuration Structures (Phase 4)
# =============================================================================

class UpdatePreferencesConfig(msgspec.Struct):
    """
    Update preferences configuration.

    Controls which types of DLLs should be updated.
    """
    update_dlss: bool = True
    update_direct_storage: bool = True
    update_xess: bool = True
    update_fsr: bool = True
    update_streamline: bool = True
    create_backups: bool = True


class LauncherPathsConfig(msgspec.Struct):
    """
    Launcher paths configuration.

    Stores custom paths for each game launcher.
    """
    steam_path: Optional[str] = None
    ea_path: Optional[str] = None
    epic_path: Optional[str] = None
    gog_path: Optional[str] = None
    ubisoft_path: Optional[str] = None
    battle_net_path: Optional[str] = None
    xbox_path: Optional[str] = None
    custom_path_1: Optional[str] = None
    custom_path_2: Optional[str] = None
    custom_path_3: Optional[str] = None
    custom_path_4: Optional[str] = None


class PerformanceConfig(msgspec.Struct):
    """
    Performance settings configuration.

    Controls thread pool sizes and other performance tuning.
    """
    max_worker_threads: int = 8

    def __post_init__(self):
        """Validate worker thread count"""
        if not 1 <= self.max_worker_threads <= 32:
            raise ValueError(f"max_worker_threads must be between 1 and 32, got {self.max_worker_threads}")


# =============================================================================
# Scanner/Updater Structures (Phase 5)
# =============================================================================

class ProcessedDLLResult(msgspec.Struct):
    """
    Result from DLL update operation.

    Returned by update_dll() function to indicate success/failure.
    """
    success: bool
    backup_path: Optional[str] = None
    dll_type: str = "Unknown"


class DLLDiscoveryResult(msgspec.Struct):
    """
    Result from DLL discovery scan.

    Contains information about a discovered DLL and update availability.
    """
    dll_path: str
    launcher: str
    dll_type: str
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    update_available: bool = False


# =============================================================================
# UI Data Structures (Phase 6)
# =============================================================================

class DLLInfo(msgspec.Struct):
    """
    DLL information for UI display.

    Displayed in game cards showing DLL version info.
    """
    dll_type: str
    current_version: str
    latest_version: str
    update_available: bool


class GameCardData(msgspec.Struct):
    """
    Game card data for launcher cards.

    Contains game information and associated DLLs for UI display.
    """
    name: str
    path: str
    dlls: List[DLLInfo]
