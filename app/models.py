from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PhotoAnalysis:
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
        return f"/thumbs/{self.thumbnail_id}.jpg"

    @property
    def basename(self) -> str:
        return self.path.name
