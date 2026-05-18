from __future__ import annotations

from pathlib import Path

from app.runtime_store import read_google_drive_runtime, write_google_drive_runtime


def test_google_drive_runtime_roundtrip(tmp_path: Path) -> None:
    write_google_drive_runtime(
        tmp_path,
        {"enabled": True, "name": "my-drive", "folder_id": "abc123"},
    )
    got = read_google_drive_runtime(tmp_path)
    assert got["enabled"] is True
    assert got["name"] == "my-drive"
    assert got["folder_id"] == "abc123"
