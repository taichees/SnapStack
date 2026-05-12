from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import load_settings
from app.services.scanner import PhotoScanner


settings = load_settings()
scanner = PhotoScanner(settings)

app = FastAPI(title="SnapStack", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


class ScanRequest(BaseModel):
    """スキャン対象ルート名のリクエスト本文です。
    Request body containing the root names selected for scanning.
    """

    root_names: list[str] = []


@app.get("/")
def index(request: Request):
    """ルート選択と結果表示を行うメイン画面を返します。
    Renders the main page for selecting roots and viewing scan results.
    """

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "photo_roots": settings.photo_roots,
            "recommendation_count": settings.recommendation_count,
            "last_scan_at": scanner.last_scan_at(),
        },
    )


@app.get("/api/config")
def get_config():
    """UIやデバッグ用に現在の設定を返します。
    Returns current configuration for the UI and debugging.
    """

    return {
        "photo_roots": [
            {
                "name": root.name,
                "path": str(root.path),
                "exists": root.path.exists(),
            }
            for root in settings.photo_roots
        ],
        "recommendation_count": settings.recommendation_count,
        "hash_distance_threshold": settings.hash_distance_threshold,
        "burst_time_window_seconds": settings.burst_time_window_seconds,
        "last_scan_at": scanner.last_scan_at(),
    }


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
