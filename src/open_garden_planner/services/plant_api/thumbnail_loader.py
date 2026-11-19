"""Thumbnail loader for plant profile images (US-G2, issue #317).

Fetches plant thumbnails off-thread and caches them on disk. Follows the
``climate_service.py`` cache pattern: ``QStandardPaths.AppLocalDataLocation``
+ subdirectory.

The loader is Qt-free (uses ``requests`` directly) so it can be unit-tested
without a QApplication and reused off the UI thread.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from open_garden_planner.services.image_download import (
    THUMBNAIL_MAX_BYTES,
    THUMBNAIL_MAX_PIXELS,
    decode_image,
    fetch_image,
    thumbnail_png,
)

logger = logging.getLogger(__name__)

_CACHE_DIR_NAME = "plant_thumbs"
_TIMEOUT_SECONDS = 10
_CACHE_MAX_BYTES = 24 * 1024 * 1024  # worst-case 2048² RGBA PNG plus encoding overhead
_PHOTO_EDGE = 512
_PHOTO_PREFIX = "data:image/png;base64,"
_PHOTO_MAX_ENCODED = 2 * 1024 * 1024


def import_plant_photo(path: Path) -> str:
    """Normalize a chosen image into a portable, bounded library photo."""
    with path.open("rb") as stream:
        content = stream.read(THUMBNAIL_MAX_BYTES + 1)
    if len(content) > THUMBNAIL_MAX_BYTES:
        raise ValueError("Photo exceeds the permitted byte limit")
    image = ImageOps.exif_transpose(decode_image(content, max_pixels=THUMBNAIL_MAX_PIXELS))
    image.thumbnail((_PHOTO_EDGE, _PHOTO_EDGE), Image.Resampling.LANCZOS)
    return _PHOTO_PREFIX + base64.b64encode(thumbnail_png(image)).decode("ascii")


def embedded_photo_bytes(url: str) -> bytes | None:
    """Read the editor's PNG format without accepting arbitrary data URLs."""
    if not url.startswith(_PHOTO_PREFIX) or len(url) > _PHOTO_MAX_ENCODED:
        return None
    try:
        content = base64.b64decode(url[len(_PHOTO_PREFIX):], validate=True)
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        decode_image(content, max_pixels=_PHOTO_EDGE ** 2, max_edge=_PHOTO_EDGE)
    except (binascii.Error, ValueError, OSError, SyntaxError, Image.DecompressionBombError):
        return None
    return content


def get_cache_dir() -> Path:
    """Return the thumbnail cache directory, creating it if needed."""
    from PyQt6.QtCore import QStandardPaths

    app_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    cache_dir = Path(app_data) / _CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_key(url: str) -> str:
    """Generate a filesystem-safe cache key from a URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _cache_path(url: str) -> Path:
    """Return the cache file path for a given URL."""
    return get_cache_dir() / f"{_cache_key(url)}.png"


def is_cached(url: str) -> bool:
    """Check if a thumbnail is already cached on disk."""
    return get_cached_thumbnail(url) is not None


def get_cached_thumbnail(url: str) -> Path | None:
    """Return the cached thumbnail path if it exists, else None."""
    try:
        path = _cache_path(url)
        exists = path.exists()
    except OSError:
        return None
    if exists:
        try:
            if path.stat().st_size > _CACHE_MAX_BYTES:
                raise ValueError("Oversized cached thumbnail")
            content = path.read_bytes()
            image = decode_image(content, max_pixels=THUMBNAIL_MAX_PIXELS)
            if not content.startswith(b"\x89PNG\r\n\x1a\n") or max(image.size) > 2048:
                _publish_cache(path, thumbnail_png(image))
            return path
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Cannot remove an invalid cached thumbnail")
    return None


def _publish_cache(path: Path, content: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".thumb-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def fetch_thumbnail(url: str) -> Path | None:
    """Fetch a thumbnail from a URL and cache it on disk.

    Returns the cache path on success, None on failure (network error,
    invalid image, etc.). Never raises — failures are logged and return
    None so the caller can show a placeholder.
    """
    if not url:
        return None

    try:
        cached = get_cached_thumbnail(url)
        if cached is not None:
            return cached
        if url.startswith("data:"):
            content = embedded_photo_bytes(url)
            if content is None:
                return None
        else:
            image = fetch_image(url, max_bytes=THUMBNAIL_MAX_BYTES,
                                max_pixels=THUMBNAIL_MAX_PIXELS, timeout=_TIMEOUT_SECONDS)
            content = thumbnail_png(image)
        path = _cache_path(url)
        _publish_cache(path, content)
        return path
    except Exception:
        logger.warning("Thumbnail download or decoding failed")
        return None


def clear_cache() -> int:
    """Clear all cached thumbnails. Returns the number of files removed."""
    cache_dir = get_cache_dir()
    if not cache_dir.exists():
        return 0
    count = 0
    for f in cache_dir.glob("*.png"):
        f.unlink()
        count += 1
    return count
