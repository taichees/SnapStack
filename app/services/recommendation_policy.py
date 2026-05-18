from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.config import Settings

from app.models import PhotoAnalysis
from app.services.analyzer import hamming_distance

StoragePriority = Literal["nas_first", "cloud_first"]


@dataclass(frozen=True)
class RecommendationPolicy:
    zero_score_for_duplicate_files: bool
    storage_priority: StoragePriority
    same_file_max_hash_distance: int


@dataclass(frozen=True)
class AdjustedPhoto:
    photo: PhotoAnalysis
    duplicate_penalized: bool = False


def storage_tier(photo: PhotoAnalysis, settings: Settings) -> int:
    """0 = NAS（ローカル NAS ルート）、1 = Cloud。"""
    if str(photo.path).startswith("/__gdrive__/"):
        return 1
    if photo.root_name in settings.cloud_root_names:
        return 1
    return 0


def duplicate_sort_key(photo: PhotoAnalysis, settings: Settings, policy: RecommendationPolicy) -> tuple:
    tier = storage_tier(photo, settings)
    if policy.storage_priority == "nas_first":
        tier_key = tier
    else:
        tier_key = -tier
    return (tier_key, str(photo.path).lower(), photo.basename.lower())


def _duplicate_clusters(photos: list[PhotoAnalysis], max_hash_distance: int) -> list[list[int]]:
    size = len(photos)
    parent = list(range(size))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(size):
        for j in range(i + 1, size):
            if hamming_distance(photos[i].phash, photos[j].phash) <= max_hash_distance:
                union(i, j)

    buckets: dict[int, list[int]] = {}
    for i in range(size):
        buckets.setdefault(find(i), []).append(i)
    return [indices for indices in buckets.values() if len(indices) > 1]


def apply_duplicate_score_policy(
    photos: list[PhotoAnalysis],
    *,
    settings: Settings,
    policy: RecommendationPolicy,
) -> list[AdjustedPhoto]:
    """同一ファイル群のうち、並び順で後ろの写真の総合スコアを 0 にする（設定が有効な場合）。"""
    if not photos:
        return []
    if not policy.zero_score_for_duplicate_files:
        return [AdjustedPhoto(photo=p) for p in photos]

    penalized: set[int] = set()
    indexed = list(enumerate(photos))
    for cluster in _duplicate_clusters(photos, policy.same_file_max_hash_distance):
        ordered = sorted(
            cluster,
            key=lambda idx: duplicate_sort_key(photos[idx], settings, policy),
        )
        for idx in ordered[1:]:
            penalized.add(idx)

    result: list[AdjustedPhoto] = []
    for idx, photo in indexed:
        if idx in penalized:
            result.append(
                AdjustedPhoto(
                    photo=replace(photo, score=0.0),
                    duplicate_penalized=True,
                )
            )
        else:
            result.append(AdjustedPhoto(photo=photo))
    return result
