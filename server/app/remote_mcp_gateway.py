from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, AsyncIterator, Iterator
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.responses import Response

from app.config import fresh_settings
from app.remote_mcp_credentials import RemoteMcpCredentialStore, remote_mcp_credential_store
from app.remote_mcp_oauth import oauth_access_token
from app.remote_mcp_registry import RemoteMcpRegistry, remote_mcp_registry, resolve_public_addresses

router = APIRouter(prefix="/internal/codex-mcp", include_in_schema=False)

_MAX_REQUEST_BODY = 8 * 1024 * 1024
_LEASE_HOURS = 6
_CAPABILITY_HEADER = "X-FDEX-MCP-Capability"
_ALLOWED_METHODS = {"GET", "POST", "DELETE"}
_PROXY_MARKER_HEADERS = {
    "forwarded",
    "via",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
}
_REQUEST_HEADER_ALLOWLIST = {
    "accept",
    "content-type",
    "mcp-protocol-version",
    "mcp-session-id",
    "last-event-id",
}
_RESPONSE_HEADER_ALLOWLIST = {
    "content-type",
    "content-encoding",
    "cache-control",
    "mcp-protocol-version",
    "mcp-session-id",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _direct_loopback_client(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if not address.is_loopback:
        return False
    return not any(request.headers.get(name) for name in _PROXY_MARKER_HEADERS)


def _safe_server_config_name(name: str, server_id: str) -> str:
    suffix = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)[:40] or "mcp"
    stable = hashlib.sha256(server_id.encode("utf-8")).hexdigest()[:10]
    return f"fdex_{suffix}_{stable}"


class RemoteMcpLeaseStore:
    """Cross-worker ephemeral capability leases for the local MCP gateway.

    A lease is bound to both the public registry revision and the FDEX-held credential grant
    revision. Static Bearer rotation changes that revision. OAuth access-token refresh keeps the
    same grant revision, while OAuth reauthorization/revocation changes it and invalidates leases.
    """

    def __init__(
        self,
        registry: RemoteMcpRegistry | None = None,
        credential_store: RemoteMcpCredentialStore | None = None,
    ) -> None:
        self.registry = registry or remote_mcp_registry()
        if credential_store is not None:
            self.credentials = credential_store
        elif registry is None:
            self.credentials = remote_mcp_credential_store()
        else:
            self.credentials = RemoteMcpCredentialStore(
                self.registry,
                key_path=self.registry.path.with_name(f".{self.registry.path.name}.credentials.key"),
            )
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        self.registry.init()
        self.credentials.init()
        with self.registry.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_mcp_leases (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    server_updated_at TEXT NOT NULL DEFAULT '',
                    credential_updated_at TEXT NOT NULL DEFAULT '',
                    token_hash TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT '',
                    revoked_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_remote_mcp_leases_task
                    ON remote_mcp_leases(owner_id,task_id,state,expires_at);
                CREATE INDEX IF NOT EXISTS idx_remote_mcp_leases_server
                    ON remote_mcp_leases(owner_id,server_id,state,expires_at);
                """
            )
            conn.execute("BEGIN IMMEDIATE")
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(remote_mcp_leases)").fetchall()}
            if "server_updated_at" not in columns:
                conn.execute("ALTER TABLE remote_mcp_leases ADD COLUMN server_updated_at TEXT NOT NULL DEFAULT ''")
            if "credential_updated_at" not in columns:
                conn.execute("ALTER TABLE remote_mcp_leases ADD COLUMN credential_updated_at TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                UPDATE remote_mcp_leases
                SET state='revoked',revoked_at=?
                WHERE state='active' AND server_updated_at=''
                """,
                (_iso(_now()),),
            )
        self._initialized = True

    def issue(self, owner_id: str, task_id: str, server_id: str) -> tuple[dict[str, Any], str]:
        self.init()
        server = self.registry.get(owner_id, server_id)
        if server is None or not bool(server.get("enabled")):
            raise ValueError("Remote MCP is not enabled for this owner")
        if not server.get("enabled_tools"):
            raise ValueError("Remote MCP has no explicit tool allowlist")
        server_updated_at = str(server.get("updated_at") or "")
        if not server_updated_at:
            raise ValueError("Remote MCP registry revision is missing")
        credential_updated_at = self.credentials.revision(owner_id, server_id)
        raw_token = secrets.token_urlsafe(32)
        lease_id = f"lease_{secrets.token_hex(16)}"
        now = _now()
        expires = now + timedelta(hours=_LEASE_HOURS)
        with self.registry.db() as conn:
            conn.execute(
                """
                INSERT INTO remote_mcp_leases(
                    id,owner_id,task_id,server_id,server_updated_at,credential_updated_at,
                    token_hash,state,created_at,expires_at,last_used_at,revoked_at
                ) VALUES(?,?,?,?,?,?,?,'active',?,?,?,'')
                """,
                (
                    lease_id,
                    owner_id,
                    task_id,
                    server_id,
                    server_updated_at,
                    credential_updated_at,
                    _token_hash(raw_token),
                    _iso(now),
                    _iso(expires),
                    "",
                ),
            )
        return (
            {
                "id": lease_id,
                "owner_id": owner_id,
                "task_id": task_id,
                "server_id": server_id,
                "server_updated_at": server_updated_at,
                "credential_updated_at": credential_updated_at,
                "expires_at": _iso(expires),
            },
            raw_token,
        )

    def resolve(self, lease_id: str, token: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        self.init()
        if not lease_id.startswith("lease_") or len(token) < 32:
            return None
        with self.registry.db() as conn:
            row = conn.execute("SELECT * FROM remote_mcp_leases WHERE id=?", (lease_id,)).fetchone()
            if row is None or str(row["state"]) != "active":
                return None
            now = _now()
            if _parse_time(str(row["expires_at"])) <= now:
                conn.execute(
                    "UPDATE remote_mcp_leases SET state='expired',revoked_at=? WHERE id=? AND state='active'",
                    (_iso(now), lease_id),
                )
                return None
            if not hmac.compare_digest(str(row["token_hash"]), _token_hash(token)):
                return None
            owner_id = str(row["owner_id"])
            server_id = str(row["server_id"])
            server = self.registry.get(owner_id, server_id)
            current_server_revision = str((server or {}).get("updated_at") or "")
            issued_server_revision = str(row["server_updated_at"] or "")
            current_credential_revision = self.credentials.revision(owner_id, server_id)
            issued_credential_revision = str(row["credential_updated_at"] or "")
            if (
                server is None
                or not bool(server.get("enabled"))
                or not server.get("enabled_tools")
                or not current_server_revision
                or current_server_revision != issued_server_revision
                or current_credential_revision != issued_credential_revision
            ):
                conn.execute(
                    "UPDATE remote_mcp_leases SET state='revoked',revoked_at=? WHERE id=? AND state='active'",
                    (_iso(now), lease_id),
                )
                return None
            conn.execute(
                "UPDATE remote_mcp_leases SET last_used_at=? WHERE id=? AND state='active'",
                (_iso(now), lease_id),
            )
            lease = {
                "id": str(row["id"]),
                "owner_id": owner_id,
                "task_id": str(row["task_id"]),
                "server_id": server_id,
                "server_updated_at": issued_server_revision,
                "credential_updated_at": issued_credential_revision,
                "expires_at": str(row["expires_at"]),
            }
        return lease, server

    def revoke_task(self, owner_id: str, task_id: str) -> int:
        self.init()
        now = _iso(_now())
        with self.registry.db() as conn:
            cursor = conn.execute(
                """
                UPDATE remote_mcp_leases SET state='revoked',revoked_at=?
                WHERE owner_id=? AND task_id=? AND state='active'
                """,
                (now, owner_id, task_id),
            )
        return max(0, int(cursor.rowcount))

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        with self.registry.db() as conn:
            cursor = conn.execute("DELETE FROM remote_mcp_leases WHERE owner_id=?", (owner_id,))
        return max(0, int(cursor.rowcount))

    def purge_expired(self) -> int:
        self.init()
        now = _iso(_now())
        with self.registry.db() as conn:
            cursor = conn.execute(
                """
                UPDATE remote_mcp_leases SET state='expired',revoked_at=?
                WHERE state='active' AND expires_at<=?
                """,
                (now, now),
            )
        return max(0, int(cursor.rowcount))


@lru_cache
def remote_mcp_lease_store() -> RemoteMcpLeaseStore:
    store = RemoteMcpLeaseStore()
    store.init()
    return store


def build_codex_remote_mcp_config(owner_id: str, task_id: str) -> dict[str, dict[str, Any]]:
    registry = remote_mcp_registry()
    leases = remote_mcp_lease_store()
    leases.revoke_task(owner_id, task_id)
    port = int(fresh_settings().fdex_port)
    if not 1 <= port <= 65535:
        raise ValueError("FDEX internal HTTP port is invalid")
    result: dict[str, dict[str, Any]] = {}
    for server in registry.list(owner_id):
        if not bool(server.get("enabled")):
            continue
        enabled_tools = [str(item) for item in server.get("enabled_tools") or [] if str(item)]
        if not enabled_tools:
            continue
        lease, token = leases.issue(owner_id, task_id, str(server["id"]))
        local_url = f"http://127.0.0.1:{port}/internal/codex-mcp/{lease['id']}"
        result[_safe_server_config_name(str(server["name"]), str(server["id"]))] = {
            "url": local_url,
            "http_headers": {_CAPABILITY_HEADER: token},
            "enabled": True,
            "required": False,
            "startup_timeout_sec": int(server["startup_timeout_sec"]),
            "tool_timeout_sec": int(server["tool_timeout_sec"]),
            "enabled_tools": enabled_tools,
            "default_tools_approval_mode": "writes",
        }
    return result


class PinnedResolver(AbstractResolver):
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
            raise OSError("Remote MCP resolver host mismatch")
        results: list[ResolveResult] = []
        for value in self.addresses:
            address = ipaddress.ip_address(value)
            address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            if family not in (socket.AF_UNSPEC, address_family):
                continue
            results.append(
                ResolveResult(
                    hostname=host,
                    host=str(address),
                    port=port,
                    family=address_family,
                    proto=socket.IPPROTO_TCP,
                    flags=socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
                )
            )
        if not results:
            raise OSError("Remote MCP resolver has no address for requested family")
        return results

    async def close(self) -> None:
        return None


def _forward_request_headers(request: Request, *, bearer_token: str | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _REQUEST_HEADER_ALLOWLIST:
            result[key] = value
    result["Accept-Encoding"] = "identity"
    result["User-Agent"] = "FDEX-Remote-MCP-Gateway/1"
    if bearer_token:
        result["Authorization"] = f"Bearer {bearer_token}"
    return result


def _forward_response_headers(response: aiohttp.ClientResponse) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in response.headers.items():
        if key.lower() in _RESPONSE_HEADER_ALLOWLIST:
            result[key] = value
    return result


def _tool_calls(payload: Any) -> Iterator[str]:
    records = payload if isinstance(payload, list) else [payload]
    for record in records:
        if not isinstance(record, dict) or str(record.get("method") or "") != "tools/call":
            continue
        params = record.get("params")
        if not isinstance(params, dict) or not str(params.get("name") or "").strip():
            raise ValueError("MCP tools/call is missing params.name")
        yield str(params["name"]).strip()


def enforce_tool_allowlist(body: bytes, content_type: str, enabled_tools: list[str]) -> None:
    if not body:
        return
    if "application/json" not in (content_type or "").lower():
        raise ValueError("FDEX only proxies inspectable application/json MCP POST requests")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Remote MCP request is not valid JSON") from exc
    allowed = set(enabled_tools)
    for name in _tool_calls(payload):
        if name not in allowed:
            raise PermissionError(f"Remote MCP tool is outside the owner allowlist: {name}")


async def _bounded_body(request: Request) -> bytes:
    length = request.headers.get("content-length", "").strip()
    if length:
        try:
            declared = int(length)
        except ValueError as exc:
            raise ValueError("Remote MCP Content-Length is invalid") from exc
        if declared < 0:
            raise ValueError("Remote MCP Content-Length is invalid")
        if declared > _MAX_REQUEST_BODY:
            raise ValueError("Remote MCP request body exceeds 8 MiB")
    body = await request.body()
    if len(body) > _MAX_REQUEST_BODY:
        raise ValueError("Remote MCP request body exceeds 8 MiB")
    return body


async def _relay(
    request: Request,
    *,
    server: dict[str, Any],
    body: bytes,
    bearer_token: str | None = None,
) -> Response:
    target = str(server["url"])
    parsed = urlsplit(target)
    hostname = str(parsed.hostname or "").rstrip(".").lower()
    addresses = resolve_public_addresses(hostname)
    resolver = PinnedResolver(hostname, addresses)
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        force_close=True,
        limit=1,
        enable_cleanup_closed=True,
    )
    tool_timeout = max(2, min(600, int(server.get("tool_timeout_sec") or 60)))
    timeout = aiohttp.ClientTimeout(
        total=None if request.method == "GET" else tool_timeout,
        connect=15,
        sock_connect=15,
        sock_read=None,
    )
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        auto_decompress=False,
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
    )
    try:
        upstream = await session.request(
            request.method,
            target,
            data=body if body else None,
            headers=_forward_request_headers(request, bearer_token=bearer_token),
            allow_redirects=False,
            ssl=True,
        )
    except Exception:
        await session.close()
        raise

    if 300 <= upstream.status < 400:
        upstream.release()
        await session.close()
        return JSONResponse(
            {"error": "Remote MCP redirects are blocked by FDEX destination policy"},
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )
    if upstream.status in {401, 403, 407}:
        upstream.release()
        await session.close()
        return JSONResponse(
            {"error": "Remote MCP authentication failed"},
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )

    response_headers = _forward_response_headers(upstream)
    response_headers["X-FDEX-MCP-Gateway"] = "pinned"

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                if chunk:
                    yield bytes(chunk)
        finally:
            upstream.release()
            await session.close()

    return StreamingResponse(stream(), status_code=upstream.status, headers=response_headers, media_type=None)


async def _authorization_bearer(lease: dict[str, Any]) -> str | None:
    owner_id = str(lease["owner_id"])
    server_id = str(lease["server_id"])
    expected = str(lease.get("credential_updated_at") or "")
    credentials = remote_mcp_credential_store()
    metadata = credentials.metadata(owner_id, server_id)
    if metadata is None:
        if expected:
            raise RuntimeError("Remote MCP credential disappeared after lease admission")
        return None
    current_revision = str(metadata.get("lease_revision") or "")
    if current_revision != expected:
        raise RuntimeError("Remote MCP credential changed after lease admission")
    auth_type = str(metadata.get("auth_type") or "")
    if auth_type == "bearer":
        return credentials.get_bearer(owner_id, server_id, expected_revision=expected)
    if auth_type == "oauth":
        return await oauth_access_token(owner_id, server_id, expected)
    raise RuntimeError("Remote MCP credential type is unsupported")


@router.api_route("/{lease_id}", methods=["GET", "POST", "DELETE"], response_model=None)
async def remote_mcp_gateway(lease_id: str, request: Request) -> Response:
    if not _direct_loopback_client(request):
        return PlainTextResponse("not found", status_code=404)
    if request.method not in _ALLOWED_METHODS or request.url.query:
        return PlainTextResponse("method/query not allowed", status_code=405)
    token = request.headers.get(_CAPABILITY_HEADER, "")
    resolved = remote_mcp_lease_store().resolve(lease_id, token)
    if resolved is None:
        return PlainTextResponse("expired or invalid capability", status_code=404)
    lease, server = resolved
    try:
        bearer_token = await _authorization_bearer(lease)
        body = await _bounded_body(request)
        if request.method == "POST":
            enforce_tool_allowlist(
                body,
                request.headers.get("content-type", ""),
                [str(item) for item in server.get("enabled_tools") or []],
            )
        if bearer_token is None:
            return await _relay(request, server=server, body=body)
        return await _relay(request, server=server, body=body, bearer_token=bearer_token)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403, headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, headers={"Cache-Control": "no-store"})
    except RuntimeError:
        return JSONResponse(
            {"error": "Remote MCP credential is unavailable, expired or changed"},
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )
    except (aiohttp.ClientError, OSError, TimeoutError) as exc:
        return JSONResponse(
            {"error": f"Remote MCP transport failed: {type(exc).__name__}"},
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )