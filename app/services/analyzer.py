from __future__ import annotations

import hashlib
from datetime import datetime
from io import BytesIO
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
    """画像を読み込み、特徴量・品質スコア・サムネイルを作成します。
    Reads an image and generates features, quality scores, and a thumbnail.
    """

    stat = path.stat()
    try:
        with Image.open(path) as raw_image:
            image = ImageOps.exif_transpose(raw_image)
            image.load()
            captured_at = _extract_capture_time(raw_image)
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"Cannot read image {path}: {exc}") from exc

    return _finalize_analysis(
        image=image,
        captured_at=captured_at,
        logical_path=path,
        root_name=root_name,
        thumbnail_dir=thumbnail_dir,
        mtime=stat.st_mtime,
        size_bytes=stat.st_size,
    )


def analyze_photo_bytes(
    data: bytes,
    logical_path: Path,
    root_name: str,
    thumbnail_dir: Path,
    *,
    mtime: float,
    size_bytes: int,
) -> PhotoAnalysis:
    """メモリ上の画像バイト列を解析します（WebDAV 等向け）。
    Analyzes in-memory image bytes (e.g. for WebDAV-backed files).
    """

    try:
        with Image.open(BytesIO(data)) as raw_image:
            image = ImageOps.exif_transpose(raw_image)
            image.load()
            captured_at = _extract_capture_time(raw_image)
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"Cannot read image {logical_path}: {exc}") from exc

    return _finalize_analysis(
        image=image,
        captured_at=captured_at,
        logical_path=logical_path,
        root_name=root_name,
        thumbnail_dir=thumbnail_dir,
        mtime=mtime,
        size_bytes=size_bytes,
    )


def _finalize_analysis(
    *,
    image: Image.Image,
    captured_at: datetime | None,
    logical_path: Path,
    root_name: str,
    thumbnail_dir: Path,
    mtime: float,
    size_bytes: int,
) -> PhotoAnalysis:
    rgb_image = image.convert("RGB")
    width, height = rgb_image.size
    phash = str(imagehash.phash(rgb_image))
    thumbnail_id = _thumbnail_id(logical_path, mtime, size_bytes)
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
        path=logical_path,
        root_name=root_name,
        mtime=mtime,
        size_bytes=size_bytes,
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
    """2つのpHashのハミング距離を計算します。
    Calculates the Hamming distance between two perceptual hashes.
    """

    return imagehash.hex_to_hash(left_hash) - imagehash.hex_to_hash(right_hash)


def _extract_capture_time(image: Image.Image) -> datetime | None:
    """EXIFから撮影日時を取り出します。
    Extracts the captured-at timestamp from EXIF metadata.
    """

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
    """軽量な画像品質スコアを計算します。
    Computes lightweight image quality scores for sharpness, exposure, and contrast.
    """

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
    """元画像の状態から安定したサムネイルIDを作ります。
    Creates a stable thumbnail ID from the source image state.
    """

    digest = hashlib.sha1(f"{path}:{mtime}:{size_bytes}".encode("utf-8")).hexdigest()
    return digest[:24]


def _write_thumbnail(image: Image.Image, destination: Path) -> None:
    """ブラウザ表示用の小さなJPEGサムネイルを書き出します。
    Writes a small JPEG thumbnail for browser display.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    thumbnail = image.copy()
    thumbnail.thumbnail((420, 420))
    thumbnail.save(destination, format="JPEG", quality=82, optimize=True)
