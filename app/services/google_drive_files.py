from __future__ import annotations

import io
from collections import deque
from datetime import datetime
from typing import Any, Iterable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


def _parse_drive_time(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _is_image_record(name: str, mime: str, image_extensions: set[str]) -> bool:
    if mime.startswith("image/"):
        return True
    lower = name.lower()
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    return ext in image_extensions


def iter_drive_image_files(
    creds: Credentials,
    folder_id: str,
    image_extensions: set[str],
) -> Iterable[dict[str, Any]]:
    """指定フォルダ以下の画像ファイルを BFS で列挙します。"""
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    queue: deque[str] = deque([folder_id or "root"])
    seen_folders: set[str] = set()

    while queue:
        parent = queue.popleft()
        if parent in seen_folders:
            continue
        seen_folders.add(parent)
        page_token: str | None = None
        q = f"'{parent}' in parents and trashed = false"
        while True:
            resp = (
                service.files()
                .list(
                    q=q,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                    pageToken=page_token,
                    pageSize=200,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for item in resp.get("files", []):
                fid = item.get("id")
                name = item.get("name") or ""
                mime = item.get("mimeType") or ""
                if not fid:
                    continue
                if mime == "application/vnd.google-apps.folder":
                    queue.append(fid)
                elif _is_image_record(name, mime, image_extensions):
                    size_raw = item.get("size")
                    try:
                        size_bytes = int(size_raw) if size_raw else 0
                    except (TypeError, ValueError):
                        size_bytes = 0
                    yield {
                        "id": fid,
                        "name": name,
                        "mtime": _parse_drive_time(item.get("modifiedTime")),
                        "size_bytes": size_bytes,
                    }
            page_token = resp.get("nextPageToken")
            if not page_token:
                break


def download_drive_file(creds: Credentials, file_id: str) -> bytes:
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()
