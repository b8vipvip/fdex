from __future__ import annotations

import ipaddress
import json
import re
import socket
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from app.config import SERVER_DIR

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_MAX_SERVERS_PER_OWNER = 20
_MAX_TOOLS_PER_SERVER = 64


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _clean_owner(owner_id: str) -> str:
    value = (owner_id or "").strip()
    if not value or len(value) > 160:
        raise ValueError("invalid FDEX owner id")
    return value


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(address.is_global)


def resolve_public_addresses(hostname: str) -> tuple[str, ...]:
    """Resolve one registry hostname and reject mixed/private answers.

    Phase 7.25 does not make an HTTP request to the MCP endpoint. DNS resolution is only an
    admission check for the registry. Runtime activation remains disabled until FDEX owns a
    destination-enforcing egress layer, because DNS rebinding/redirect policy cannot be enforced
    by a save-time check alone.
    """
    host = (hostname or "").strip().rstrip(".")
    if not host:
        raise ValueError("Remote MCP URL 缺少主机名")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        value = str(literal)
        if not _public_ip(value):
            raise ValueError("Remote MCP URL 不允许本机、内网、保留或非公网 IP")
        return (value,)

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Remote MCP 主机名不是有效的 DNS 名称") from exc
    if ascii_host.lower() == "localhost" or ascii_host.lower().endswith(".localhost"):
        raise ValueError("Remote MCP URL 不允许 localhost")
    try:
        answers = socket.getaddrinfo(ascii_host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Remote MCP 主机名当前无法解析") from exc
    addresses = sorted({str(item[4][0]).split("%", 1)[0] for item in answers if item and item[4]})
    if not addresses:
        raise ValueError("Remote MCP 主机名没有可用 DNS 地址")
    blocked = [address for address in addresses if not _public_ip(address)]
    if blocked:
        raise ValueError("Remote MCP DNS 同时返回了本机、内网、保留或非公网地址")
    return tuple(addresses)


def normalize_remote_mcp_url(value: str, *, resolve_dns: bool = True) -> tuple[str, tuple[str, ...]]:
    raw = (value or "").strip()
    if not raw or len(raw) > 2048 or any(ord(ch) < 32 for ch in raw):
        raise ValueError("Remote MCP URL 无效或过长")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ValueError("Remote MCP URL 无效") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("Remote MCP 仅允许 HTTPS")
    if not parsed.hostname:
        raise ValueError("Remote MCP URL 缺少主机名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Remote MCP URL 不允许包含用户名或密码")
    if parsed.query:
        raise ValueError("Remote MCP URL 不允许 query 参数；凭据不得放入 URL")
    if parsed.fragment:
        raise ValueError("Remote MCP URL 不允许 fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Remote MCP URL 端口无效") from exc
    if port not in (None, 443):
        raise ValueError("Remote MCP 当前仅允许 HTTPS 443 端口")

    host = parsed.hostname.rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        canonical_host = str(literal).lower()
        netloc = f"[{canonical_host}]" if isinstance(literal, ipaddress.IPv6Address) else canonical_host
    else:
        try:
            canonical_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Remote MCP 主机名不是有效的 DNS 名称") from exc
        netloc = canonical_host
    path = parsed.path or "/"
    normalized = urlunsplit(("https", netloc, path, "", ""))
    addresses = resolve_public_addresses(canonical_host) if resolve_dns else ()
    return normalized, addresses


def normalize_tools(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = value.replace(",", "\n").splitlines()
    else:
        raw = [str(item) for item in value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item).strip()
        if not name:
            continue
        if not _TOOL_RE.fullmatch(name):
            raise ValueError(f"Remote MCP 工具名无效：{name[:80]}")
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    if len(result) > _MAX_TOOLS_PER_SERVER:
        raise ValueError(f"单个 Remote MCP 最多允许 {_MAX_TOOLS_PER_SERVER} 个工具")
    return tuple(result)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        tools = json.loads(str(row["enabled_tools_json"] or "[]"))
    except json.JSONDecodeError:
        tools = []
    try:
        addresses = json.loads(str(row["resolved_addresses_json"] or "[]"))
    except json.JSONDecodeError:
        addresses = []
    return {
        "id": str(row["id"]),
        "owner_id": str(row["owner_id"]),
        "name": str(row["name"]),
        "url": str(row["url"]),
        "enabled": bool(row["enabled"]),
        "enabled_tools": [str(item) for item in tools if str(item)],
        "resolved_addresses": [str(item) for item in addresses if str(item)],
        "startup_timeout_sec": int(row["startup_timeout_sec"]),
        "tool_timeout_sec": int(row["tool_timeout_sec"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


class RemoteMcpRegistry:
    """Owner-scoped, credential-free Remote MCP registry.

    This store deliberately contains no bearer token, OAuth token, HTTP header, shell command,
    argv, cwd or environment variable. Phase 7.25 is an admission/control-plane layer only;
    entries are not injected into Codex until a later FDEX-controlled egress/credential bridge
    can enforce the network destination at request time.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or (SERVER_DIR / "data" / "remote-mcp.sqlite3")).resolve()
        self._initialized = False
        self._lock = threading.Lock()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            with self.db() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS remote_mcp_servers (
                        id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        url TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        enabled_tools_json TEXT NOT NULL DEFAULT '[]',
                        resolved_addresses_json TEXT NOT NULL DEFAULT '[]',
                        startup_timeout_sec INTEGER NOT NULL DEFAULT 15,
                        tool_timeout_sec INTEGER NOT NULL DEFAULT 60,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(owner_id,name),
                        UNIQUE(owner_id,url)
                    );
                    CREATE INDEX IF NOT EXISTS idx_remote_mcp_owner
                        ON remote_mcp_servers(owner_id,updated_at DESC);
                    """
                )
            self._initialized = True

    def list(self, owner_id: str) -> list[dict[str, Any]]:
        owner = _clean_owner(owner_id)
        self.init()
        with self.db() as conn:
            rows = conn.execute(
                "SELECT * FROM remote_mcp_servers WHERE owner_id=? ORDER BY name COLLATE NOCASE,id",
                (owner,),
            ).fetchall()
        return [_row(row) for row in rows]

    def get(self, owner_id: str, server_id: str) -> dict[str, Any] | None:
        owner = _clean_owner(owner_id)
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM remote_mcp_servers WHERE owner_id=? AND id=?",
                (owner, str(server_id)),
            ).fetchone()
        return _row(row) if row is not None else None

    def save(
        self,
        owner_id: str,
        *,
        name: str,
        url: str,
        enabled_tools: str | list[str] | tuple[str, ...],
        enabled: bool = False,
        startup_timeout_sec: int = 15,
        tool_timeout_sec: int = 60,
        server_id: str | None = None,
        resolve_dns: bool = True,
    ) -> dict[str, Any]:
        owner = _clean_owner(owner_id)
        clean_name = (name or "").strip()
        if not _NAME_RE.fullmatch(clean_name):
            raise ValueError("Remote MCP 名称只能使用字母、数字、点、下划线和连字符，最长 64 字符")
        clean_url, addresses = normalize_remote_mcp_url(url, resolve_dns=resolve_dns)
        tools = normalize_tools(enabled_tools)
        if enabled and not tools:
            raise ValueError("启用 Remote MCP 前必须配置至少一个显式工具 allowlist")
        startup = max(2, min(120, int(startup_timeout_sec)))
        tool_timeout = max(2, min(600, int(tool_timeout_sec)))
        self.init()
        now = _now()
        identifier = (server_id or f"mcp_{uuid.uuid4().hex}").strip()
        with self.db() as conn:
            current = conn.execute(
                "SELECT id,created_at FROM remote_mcp_servers WHERE owner_id=? AND id=?",
                (owner, identifier),
            ).fetchone()
            if current is None:
                count = int(conn.execute(
                    "SELECT COUNT(*) FROM remote_mcp_servers WHERE owner_id=?",
                    (owner,),
                ).fetchone()[0])
                if count >= _MAX_SERVERS_PER_OWNER:
                    raise ValueError(f"每个 FDEX 账号最多保存 {_MAX_SERVERS_PER_OWNER} 个 Remote MCP")
                created_at = now
            else:
                created_at = str(current["created_at"])
            try:
                conn.execute(
                    """
                    INSERT INTO remote_mcp_servers(
                        id,owner_id,name,url,enabled,enabled_tools_json,resolved_addresses_json,
                        startup_timeout_sec,tool_timeout_sec,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        url=excluded.url,
                        enabled=excluded.enabled,
                        enabled_tools_json=excluded.enabled_tools_json,
                        resolved_addresses_json=excluded.resolved_addresses_json,
                        startup_timeout_sec=excluded.startup_timeout_sec,
                        tool_timeout_sec=excluded.tool_timeout_sec,
                        updated_at=excluded.updated_at
                    WHERE remote_mcp_servers.owner_id=excluded.owner_id
                    """,
                    (
                        identifier,
                        owner,
                        clean_name,
                        clean_url,
                        1 if enabled else 0,
                        json.dumps(list(tools), ensure_ascii=False, separators=(",", ":")),
                        json.dumps(list(addresses), ensure_ascii=False, separators=(",", ":")),
                        startup,
                        tool_timeout,
                        created_at,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("当前账号已经存在相同名称或 URL 的 Remote MCP") from exc
            saved = conn.execute(
                "SELECT * FROM remote_mcp_servers WHERE owner_id=? AND id=?",
                (owner, identifier),
            ).fetchone()
        if saved is None:
            raise ValueError("Remote MCP 不存在或不属于当前账号")
        return _row(saved)

    def set_enabled(self, owner_id: str, server_id: str, enabled: bool) -> dict[str, Any]:
        owner = _clean_owner(owner_id)
        current = self.get(owner, server_id)
        if current is None:
            raise KeyError("Remote MCP 不存在")
        if not enabled:
            # Disabling is a safety escape hatch. It must not depend on current DNS health: an
            # endpoint whose DNS has just become private/malicious must still be immediately
            # switchable off. Re-enabling always runs the full public-DNS admission check.
            with self.db() as conn:
                conn.execute(
                    "UPDATE remote_mcp_servers SET enabled=0,updated_at=? WHERE owner_id=? AND id=?",
                    (_now(), owner, str(server_id)),
                )
                row = conn.execute(
                    "SELECT * FROM remote_mcp_servers WHERE owner_id=? AND id=?",
                    (owner, str(server_id)),
                ).fetchone()
            if row is None:
                raise KeyError("Remote MCP 不存在")
            return _row(row)
        if not current["enabled_tools"]:
            raise ValueError("启用 Remote MCP 前必须配置至少一个显式工具 allowlist")
        return self.save(
            owner,
            name=str(current["name"]),
            url=str(current["url"]),
            enabled_tools=list(current["enabled_tools"]),
            enabled=True,
            startup_timeout_sec=int(current["startup_timeout_sec"]),
            tool_timeout_sec=int(current["tool_timeout_sec"]),
            server_id=str(current["id"]),
            resolve_dns=True,
        )

    def delete(self, owner_id: str, server_id: str) -> bool:
        owner = _clean_owner(owner_id)
        self.init()
        with self.db() as conn:
            cursor = conn.execute(
                "DELETE FROM remote_mcp_servers WHERE owner_id=? AND id=?",
                (owner, str(server_id)),
            )
        return cursor.rowcount > 0

    def delete_owner(self, owner_id: str) -> int:
        owner = _clean_owner(owner_id)
        self.init()
        with self.db() as conn:
            cursor = conn.execute("DELETE FROM remote_mcp_servers WHERE owner_id=?", (owner,))
        return max(0, int(cursor.rowcount))

    def export_owner(self, owner_id: str) -> list[dict[str, Any]]:
        rows = self.list(owner_id)
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "url": row["url"],
                "enabled": row["enabled"],
                "enabled_tools": list(row["enabled_tools"]),
                "startup_timeout_sec": row["startup_timeout_sec"],
                "tool_timeout_sec": row["tool_timeout_sec"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]


@lru_cache
def remote_mcp_registry() -> RemoteMcpRegistry:
    store = RemoteMcpRegistry()
    store.init()
    return store
