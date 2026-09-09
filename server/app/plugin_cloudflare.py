from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import fresh_settings


_API_ROOT = "https://api.cloudflare.com/client/v4"
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,100}$")
_ACCOUNT_RE = re.compile(r"^[A-Fa-f0-9]{32}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,200}$")
_RUNTIME = fresh_settings()
_DATA_DIR = Path(_RUNTIME.app_dir) / "server" / "data"
DB_PATH = _DATA_DIR / "plugin-cloudflare.db"
KEY_PATH = _DATA_DIR / "plugin-cloudflare.key"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class CloudflarePluginError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _owner(value: str) -> str:
    clean = str(value or "").strip()
    if not _OWNER_RE.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("FDEX owner scope is invalid")
    return clean


def _account_id(value: str) -> str:
    clean = str(value or "").strip()
    if not _ACCOUNT_RE.fullmatch(clean):
        raise ValueError("Cloudflare Account ID 必须是 32 位十六进制标识")
    return clean


def _project_name(value: str) -> str:
    clean = str(value or "").strip()
    if not _NAME_RE.fullmatch(clean):
        raise ValueError("Cloudflare Pages project_name 格式无效")
    return clean


def _deployment_id(value: str) -> str:
    clean = str(value or "").strip()
    if not _ID_RE.fullmatch(clean):
        raise ValueError("Cloudflare Pages deployment_id 格式无效")
    return clean


def _load_or_create_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    if path.exists():
        key = path.read_bytes().strip()
        Fernet(key)
        return key
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key + b"\n")
    finally:
        os.close(fd)
    return key


def _error(response: httpx.Response, token: str) -> str:
    message = ""
    try:
        payload = response.json()
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            message = str(errors[0].get("message") or errors[0].get("code") or "")
    except ValueError:
        pass
    return message.replace(token, "[redacted]")[:700] if message else ""


def _request_with_token(token: str, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None, context: str) -> Any:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    try:
        response = httpx.request(method, f"{_API_ROOT}{path}", headers=headers, params=params, json=json_body, timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=False)
    except httpx.HTTPError as exc:
        raise CloudflarePluginError(f"{context}网络请求失败：{type(exc).__name__}") from exc
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise CloudflarePluginError(f"{context}响应过大，已拒绝处理")
    if response.status_code < 200 or response.status_code >= 300:
        detail = _error(response, token)
        raise CloudflarePluginError(f"{context}失败（HTTP {response.status_code}）" + (f"：{detail}" if detail else ""))
    if not response.content:
        return None
    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudflarePluginError(f"{context}返回了无效 JSON") from exc
    if isinstance(payload, dict) and payload.get("success") is False:
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        detail = str(errors[0].get("message") or "")[:700] if errors and isinstance(errors[0], dict) else ""
        raise CloudflarePluginError(f"{context}失败" + (f"：{detail}" if detail else ""))
    return payload.get("result") if isinstance(payload, dict) and "result" in payload else payload


