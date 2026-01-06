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
from enum import StrEnum

# =============================================================================
# Constants
# =============================================================================

# Maximum number of paths (sub-folders) allowed per launcher
MAX_PATHS_PER_LAUNCHER = 5
from datetime import datetime


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
    scan_results: dict[str, list[str]]  # launcher -> DLL paths
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
    updated_games: list[str]
    skipped_games: list[str]
    errors: list[dict[str, str]]
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
    steam_app_id: int | None = None
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
    current_version: str | None = None
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
    original_version: str | None = None
    backup_created_at: datetime = msgspec.field(default_factory=datetime.now)
    is_active: bool = True


class GameDLLBackup(msgspec.Struct):
    """
    Extended DLL backup model that includes game_id and dll_type for filtering.

    Used for per-game backup restore functionality where we need to filter
    and group backups by game and DLL type.
    """
    id: int
    game_dll_id: int
    game_id: int
    game_name: str
    dll_type: str
    dll_filename: str
    backup_path: str
    backup_size: int
    original_version: str | None = None
    backup_created_at: datetime = msgspec.field(default_factory=datetime.now)
    is_active: bool = True


class GameBackupSummary(msgspec.Struct):
    """
    Summary of backups for a specific game - for UI state checks.

    Provides aggregated backup information for a game including count,
    total size, and date range of backups.
    """
    game_id: int
    game_name: str
    backup_count: int
    total_backup_size: int
    dll_types: list[str]
    oldest_backup: datetime | None = None
    newest_backup: datetime | None = None


class GameWithBackupCount(msgspec.Struct):
    """
    Game info with backup count for filter dropdowns.

    Used in UI components to show games that have available backups.
    """
    game_id: int
    game_name: str
    launcher: str
    backup_count: int


class UpdateHistory(msgspec.Struct):
    """
    Update history database model.

    Tracks the history of DLL updates for analytics.
    """
    id: int
    game_dll_id: int
    success: bool
    from_version: str | None = None
    to_version: str | None = None
    updated_at: datetime = msgspec.field(default_factory=datetime.now)


class SteamImage(msgspec.Struct):
    """
    Steam image cache database model.

    Caches Steam CDN image URLs and local paths for game headers.
    """
    steam_app_id: int
    image_url: str
    local_path: str | None = None
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
    high_performance_mode: bool = False  # Default OFF - opt-in only


class LauncherPathsConfig(msgspec.Struct):
    """
    Launcher paths configuration.

    Stores custom paths for each game launcher.
    """
    steam_path: str | None = None
    ea_path: str | None = None
    epic_path: str | None = None
    gog_path: str | None = None
    ubisoft_path: str | None = None
    battle_net_path: str | None = None
    xbox_path: str | None = None
    custom_path_1: str | None = None
    custom_path_2: str | None = None
    custom_path_3: str | None = None
    custom_path_4: str | None = None


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
    backup_path: str | None = None
    dll_type: str = "Unknown"


class DLLDiscoveryResult(msgspec.Struct):
    """
    Result from DLL discovery scan.

    Contains information about a discovered DLL and update availability.
    """
    dll_path: str
    launcher: str
    dll_type: str
    current_version: str | None = None
    latest_version: str | None = None
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
    dlls: list[DLLInfo]


# =============================================================================
# High Performance Update Structures
# =============================================================================

class MemoryStatus(msgspec.Struct):
    """System memory status for high-performance mode decisions."""
    total_bytes: int
    available_bytes: int
    percent_used: float
    can_use_aggressive_mode: bool
    recommended_cache_mb: int


class CacheStats(msgspec.Struct):
    """Source DLL cache statistics."""
    dlls_cached: int
    total_size_bytes: int
    cache_hits: int
    cache_misses: int


class BackupEntry(msgspec.Struct):
    """Single backup entry in the manifest."""
    original_path: str
    backup_path: str
    original_size: int
    verified: bool = False


