from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.config import PhotoRoot, Settings
from app.models import PhotoAnalysis
from app.services.analyzer import analyze_photo, hamming_distance
from app.storage import AnalysisStore


@dataclass(frozen=True)
class ScanSummary:
    scanned_files: int
    analyzed_files: int
    cached_files: int
    failed_files: int
    grouped_files: int
    ungrouped_files: int
    roots: list[str]
    errors: list[str]


class PhotoScanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.thumbnail_dir = settings.data_dir / "thumbnails"
        self.store = AnalysisStore(settings.data_dir / "snapstack.db")

    def scan(self, root_names: Iterable[str] | None = None) -> dict:
        selected_roots = self._select_roots(root_names)
        errors: list[str] = []
        photos: list[PhotoAnalysis] = []
        scanned_files = 0
        analyzed_files = 0
        cached_files = 0

        for root in selected_roots:
            for path in self._iter_images(root):
                scanned_files += 1
                try:
                    stat = path.stat()
                    cached = self.store.get_valid(path, stat.st_mtime, stat.st_size)
                    thumbnail_exists = cached and (self.thumbnail_dir / f"{cached.thumbnail_id}.jpg").exists()
                    if cached and thumbnail_exists:
                        photos.append(cached)
                        cached_files += 1
                        continue

                    analysis = analyze_photo(path, root.name, self.thumbnail_dir)
                    self.store.upsert(analysis)
                    photos.append(analysis)
                    analyzed_files += 1
                except Exception as exc:
                    errors.append(f"{path}: {exc}")

        groups = self._group_photos(photos)
        grouped_files = sum(len(group["photos"]) for group in groups)
        summary = ScanSummary(
            scanned_files=scanned_files,
            analyzed_files=analyzed_files,
            cached_files=cached_files,
            failed_files=len(errors),
            grouped_files=grouped_files,
            ungrouped_files=max(0, len(photos) - grouped_files),
            roots=[root.name for root in selected_roots],
            errors=errors[:100],
        )
        return {
            "summary": summary.__dict__,
            "groups": groups,
        }

    def _select_roots(self, root_names: Iterable[str] | None) -> list[PhotoRoot]:
        roots_by_name = {root.name: root for root in self.settings.photo_roots}
        requested = [name for name in (root_names or []) if name]
        if not requested:
            return self.settings.photo_roots

        missing = sorted(set(requested) - set(roots_by_name))
        if missing:
            raise ValueError(f"Unknown photo roots: {', '.join(missing)}")
        return [roots_by_name[name] for name in requested]

    def _iter_images(self, root: PhotoRoot) -> Iterable[Path]:
        if not root.path.exists():
            raise ValueError(f"Photo root does not exist: {root.path}")
        for current_dir, _, filenames in os.walk(root.path):
            for filename in filenames:
                path = Path(current_dir) / filename
                if path.suffix.lower() in self.settings.image_extensions:
                    yield path

    def _group_photos(self, photos: list[PhotoAnalysis]) -> list[dict]:
        if len(photos) < 2:
            return []

        disjoint_set = _DisjointSet(len(photos))
        self._connect_burst_candidates(photos, disjoint_set)
        self._connect_global_similar_candidates(photos, disjoint_set)

        grouped: dict[int, list[PhotoAnalysis]] = {}
        for index, photo in enumerate(photos):
            grouped.setdefault(disjoint_set.find(index), []).append(photo)

        groups = [
            self._serialize_group(group_id, group_photos)
            for group_id, group_photos in grouped.items()
            if len(group_photos) > 1
        ]
        groups.sort(key=lambda group: (group["captured_start"] or "", group["group_id"]))
        return groups

    def _connect_burst_candidates(self, photos: list[PhotoAnalysis], disjoint_set: "_DisjointSet") -> None:
        indexed = sorted(
            enumerate(photos),
            key=lambda item: _timestamp_for_sort(item[1]),
        )
        recent: list[tuple[int, PhotoAnalysis]] = []
        burst_threshold = self.settings.hash_distance_threshold + 6

        for index, photo in indexed:
            photo_time = _timestamp_for_sort(photo)
            recent = [
                item
                for item in recent
                if photo_time - _timestamp_for_sort(item[1]) <= self.settings.burst_time_window_seconds
            ]
            for other_index, other in recent:
                if hamming_distance(photo.phash, other.phash) <= burst_threshold:
                    disjoint_set.union(index, other_index)
            recent.append((index, photo))

    def _connect_global_similar_candidates(self, photos: list[PhotoAnalysis], disjoint_set: "_DisjointSet") -> None:
        representatives: list[int] = []
        for index, photo in enumerate(photos):
            for representative_index in representatives:
                representative = photos[representative_index]
                if hamming_distance(photo.phash, representative.phash) <= self.settings.hash_distance_threshold:
                    disjoint_set.union(index, representative_index)
                    break
            else:
                representatives.append(index)

    def _serialize_group(self, group_id: int, photos: list[PhotoAnalysis]) -> dict:
        sorted_photos = sorted(photos, key=lambda photo: (-photo.score, str(photo.path)))
        recommended = _select_recommendations(sorted_photos, self.settings.recommendation_count)
        captured_times = [photo.captured_at for photo in photos if photo.captured_at]
        return {
            "group_id": f"group-{group_id}",
            "count": len(photos),
            "roots": sorted({photo.root_name for photo in photos}),
            "captured_start": min(captured_times).isoformat() if captured_times else None,
            "captured_end": max(captured_times).isoformat() if captured_times else None,
            "recommended": [_serialize_photo(photo) for photo in recommended],
            "photos": [_serialize_photo(photo) for photo in sorted_photos],
        }


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _select_recommendations(photos: list[PhotoAnalysis], count: int) -> list[PhotoAnalysis]:
    return photos[:count]


def _serialize_photo(photo: PhotoAnalysis) -> dict:
    return {
        "path": str(photo.path),
        "basename": photo.basename,
        "root": photo.root_name,
        "width": photo.width,
        "height": photo.height,
        "captured_at": photo.captured_at.isoformat() if photo.captured_at else None,
        "score": photo.score,
        "scores": {
            "sharpness": photo.sharpness_score,
            "exposure": photo.exposure_score,
            "contrast": photo.contrast_score,
            "resolution": photo.resolution_score,
        },
        "thumbnail_url": photo.thumbnail_url,
    }


def _timestamp_for_sort(photo: PhotoAnalysis) -> float:
    if photo.captured_at:
        return photo.captured_at.timestamp()
    return photo.mtime
