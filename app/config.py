from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


DEFAULT_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".heic",
    ".heif",
}


@dataclass(frozen=True)
class PhotoRoot:
    """スキャン対象の写真ルートを表します。
    Represents one configured photo root to scan.
    """

    name: str
    path: Path


@dataclass(frozen=True)
class Settings:
    """アプリ全体で使う設定値をまとめます。
    Holds application-wide settings loaded from YAML or environment variables.
    """

    photo_roots: list[PhotoRoot]
    data_dir: Path
    image_extensions: set[str]
    hash_distance_threshold: int
    burst_time_window_seconds: int
    recommendation_count: int


def _slugify(value: str) -> str:
    """表示名や環境変数由来のパスから安全な短い名前を作ります。
    Builds a safe short name from a display value or path.
    """

    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return "-".join(part for part in slug.split("-") if part) or "photos"


def _roots_from_env() -> list[PhotoRoot]:
    """環境変数からカンマ区切りの写真ルート一覧を読み込みます。
    Loads comma-separated photo roots from the environment.
    """

    raw_roots = os.getenv("SNAPSTACK_PHOTO_ROOTS", "")
    roots: list[PhotoRoot] = []
    for index, raw_path in enumerate(part.strip() for part in raw_roots.split(",") if part.strip()):
        path = Path(raw_path)
        roots.append(PhotoRoot(name=f"{index + 1}-{_slugify(path.name or str(index + 1))}", path=path))
    return roots


def _roots_from_config(config: dict[str, Any]) -> list[PhotoRoot]:
    """YAML設定から複数の写真ルートを読み込みます。
    Loads multiple photo roots from the YAML configuration.
    """

    roots: list[PhotoRoot] = []
    for index, root in enumerate(config.get("photo_roots", [])):
        if isinstance(root, str):
            path = Path(root)
            name = f"{index + 1}-{_slugify(path.name or str(index + 1))}"
        else:
            path = Path(str(root["path"]))
            name = str(root.get("name") or f"{index + 1}-{_slugify(path.name or str(index + 1))}")
        roots.append(PhotoRoot(name=name, path=path))
    return roots


def load_settings() -> Settings:
    """設定ファイルと環境変数を統合してアプリ設定を作ります。
    Combines config file values and environment variables into Settings.
    """

    config_path = Path(os.getenv("SNAPSTACK_CONFIG", "/config/snapstack.yml"))
    config: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as file:
            loaded = yaml.safe_load(file) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"Config file must contain a mapping: {config_path}")
            config = loaded

    photo_roots = _roots_from_config(config) or _roots_from_env()
    data_dir = Path(os.getenv("SNAPSTACK_DATA_DIR", str(config.get("data_dir", "/data"))))
    image_extensions = {
        str(ext).lower() if str(ext).startswith(".") else f".{str(ext).lower()}"
        for ext in config.get("image_extensions", DEFAULT_IMAGE_EXTENSIONS)
    }

    return Settings(
        photo_roots=photo_roots,
        data_dir=data_dir,
        image_extensions=image_extensions,
        hash_distance_threshold=int(config.get("hash_distance_threshold", 8)),
        burst_time_window_seconds=int(config.get("burst_time_window_seconds", 20)),
        recommendation_count=int(config.get("recommendation_count", 3)),
    )
