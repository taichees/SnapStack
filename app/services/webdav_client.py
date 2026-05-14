from __future__ import annotations

import email.utils
from dataclasses import dataclass
from typing import Iterable
from collections import deque
from urllib.parse import quote, unquote, urljoin, urlparse
from xml.etree import ElementTree as ET

import requests


@dataclass(frozen=True)
class WebDavListEntry:
    """PROPFIND で得た1エントリです。"""

    href: str
    is_collection: bool
    mtime: float | None
    size: int | None


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _parse_iso_or_http_date(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _propfind(session: requests.Session, url: str, depth: str = "1") -> ET.Element:
    body = b'<?xml version="1.0" encoding="utf-8"?>'
    body += b'<d:propfind xmlns:d="DAV:"><d:prop>'
    body += b"<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
    body += b"</d:prop></d:propfind>"
    response = session.request(
        "PROPFIND",
        url,
        data=body,
        headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
        timeout=120,
    )
    response.raise_for_status()
    return ET.fromstring(response.content)


def _child_href_entries(collection_url: str, multistatus: ET.Element) -> list[WebDavListEntry]:
    """1階層分のメンバーを返します（コレクション自身は除く）。"""
    collection_url = collection_url.rstrip("/") + "/"
    parsed = urlparse(collection_url)
    collection_path = unquote(parsed.path)
    prefix = collection_path.rstrip("/") + "/"

    out: list[WebDavListEntry] = []
    for response in multistatus:
        if _local_tag(response.tag) != "response":
            continue
        href_el = next((c for c in response if _local_tag(c.tag) == "href"), None)
        if href_el is None or not href_el.text:
            continue
        href_text = href_el.text.strip()
        full = urljoin(collection_url, href_text)
        path = unquote(urlparse(full).path)
        if path.rstrip("/") == prefix.rstrip("/").rstrip("/"):
            continue
        if not path.startswith(prefix):
            continue

        is_collection = False
        mtime: float | None = None
        size: int | None = None
        propstat = next((c for c in response if _local_tag(c.tag) == "propstat"), None)
        if propstat is not None:
            prop = next((c for c in propstat if _local_tag(c.tag) == "prop"), None)
            if prop is not None:
                for child in prop:
                    tag = _local_tag(child.tag)
                    if tag == "resourcetype":
                        for rt in child:
                            if _local_tag(rt.tag) == "collection":
                                is_collection = True
                    elif tag == "getlastmodified" and child.text:
                        mtime = _parse_iso_or_http_date(child.text)
                    elif tag == "getcontentlength" and child.text and child.text.strip().isdigit():
                        size = int(child.text.strip())

        rel = path[len(prefix) :].lstrip("/")
        if not rel:
            continue
        out.append(WebDavListEntry(href=rel, is_collection=is_collection, mtime=mtime, size=size))
    return out


def iter_webdav_image_relpaths(
    session: requests.Session,
    base_url: str,
    remote_prefix: str,
    image_extensions: set[str],
) -> Iterable[tuple[str, float | None, int | None]]:
    """WebDAV 上の画像ファイルを BFS で列挙します。"""
    root_url = _join_collection_url(base_url, remote_prefix)
    queue: deque[tuple[str, str]] = deque([("", root_url)])
    seen_dirs: set[str] = set()
    while queue:
        rel_prefix, coll_url = queue.popleft()
        key = coll_url.rstrip("/")
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        tree = _propfind(session, coll_url, depth="1")
        for entry in _child_href_entries(coll_url, tree):
            child_rel = "/".join(part for part in (rel_prefix, entry.href) if part).replace("//", "/")
            base_name = entry.href.rstrip("/").split("/")[-1].lower()
            ext = "." + base_name.rsplit(".", 1)[-1] if "." in base_name else ""
            if entry.is_collection:
                child_url = _join_collection_url(base_url, remote_prefix, child_rel)
                queue.append((child_rel, child_url))
            elif ext in image_extensions:
                yield child_rel, entry.mtime, entry.size


def fetch_webdav_file(session: requests.Session, base_url: str, remote_prefix: str, rel: str) -> tuple[bytes, float | None, int]:
    """1ファイルを取得し、バイト列とメタデータを返します。"""
    file_url = _join_file_url(base_url, remote_prefix, rel)
    response = session.get(file_url, timeout=300)
    response.raise_for_status()
    data = response.content
    lm = response.headers.get("Last-Modified")
    mtime = _parse_iso_or_http_date(lm) if lm else None
    return data, mtime, len(data)


def _parts_join(*chunks: str) -> str:
    parts: list[str] = []
    for chunk in chunks:
        for piece in chunk.strip("/").split("/"):
            if piece:
                parts.append(piece)
    return "/".join(parts)


def _encode_rel(rel: str) -> str:
    return "/".join(quote(segment, safe="-_.~()") for segment in rel.strip("/").split("/") if segment)


def _join_collection_url(base_url: str, remote_prefix: str, *extra: str) -> str:
    base = base_url.rstrip("/") + "/"
    tail = _parts_join(remote_prefix, *extra)
    if not tail:
        return base
    return urljoin(base, _encode_rel(tail) + "/")


def _join_file_url(base_url: str, remote_prefix: str, rel: str) -> str:
    base = base_url.rstrip("/") + "/"
    tail = _parts_join(remote_prefix, rel)
    return urljoin(base, _encode_rel(tail))
