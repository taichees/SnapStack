from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.models import PhotoAnalysis

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None


EXIF_DATETIME_TAGS = (36867, 36868, 306)


def analyze_photo(path: Path, root_name: str, thumbnail_dir: Path) -> PhotoAnalysis:
    stat = path.stat()
    try:
        with Image.open(path) as raw_image:
            image = ImageOps.exif_transpose(raw_image)
            image.load()
            captured_at = _extract_capture_time(raw_image)
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"Cannot read image {path}: {exc}") from exc

    rgb_image = image.convert("RGB")
    width, height = rgb_image.size
    phash = str(imagehash.phash(rgb_image))
    thumbnail_id = _thumbnail_id(path, stat.st_mtime, stat.st_size)
    _write_thumbnail(rgb_image, thumbnail_dir / f"{thumbnail_id}.jpg")

    sharpness_score, exposure_score, contrast_score = _quality_scores(rgb_image)
    megapixels = (width * height) / 1_000_000
    resolution_score = min(1.0, megapixels / 12.0)
    score = (
        sharpness_score * 0.45
        + exposure_score * 0.25
        + contrast_score * 0.20
        + resolution_score * 0.10
    )

    return PhotoAnalysis(
        path=path,
        root_name=root_name,
        mtime=stat.st_mtime,
        size_bytes=stat.st_size,
        width=width,
        height=height,
        captured_at=captured_at,
        phash=phash,
        sharpness_score=round(sharpness_score, 4),
        exposure_score=round(exposure_score, 4),
        contrast_score=round(contrast_score, 4),
        resolution_score=round(resolution_score, 4),
        score=round(score, 4),
        thumbnail_id=thumbnail_id,
    )


def hamming_distance(left_hash: str, right_hash: str) -> int:
    return imagehash.hex_to_hash(left_hash) - imagehash.hex_to_hash(right_hash)


def _extract_capture_time(image: Image.Image) -> datetime | None:
    try:
        exif = image.getexif()
    except (AttributeError, OSError):
        return None

    for tag in EXIF_DATETIME_TAGS:
        value = exif.get(tag)
        if not value:
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(str(value), fmt)
            except ValueError:
                pass
    return None


def _quality_scores(image: Image.Image) -> tuple[float, float, float]:
    gray = image.convert("L").resize((512, 512))
    values = np.asarray(gray, dtype=np.float32) / 255.0

    gradient_x = np.diff(values, axis=1)
    gradient_y = np.diff(values, axis=0)
    gradient_variance = float(np.var(gradient_x) + np.var(gradient_y))
    sharpness_score = min(1.0, gradient_variance / 0.008)

    mean_luma = float(np.mean(values))
    clipped_ratio = float(np.mean((values <= 0.02) | (values >= 0.98)))
    centered_exposure = max(0.0, 1.0 - abs(mean_luma - 0.5) * 2.0)
    exposure_score = max(0.0, centered_exposure * (1.0 - min(0.8, clipped_ratio * 4.0)))

    contrast_score = min(1.0, float(np.std(values)) / 0.25)
    return sharpness_score, exposure_score, contrast_score


def _thumbnail_id(path: Path, mtime: float, size_bytes: int) -> str:
    digest = hashlib.sha1(f"{path}:{mtime}:{size_bytes}".encode("utf-8")).hexdigest()
    return digest[:24]


def _write_thumbnail(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    thumbnail = image.copy()
    thumbnail.thumbnail((420, 420))
    thumbnail.save(destination, format="JPEG", quality=82, optimize=True)
