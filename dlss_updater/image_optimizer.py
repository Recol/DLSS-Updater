"""
Image optimization utilities for thumbnail generation.

All operations are async/non-blocking using asyncio.to_thread() for CPU-bound
Pillow operations and aiofiles for file I/O.

This module is optimized for Python 3.14 free-threaded builds where the GIL
is disabled, allowing true parallel execution of thumbnail processing.
"""
import asyncio
import io
from pathlib import Path

import aiofiles
from PIL import Image

from .logger import setup_logger

logger = setup_logger()

# Configuration
# Display is 140x140 with ImageFit.COVER - need minimum dimension >= 160 for quality
# Steam headers are 460x215 (landscape), so we scale by HEIGHT to ensure cover works
MIN_DIMENSION = 160  # Minimum size for the shorter dimension (height for landscape)
WEBP_QUALITY = 80  # Good balance of quality and compression


async def create_thumbnail(image_data: bytes) -> bytes:
    """
    Create optimized WebP thumbnail from raw image data.

    Non-blocking: CPU-intensive Pillow work runs in thread pool via to_thread().
    The GIL is released during image operations in Pillow 12.0+.

    For Steam headers (460x215), resizes to ~343x160 to ensure quality
    when displayed at 140x140 with ImageFit.COVER.

    Args:
        image_data: Raw image bytes (JPEG from Steam CDN)

    Returns:
        WebP thumbnail bytes (scaled so min dimension = 160, quality 80)
    """
    return await asyncio.to_thread(_process_thumbnail_sync, image_data)


async def save_thumbnail(image_data: bytes, output_path: Path) -> None:
    """
    Create and save WebP thumbnail to disk.

    Non-blocking:
    - CPU work (Pillow) runs via asyncio.to_thread()
    - File I/O uses aiofiles for async writes

    Args:
        image_data: Raw image bytes (JPEG from Steam CDN)
        output_path: Path to save the WebP thumbnail
    """
    thumb_data = await create_thumbnail(image_data)
    async with aiofiles.open(output_path, 'wb') as f:
        await f.write(thumb_data)
    logger.debug(f"Saved thumbnail: {output_path} ({len(thumb_data)} bytes)")


def _process_thumbnail_sync(image_data: bytes) -> bytes:
    """
    Synchronous thumbnail processing (runs in thread pool).

    This function is designed to be called via asyncio.to_thread() to avoid
    blocking the event loop. Pillow 12.0+ releases the GIL during image
    operations, enabling true parallelism on free-threaded Python.

    For Steam headers (460x215 landscape), we resize so the shorter dimension
    (height) is at least MIN_DIMENSION pixels. This ensures the image looks
    sharp when displayed at 140x140 with ImageFit.COVER.

    Args:
        image_data: Raw image bytes

    Returns:
        WebP thumbnail bytes
    """
    with Image.open(io.BytesIO(image_data)) as img:
        # Convert to RGB if necessary (WebP doesn't support all color modes)
        # Steam headers are typically RGB JPEG, but handle edge cases
        if img.mode in ('RGBA', 'P', 'LA', 'PA'):
            # For images with alpha, convert to RGB with white background
            if img.mode in ('RGBA', 'LA', 'PA'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'PA':
                    img = img.convert('RGBA')
                elif img.mode == 'LA':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert('RGB')
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Calculate new size: scale so the SHORTER dimension = MIN_DIMENSION
        # This ensures ImageFit.COVER has enough pixels to work with
        width, height = img.size
        if width > height:
            # Landscape image (like Steam headers 460x215)
            # Scale by height to ensure cover works
            scale = MIN_DIMENSION / height
        else:
            # Portrait or square image - scale by width
            scale = MIN_DIMENSION / width

        # Only downscale, never upscale
        if scale < 1.0:
            new_width = int(width * scale)
            new_height = int(height * scale)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Save as WebP with quality 80 and method 4 (balanced speed/compression)
        buffer = io.BytesIO()
        img.save(buffer, format='WEBP', quality=WEBP_QUALITY, method=4)
        return buffer.getvalue()
