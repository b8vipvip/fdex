from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import secrets
import socket
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from app.config import fresh_settings
from app.remote_mcp_credentials import RemoteMcpCredentialStore, remote_mcp_credential_store
from app.remote_mcp_registry import RemoteMcpRegistry, remote_mcp_registry, resolve_public_addresses

_FLOW_MINUTES = 10
_REFRESH_SKEW_SECONDS = 90
_REFRESH_LOCK_SECONDS = 30
_MAX_SCOPES = 32
_MAX_SCOPE_CHARS = 128
_ALLOWED_CLIENT_AUTH = {"none", "client_secret_post", "client_secret_basic"}


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat(timespec="microseconds")


def _parse_time(value: str) -> datetime | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _clean_scopes(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = value.replace(",", " ").split()
    else:
        raw = [str(item) for item in value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        scope = str(item).strip()
        if not scope:
            continue
        if len(scope) > _MAX_SCOPE_CHARS or any(ch.isspace() or ord(ch) < 0x21 for ch in scope):
            raise ValueError("OAuth scope 无效")
        if scope not in seen:
            result.append(scope)
            seen.add(scope)
    if len(result) > _MAX_SCOPES:
        raise ValueError(f"OAuth scope 最多 {_MAX_SCOPES} 个")
    return tuple(result)


def normalize_oauth_endpoint(value: str, *, allow_query: bool) -> tuple[str, tuple[str, ...]]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048 or any(ord(ch) < 32 for ch in raw):
        raise ValueError("OAuth endpoint 无效或过长")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ValueError("OAuth endpoint 无效") from exc
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise ValueError("OAuth endpoint 必须是 HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("OAuth endpoint 不允许 URL userinfo")
    if parsed.fragment:
        raise ValueError("OAuth endpoint 不允许 fragment")
    if parsed.query and not allow_query:
        raise ValueError("该 OAuth endpoint 不允许预置 query")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("OAuth endpoint 端口无效") from exc
    if port not in (None, 443):
        raise ValueError("OAuth endpoint 当前仅允许 HTTPS 443")
    host = parsed.hostname.rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        canonical_host = str(literal).lower()
        netloc = f"[{canonical_host}]" if literal.version == 6 else canonical_host
    else:
        try:
            canonical_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("OAuth endpoint DNS 名称无效") from exc
        netloc = canonical_host
    path = parsed.path or "/"
    normalized = urlunsplit(("https", netloc, path, parsed.query if allow_query else "", ""))
    return normalized, resolve_public_addresses(canonical_host)


def _redirect_uri() -> str:
    base = fresh_settings().public_base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise RuntimeError("FDEX public_base_url 必须是 HTTPS 才能启用 Remote MCP OAuth")
    return f"{base}/account/agent/runtime/mcp/oauth/callback"


class _PinnedResolver(AbstractResolver):
    def __init__(self, hostname: str, addresses: tuple[str, ...]) -> None:
        self.hostname = hostname.casefold().rstrip(".")
        self.addresses = addresses

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        if host.casefold().rstrip(".") != self.hostname:
            raise OSError("OAuth resolver host mismatch")
        result: list[ResolveResult] = []
        for value in self.addresses:
            address = ipaddress.ip_address(value)
            address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            if family not in (socket.AF_UNSPEC, address_family):
                continue
            result.append(
                ResolveResult(
                    hostname=host,
                    host=str(address),
                    port=port,
                    family=address_family,
                    proto=socket.IPPROTO_TCP,
                    flags=socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
                )
            )
        if not result:
            raise OSError("OAuth resolver has no admitted address")
        return result

    async def close(self) -> None:
        return None


async def _oauth_post(url: str, data: dict[str, str], *, basic_auth: tuple[str, str] | None = None) -> dict[str, Any]:
    normalized, addresses = normalize_oauth_endpoint(url, allow_query=False)
    parsed = urlsplit(normalized)
    host = str(parsed.hostname)
    resolver = _PinnedResolver(host, addresses)
    connector = aiohttp.TCPConnector(resolver=resolver, ssl=True, use_dns_cache=False)
    headers = {"Accept": "application/json", "User-Agent": "FDEX-Remote-MCP-OAuth/1"}
    auth = aiohttp.BasicAuth(*basic_auth) if basic_auth is not None else None
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        trust_env=False,
        auto_decompress=False,
    ) as session:
        async with session.post(
            normalized,
            data=data,
            headers=headers,
            auth=auth,
            allow_redirects=False,
        ) as response:
            body = await response.read()
            if len(body) > 1024 * 1024:
                raise RuntimeError("OAuth token response exceeds 1 MiB")
            if 300 <= response.status < 400:
                raise RuntimeError("OAuth token endpoint redirect is not allowed")
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"OAuth token endpoint returned invalid JSON (HTTP {response.status})") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("OAuth token endpoint returned invalid object")
            if response.status < 200 or response.status >= 300:
                error = str(payload.get("error") or "token_exchange_failed")[:120]
                raise RuntimeError(f"OAuth token endpoint failed: HTTP {response.status} · {error}")
            return payload


class RemoteMcpOAuthStore:
    def __init__(
        self,
        registry: RemoteMcpRegistry | None = None,
        credentials: RemoteMcpCredentialStore | None = None,
    ) -> None:
        self.registry = registry or remote_mcp_registry()
        self.credentials = credentials or remote_mcp_credential_store()
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        self.registry.init()
        self.credentials.init()
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_mcp_oauth_configs (
                    owner_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    authorization_url TEXT NOT NULL,
                    token_url TEXT NOT NULL,
                    revocation_url TEXT NOT NULL DEFAULT '',
                    client_id TEXT NOT NULL,
                    client_secret_cipher TEXT NOT NULL DEFAULT '',
                    client_auth_method TEXT NOT NULL DEFAULT 'none',
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    refresh_lock_token TEXT NOT NULL DEFAULT '',
                    refresh_lock_until TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(owner_id,server_id)
                );
                CREATE TABLE IF NOT EXISTS remote_mcp_oauth_flows (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    state_hash TEXT NOT NULL UNIQUE,
                    verifier_cipher TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE INDEX IF NOT EXISTS idx_remote_mcp_oauth_flows_owner
                    ON remote_mcp_oauth_flows(owner_id,expires_at);
                """
            )
            now = _now()
            conn.execute(
                "DELETE FROM remote_mcp_oauth_flows WHERE expires_at<=? OR state<>'pending'",
                (now,),
            )
        self._initialized = True

    def config(self, owner_id: str, server_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
        self.init()
        if self.registry.get(owner_id, server_id) is None:
            return None
        with self.registry.db() as conn:
            row = conn.execute(
                "SELECT * FROM remote_mcp_oauth_configs WHERE owner_id=? AND server_id=?",
                (owner_id, server_id),
            ).fetchone()
        if row is None:
            return None
        try:
            scopes = json.loads(str(row["scopes_json"] or "[]"))
        except json.JSONDecodeError:
            scopes = []
        secret_cipher = str(row["client_secret_cipher"] or "")
        result: dict[str, Any] = {
            "owner_id": str(row["owner_id"]),
            "server_id": str(row["server_id"]),
            "authorization_url": str(row["authorization_url"]),
            "token_url": str(row["token_url"]),
            "revocation_url": str(row["revocation_url"]),
            "client_id": str(row["client_id"]),
            "client_auth_method": str(row["client_auth_method"]),
            "scopes": [str(item) for item in scopes if str(item)],
            "has_client_secret": bool(secret_cipher),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        if include_secret:
            result["client_secret"] = self.credentials.open_text(secret_cipher) if secret_cipher else ""
        return result

    def list_configs(self, owner_id: str) -> dict[str, dict[str, Any]]:
        self.init()
        return {
            str(server["id"]): config
            for server in self.registry.list(owner_id)
            if (config := self.config(owner_id, str(server["id"]))) is not None
        }

    def save_config(
        self,
        owner_id: str,
        server_id: str,
        *,
        authorization_url: str,
        token_url: str,
        client_id: str,
        scopes: str | list[str],
        client_auth_method: str = "none",
        client_secret: str = "",
        revocation_url: str = "",
    ) -> dict[str, Any]:
        self.init()
        if self.registry.get(owner_id, server_id) is None:
            raise KeyError("Remote MCP 不存在或不属于当前账号")
        auth_url, _ = normalize_oauth_endpoint(authorization_url, allow_query=True)
        token_url_clean, _ = normalize_oauth_endpoint(token_url, allow_query=False)
        revoke_clean = ""
        if str(revocation_url or "").strip():
            revoke_clean, _ = normalize_oauth_endpoint(revocation_url, allow_query=False)
        client_id_clean = str(client_id or "").strip()
        if not client_id_clean or len(client_id_clean) > 512 or any(ord(ch) < 0x20 for ch in client_id_clean):
            raise ValueError("OAuth client_id 无效")
        method = str(client_auth_method or "none").strip().casefold()
        if method not in _ALLOWED_CLIENT_AUTH:
            raise ValueError("OAuth client authentication method 不受支持")
        current = self.config(owner_id, server_id, include_secret=True)
        secret = str(client_secret or "")
        if not secret and current:
            secret = str(current.get("client_secret") or "")
        if method != "none" and not secret:
            raise ValueError("该 OAuth client authentication method 需要 client_secret")
        if method == "none":
            secret = ""
        secret_cipher = self.credentials.seal_text(secret) if secret else ""
        scopes_clean = _clean_scopes(scopes)
        now = _now()
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT created_at FROM remote_mcp_oauth_configs WHERE owner_id=? AND server_id=?",
                (owner_id, server_id),
            ).fetchone()
            created = str(row["created_at"]) if row is not None else now
            conn.execute(
                """
                INSERT INTO remote_mcp_oauth_configs(
                    owner_id,server_id,authorization_url,token_url,revocation_url,client_id,
                    client_secret_cipher,client_auth_method,scopes_json,created_at,updated_at,
                    refresh_lock_token,refresh_lock_until
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'','')
                ON CONFLICT(owner_id,server_id) DO UPDATE SET
                    authorization_url=excluded.authorization_url,
                    token_url=excluded.token_url,
                    revocation_url=excluded.revocation_url,
                    client_id=excluded.client_id,
                    client_secret_cipher=excluded.client_secret_cipher,
                    client_auth_method=excluded.client_auth_method,
                    scopes_json=excluded.scopes_json,
                    updated_at=excluded.updated_at,
                    refresh_lock_token='',refresh_lock_until=''
                """,
                (
                    owner_id,
                    server_id,
                    auth_url,
                    token_url_clean,
                    revoke_clean,
                    client_id_clean,
                    secret_cipher,
                    method,
                    json.dumps(scopes_clean, separators=(",", ":")),
                    created,
                    now,
                ),
            )
            RemoteMcpCredentialStore._revoke_server_leases(conn, owner_id, server_id, now)
        result = self.config(owner_id, server_id)
        if result is None:
            raise RuntimeError("Remote MCP OAuth config write failed")
        return result

    def begin_flow(self, owner_id: str, server_id: str) -> dict[str, str]:
        self.init()
        config = self.config(owner_id, server_id)
        if config is None:
            raise ValueError("请先保存 OAuth 配置")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        redirect_uri = _redirect_uri()
        flow_id = f"oauth_{uuid.uuid4().hex}"
        now = _now_dt()
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM remote_mcp_oauth_flows WHERE owner_id=? AND server_id=?",
                (owner_id, server_id),
            )
            conn.execute(
                """
                INSERT INTO remote_mcp_oauth_flows(
                    id,owner_id,server_id,state_hash,verifier_cipher,redirect_uri,
                    created_at,expires_at,state
                ) VALUES(?,?,?,?,?,?,?,?,'pending')
                """,
                (
                    flow_id,
                    owner_id,
                    server_id,
                    _state_hash(state),
                    self.credentials.seal_text(verifier),
                    redirect_uri,
                    now.isoformat(timespec="microseconds"),
                    (now + timedelta(minutes=_FLOW_MINUTES)).isoformat(timespec="microseconds"),
                ),
            )
        parsed = urlsplit(str(config["authorization_url"]))
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(
            {
                "response_type": "code",
                "client_id": str(config["client_id"]),
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        scopes = [str(item) for item in config.get("scopes") or []]
        if scopes:
            query["scope"] = " ".join(scopes)
        authorization_url = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), "")
        )
        return {"flow_id": flow_id, "authorization_url": authorization_url}

    def claim_flow(self, owner_id: str, state: str) -> dict[str, Any]:
        self.init()
        state_clean = str(state or "").strip()
        if len(state_clean) < 32:
            raise ValueError("OAuth state 无效")
        now = _now()
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM remote_mcp_oauth_flows
                WHERE owner_id=? AND state_hash=? AND state='pending'
                """,
                (owner_id, _state_hash(state_clean)),
            ).fetchone()
            if row is None:
                raise ValueError("OAuth state 不存在、已使用或不属于当前账号")
            if str(row["expires_at"]) <= now:
                conn.execute("DELETE FROM remote_mcp_oauth_flows WHERE id=?", (row["id"],))
                raise ValueError("OAuth state 已过期")
            cursor = conn.execute(
                "UPDATE remote_mcp_oauth_flows SET state='claimed' WHERE id=? AND state='pending'",
                (row["id"],),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("OAuth callback 已被另一个 worker 领取")
        return {
            "id": str(row["id"]),
            "owner_id": str(row["owner_id"]),
            "server_id": str(row["server_id"]),
            "verifier": self.credentials.open_text(str(row["verifier_cipher"])),
            "redirect_uri": str(row["redirect_uri"]),
        }

    def finish_flow(self, flow_id: str) -> None:
        self.init()
        with self.registry.db() as conn:
            conn.execute("DELETE FROM remote_mcp_oauth_flows WHERE id=?", (flow_id,))

    def claim_refresh(self, owner_id: str, server_id: str) -> str | None:
        self.init()
        now = _now_dt()
        token = secrets.token_urlsafe(24)
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT refresh_lock_token,refresh_lock_until FROM remote_mcp_oauth_configs
                WHERE owner_id=? AND server_id=?
                """,
                (owner_id, server_id),
            ).fetchone()
            if row is None:
                return None
            until = _parse_time(str(row["refresh_lock_until"] or ""))
            if until is not None and until > now and str(row["refresh_lock_token"] or ""):
                return None
            conn.execute(
                """
                UPDATE remote_mcp_oauth_configs
                SET refresh_lock_token=?,refresh_lock_until=?
                WHERE owner_id=? AND server_id=?
                """,
                (
                    token,
                    (now + timedelta(seconds=_REFRESH_LOCK_SECONDS)).isoformat(timespec="microseconds"),
                    owner_id,
                    server_id,
                ),
            )
        return token

    def release_refresh(self, owner_id: str, server_id: str, token: str) -> None:
        self.init()
        with self.registry.db() as conn:
            conn.execute(
                """
                UPDATE remote_mcp_oauth_configs
                SET refresh_lock_token='',refresh_lock_until=''
                WHERE owner_id=? AND server_id=? AND refresh_lock_token=?
                """,
                (owner_id, server_id, token),
            )

    def delete_config(self, owner_id: str, server_id: str) -> bool:
        self.init()
        now = _now()
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM remote_mcp_oauth_flows WHERE owner_id=? AND server_id=?",
                (owner_id, server_id),
            )
            cursor = conn.execute(
                "DELETE FROM remote_mcp_oauth_configs WHERE owner_id=? AND server_id=?",
                (owner_id, server_id),
            )
            if cursor.rowcount:
                RemoteMcpCredentialStore._revoke_server_leases(conn, owner_id, server_id, now)
        return bool(cursor.rowcount)

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        self.init()
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            flows = conn.execute("DELETE FROM remote_mcp_oauth_flows WHERE owner_id=?", (owner_id,))
            configs = conn.execute("DELETE FROM remote_mcp_oauth_configs WHERE owner_id=?", (owner_id,))
        return {"flows": max(0, int(flows.rowcount)), "configs": max(0, int(configs.rowcount))}


@lru_cache
def remote_mcp_oauth_store() -> RemoteMcpOAuthStore:
    store = RemoteMcpOAuthStore()
    store.init()
    return store


def _token_expiry(payload: dict[str, Any]) -> str:
    expires_in = payload.get("expires_in")
    try:
        seconds = max(0, min(int(expires_in), 31_536_000))
    except (TypeError, ValueError):
        return ""
    return (_now_dt() + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def _client_auth(config: dict[str, Any], data: dict[str, str]) -> tuple[str, str] | None:
    method = str(config.get("client_auth_method") or "none")
    secret = str(config.get("client_secret") or "")
    client_id = str(config["client_id"])
    if method == "client_secret_post":
        data["client_secret"] = secret
        return None
    if method == "client_secret_basic":
        return client_id, secret
    return None


async def exchange_oauth_code(owner_id: str, flow: dict[str, Any], code: str) -> dict[str, Any]:
    store = remote_mcp_oauth_store()
    credentials = remote_mcp_credential_store()
    server_id = str(flow["server_id"])
    config = store.config(owner_id, server_id, include_secret=True)
    if config is None:
        raise RuntimeError("OAuth 配置已被删除")
    clean_code = str(code or "").strip()
    if not clean_code or len(clean_code) > 8192:
        raise ValueError("OAuth authorization code 无效")
    data = {
        "grant_type": "authorization_code",
        "code": clean_code,
        "redirect_uri": str(flow["redirect_uri"]),
        "client_id": str(config["client_id"]),
        "code_verifier": str(flow["verifier"]),
    }
    basic = _client_auth(config, data)
    payload = await _oauth_post(str(config["token_url"]), data, basic_auth=basic)
    access = str(payload.get("access_token") or "")
    if not access:
        raise RuntimeError("OAuth token response 缺少 access_token")
    metadata = credentials.set_oauth_grant(
        owner_id,
        server_id,
        access_token=access,
        refresh_token=str(payload.get("refresh_token") or ""),
        expires_at=_token_expiry(payload),
        scope=str(payload.get("scope") or " ".join(config.get("scopes") or [])),
        token_type=str(payload.get("token_type") or "Bearer"),
    )
    return metadata


async def _refresh_oauth(owner_id: str, server_id: str, expected_revision: str) -> dict[str, Any]:
    store = remote_mcp_oauth_store()
    credentials = remote_mcp_credential_store()
    config = store.config(owner_id, server_id, include_secret=True)
    if config is None:
        raise RuntimeError("OAuth 配置已被删除")
    grant = credentials.get_oauth_grant(owner_id, server_id, expected_revision=expected_revision)
    if grant is None:
        raise RuntimeError("OAuth grant 已被撤销")
    refresh_token = str(grant.get("refresh_token") or "")
    if not refresh_token:
        raise RuntimeError("OAuth access token 已过期且没有 refresh_token")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": str(config["client_id"]),
    }
    scopes = [str(item) for item in config.get("scopes") or []]
    if scopes:
        data["scope"] = " ".join(scopes)
    basic = _client_auth(config, data)
    payload = await _oauth_post(str(config["token_url"]), data, basic_auth=basic)
    access = str(payload.get("access_token") or "")
    if not access:
        raise RuntimeError("OAuth refresh response 缺少 access_token")
    new_refresh = str(payload.get("refresh_token") or refresh_token)
    credentials.refresh_oauth_grant(
        owner_id,
        server_id,
        expected_revision=expected_revision,
        access_token=access,
        refresh_token=new_refresh,
        expires_at=_token_expiry(payload),
        scope=str(payload.get("scope") or grant.get("scope") or ""),
        token_type=str(payload.get("token_type") or "Bearer"),
    )
    refreshed = credentials.get_oauth_grant(owner_id, server_id, expected_revision=expected_revision)
    if refreshed is None:
        raise RuntimeError("OAuth grant vanished after refresh")
    return refreshed


async def oauth_access_token(owner_id: str, server_id: str, expected_revision: str) -> str:
    store = remote_mcp_oauth_store()
    credentials = remote_mcp_credential_store()
    grant = credentials.get_oauth_grant(owner_id, server_id, expected_revision=expected_revision)
    if grant is None:
        raise RuntimeError("OAuth grant 不存在")
    expires = _parse_time(str(grant.get("expires_at") or ""))
    if expires is None or expires > _now_dt() + timedelta(seconds=_REFRESH_SKEW_SECONDS):
        return str(grant["access_token"])

    lock_token = store.claim_refresh(owner_id, server_id)
    if lock_token:
        try:
            refreshed = await _refresh_oauth(owner_id, server_id, expected_revision)
            return str(refreshed["access_token"])
        finally:
            store.release_refresh(owner_id, server_id, lock_token)

    # Another worker owns the refresh lock. Wait only a bounded period for its durable update.
    original_updated = str(grant.get("updated_at") or "")
    for _ in range(20):
        await asyncio.sleep(0.25)
        current = credentials.get_oauth_grant(
            owner_id,
            server_id,
            expected_revision=expected_revision,
        )
        if current is None:
            raise RuntimeError("OAuth grant disappeared while waiting for refresh")
        current_expires = _parse_time(str(current.get("expires_at") or ""))
        if str(current.get("updated_at") or "") != original_updated and (
            current_expires is None
            or current_expires > _now_dt() + timedelta(seconds=_REFRESH_SKEW_SECONDS)
        ):
            return str(current["access_token"])
    raise RuntimeError("OAuth refresh is busy or timed out")


async def revoke_oauth_grant(owner_id: str, server_id: str) -> bool:
    store = remote_mcp_oauth_store()
    credentials = remote_mcp_credential_store()
    metadata = credentials.metadata(owner_id, server_id)
    if metadata is None or str(metadata.get("auth_type")) != "oauth":
        return False
    grant = credentials.get_oauth_grant(owner_id, server_id, expected_revision=credentials.revision(owner_id, server_id))
    config = store.config(owner_id, server_id, include_secret=True)
    if grant is None:
        return False
    if config and str(config.get("revocation_url") or ""):
        # Prefer refresh token because revoking it generally invalidates the whole grant. If no
        # refresh token exists, revoke the access token. A configured remote revocation failure
        # keeps the local encrypted grant so the user can retry instead of falsely claiming revoke.
        candidate = str(grant.get("refresh_token") or grant.get("access_token") or "")
        if candidate:
            data = {"token": candidate, "client_id": str(config["client_id"])}
            basic = _client_auth(config, data)
            await _oauth_post(str(config["revocation_url"]), data, basic_auth=basic)
    return credentials.delete(owner_id, server_id)
