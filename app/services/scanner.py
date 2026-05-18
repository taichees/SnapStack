from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.config import GoogleDrivePhotoRoot, PhotoRoot, ScanRoot, Settings, google_drive_logical_path
from app.models import PhotoAnalysis
from app.services.analyzer import analyze_photo, analyze_photo_bytes, hamming_distance
from app.services.recommendation_policy import AdjustedPhoto, apply_duplicate_score_policy
from app.services.google_drive_files import download_drive_file, iter_drive_image_files
from app.services.google_oauth import credentials_fresh
from app.storage import AnalysisStore


@dataclass(frozen=True)
class _ScanWork:
    root_name: str
    local_path: Path | None
    drive_file_id: str | None
    logical_path: Path
    mtime: float
    size_bytes: int
    label: str


@dataclass(frozen=True)
class ScanSummary:
    """スキャン結果の件数とエラー概要を保持します。
    Stores counts and error details from one scan run.
    """

    scanned_files: int
    analyzed_files: int
    cached_files: int
    deleted_cached_files: int
    failed_files: int
    grouped_files: int
    ungrouped_files: int
    last_scan_at: str
    roots: list[str]
    errors: list[str]


class PhotoScanner:
    def __init__(self, settings: Settings) -> None:
        """設定に基づいてスキャナーとキャッシュ保存先を初期化します。
        Initializes the scanner and cache locations from the app settings.
        """

        self.settings = settings
        self.thumbnail_dir = settings.data_dir / "thumbnails"
        self.store = AnalysisStore(settings.data_dir / "snapstack.db")
        self._progress_lock = threading.Lock()
        self._progress: dict[str, Any] | None = None

    def get_scan_progress(self) -> dict[str, Any]:
        """スキャン処理中の進捗を返します（別スレッドからポーリング可能）。
        Returns in-flight scan progress for polling from another request thread.
        """

        with self._progress_lock:
            if self._progress is None:
                return {
                    "phase": "idle",
                    "current": 0,
                    "total": 0,
                    "roots": [],
                    "last_file": None,
                }
            return dict(self._progress)

    def _progress_update(self, **fields: Any) -> None:
        with self._progress_lock:
            base = dict(self._progress) if self._progress else {}
            base.update(fields)
            self._progress = base

    def _progress_clear(self) -> None:
        with self._progress_lock:
            self._progress = None

    def scan(self, root_names: Iterable[str] | None = None) -> dict:
        """選択された複数ルートを走査し、類似写真グループを返します。
        Scans selected roots and returns grouped similar photos.
        """

        try:
            selected_roots = self._select_roots(root_names)
            root_labels = [root.name for root in selected_roots]
            self._progress_update(
                phase="enumerating",
                current=0,
                total=0,
                roots=root_labels,
                last_file=None,
            )

            drive_creds = None
            if any(isinstance(r, GoogleDrivePhotoRoot) for r in selected_roots):
                drive_creds = credentials_fresh(self.settings.data_dir)
                if drive_creds is None:
                    raise ValueError(
                        "Google Drive ルートが選択されていますが OAuth トークンがありません。"
                        "ブラウザで「Google Drive と接続」から認証してください。",
                    )

            tasks: list[_ScanWork] = []
            for root in selected_roots:
                if isinstance(root, PhotoRoot):
                    if not root.path.exists():
                        raise ValueError(f"Photo root does not exist: {root.path}")
                    for path in self._iter_images(root):
                        stat = path.stat()
                        tasks.append(
                            _ScanWork(
                                root_name=root.name,
                                local_path=path,
                                drive_file_id=None,
                                logical_path=path,
                                mtime=stat.st_mtime,
                                size_bytes=stat.st_size,
                                label=path.name,
                            )
                        )
                        if len(tasks) % 400 == 0:
                            self._progress_update(
                                phase="enumerating",
                                current=len(tasks),
                                total=0,
                                roots=root_labels,
                                last_file=path.name,
                            )
                else:
                    assert drive_creds is not None
                    for meta in iter_drive_image_files(
                        drive_creds,
                        root.folder_id,
                        self.settings.image_extensions,
                    ):
                        fid = meta["id"]
                        logical = google_drive_logical_path(fid)
                        tasks.append(
                            _ScanWork(
                                root_name=root.name,
                                local_path=None,
                                drive_file_id=fid,
                                logical_path=logical,
                                mtime=float(meta["mtime"] or 0.0),
                                size_bytes=int(meta.get("size_bytes") or 0),
                                label=str(meta.get("name") or fid),
                            )
                        )
                        if len(tasks) % 400 == 0:
                            self._progress_update(
                                phase="enumerating",
                                current=len(tasks),
                                total=0,
                                roots=root_labels,
                                last_file=str(meta.get("name") or ""),
                            )

            total_files = len(tasks)
            self._progress_update(
                phase="analyzing",
                current=0,
                total=total_files,
                roots=root_labels,
                last_file=None,
            )

            started_at = datetime.now(timezone.utc)
            errors: list[str] = []
            photos: list[PhotoAnalysis] = []
            seen_paths: set[Path] = set()
            scanned_files = 0
            analyzed_files = 0
            cached_files = 0
            emit_every = max(1, total_files // 120) if total_files else 1

            for index, sw in enumerate(tasks):
                scanned_files += 1
                seen_paths.add(sw.logical_path)
                step = index + 1
                if step == 1 or step == total_files or step % emit_every == 0:
                    self._progress_update(
                        phase="analyzing",
                        current=step,
                        total=total_files,
                        roots=root_labels,
                        last_file=sw.label,
                    )
                try:
                    if sw.local_path is not None:
                        stat = sw.local_path.stat()
                        cached = self.store.get_valid(sw.logical_path, stat.st_mtime, stat.st_size)
                        thumbnail_exists = cached and (self.thumbnail_dir / f"{cached.thumbnail_id}.jpg").exists()
                        if cached and thumbnail_exists:
                            photos.append(cached)
                            cached_files += 1
                            continue

                        analysis = analyze_photo(sw.local_path, sw.root_name, self.thumbnail_dir)
                        self.store.upsert(analysis)
                        photos.append(analysis)
                        analyzed_files += 1
                    else:
                        assert drive_creds is not None and sw.drive_file_id is not None
                        cached = self.store.get_valid(sw.logical_path, sw.mtime, sw.size_bytes)
                        thumbnail_exists = cached and (self.thumbnail_dir / f"{cached.thumbnail_id}.jpg").exists()
                        if cached and thumbnail_exists:
                            photos.append(cached)
                            cached_files += 1
                            continue

                        data = download_drive_file(drive_creds, sw.drive_file_id)
                        mtime = sw.mtime
                        size_bytes = len(data) if sw.size_bytes <= 0 else sw.size_bytes
                        analysis = analyze_photo_bytes(
                            data,
                            sw.logical_path,
                            sw.root_name,
                            self.thumbnail_dir,
                            mtime=mtime,
                            size_bytes=size_bytes,
                        )
                        self.store.upsert(analysis)
                        photos.append(analysis)
                        analyzed_files += 1
                except Exception as exc:
                    errors.append(f"{sw.logical_path}: {exc}")

            deleted_cached_files = self.store.delete_missing_for_roots(
                [root.name for root in selected_roots],
                seen_paths,
            )
            self._progress_update(
                phase="grouping",
                current=0,
                total=0,
                roots=root_labels,
                last_file=None,
            )
            groups = self._group_photos(photos)
            grouped_files = sum(len(group["photos"]) for group in groups)
            finished_at = datetime.now(timezone.utc)
            self.store.record_scan_run(
                started_at=started_at,
                finished_at=finished_at,
                roots=[root.name for root in selected_roots],
                scanned_files=scanned_files,
                analyzed_files=analyzed_files,
                cached_files=cached_files,
                deleted_cached_files=deleted_cached_files,
                failed_files=len(errors),
            )
            summary = ScanSummary(
                scanned_files=scanned_files,
                analyzed_files=analyzed_files,
                cached_files=cached_files,
                deleted_cached_files=deleted_cached_files,
                failed_files=len(errors),
                grouped_files=grouped_files,
                ungrouped_files=max(0, len(photos) - grouped_files),
                last_scan_at=finished_at.isoformat(),
                roots=[root.name for root in selected_roots],
                errors=errors[:100],
            )
            return {
                "summary": summary.__dict__,
                "groups": groups,
            }
        finally:
            self._progress_clear()

    def last_scan_at(self) -> str | None:
        """最後に完了したスキャン時刻を取得します。
        Returns the timestamp for the most recently completed scan.
        """

        return self.store.get_last_scan_finished_at()

    def _select_roots(self, root_names: Iterable[str] | None) -> list[ScanRoot]:
        """リクエストされたルート名を設定済みルートに解決します。
        Resolves requested root names to configured scan roots.
        """

        roots_by_name = {root.name: root for root in self.settings.roots}
        requested = [name for name in (root_names or []) if name]
        if not requested:
            return self.settings.roots

        missing = sorted(set(requested) - set(roots_by_name))
        if missing:
            raise ValueError(f"Unknown photo roots: {', '.join(missing)}")
        return [roots_by_name[name] for name in requested]

    def _iter_images(self, root: PhotoRoot) -> Iterable[Path]:
        """指定ルート配下から対応画像ファイルだけを列挙します。
        Yields supported image files under a configured root.
        """

        if not root.path.exists():
            raise ValueError(f"Photo root does not exist: {root.path}")
        for current_dir, _, filenames in os.walk(root.path):
            for filename in filenames:
                path = Path(current_dir) / filename
                if path.suffix.lower() in self.settings.image_extensions:
                    yield path

    def _group_photos(self, photos: list[PhotoAnalysis]) -> list[dict]:
        """pHashと撮影時刻を使って写真を類似グループへまとめます。
        Groups photos by perceptual hash similarity and capture time.
        """

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
        """撮影時刻が近い連写候補を同じグループへ接続します。
        Connects burst candidates captured close together into the same group.
        """

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
        """時刻が離れていても見た目が近い写真を代表値比較で接続します。
        Connects visually similar photos across time using representative hashes.
        """

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
        """1つのグループをAPI/UI向けの辞書形式に変換します。
        Converts one group into the dictionary shape consumed by the API/UI.
        """

        adjusted = apply_duplicate_score_policy(
            photos,
            settings=self.settings,
            policy=self.settings.recommendation_policy,
        )
        sorted_adjusted = sorted(
            adjusted,
            key=lambda item: (-item.photo.score, str(item.photo.path)),
        )
        recommended = _select_recommendations(sorted_adjusted, self.settings.recommendation_count)
        captured_times = [item.photo.captured_at for item in adjusted if item.photo.captured_at]
        return {
            "group_id": f"group-{group_id}",
            "count": len(photos),
            "roots": sorted({item.photo.root_name for item in adjusted}),
            "captured_start": min(captured_times).isoformat() if captured_times else None,
            "captured_end": max(captured_times).isoformat() if captured_times else None,
            "recommended": [_serialize_photo(item) for item in recommended],
            "photos": [_serialize_photo(item) for item in sorted_adjusted],
        }


class _DisjointSet:
    def __init__(self, size: int) -> None:
        """グループ結合用のUnion-Find構造を初期化します。
        Initializes a union-find structure used to merge photo groups.
        """

        self.parent = list(range(size))

    def find(self, item: int) -> int:
        """要素が属するグループ代表を返します。
        Returns the representative group for an item.
        """

        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        """2つの要素が属するグループを結合します。
        Merges the groups containing two items.
        """

        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _select_recommendations(photos: list[AdjustedPhoto], count: int) -> list[AdjustedPhoto]:
    """スコア順に並んだ写真からおすすめ上位を選びます（0 点はおすすめに含めない）。
    Picks the top recommendations from photos already sorted by score.
    """

    eligible = [item for item in photos if item.photo.score > 0]
    return eligible[:count]


def _serialize_photo(item: AdjustedPhoto) -> dict:
    """1枚の解析結果をAPI/UI向けの辞書形式に変換します。
    Converts one analysis result into the dictionary shape consumed by the API/UI.
    """

    photo = item.photo
    payload = {
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
    if item.duplicate_penalized:
        payload["duplicate_penalized"] = True
    return payload


def _timestamp_for_sort(photo: PhotoAnalysis) -> float:
    """撮影日時を優先し、無ければ更新時刻を並び替えキーに使います。
    Uses capture time first, falling back to file modification time for sorting.
    """

    if photo.captured_at:
        return photo.captured_at.timestamp()
    return photo.mtime
