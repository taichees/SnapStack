from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PhotoAnalysis:
    """1枚の写真から抽出した解析結果を保持します。
    Stores all analysis data extracted from a single photo.
    """

    path: Path
    root_name: str
    mtime: float
    size_bytes: int
    width: int
    height: int
    captured_at: datetime | None
    phash: str
    sharpness_score: float
    exposure_score: float
    contrast_score: float
    resolution_score: float
    score: float
    thumbnail_id: str

    @property
    def thumbnail_url(self) -> str:
        """ブラウザから参照するサムネイルURLを返します。
        Returns the thumbnail URL used by the browser UI.
        """

        return f"/thumbs/{self.thumbnail_id}.jpg"

    @property
    def basename(self) -> str:
        """UI表示用のファイル名だけを返します。
        Returns only the filename for UI display.
        """

        return self.path.name
