from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.models import PhotoAnalysis


SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    path TEXT PRIMARY KEY,
    root_name TEXT NOT NULL,
    mtime REAL NOT NULL,
    size_bytes INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    captured_at TEXT,
    phash TEXT NOT NULL,
    sharpness_score REAL NOT NULL,
    exposure_score REAL NOT NULL,
    contrast_score REAL NOT NULL,
    resolution_score REAL NOT NULL,
    score REAL NOT NULL,
    thumbnail_id TEXT NOT NULL
);
"""


class AnalysisStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def get_valid(self, path: Path, mtime: float, size_bytes: int) -> PhotoAnalysis | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM photos WHERE path = ? AND mtime = ? AND size_bytes = ?",
                (str(path), mtime, size_bytes),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_analysis(row)

    def upsert(self, analysis: PhotoAnalysis) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO photos (
                    path, root_name, mtime, size_bytes, width, height, captured_at,
                    phash, sharpness_score, exposure_score, contrast_score,
                    resolution_score, score, thumbnail_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    root_name = excluded.root_name,
                    mtime = excluded.mtime,
                    size_bytes = excluded.size_bytes,
                    width = excluded.width,
                    height = excluded.height,
                    captured_at = excluded.captured_at,
                    phash = excluded.phash,
                    sharpness_score = excluded.sharpness_score,
                    exposure_score = excluded.exposure_score,
                    contrast_score = excluded.contrast_score,
                    resolution_score = excluded.resolution_score,
                    score = excluded.score,
                    thumbnail_id = excluded.thumbnail_id
                """,
                (
                    str(analysis.path),
                    analysis.root_name,
                    analysis.mtime,
                    analysis.size_bytes,
                    analysis.width,
                    analysis.height,
                    analysis.captured_at.isoformat() if analysis.captured_at else None,
                    analysis.phash,
                    analysis.sharpness_score,
                    analysis.exposure_score,
                    analysis.contrast_score,
                    analysis.resolution_score,
                    analysis.score,
                    analysis.thumbnail_id,
                ),
            )

    def _row_to_analysis(self, row: sqlite3.Row) -> PhotoAnalysis:
        captured_at = datetime.fromisoformat(row["captured_at"]) if row["captured_at"] else None
        return PhotoAnalysis(
            path=Path(row["path"]),
            root_name=row["root_name"],
            mtime=row["mtime"],
            size_bytes=row["size_bytes"],
            width=row["width"],
            height=row["height"],
            captured_at=captured_at,
            phash=row["phash"],
            sharpness_score=row["sharpness_score"],
            exposure_score=row["exposure_score"],
            contrast_score=row["contrast_score"],
            resolution_score=row["resolution_score"],
            score=row["score"],
            thumbnail_id=row["thumbnail_id"],
        )
