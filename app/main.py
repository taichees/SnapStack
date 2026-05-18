from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.config import PhotoRoot, load_settings, sanitize_root_name, validate_ui_local_path
from app.runtime_store import (
    read_google_drive_runtime,
    read_recommendation_runtime,
    read_runtime,
    write_google_drive_runtime,
    write_recommendation_runtime,
    write_runtime,
)
from app.services.google_oauth import (
    delete_client_config_file,
    describe_oauth_client_for_ui,
    disconnect as google_disconnect,
    finish_authorization,
    is_connected as google_is_connected,
    oauth_client_configured,
    save_client_config_from_ui,
    start_authorization,
)
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
        else:
            cards.append(
                {
                    "name": root.name,
                    "subtitle": f"Google Drive (folder: {root.folder_id})",
                    "kind": "google-drive",
                    "managed": False,
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
        else:
            rows.append(
                {
                    "name": root.name,
                    "path": f"gdrive:{root.folder_id}",
                    "kind": "google-drive",
                    "exists": True,
                    "managed": False,
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


class GoogleOAuthClientBody(BaseModel):
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(default="")
    redirect_uri: str = Field(default="")


class GoogleDriveScanBody(BaseModel):
    enabled: bool = False
    name: str = Field(default="google-drive", min_length=1)
    folder_id: str = Field(default="root", min_length=1)


class RecommendationSettingsBody(BaseModel):
    zero_score_for_duplicate_files: bool = True
    storage_priority: str = Field(default="nas_first")
    same_file_max_hash_distance: int = Field(default=0, ge=0, le=16)


DEFAULT_GOOGLE_REDIRECT = "http://127.0.0.1:8000/oauth/google/callback"


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
            "google_drive_summary": _google_drive_summary(),
        },
    )


@app.get("/settings/recommendation")
def recommendation_settings_page(request: Request):
    rec = read_recommendation_runtime(settings.data_dir)
    return templates.TemplateResponse(
        request,
        "recommendation_settings.html",
        {"data_dir": str(settings.data_dir), "rec": rec},
    )


@app.get("/api/settings/recommendation")
def get_recommendation_settings():
    return read_recommendation_runtime(settings.data_dir)


@app.put("/api/settings/recommendation")
def put_recommendation_settings(body: RecommendationSettingsBody):
    priority = body.storage_priority.strip()
    if priority not in ("nas_first", "cloud_first"):
        raise HTTPException(status_code=400, detail="storage_priority は nas_first または cloud_first です")
    try:
        write_recommendation_runtime(
            settings.data_dir,
            {
                "zero_score_for_duplicate_files": body.zero_score_for_duplicate_files,
                "storage_priority": priority,
                "same_file_max_hash_distance": body.same_file_max_hash_distance,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reload_app()
    return read_recommendation_runtime(settings.data_dir)


@app.get("/settings/google-drive")
def google_drive_settings_page(request: Request):
    """Google Drive API / OAuth / スキャン対象の設定画面。"""
    gd = read_google_drive_runtime(settings.data_dir)
    return templates.TemplateResponse(
        request,
        "google_drive_settings.html",
        {
            "data_dir": str(settings.data_dir),
            "default_redirect_uri": DEFAULT_GOOGLE_REDIRECT,
            "google_drive": gd,
            "oauth_ready": settings.google_oauth_env_ready,
            "connected": google_is_connected(settings.data_dir),
        },
    )


def _google_drive_summary() -> dict[str, object]:
    gd = read_google_drive_runtime(settings.data_dir)
    connected = google_is_connected(settings.data_dir)
    in_roots = any(not isinstance(r, PhotoRoot) for r in settings.roots)
    return {
        "scan_enabled": gd["enabled"],
        "connected": connected,
        "in_roots": in_roots,
        "name": gd["name"],
    }


@app.get("/api/google-drive/scan")
def get_google_drive_scan():
    return read_google_drive_runtime(settings.data_dir)


@app.put("/api/google-drive/scan")
def put_google_drive_scan(body: GoogleDriveScanBody):
    name = sanitize_root_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="表示名が不正です")
    folder_id = body.folder_id.strip() or "root"
    write_google_drive_runtime(
        settings.data_dir,
        {"enabled": body.enabled, "name": name, "folder_id": folder_id},
    )
    _reload_app()
    return {"ok": True, **read_google_drive_runtime(settings.data_dir)}


@app.get("/api/google-oauth/client")
def get_google_oauth_client():
    """画面用: クライアント ID / リダイレクト（秘密は返しません）。"""
    return describe_oauth_client_for_ui(settings.data_dir)


@app.post("/api/google-oauth/client")
def post_google_oauth_client(body: GoogleOAuthClientBody):
    """OAuth クライアントを data_dir の JSON に保存します（秘密は空欄で既存を維持可）。"""
    try:
        save_client_config_from_ui(
            settings.data_dir,
            client_id=body.client_id,
            client_secret=body.client_secret,
            redirect_uri=body.redirect_uri,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reload_app()
    return {"ok": True}


@app.delete("/api/google-oauth/client")
def delete_google_oauth_client_file():
    """画面保存のクライアント JSON のみ削除します（トークンや環境変数は触りません）。"""
    deleted = delete_client_config_file(settings.data_dir)
    _reload_app()
    return {"ok": True, "deleted": deleted}


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
        "google_drive": read_google_drive_runtime(settings.data_dir),
        "google_oauth": {
            "env_ready": settings.google_oauth_env_ready,
            "scan_enabled": settings.google_drive_scan_enabled,
            "connected": google_is_connected(settings.data_dir),
            "default_redirect_uri": DEFAULT_GOOGLE_REDIRECT,
        },
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
    write_runtime(settings.data_dir, runtime)
    _reload_app()
    return {"ok": True}


@app.get("/oauth/google/start")
def oauth_google_start():
    """Google OAuth 同意画面へリダイレクトします。"""
    if not oauth_client_configured(settings.data_dir):
        raise HTTPException(
            status_code=503,
            detail="OAuth クライアントが未設定です。画面のフォームで保存するか、環境変数を設定してください。",
        )
    try:
        url = start_authorization(settings.data_dir)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(url)


@app.get("/oauth/google/callback")
def oauth_google_callback(request: Request):
    """Google からのリダイレクトでトークンを保存します。"""
    try:
        finish_authorization(
            settings.data_dir,
            authorization_response=str(request.url),
            state_param=request.query_params.get("state"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _reload_app()
    return RedirectResponse("/settings/google-drive?google=connected")


@app.post("/oauth/google/disconnect")
def oauth_google_disconnect():
    """保存済み Google OAuth トークンを削除します。"""
    google_disconnect(settings.data_dir)
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
