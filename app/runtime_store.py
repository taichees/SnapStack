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
        return {"version": RUNTIME_VERSION, "local": [], "webdav": []}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        return {"version": RUNTIME_VERSION, "local": [], "webdav": []}
    data.setdefault("version", RUNTIME_VERSION)
    data.setdefault("local", [])
    data.setdefault("webdav", [])
    return data


def write_runtime(data_dir: Path, data: dict[str, Any]) -> None:
    path = runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
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
