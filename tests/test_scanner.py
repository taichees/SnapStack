from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from app.config import PhotoRoot, Settings
from app.models import PhotoAnalysis
from app.services.scanner import PhotoScanner


def test_selects_multiple_requested_roots(tmp_path: Path) -> None:
    settings = _settings(tmp_path, roots=["camera", "archive"])
    scanner = PhotoScanner(settings)

    selected = scanner._select_roots(["archive", "camera"])

    assert [root.name for root in selected] == ["archive", "camera"]


def test_groups_burst_photos_and_recommends_top_three(tmp_path: Path) -> None:
    settings = _settings(tmp_path, roots=["camera"])
    scanner = PhotoScanner(settings)
    start = datetime(2026, 5, 12, 10, 0, 0)
    photos = [
        _photo("a.jpg", "0000000000000000", 0.60, start),
        _photo("b.jpg", "0000000000000001", 0.99, start + timedelta(seconds=1)),
        _photo("c.jpg", "0000000000000003", 0.80, start + timedelta(seconds=2)),
        _photo("d.jpg", "0000000000000007", 0.70, start + timedelta(seconds=3)),
        _photo("far.jpg", "ffffffffffffffff", 1.00, start + timedelta(minutes=10)),
    ]

    groups = scanner._group_photos(photos)

    assert len(groups) == 1
    assert groups[0]["count"] == 4
    assert [photo["basename"] for photo in groups[0]["recommended"]] == ["b.jpg", "c.jpg", "d.jpg"]


def _settings(tmp_path: Path, roots: list[str]) -> Settings:
    return Settings(
        roots=[PhotoRoot(name=root, path=tmp_path / root) for root in roots],
        managed_root_names=frozenset(),
        ui_local_prefixes=(tmp_path,),
        data_dir=tmp_path / "data",
        image_extensions={".jpg"},
        hash_distance_threshold=8,
        burst_time_window_seconds=20,
        recommendation_count=3,
    )


def _photo(name: str, phash: str, score: float, captured_at: datetime) -> PhotoAnalysis:
    return PhotoAnalysis(
        path=Path("/photos/camera") / name,
        root_name="camera",
        mtime=captured_at.timestamp(),
        size_bytes=100,
        width=4000,
        height=3000,
        captured_at=captured_at,
        phash=phash,
        sharpness_score=score,
        exposure_score=score,
        contrast_score=score,
        resolution_score=score,
        score=score,
        thumbnail_id=name.replace(".", "-"),
    )