class BatchUpdateResult(msgspec.Struct):
    """Result from high-performance batch update."""
    mode_used: str  # "high_performance" | "standard" | "fallback"
    backups_created: int
    updates_succeeded: int
    updates_failed: int
    updates_skipped: int
    memory_peak_mb: float
    duration_seconds: float
    errors: list[dict[str, str]] = msgspec.field(default_factory=list)
    # Detailed tracking: list of dicts with game_name, dll_name, old_version, new_version
    detailed_updates: list[dict[str, str]] = msgspec.field(default_factory=list)
    detailed_skipped: list[dict[str, str]] = msgspec.field(default_factory=list)


# =============================================================================
# GPU Detection & DLSS Preset Structures
# =============================================================================

class GPUArchitecture(StrEnum):
    """
    NVIDIA GPU architecture generations based on SM (Streaming Multiprocessor) version.

    Used to determine which DLSS preset is optimal for the user's GPU.
    """
    UNKNOWN = "Unknown"
    TURING = "Turing"           # SM 7.5 - RTX 20xx series
    AMPERE = "Ampere"           # SM 8.0/8.6/8.7 - RTX 30xx series
    ADA_LOVELACE = "Ada"        # SM 8.9 - RTX 40xx series
    BLACKWELL = "Blackwell"     # SM 10.0+ - RTX 50xx series


class DLSSPreset(StrEnum):
    """
    DLSS Super Resolution render presets.

    These presets control the internal DLSS algorithm behavior:
    - DEFAULT: Let the driver choose the optimal preset automatically
    - PRESET_K: Lighter preset for RTX 20/30 - ID 11
    - PRESET_L: Balanced preset for RTX 40/50 - ID 12
    - PRESET_M: Heavier preset (more aggressive processing) - ID 13
    """
    DEFAULT = "default"
    PRESET_K = "preset_k"
    PRESET_L = "preset_l"
    PRESET_M = "preset_m"

    @property
    def registry_value(self) -> int | None:
        """Get the Windows registry DWORD value for this preset."""
        mapping = {
            DLSSPreset.DEFAULT: None,  # Delete key to use default
            DLSSPreset.PRESET_K: 11,
            DLSSPreset.PRESET_L: 12,
            DLSSPreset.PRESET_M: 13,
        }
        return mapping.get(self)

    @property
    def dxvk_env_value(self) -> str | None:
        """Get the DXVK-NVAPI environment variable value for Linux/Proton."""
        mapping = {
            DLSSPreset.DEFAULT: None,
            DLSSPreset.PRESET_K: "render_preset_k",
            DLSSPreset.PRESET_L: "render_preset_l",
            DLSSPreset.PRESET_M: "render_preset_m",
        }
        return mapping.get(self)

    @property
    def display_name(self) -> str:
        """Human-readable name for UI display."""
        names = {
            DLSSPreset.DEFAULT: "Default (Auto)",
            DLSSPreset.PRESET_K: "Preset K (RTX 20/30)",
            DLSSPreset.PRESET_L: "Preset L (Heavier - may reduce performance)",
            DLSSPreset.PRESET_M: "Preset M (RTX 40/50)",
        }
        return names.get(self, str(self))


class GPUInfo(msgspec.Struct):
    """
    Detected NVIDIA GPU information.

    Contains architecture details needed for DLSS preset recommendations.
    Used by the GPU detection module to pass GPU info to the UI.
    """
    name: str
    architecture: str  # GPUArchitecture value
    sm_version_major: int
    sm_version_minor: int
    vram_mb: int
    driver_version: str
    recommended_preset: str  # DLSSPreset value
    detection_method: str  # "nvml" | "fallback" | "manual"


class DLSSPresetConfig(msgspec.Struct):
    """
    DLSS preset configuration for persistence.

    Stores user preset preferences and GPU detection results.
    Persisted via ConfigManager in the [DLSSPresets] INI section.
    """
    selected_preset: str = "default"  # DLSSPreset value
    auto_detect_enabled: bool = True
    detected_architecture: str | None = None  # GPUArchitecture value
    last_detection_time: str | None = None  # ISO format datetime
    linux_overlay_enabled: bool = False
