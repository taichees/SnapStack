from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
PENDING_NAME = ".google_oauth_pending.json"
TOKEN_NAME = "google_oauth_token.json"
CLIENT_FILE_NAME = "google_oauth_client.json"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8000/oauth/google/callback"


def _allow_http_redirect_for_local_dev(redirect_uri: str) -> None:
    """oauthlib は既定で http を拒否する。127.0.0.1 / localhost のローカル開発のみ許可する。"""
    if redirect_uri.startswith("http://") and (
        "127.0.0.1" in redirect_uri or "localhost" in redirect_uri
    ):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def token_path(data_dir: Path) -> Path:
    return data_dir / TOKEN_NAME


def pending_path(data_dir: Path) -> Path:
    return data_dir / PENDING_NAME


def client_secrets_path(data_dir: Path) -> Path:
    return data_dir / CLIENT_FILE_NAME


def load_client_file_raw(data_dir: Path) -> dict[str, str]:
    path = client_secrets_path(data_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v).strip() for k, v in raw.items() if isinstance(v, str)}


def _effective_redirect(file_d: dict[str, str]) -> str:
    env_r = os.getenv("SNAPSTACK_GOOGLE_REDIRECT_URI", "").strip()
    if env_r:
        return env_r
    f = file_d.get("redirect_uri", "").strip()
    return f if f else DEFAULT_REDIRECT_URI


def resolve_oauth_client(data_dir: Path) -> tuple[str, str, str] | None:
    file_d = load_client_file_raw(data_dir)
    cid = os.getenv("SNAPSTACK_GOOGLE_CLIENT_ID", "").strip() or file_d.get("client_id", "").strip()
    sec = os.getenv("SNAPSTACK_GOOGLE_CLIENT_SECRET", "").strip() or file_d.get("client_secret", "").strip()
    redir = _effective_redirect(file_d)
    if cid and sec:
        return (cid, sec, redir)
    return None


def oauth_client_configured(data_dir: Path) -> bool:
    return resolve_oauth_client(data_dir) is not None


def describe_oauth_client_for_ui(data_dir: Path) -> dict[str, Any]:
    file_d = load_client_file_raw(data_dir)
    r = resolve_oauth_client(data_dir)
    path = client_secrets_path(data_dir)
    env_id = bool(os.getenv("SNAPSTACK_GOOGLE_CLIENT_ID", "").strip())
    env_sec = bool(os.getenv("SNAPSTACK_GOOGLE_CLIENT_SECRET", "").strip())
    env_redir = bool(os.getenv("SNAPSTACK_GOOGLE_REDIRECT_URI", "").strip())
    client_id = (r[0] if r else (os.getenv("SNAPSTACK_GOOGLE_CLIENT_ID", "").strip() or file_d.get("client_id", "")))
    redirect_field = os.getenv("SNAPSTACK_GOOGLE_REDIRECT_URI", "").strip() or file_d.get("redirect_uri", "").strip()
    return {
        "configured": r is not None,
        "client_id": client_id,
        "redirect_uri": redirect_field,
        "effective_redirect_uri": _effective_redirect(file_d),
        "has_saved_client_file": path.exists(),
        "has_saved_secret_in_file": bool(file_d.get("client_secret", "").strip()),
        "env_overrides": {"client_id": env_id, "client_secret": env_sec, "redirect_uri": env_redir},
    }


def save_client_config_from_ui(
    data_dir: Path,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> None:
    file_d = load_client_file_raw(data_dir)
    secret = (client_secret or "").strip()
    if not secret:
        secret = file_d.get("client_secret", "").strip()
    if not secret:
        secret = os.getenv("SNAPSTACK_GOOGLE_CLIENT_SECRET", "").strip()
    if not secret:
        raise ValueError("クライアント秘密が必要です（初回は入力してください）")
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("クライアント ID が必要です")
    redir = (redirect_uri or "").strip() or DEFAULT_REDIRECT_URI
    payload: dict[str, str] = {"client_id": cid, "client_secret": secret}
    if redir != DEFAULT_REDIRECT_URI:
        payload["redirect_uri"] = redir
    data_dir.mkdir(parents=True, exist_ok=True)
    path = client_secrets_path(data_dir)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except (OSError, AttributeError):
        pass


def delete_client_config_file(data_dir: Path) -> bool:
    path = client_secrets_path(data_dir)
    if not path.exists():
        return False
    path.unlink()
    return True


def _client_config(data_dir: Path) -> dict[str, Any]:
    resolved = resolve_oauth_client(data_dir)
    if not resolved:
        raise RuntimeError("Google OAuth client is not configured")
    cid, sec, redir = resolved
    return {
        "web": {
            "client_id": cid,
            "client_secret": sec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redir],
        }
    }


def redirect_uri_for_flow(data_dir: Path) -> str:
    r = resolve_oauth_client(data_dir)
    if not r:
        return DEFAULT_REDIRECT_URI
    return r[2]


def is_connected(data_dir: Path) -> bool:
    return token_path(data_dir).exists()


def load_credentials(data_dir: Path) -> Credentials | None:
    path = token_path(data_dir)
    if not path.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(path), SCOPES)
    except (ValueError, json.JSONDecodeError, OSError):
        return None


def credentials_fresh(data_dir: Path) -> Credentials | None:
    creds = load_credentials(data_dir)
    if creds is None:
        return None
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            save_credentials(data_dir, creds)
        elif not creds.valid:
            return None
    return creds


def save_credentials(data_dir: Path, creds: Credentials) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = token_path(data_dir)
    path.write_text(creds.to_json(), encoding="utf-8")


def start_authorization(data_dir: Path) -> str:
    """Google 同意画面への URL を返します。"""
    if not oauth_client_configured(data_dir):
        raise RuntimeError("Google OAuth client is not configured")

    data_dir.mkdir(parents=True, exist_ok=True)
    redir = redirect_uri_for_flow(data_dir)
    _allow_http_redirect_for_local_dev(redir)
    flow = Flow.from_client_config(
        _client_config(data_dir),
        scopes=SCOPES,
        redirect_uri=redir,
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    if not flow.code_verifier:
        raise RuntimeError("OAuth PKCE code_verifier was not generated")
    pending_path(data_dir).write_text(
        json.dumps(
            {
                "state": state,
                "code_verifier": flow.code_verifier,
                "created": time.time(),
            }
        ),
        encoding="utf-8",
    )
    return authorization_url


def finish_authorization(data_dir: Path, *, authorization_response: str, state_param: str | None) -> None:
    """コールバック URL 全体からトークンを取得して保存します。"""
    raw = pending_path(data_dir).read_text(encoding="utf-8")
    pending = json.loads(raw)
    expected = pending.get("state")
    code_verifier = pending.get("code_verifier")
    if not expected or state_param != expected:
        raise ValueError("Invalid OAuth state")
    if not code_verifier:
        raise ValueError("OAuth session incomplete; start authorization again")
    if time.time() - float(pending.get("created", 0)) > 900:
        raise ValueError("OAuth session expired; start again")

    redir = redirect_uri_for_flow(data_dir)
    _allow_http_redirect_for_local_dev(redir)
    flow = Flow.from_client_config(
        _client_config(data_dir),
        scopes=SCOPES,
        redirect_uri=redir,
        state=expected,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.fetch_token(authorization_response=authorization_response)
    save_credentials(data_dir, flow.credentials)
    try:
        pending_path(data_dir).unlink()
    except OSError:
        pass


def disconnect(data_dir: Path) -> None:
    for name in (TOKEN_NAME, PENDING_NAME):
        p = data_dir / name
        if p.exists():
            p.unlink()
