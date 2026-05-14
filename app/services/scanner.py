from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from app.config import PhotoRoot, ScanRoot, Settings, WebDavPhotoRoot, webdav_logical_path
from app.models import PhotoAnalysis
from app.services.analyzer import analyze_photo, analyze_photo_bytes, hamming_distance
from app.services.webdav_client import fetch_webdav_file, iter_webdav_image_relpaths
from app.storage import AnalysisStore


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


@dataclass(frozen=True)
class ScanFile:
    """1枚分のスキャン対象（ローカルまたは WebDAV）です。"""

    root_name: str
    logical_path: Path
    local_path: Path | None
    dav: WebDavPhotoRoot | None
    dav_rel: str | None
    mtime: float
    size_bytes: int


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

            dav_sessions: dict[str, requests.Session] = {}
            for root in selected_roots:
                if isinstance(root, WebDavPhotoRoot):
                    session = requests.Session()
                    if root.username or root.password:
                        session.auth = (root.username, root.password)
                    dav_sessions[root.name] = session

            tasks: list[ScanFile] = []
            for root in selected_roots:
                if isinstance(root, PhotoRoot):
                    if not root.path.exists():
                        raise ValueError(f"Photo root does not exist: {root.path}")
                    for path in self._iter_images(root):
                        stat = path.stat()
                        tasks.append(
                            ScanFile(
                                root_name=root.name,
                                logical_path=path,
                                local_path=path,
                                dav=None,
                                dav_rel=None,
                                mtime=stat.st_mtime,
                                size_bytes=stat.st_size,
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
                    session = dav_sessions[root.name]
                    for rel, mt, sz in iter_webdav_image_relpaths(
                        session,
                        root.base_url,
                        root.remote_path,
                        self.settings.image_extensions,
                    ):
                        logical = webdav_logical_path(root.dav_id, rel)
                        tasks.append(
                            ScanFile(
                                root_name=root.name,
                                logical_path=logical,
                                local_path=None,
                                dav=root,
                                dav_rel=rel,
                                mtime=float(mt or 0.0),
                                size_bytes=int(sz or 0),
                            )
                        )
                        if len(tasks) % 400 == 0:
                            label = rel.split("/")[-1] or rel
                            self._progress_update(
                                phase="enumerating",
                                current=len(tasks),
                                total=0,
                                roots=root_labels,
                                last_file=label,
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

            for index, sf in enumerate(tasks):
                scanned_files += 1
                seen_paths.add(sf.logical_path)
                step = index + 1
                label = sf.logical_path.name
                if step == 1 or step == total_files or step % emit_every == 0:
                    self._progress_update(
                        phase="analyzing",
                        current=step,
                        total=total_files,
                        roots=root_labels,
                        last_file=label,
                    )
                try:
                    if sf.local_path is not None:
                        stat = sf.local_path.stat()
                        cached = self.store.get_valid(sf.logical_path, stat.st_mtime, stat.st_size)
                        thumbnail_exists = cached and (self.thumbnail_dir / f"{cached.thumbnail_id}.jpg").exists()
                        if cached and thumbnail_exists:
                            photos.append(cached)
                            cached_files += 1
                            continue

                        analysis = analyze_photo(sf.local_path, sf.root_name, self.thumbnail_dir)
                        self.store.upsert(analysis)
                        photos.append(analysis)
                        analyzed_files += 1
                    else:
                        assert sf.dav is not None and sf.dav_rel is not None
                        session = dav_sessions[sf.root_name]
                        cached = self.store.get_valid(sf.logical_path, sf.mtime, sf.size_bytes)
                        thumbnail_exists = cached and (self.thumbnail_dir / f"{cached.thumbnail_id}.jpg").exists()
                        if cached and thumbnail_exists:
                            photos.append(cached)
                            cached_files += 1
                            continue

                        data, hdr_mtime, data_len = fetch_webdav_file(
                            session,
                            sf.dav.base_url,
                            sf.dav.remote_path,
                            sf.dav_rel,
                        )
                        mtime = float(hdr_mtime if hdr_mtime is not None else sf.mtime)
                        size_bytes = int(data_len)
                        analysis = analyze_photo_bytes(
                            data,
                            sf.logical_path,
                            sf.root_name,
                            self.thumbnail_dir,
                            mtime=mtime,
                            size_bytes=size_bytes,
                        )
                        self.store.upsert(analysis)
                        photos.append(analysis)
                        analyzed_files += 1
                except Exception as exc:
                    errors.append(f"{sf.logical_path}: {exc}")

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


def _select_recommendations(photos: list[PhotoAnalysis], count: int) -> list[PhotoAnalysis]:
    """スコア順に並んだ写真からおすすめ上位を選びます。
    Picks the top recommendations from photos already sorted by score.
    """

    return photos[:count]


def _serialize_photo(photo: PhotoAnalysis) -> dict:
    """1枚の解析結果をAPI/UI向けの辞書形式に変換します。
    Converts one analysis result into the dictionary shape consumed by the API/UI.
    """

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
    """撮影日時を優先し、無ければ更新時刻を並び替えキーに使います。
    Uses capture time first, falling back to file modification time for sorting.
    """

    if photo.captured_at:
        return photo.captured_at.timestamp()
    return photo.mtime
