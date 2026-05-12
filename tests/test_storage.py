from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.models import PhotoAnalysis
from app.storage import AnalysisStore


def test_delete_missing_for_roots_only_cleans_selected_roots(tmp_path: Path) -> None:
    store = AnalysisStore(tmp_path / "snapstack.db")
    kept = _photo(tmp_path / "camera" / "kept.jpg", "camera")
    deleted = _photo(tmp_path / "camera" / "deleted.jpg", "camera")
    untouched_other_root = _photo(tmp_path / "archive" / "deleted.jpg", "archive")
    for analysis in [kept, deleted, untouched_other_root]:
        store.upsert(analysis)

    deleted_count = store.delete_missing_for_roots(["camera"], {kept.path})

    assert deleted_count == 1
    assert store.get_valid(kept.path, kept.mtime, kept.size_bytes) is not None
    assert store.get_valid(deleted.path, deleted.mtime, deleted.size_bytes) is None
    assert store.get_valid(
        untouched_other_root.path,
        untouched_other_root.mtime,
        untouched_other_root.size_bytes,
    ) is not None


def test_records_last_scan_finished_at(tmp_path: Path) -> None:
    store = AnalysisStore(tmp_path / "snapstack.db")
    started_at = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
    finished_at = datetime(2026, 5, 12, 10, 5, tzinfo=timezone.utc)

    store.record_scan_run(
        started_at=started_at,
        finished_at=finished_at,
        roots=["camera", "archive"],
        scanned_files=10,
        analyzed_files=2,
        cached_files=8,
        deleted_cached_files=1,
        failed_files=0,
    )

    assert store.get_last_scan_finished_at() == finished_at.isoformat()


def _photo(path: Path, root_name: str) -> PhotoAnalysis:
    return PhotoAnalysis(
        path=path,
        root_name=root_name,
        mtime=100.0,
        size_bytes=200,
        width=4000,
        height=3000,
        captured_at=None,
        phash="0000000000000000",
        sharpness_score=0.8,
        exposure_score=0.8,
        contrast_score=0.8,
        resolution_score=1.0,
        score=0.82,
        thumbnail_id=path.stem,
    )
