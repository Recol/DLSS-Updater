"""
GPU Detection Module
Provides async-safe NVIDIA GPU detection and architecture identification.

Uses nvidia-ml-py (NVML bindings) for accurate GPU information.
Gracefully falls back when NVML is unavailable (no NVIDIA GPU, driver issues).

Thread-safe for Python 3.14 free-threading.
"""

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from dlss_updater.platform_utils import IS_WINDOWS, IS_LINUX

if TYPE_CHECKING:
    from dlss_updater.models import GPUInfo, GPUArchitecture

logger = logging.getLogger("DLSSUpdater")

# Thread-safety lock for NVML operations
_nvml_lock = threading.Lock()
_nvml_initialized = False

# SM Version to Architecture mapping
# Based on NVIDIA CUDA Compute Capability documentation
COMPUTE_CAP_TO_ARCH: dict[tuple[int, int], str] = {
    (7, 5): "Turing",       # RTX 20xx (e.g., RTX 2080)
    (8, 0): "Ampere",       # A100 datacenter
    (8, 6): "Ampere",       # RTX 30xx (e.g., RTX 3080)
    (8, 7): "Ampere",       # RTX 30xx Mobile
    (8, 9): "Ada",          # RTX 40xx (e.g., RTX 4090)
    (10, 0): "Blackwell",   # RTX 50xx (estimated)
}


def _get_architecture_from_sm(major: int, minor: int) -> str:
    """
    Convert CUDA SM version to architecture name.

    Args:
        major: SM major version (e.g., 8 for SM 8.6)
        minor: SM minor version (e.g., 6 for SM 8.6)

    Returns:
        Architecture name string (e.g., "Ampere")
    """
    # Direct lookup
    arch = COMPUTE_CAP_TO_ARCH.get((major, minor))
    if arch:
        return arch

    # Fallback for future architectures
    sm = major * 10 + minor
    if sm >= 100:
        return "Blackwell"
    elif sm >= 89:
        return "Ada"
    elif sm >= 80:
        return "Ampere"
    elif sm >= 75:
        return "Turing"

    return "Unknown"


async def detect_nvidia_gpu() -> "GPUInfo | None":
    """
    Detect NVIDIA GPU and return architecture information.

    Returns:
        GPUInfo struct with GPU details, or None if no NVIDIA GPU found.

    Uses asyncio.to_thread() to avoid blocking the event loop.
    Thread-safe for concurrent calls.
    """
    return await asyncio.to_thread(_detect_nvidia_gpu_sync)


def _detect_nvidia_gpu_sync() -> "GPUInfo | None":
    """
    Synchronous GPU detection (runs in thread pool).

    Uses NVML to query:
    - GPU name
    - Compute capability (SM version)
    - VRAM size
    - Driver version
    """
    global _nvml_initialized

    from dlss_updater.models import GPUInfo

    try:
        import pynvml
    except ImportError:
        logger.warning("nvidia-ml-py not installed - GPU detection unavailable")
        return None

    with _nvml_lock:
        try:
            if not _nvml_initialized:
                pynvml.nvmlInit()
                _nvml_initialized = True

            # Get device count
            device_count = pynvml.nvmlDeviceGetCount()
            if device_count == 0:
                logger.info("No NVIDIA GPUs detected")
                return None

            # Get first GPU (primary display adapter)
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)

            # Query GPU properties
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")

            # Get compute capability (SM version)
            major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)

            # Get VRAM
            memory_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            vram_mb = memory_info.total // (1024 * 1024)

            # Get driver version
            driver_version = pynvml.nvmlSystemGetDriverVersion()
            if isinstance(driver_version, bytes):
                driver_version = driver_version.decode("utf-8")

            # Determine architecture
            architecture = _get_architecture_from_sm(major, minor)

            logger.info(
                f"Detected GPU: {name} ({architecture}, SM {major}.{minor}), "
                f"VRAM: {vram_mb}MB, Driver: {driver_version}"
            )

            return GPUInfo(
                name=name,
                architecture=architecture,
                sm_version_major=major,
                sm_version_minor=minor,
                vram_mb=vram_mb,
                driver_version=driver_version,
                detection_method="nvml",
            )

        except pynvml.NVMLError as e:
            logger.error(f"NVML error during GPU detection: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during GPU detection: {e}", exc_info=True)
            return None


async def is_nvidia_gpu_present() -> bool:
    """
    Quick check if any NVIDIA GPU is present.

    Returns:
        True if NVIDIA GPU detected, False otherwise.

    Faster than full detection - use for feature flag checks.
    """
    return await asyncio.to_thread(_is_nvidia_gpu_present_sync)


def _is_nvidia_gpu_present_sync() -> bool:
    """Synchronous check for NVIDIA GPU presence."""
    global _nvml_initialized

    try:
        import pynvml
    except ImportError:
        return False

    with _nvml_lock:
        try:
            if not _nvml_initialized:
                pynvml.nvmlInit()
                _nvml_initialized = True

            device_count = pynvml.nvmlDeviceGetCount()
            return device_count > 0

        except Exception:
            return False


async def cleanup_nvml() -> None:
    """
    Cleanup NVML resources on application exit.

    Should be called during application shutdown.
    """
    global _nvml_initialized

    def _shutdown():
        global _nvml_initialized
        with _nvml_lock:
            if _nvml_initialized:
                try:
                    import pynvml
                    pynvml.nvmlShutdown()
                    _nvml_initialized = False
                    logger.debug("NVML shutdown complete")
                except Exception as e:
                    logger.warning(f"NVML shutdown error: {e}")

    await asyncio.to_thread(_shutdown)
