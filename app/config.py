from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

import yaml

from app.runtime_store import google_drive_block_from_sources, read_recommendation_runtime, read_runtime
from app.services.recommendation_policy import RecommendationPolicy
from app.services.google_oauth import (
    is_connected as google_oauth_token_exists,
    oauth_client_configured,
    resolve_oauth_client,
)

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
    """スキャン対象のローカル（バインドマウント）写真ルートを表します。
    Represents one local bind-mounted photo root to scan.
    """

    name: str
    path: Path


@dataclass(frozen=True)
class GoogleDrivePhotoRoot:
    """OAuth 済み Google Drive 上のフォルダを1ルートとして扱います。"""

    name: str
    folder_id: str


ScanRoot = Union[PhotoRoot, GoogleDrivePhotoRoot]


@dataclass(frozen=True)
class Settings:
    """アプリ全体で使う設定値をまとめます。
    Holds application-wide settings loaded from YAML or environment variables.
    """

    roots: list[ScanRoot]
    managed_root_names: frozenset[str]
    ui_local_prefixes: tuple[Path, ...]
    data_dir: Path
    image_extensions: set[str]
    hash_distance_threshold: int
    burst_time_window_seconds: int
    recommendation_count: int
    google_oauth_env_ready: bool
    google_drive_scan_enabled: bool
    recommendation_policy: RecommendationPolicy
    cloud_root_names: frozenset[str]


def _slugify(value: str) -> str:
    """表示名や環境変数由来のパスから安全な短い名前を作ります。
    Builds a safe short name from a display value or path.
    """

    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return "-".join(part for part in slug.split("-") if part) or "photos"


def sanitize_root_name(name: str) -> str:
    """UI から渡されたルート表示名を安全な識別子に整えます。
    Normalizes a user-supplied root display name into a safe identifier.
    """

    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", name.strip()).strip("-_.")
    return (cleaned or "folder")[:64]


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


def _ui_prefixes_from_env() -> tuple[Path, ...]:
    raw = os.getenv("SNAPSTACK_UI_LOCAL_PREFIXES", "")
    parts = [Path(p.strip()) for p in raw.split(",") if p.strip()]
    return tuple(parts)


def validate_ui_local_path(path: Path, prefixes: tuple[Path, ...]) -> Path:
    """UI から追加するローカルパスが許可プレフィックス配下か検証します。
    Ensures a UI-added local path stays under configured allow prefixes.
    """

    if not prefixes:
        raise ValueError(
            "SNAPSTACK_UI_LOCAL_PREFIXES が未設定のため、クラウド同期フォルダを UI から追加できません。"
            " docker-compose で /photos/cloud などをマウントし、同じパスをカンマ区切りで指定してください。",
        )
    resolved = path.expanduser().resolve()
    for prefix in prefixes:
        pr = prefix.expanduser().resolve()
        if resolved == pr or resolved.is_relative_to(pr):
            return resolved
    allowed = ", ".join(str(p) for p in prefixes)
    raise ValueError(f"パスは次のいずれかの配下である必要があります: {allowed}")


def google_drive_logical_path(file_id: str) -> Path:
    return Path(f"/__gdrive__/{file_id}")


def _google_drive_root_from_config(config: dict[str, Any], data_dir: Path) -> GoogleDrivePhotoRoot | None:
    block = google_drive_block_from_sources(config, data_dir)
    if not isinstance(block, dict) or not block.get("enabled"):
        return None
    if resolve_oauth_client(data_dir) is None:
        return None
    if not google_oauth_token_exists(data_dir):
        return None
    name = sanitize_root_name(str(block.get("name") or "google-drive"))
    fid = str(block.get("folder_id") or "root").strip() or "root"
    return GoogleDrivePhotoRoot(name=name, folder_id=fid)


def _runtime_photo_roots(
    *,
    data_dir: Path,
    ui_prefixes: tuple[Path, ...],
    yaml_roots: list[PhotoRoot],
) -> tuple[list[PhotoRoot], frozenset[str]]:
    """runtime_roots.json から UI 管理ルートを読み込みます。
    Loads UI-managed roots from runtime_roots.json.
    """

    runtime = read_runtime(data_dir)
    yaml_names = {root.name for root in yaml_roots}
    managed: set[str] = set()
    extra_locals: list[PhotoRoot] = []
    for item in runtime.get("local", []):
        if not isinstance(item, dict):
            continue
        name = sanitize_root_name(str(item.get("name", "")))
        raw_path = str(item.get("path", "")).strip()
        if not name or not raw_path or name in yaml_names or name in managed:
            continue
        try:
            path = validate_ui_local_path(Path(raw_path), ui_prefixes)
        except (OSError, ValueError):
            continue
        extra_locals.append(PhotoRoot(name=name, path=path))
        managed.add(name)

    return extra_locals, frozenset(managed)


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

    env_roots_raw = os.getenv("SNAPSTACK_PHOTO_ROOTS", "").strip()
    if env_roots_raw:
        yaml_roots = _roots_from_env()
    else:
        yaml_roots = _roots_from_config(config) or _roots_from_env()
    data_dir = Path(os.getenv("SNAPSTACK_DATA_DIR", str(config.get("data_dir", "/data"))))
    ui_prefixes = _ui_prefixes_from_env()
    extra_locals, managed = _runtime_photo_roots(
        data_dir=data_dir,
        ui_prefixes=ui_prefixes,
        yaml_roots=yaml_roots,
    )
    drive_root = _google_drive_root_from_config(config, data_dir)
    roots: list[ScanRoot] = [*yaml_roots, *extra_locals]
    if drive_root is not None:
        roots.append(drive_root)

    image_extensions = {
        str(ext).lower() if str(ext).startswith(".") else f".{str(ext).lower()}"
        for ext in config.get("image_extensions", DEFAULT_IMAGE_EXTENSIONS)
    }

    google_ready = oauth_client_configured(data_dir)
    gd_block = google_drive_block_from_sources(config, data_dir)
    gd_scan_enabled = isinstance(gd_block, dict) and bool(gd_block.get("enabled"))

    yaml_rec = config.get("recommendation")
    rec_raw = read_recommendation_runtime(
        data_dir,
        yaml_block=yaml_rec if isinstance(yaml_rec, dict) else None,
    )
    rec_policy = RecommendationPolicy(
        zero_score_for_duplicate_files=bool(rec_raw["zero_score_for_duplicate_files"]),
        storage_priority=rec_raw["storage_priority"],  # type: ignore[arg-type]
        same_file_max_hash_distance=int(rec_raw["same_file_max_hash_distance"]),
    )
    cloud_names = _cloud_root_names(roots, managed)

    return Settings(
        roots=roots,
        managed_root_names=managed,
        ui_local_prefixes=ui_prefixes,
        data_dir=data_dir,
        image_extensions=image_extensions,
        hash_distance_threshold=int(config.get("hash_distance_threshold", 8)),
        burst_time_window_seconds=int(config.get("burst_time_window_seconds", 20)),
        recommendation_count=int(config.get("recommendation_count", 3)),
        google_oauth_env_ready=google_ready,
        google_drive_scan_enabled=gd_scan_enabled,
        recommendation_policy=rec_policy,
        cloud_root_names=cloud_names,
    )


def _cloud_root_names(roots: list[ScanRoot], managed: frozenset[str]) -> frozenset[str]:
    names: set[str] = set(managed)
    for root in roots:
        if isinstance(root, GoogleDrivePhotoRoot):
            names.add(root.name)
    return frozenset(names)
