from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import PhotoRoot, Settings
from app.models import PhotoAnalysis
from app.services.recommendation_policy import RecommendationPolicy, apply_duplicate_score_policy


def _settings(tmp_path: Path, *, priority: str = "nas_first") -> Settings:
    return Settings(
        roots=[PhotoRoot(name="nas", path=tmp_path / "nas")],
        managed_root_names=frozenset({"cloud-sync"}),
        ui_local_prefixes=(tmp_path,),
        data_dir=tmp_path / "data",
        image_extensions={".jpg"},
        hash_distance_threshold=8,
        burst_time_window_seconds=20,
        recommendation_count=3,
        google_oauth_env_ready=False,
        google_drive_scan_enabled=False,
        recommendation_policy=RecommendationPolicy(
            zero_score_for_duplicate_files=True,
            storage_priority=priority,  # type: ignore[arg-type]
            same_file_max_hash_distance=0,
        ),  # noqa: duplicate policy built from priority arg
        cloud_root_names=frozenset({"cloud-sync", "google-drive"}),
    )


def _photo(
    *,
    path: str,
    root: str,
    phash: str = "aa",
    score: float = 0.9,
) -> PhotoAnalysis:
    return PhotoAnalysis(
        path=Path(path),
        root_name=root,
        mtime=0.0,
        size_bytes=100,
        width=100,
        height=100,
        captured_at=datetime(2026, 5, 1, 12, 0, 0),
        phash=phash,
        sharpness_score=score,
        exposure_score=score,
        contrast_score=score,
        resolution_score=score,
        score=score,
        thumbnail_id="t1",
    )


def test_nas_keeps_score_cloud_zeroed_when_nas_first(tmp_path: Path) -> None:
    settings = _settings(tmp_path, priority="nas_first")
    photos = [
        _photo(path="/nas/a.jpg", root="nas", score=0.7),
        _photo(path="/cloud/b.jpg", root="cloud-sync", score=0.95),
    ]
    adjusted = apply_duplicate_score_policy(photos, settings=settings, policy=settings.recommendation_policy)
    by_path = {str(a.photo.path): a for a in adjusted}
    assert by_path["/nas/a.jpg"].photo.score == 0.7
    assert not by_path["/nas/a.jpg"].duplicate_penalized
    assert by_path["/cloud/b.jpg"].photo.score == 0.0
    assert by_path["/cloud/b.jpg"].duplicate_penalized


def test_cloud_keeps_score_when_cloud_first(tmp_path: Path) -> None:
    policy = RecommendationPolicy(
        zero_score_for_duplicate_files=True,
        storage_priority="cloud_first",
        same_file_max_hash_distance=0,
    )
    settings = _settings(tmp_path, priority="cloud_first")
    settings = Settings(
        roots=settings.roots,
        managed_root_names=settings.managed_root_names,
        ui_local_prefixes=settings.ui_local_prefixes,
        data_dir=settings.data_dir,
        image_extensions=settings.image_extensions,
        hash_distance_threshold=settings.hash_distance_threshold,
        burst_time_window_seconds=settings.burst_time_window_seconds,
        recommendation_count=settings.recommendation_count,
        google_oauth_env_ready=settings.google_oauth_env_ready,
        google_drive_scan_enabled=settings.google_drive_scan_enabled,
        recommendation_policy=policy,
        cloud_root_names=settings.cloud_root_names,
    )
    photos = [
        _photo(path="/nas/z.jpg", root="nas", score=0.99),
        _photo(path="/cloud/a.jpg", root="cloud-sync", score=0.5),
    ]
    adjusted = apply_duplicate_score_policy(photos, settings=settings, policy=policy)
    by_path = {str(a.photo.path): a for a in adjusted}
    assert by_path["/cloud/a.jpg"].photo.score == 0.5
    assert by_path["/nas/z.jpg"].photo.score == 0.0


def test_path_and_basename_tiebreak(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    photos = [
        _photo(path="/nas/b.jpg", root="nas", score=0.8),
        _photo(path="/nas/a.jpg", root="nas", score=0.9),
    ]
    adjusted = apply_duplicate_score_policy(photos, settings=settings, policy=settings.recommendation_policy)
    by_path = {str(a.photo.path): a for a in adjusted}
    assert by_path["/nas/a.jpg"].photo.score == 0.9
    assert by_path["/nas/b.jpg"].photo.score == 0.0


def test_disabled_policy_keeps_scores(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    policy = RecommendationPolicy(
        zero_score_for_duplicate_files=False,
        storage_priority="nas_first",
        same_file_max_hash_distance=0,
    )
    photos = [
        _photo(path="/nas/a.jpg", root="nas", score=0.7),
        _photo(path="/cloud/b.jpg", root="cloud-sync", score=0.95),
    ]
    adjusted = apply_duplicate_score_policy(photos, settings=settings, policy=policy)
    assert all(a.photo.score > 0 for a in adjusted)
    assert not any(a.duplicate_penalized for a in adjusted)
