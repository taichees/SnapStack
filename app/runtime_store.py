from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


RUNTIME_VERSION = 1


def runtime_path(data_dir: Path) -> Path:
    return data_dir / "runtime_roots.json"


def read_runtime(data_dir: Path) -> dict[str, Any]:
    path = runtime_path(data_dir)
    if not path.exists():
        return {"version": RUNTIME_VERSION, "local": []}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        return {"version": RUNTIME_VERSION, "local": []}
    data.setdefault("version", RUNTIME_VERSION)
    data.setdefault("local", [])
    data.pop("webdav", None)
    return data


def google_drive_block_from_sources(config: dict[str, Any], data_dir: Path) -> dict[str, Any] | None:
    """runtime_roots.json を優先し、未設定時のみ snapstack.yml の google_drive を参照する。"""
    runtime = read_runtime(data_dir)
    rt = runtime.get("google_drive")
    if isinstance(rt, dict):
        return rt
    yaml_block = config.get("google_drive")
    if isinstance(yaml_block, dict):
        return yaml_block
    return None


def read_google_drive_runtime(data_dir: Path) -> dict[str, Any]:
    """UI 保存の Google Drive スキャン設定（enabled / name / folder_id）。"""
    block = read_runtime(data_dir).get("google_drive")
    if not isinstance(block, dict):
        return {"enabled": False, "name": "google-drive", "folder_id": "root"}
    name = str(block.get("name") or "google-drive").strip() or "google-drive"
    folder_id = str(block.get("folder_id") or "root").strip() or "root"
    return {
        "enabled": bool(block.get("enabled")),
        "name": name,
        "folder_id": folder_id,
    }


def read_recommendation_runtime(data_dir: Path, *, yaml_block: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime = read_runtime(data_dir)
    block = runtime.get("recommendation")
    if not isinstance(block, dict):
        block = yaml_block if isinstance(yaml_block, dict) else {}
    if not block:
        return {
            "zero_score_for_duplicate_files": True,
            "storage_priority": "nas_first",
            "same_file_max_hash_distance": 0,
        }
    priority = str(block.get("storage_priority") or "nas_first").strip()
    if priority not in ("nas_first", "cloud_first"):
        priority = "nas_first"
    try:
        max_dist = int(block.get("same_file_max_hash_distance", 0))
    except (TypeError, ValueError):
        max_dist = 0
    return {
        "zero_score_for_duplicate_files": bool(block.get("zero_score_for_duplicate_files", True)),
        "storage_priority": priority,
        "same_file_max_hash_distance": max(0, min(max_dist, 16)),
    }


def write_recommendation_runtime(data_dir: Path, block: dict[str, Any]) -> None:
    priority = str(block.get("storage_priority") or "nas_first").strip()
    if priority not in ("nas_first", "cloud_first"):
        raise ValueError("storage_priority は nas_first または cloud_first です")
    try:
        max_dist = int(block.get("same_file_max_hash_distance", 0))
    except (TypeError, ValueError):
        raise ValueError("same_file_max_hash_distance が不正です")
    runtime = read_runtime(data_dir)
    runtime["recommendation"] = {
        "zero_score_for_duplicate_files": bool(block.get("zero_score_for_duplicate_files")),
        "storage_priority": priority,
        "same_file_max_hash_distance": max(0, min(max_dist, 16)),
    }
    write_runtime(data_dir, runtime)


def write_google_drive_runtime(data_dir: Path, block: dict[str, Any]) -> None:
    runtime = read_runtime(data_dir)
    runtime["google_drive"] = {
        "enabled": bool(block.get("enabled")),
        "name": str(block.get("name") or "google-drive").strip() or "google-drive",
        "folder_id": str(block.get("folder_id") or "root").strip() or "root",
    }
    write_runtime(data_dir, runtime)


def write_runtime(data_dir: Path, data: dict[str, Any]) -> None:
    path = runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = dict(data)
    clean.pop("webdav", None)
    payload = json.dumps(clean, ensure_ascii=False, indent=2)
    directory = path.parent
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=directory,
        prefix=".runtime_roots",
        suffix=".tmp",
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)
