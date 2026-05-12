from __future__ import annotations

import sqlite3
from datetime import datetime
import json
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

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    roots TEXT NOT NULL,
    scanned_files INTEGER NOT NULL,
    analyzed_files INTEGER NOT NULL,
    cached_files INTEGER NOT NULL,
    deleted_cached_files INTEGER NOT NULL,
    failed_files INTEGER NOT NULL
);
"""


class AnalysisStore:
    def __init__(self, db_path: Path) -> None:
        """解析キャッシュDBを準備します。
        Prepares the SQLite cache database for photo analysis results.
        """

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """行名でアクセスできるSQLite接続を作ります。
        Opens a SQLite connection with named-column row access.
        """

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def get_valid(self, path: Path, mtime: float, size_bytes: int) -> PhotoAnalysis | None:
        """ファイルが未変更ならキャッシュ済み解析結果を返します。
        Returns cached analysis when the file timestamp and size still match.
        """

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM photos WHERE path = ? AND mtime = ? AND size_bytes = ?",
                (str(path), mtime, size_bytes),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_analysis(row)

    def upsert(self, analysis: PhotoAnalysis) -> None:
        """最新の解析結果をキャッシュへ追加または更新します。
        Inserts or updates the cached analysis result.
        """

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

    def delete_missing_for_roots(self, root_names: list[str], seen_paths: set[Path]) -> int:
        """今回のスキャンで見つからなかった選択ルート内のDB行を削除します。
        Deletes cached DB rows for selected roots when they were not seen in this scan.
        """

        if not root_names:
            return 0

        seen_path_strings = {str(path) for path in seen_paths}
        deleted_rows = 0
        with self._connect() as connection:
            for root_name in root_names:
                if seen_path_strings:
                    placeholders = ",".join("?" for _ in seen_path_strings)
                    cursor = connection.execute(
                        f"DELETE FROM photos WHERE root_name = ? AND path NOT IN ({placeholders})",
                        (root_name, *seen_path_strings),
                    )
                else:
                    cursor = connection.execute("DELETE FROM photos WHERE root_name = ?", (root_name,))
                deleted_rows += cursor.rowcount
        return deleted_rows

    def record_scan_run(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        roots: list[str],
        scanned_files: int,
        analyzed_files: int,
        cached_files: int,
        deleted_cached_files: int,
        failed_files: int,
    ) -> None:
        """完了したスキャンの概要を履歴として保存します。
        Stores a completed scan summary as scan history.
        """

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scan_runs (
                    started_at, finished_at, roots, scanned_files, analyzed_files,
                    cached_files, deleted_cached_files, failed_files
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at.isoformat(),
                    finished_at.isoformat(),
                    json.dumps(roots),
                    scanned_files,
                    analyzed_files,
                    cached_files,
                    deleted_cached_files,
                    failed_files,
                ),
            )

    def get_last_scan_finished_at(self) -> str | None:
        """最後に完了したスキャン時刻を返します。
        Returns the finished timestamp for the most recent scan run.
        """

        with self._connect() as connection:
            row = connection.execute(
                "SELECT finished_at FROM scan_runs ORDER BY id DESC LIMIT 1",
            ).fetchone()
        return str(row["finished_at"]) if row else None

    def _row_to_analysis(self, row: sqlite3.Row) -> PhotoAnalysis:
        """SQLiteの1行をPhotoAnalysisオブジェクトへ戻します。
        Converts one SQLite row back into a PhotoAnalysis object.
        """

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