class CloudflareCredentialStore:
    def __init__(self, path: Path = DB_PATH, key_path: Path = KEY_PATH) -> None:
        self.path = path.resolve()
        self.key_path = key_path.resolve()
        self._initialized = False
        self._fernet: Fernet | None = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(_load_or_create_key(self.key_path))
        return self._fernet

    def init(self) -> None:
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS cloudflare_connections (
                owner_id TEXT PRIMARY KEY, token_cipher TEXT NOT NULL, account_id TEXT NOT NULL,
                token_id TEXT NOT NULL DEFAULT '', token_status TEXT NOT NULL DEFAULT '', token_expires_on TEXT NOT NULL DEFAULT '',
                project_count INTEGER NOT NULL DEFAULT 0, last_checked_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        try:
            os.chmod(self.path.parent, 0o700)
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._initialized = True

    def put(self, owner_id: str, token: str, metadata: dict[str, Any]) -> dict[str, Any]:
        self.init()
        clean_owner = _owner(owner_id)
        raw = str(token or "").strip()
        if len(raw) < 20 or len(raw) > 4096 or "\x00" in raw:
            raise ValueError("Cloudflare API Token 格式无效")
        cipher = self.fernet.encrypt(raw.encode()).decode("ascii")
        now = _now()
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT created_at FROM cloudflare_connections WHERE owner_id=?", (clean_owner,)).fetchone()
            created = str(row[0]) if row else now
            conn.execute("""INSERT INTO cloudflare_connections(owner_id,token_cipher,account_id,token_id,token_status,token_expires_on,project_count,last_checked_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET token_cipher=excluded.token_cipher,account_id=excluded.account_id,
                token_id=excluded.token_id,token_status=excluded.token_status,token_expires_on=excluded.token_expires_on,project_count=excluded.project_count,
                last_checked_at=excluded.last_checked_at,updated_at=excluded.updated_at""",
                (clean_owner,cipher,str(metadata.get("account_id") or ""),str(metadata.get("token_id") or "")[:64],str(metadata.get("token_status") or "")[:40],str(metadata.get("token_expires_on") or "")[:100],int(metadata.get("project_count") or 0),now,created,now))
        return self.get(clean_owner) or {}

    def get(self, owner_id: str) -> dict[str, Any] | None:
        self.init()
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM cloudflare_connections WHERE owner_id=?", (_owner(owner_id),)).fetchone()
        if row is None:
            return None
        return {key: row[key] for key in ("owner_id","account_id","token_id","token_status","token_expires_on","project_count","last_checked_at","created_at","updated_at")} | {"token_configured": bool(row["token_cipher"])}

    def token(self, owner_id: str) -> str:
        self.init()
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT token_cipher FROM cloudflare_connections WHERE owner_id=?", (_owner(owner_id),)).fetchone()
        if row is None:
            raise CloudflarePluginError("Cloudflare 尚未连接")
        try:
            return self.fernet.decrypt(str(row[0]).encode("ascii")).decode()
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise CloudflarePluginError("Cloudflare 凭据无法解密，请重新连接") from exc

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute("DELETE FROM cloudflare_connections WHERE owner_id=?", (_owner(owner_id),))
        return max(0, int(cur.rowcount or 0))


@lru_cache(maxsize=1)
def cloudflare_credential_store() -> CloudflareCredentialStore:
    store = CloudflareCredentialStore(); store.init(); return store


def _connection(owner_id: str) -> tuple[dict[str, Any], str]:
    item = cloudflare_credential_store().get(owner_id)
    if item is None:
        raise CloudflarePluginError("Cloudflare 尚未连接")
    return item, cloudflare_credential_store().token(owner_id)


def _request(owner_id: str, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None, context: str) -> Any:
    _, token = _connection(owner_id)
    return _request_with_token(token, method, path, params=params, json_body=json_body, context=context)


def _project_summary(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    return {"name": str(item.get("name") or "")[:128], "subdomain": str(item.get("subdomain") or "")[:300], "domains": [str(x)[:300] for x in item.get("domains", []) if isinstance(x, str)][:50], "production_branch": str(item.get("production_branch") or "")[:300], "created_on": str(item.get("created_on") or "")[:100], "source": {"type": str(source.get("type") or "")[:80]}}


def _deployment_summary(item: dict[str, Any]) -> dict[str, Any]:
    stage = item.get("latest_stage") if isinstance(item.get("latest_stage"), dict) else {}
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    return {"id": str(item.get("id") or "")[:200], "project_name": str(item.get("project_name") or "")[:128], "environment": str(item.get("environment") or "")[:40], "url": str(item.get("url") or "")[:1200], "aliases": [str(x)[:300] for x in item.get("aliases", []) if isinstance(x, str)][:50], "created_on": str(item.get("created_on") or "")[:100], "modified_on": str(item.get("modified_on") or "")[:100], "stage_name": str(stage.get("name") or "")[:80], "stage_status": str(stage.get("status") or "")[:80], "branch": str(source.get("config", {}).get("branch") or "")[:300] if isinstance(source.get("config"), dict) else ""}


def connect_cloudflare(owner_id: str, api_token: str, account_id: str) -> dict[str, Any]:
    token = str(api_token or "").strip()
    if len(token) < 20 or len(token) > 4096 or "\x00" in token:
        raise ValueError("Cloudflare API Token 格式无效")
    account = _account_id(account_id)
    verified = _request_with_token(token, "GET", "/user/tokens/verify", context="Cloudflare Token 验证")
    if not isinstance(verified, dict) or str(verified.get("status") or "").lower() != "active":
        raise CloudflarePluginError("Cloudflare API Token 不是 active 状态")
    projects = _request_with_token(token, "GET", f"/accounts/{account}/pages/projects", params={"per_page": 100}, context="Cloudflare Pages 项目验证")
    rows = projects if isinstance(projects, list) else []
    return cloudflare_credential_store().put(_owner(owner_id), token, {"account_id": account, "token_id": verified.get("id"), "token_status": verified.get("status"), "token_expires_on": verified.get("expires_on"), "project_count": len(rows)})


def disconnect_cloudflare(owner_id: str) -> bool:
    return bool(cloudflare_credential_store().delete_owner(owner_id))


def cloudflare_connection_status(owner_id: str) -> dict[str, Any]:
    try:
        item = cloudflare_credential_store().get(owner_id)
    except (OSError, RuntimeError, ValueError):
        item = None
    if item is None:
        return {"connected": False, "connection_count": 0, "account_label": "", "connectable": True, "state": "available"}
    return {"connected": True, "connection_count": 1, "account_label": str(item.get("account_id") or "")[:32], "project_count": int(item.get("project_count") or 0), "connectable": True, "state": "connected"}


def list_cloudflare_pages_projects(owner_id: str, *, page: int = 1, per_page: int = 50) -> dict[str, Any]:
    item, _ = _connection(owner_id); account = _account_id(str(item["account_id"]))
    p = max(1, min(int(page), 100000)); n = max(1, min(int(per_page), 100))
    rows = _request(owner_id, "GET", f"/accounts/{account}/pages/projects", params={"page": p, "per_page": n}, context="Cloudflare Pages 项目列表")
    values = rows if isinstance(rows, list) else []
    return {"plugin_id": "cloudflare", "projects": [_project_summary(x) for x in values if isinstance(x, dict)], "count": len(values)}


def list_cloudflare_pages_deployments(owner_id: str, project_name: str, *, environment: str = "", page: int = 1, per_page: int = 25) -> dict[str, Any]:
    item, _ = _connection(owner_id); account = _account_id(str(item["account_id"])); project = _project_name(project_name)
    params: dict[str, Any] = {"page": max(1, int(page)), "per_page": max(1, min(int(per_page), 100))}
    env = str(environment or "").strip().lower()
    if env:
        if env not in {"production", "preview"}: raise ValueError("Cloudflare Pages environment 无效")
        params["env"] = env
    rows = _request(owner_id, "GET", f"/accounts/{account}/pages/projects/{quote(project, safe='')}/deployments", params=params, context="Cloudflare Pages 部署列表")
    values = rows if isinstance(rows, list) else []
    return {"plugin_id": "cloudflare", "deployments": [_deployment_summary(x) for x in values if isinstance(x, dict)], "count": len(values)}


def read_cloudflare_pages_deployment(owner_id: str, project_name: str, deployment_id: str) -> dict[str, Any]:
    item, _ = _connection(owner_id); account = _account_id(str(item["account_id"])); project = _project_name(project_name); deployment = _deployment_id(deployment_id)
    payload = _request(owner_id, "GET", f"/accounts/{account}/pages/projects/{quote(project, safe='')}/deployments/{quote(deployment, safe='')}", context="Cloudflare Pages 部署读取")
    if not isinstance(payload, dict): raise CloudflarePluginError("Cloudflare Pages 部署返回格式无效")
    return {"plugin_id": "cloudflare", "deployment": _deployment_summary(payload)}


def read_cloudflare_pages_logs(owner_id: str, project_name: str, deployment_id: str) -> dict[str, Any]:
    item, _ = _connection(owner_id); account = _account_id(str(item["account_id"])); project = _project_name(project_name); deployment = _deployment_id(deployment_id)
    payload = _request(owner_id, "GET", f"/accounts/{account}/pages/projects/{quote(project, safe='')}/deployments/{quote(deployment, safe='')}/history/logs", context="Cloudflare Pages 部署日志")
    if not isinstance(payload, dict): raise CloudflarePluginError("Cloudflare Pages 日志返回格式无效")
    data = payload.get("data") if isinstance(payload.get("data"), list) else []
    lines = [{"ts": str(x.get("ts") or "")[:100], "line": str(x.get("line") or "")[:4000]} for x in data[:500] if isinstance(x, dict)]
    return {"plugin_id": "cloudflare", "logs": lines, "count": len(lines), "truncated": len(data) > len(lines)}


def retry_cloudflare_pages_deployment(owner_id: str, project_name: str, deployment_id: str) -> dict[str, Any]:
    item, _ = _connection(owner_id); account = _account_id(str(item["account_id"])); project = _project_name(project_name); deployment = _deployment_id(deployment_id)
    payload = _request(owner_id, "POST", f"/accounts/{account}/pages/projects/{quote(project, safe='')}/deployments/{quote(deployment, safe='')}/retry", json_body={}, context="Cloudflare Pages 重试部署")
    return {"plugin_id": "cloudflare", "action": "deployment_retry_requested", "deployment": _deployment_summary(payload) if isinstance(payload, dict) else {"id": deployment}}


def rollback_cloudflare_pages_production(owner_id: str, project_name: str, deployment_id: str) -> dict[str, Any]:
    source = read_cloudflare_pages_deployment(owner_id, project_name, deployment_id)["deployment"]
    if str(source.get("environment") or "").lower() != "production":
        raise ValueError("Cloudflare Pages 只允许回滚到历史 production deployment")
    item, _ = _connection(owner_id); account = _account_id(str(item["account_id"])); project = _project_name(project_name); deployment = _deployment_id(deployment_id)
    payload = _request(owner_id, "POST", f"/accounts/{account}/pages/projects/{quote(project, safe='')}/deployments/{quote(deployment, safe='')}/rollback", json_body={}, context="Cloudflare Pages 生产回滚")
    return {"plugin_id": "cloudflare", "action": "production_rollback_requested", "deployment": _deployment_summary(payload) if isinstance(payload, dict) else {"id": deployment}}
