from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.config import (
    PhotoRoot,
    WebDavPhotoRoot,
    load_settings,
    sanitize_root_name,
    validate_ui_local_path,
    validate_webdav_base_url,
)
from app.runtime_store import read_runtime, write_runtime
from app.services.scanner import PhotoScanner

settings = load_settings()
scanner = PhotoScanner(settings)

app = FastAPI(title="SnapStack", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _reload_app() -> None:
    global settings, scanner
    settings = load_settings()
    scanner = PhotoScanner(settings)


def _root_cards() -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    for root in settings.roots:
        if isinstance(root, PhotoRoot):
            cards.append(
                {
                    "name": root.name,
                    "subtitle": str(root.path),
                    "kind": "local",
                    "managed": root.name in settings.managed_root_names,
                }
            )
        elif isinstance(root, WebDavPhotoRoot):
            tail = root.remote_path or "/"
            cards.append(
                {
                    "name": root.name,
                    "subtitle": f"WebDAV {root.base_url}{tail}",
                    "kind": "webdav",
                    "managed": root.name in settings.managed_root_names,
                }
            )
    return cards


def _serialize_roots_api() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for root in settings.roots:
        if isinstance(root, PhotoRoot):
            rows.append(
                {
                    "name": root.name,
                    "path": str(root.path),
                    "kind": "local",
                    "exists": root.path.exists(),
                    "managed": root.name in settings.managed_root_names,
                }
            )
        elif isinstance(root, WebDavPhotoRoot):
            rows.append(
                {
                    "name": root.name,
                    "path": f"{root.base_url}{root.remote_path}",
                    "kind": "webdav",
                    "exists": True,
                    "managed": root.name in settings.managed_root_names,
                }
            )
    return rows


class ScanRequest(BaseModel):
    """スキャン対象ルート名のリクエスト本文です。
    Request body containing the root names selected for scanning.
    """

    root_names: list[str] = []


class AddLocalRootBody(BaseModel):
    name: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)


class AddWebDavRootBody(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    username: str = ""
    password: str = ""
    remote_path: str = ""


@app.get("/")
def index(request: Request):
    """ルート選択と結果表示を行うメイン画面を返します。
    Renders the main page for selecting roots and viewing scan results.
    """

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "root_cards": _root_cards(),
            "recommendation_count": settings.recommendation_count,
            "last_scan_at": scanner.last_scan_at(),
            "ui_local_prefixes": [str(p) for p in settings.ui_local_prefixes],
        },
    )


@app.get("/api/config")
def get_config():
    """UIやデバッグ用に現在の設定を返します。
    Returns current configuration for the UI and debugging.
    """

    return {
        "photo_roots": _serialize_roots_api(),
        "ui_local_prefixes": [str(p) for p in settings.ui_local_prefixes],
        "recommendation_count": settings.recommendation_count,
        "hash_distance_threshold": settings.hash_distance_threshold,
        "burst_time_window_seconds": settings.burst_time_window_seconds,
        "last_scan_at": scanner.last_scan_at(),
    }


@app.post("/api/ui-roots/local")
def add_ui_local_root(body: AddLocalRootBody):
    """Google Drive / Dropbox 等の同期フォルダ（compose でマウント済み）を追加します。"""
    name = sanitize_root_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="名前が不正です")
    if any(root.name == name for root in settings.roots):
        raise HTTPException(status_code=400, detail="同じ名前のルートが既に存在します")
    path = validate_ui_local_path(Path(body.path.strip()), settings.ui_local_prefixes)
    runtime = read_runtime(settings.data_dir)
    runtime["local"] = [
        item
        for item in runtime.get("local", [])
        if isinstance(item, dict) and sanitize_root_name(str(item.get("name", ""))) != name
    ]
    runtime["local"].append({"name": name, "path": str(path)})
    write_runtime(settings.data_dir, runtime)
    _reload_app()
    return {"ok": True, "name": name, "path": str(path)}


@app.post("/api/ui-roots/webdav")
def add_ui_webdav_root(body: AddWebDavRootBody):
    """WebDAV 上のフォルダをルートとして追加します（認証情報は /data に保存されます）。"""
    name = sanitize_root_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="名前が不正です")
    if any(root.name == name for root in settings.roots):
        raise HTTPException(status_code=400, detail="同じ名前のルートが既に存在します")
    base_url = validate_webdav_base_url(body.base_url)
    dav_id = uuid.uuid4().hex[:12]
    runtime = read_runtime(settings.data_dir)
    runtime["webdav"] = [
        item
        for item in runtime.get("webdav", [])
        if isinstance(item, dict) and sanitize_root_name(str(item.get("name", ""))) != name
    ]
    runtime["webdav"].append(
        {
            "id": dav_id,
            "name": name,
            "base_url": base_url,
            "username": body.username,
            "password": body.password,
            "remote_path": body.remote_path.strip().strip("/"),
        }
    )
    write_runtime(settings.data_dir, runtime)
    _reload_app()
    return {"ok": True, "name": name, "id": dav_id}


@app.delete("/api/ui-roots/{name}")
def delete_ui_root(name: str):
    """画面から追加したルートだけ削除できます。"""
    key = sanitize_root_name(name)
    if key not in settings.managed_root_names:
        raise HTTPException(status_code=404, detail="削除対象のルートが見つからないか、YAML 由来のため UI から削除できません")
    runtime = read_runtime(settings.data_dir)
    runtime["local"] = [
        item
        for item in runtime.get("local", [])
        if not isinstance(item, dict) or sanitize_root_name(str(item.get("name", ""))) != key
    ]
    runtime["webdav"] = [
        item
        for item in runtime.get("webdav", [])
        if not isinstance(item, dict) or sanitize_root_name(str(item.get("name", ""))) != key
    ]
    write_runtime(settings.data_dir, runtime)
    _reload_app()
    return {"ok": True}


@app.get("/api/scan/progress")
def scan_progress():
    """スキャン中の進捗を返します（UIがポーリングします）。
    Returns live scan progress for the UI poll loop.
    """

    return scanner.get_scan_progress()


@app.post("/api/scan")
def scan_photos(payload: ScanRequest):
    """選択された写真ルートをスキャンして類似グループを返します。
    Scans selected photo roots and returns similar-photo groups.
    """

    try:
        return scanner.scan(payload.root_names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/thumbs/{thumbnail_id}.jpg")
def get_thumbnail(thumbnail_id: str):
    """生成済みサムネイル画像を安全に返します。
    Safely serves a generated thumbnail image.
    """

    if not thumbnail_id.replace("-", "").isalnum():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = settings.data_dir / "thumbnails" / f"{thumbnail_id}.jpg"
    if not Path(path).exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")
