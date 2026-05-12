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
    root_names: list[str] = []


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "photo_roots": settings.photo_roots,
            "recommendation_count": settings.recommendation_count,
        },
    )


@app.get("/api/config")
def get_config():
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
    }


@app.post("/api/scan")
def scan_photos(payload: ScanRequest):
    try:
        return scanner.scan(payload.root_names)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/thumbs/{thumbnail_id}.jpg")
def get_thumbnail(thumbnail_id: str):
    if not thumbnail_id.replace("-", "").isalnum():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = settings.data_dir / "thumbnails" / f"{thumbnail_id}.jpg"
    if not Path(path).exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")
