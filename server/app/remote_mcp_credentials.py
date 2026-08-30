from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import SERVER_DIR
from app.remote_mcp_registry import RemoteMcpRegistry, remote_mcp_registry

_BEARER_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
_MAX_BEARER_BYTES = 8192


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _clean_bearer(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("Bearer token 不能为空")
    if len(token.encode("utf-8")) > _MAX_BEARER_BYTES:
        raise ValueError("Bearer token 超过 8 KiB 上限")
    if not token.isascii() or not _BEARER_RE.fullmatch(token):
        raise ValueError("Bearer token 包含 HTTP Bearer 不允许的字符")
    return token


class RemoteMcpCredentialStore:
    """FDEX-held owner/server Remote MCP secrets."""

    def __init__(
        self,
        registry: RemoteMcpRegistry | None = None,
        *,
        key_path: Path | None = None,
    ) -> None:
        self.registry = registry or remote_mcp_registry()
        self.key_path = (
            key_path
            or (SERVER_DIR / "data" / "remote-mcp-secrets" / "credential-vault.key")
        ).resolve()
        self._key_value: bytes | None = None
        self._cipher_value: Fernet | None = None
        self._initialized = False
        self._lock = threading.Lock()

    def _load_key(self, *, allow_create: bool) -> bytes:
        if self._key_value is not None:
            return self._key_value
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.key_path.parent, 0o700)
        except OSError:
            pass

        if not self.key_path.exists():
            if not allow_create:
                raise RuntimeError(
                    "Remote MCP credential vault key is missing while encrypted credentials exist"
                )
            generated = Fernet.generate_key()
            temp = self.key_path.with_name(
                f".{self.key_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, generated + b"\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                try:
                    os.link(temp, self.key_path)
                except FileExistsError:
                    pass
            finally:
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass

        key = self.key_path.read_bytes().strip()
        try:
            Fernet(key)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Remote MCP credential vault key is invalid") from exc
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._key_value = key
        return key

    def _cipher(self) -> Fernet:
        if self._cipher_value is None:
            self._cipher_value = Fernet(self._load_key(allow_create=True))
        return self._cipher_value

    def _fingerprint(self, token: str) -> str:
        return hmac.new(
            self._load_key(allow_create=False),
            token.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()[:16]

    @staticmethod
    def _revoke_server_leases(conn: Any, owner_id: str, server_id: str, now: str) -> int:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_mcp_leases'"
        ).fetchone()
        if table is None:
            return 0
        cursor = conn.execute(
            """
            UPDATE remote_mcp_leases SET state='revoked',revoked_at=?
            WHERE owner_id=? AND server_id=? AND state='active'
            """,
            (now, owner_id, server_id),
        )
        return max(0, int(cursor.rowcount))

    def init(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self.registry.init()
            with self.registry.db() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS remote_mcp_credentials (
                        owner_id TEXT NOT NULL,
                        server_id TEXT NOT NULL,
                        auth_type TEXT NOT NULL DEFAULT 'bearer',
                        secret_cipher TEXT NOT NULL,
                        fingerprint TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(owner_id,server_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_remote_mcp_credentials_owner
                        ON remote_mcp_credentials(owner_id,updated_at DESC);
                    """
                )
                credential_count = int(
                    conn.execute("SELECT COUNT(*) FROM remote_mcp_credentials").fetchone()[0]
                )
                sample = conn.execute(
                    "SELECT secret_cipher FROM remote_mcp_credentials LIMIT 1"
                ).fetchone()
            key = self._load_key(allow_create=credential_count == 0)
            self._cipher_value = Fernet(key)
            if sample is not None:
                try:
                    self._cipher_value.decrypt(str(sample["secret_cipher"]).encode("ascii"))
                except (InvalidToken, UnicodeEncodeError) as exc:
                    raise RuntimeError(
                        "Remote MCP credential vault key does not match stored credentials"
                    ) from exc
            self._initialized = True

    def metadata(self, owner_id: str, server_id: str) -> dict[str, Any] | None:
        self.init()
        if self.registry.get(owner_id, server_id) is None:
            return None
        with self.registry.db() as conn:
            row = conn.execute(
                """
                SELECT owner_id,server_id,auth_type,fingerprint,created_at,updated_at
                FROM remote_mcp_credentials WHERE owner_id=? AND server_id=?
                """,
                (owner_id, server_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "owner_id": str(row["owner_id"]),
            "server_id": str(row["server_id"]),
            "auth_type": str(row["auth_type"]),
            "fingerprint": str(row["fingerprint"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def revision(self, owner_id: str, server_id: str) -> str:
        metadata = self.metadata(owner_id, server_id)
        return str(metadata.get("updated_at") or "") if metadata else ""

    def list_metadata(self, owner_id: str) -> dict[str, dict[str, Any]]:
        self.init()
        server_ids = {str(row["id"]) for row in self.registry.list(owner_id)}
        if not server_ids:
            return {}
        with self.registry.db() as conn:
            rows = conn.execute(
                """
                SELECT owner_id,server_id,auth_type,fingerprint,created_at,updated_at
                FROM remote_mcp_credentials WHERE owner_id=? ORDER BY server_id
                """,
                (owner_id,),
            ).fetchall()
        return {
            str(row["server_id"]): {
                "owner_id": str(row["owner_id"]),
                "server_id": str(row["server_id"]),
                "auth_type": str(row["auth_type"]),
                "fingerprint": str(row["fingerprint"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
            if str(row["server_id"]) in server_ids
        }

    def set_bearer(self, owner_id: str, server_id: str, token: str) -> dict[str, Any]:
        self.init()
        clean = _clean_bearer(token)
        now = _now()
        cipher_text = self._cipher().encrypt(clean.encode("ascii")).decode("ascii")
        fingerprint = self._fingerprint(clean)
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            server = conn.execute(
                "SELECT id FROM remote_mcp_servers WHERE owner_id=? AND id=?",
                (owner_id, server_id),
            ).fetchone()
            if server is None:
                raise KeyError("Remote MCP 不存在或不属于当前账号")
            current = conn.execute(
                "SELECT created_at FROM remote_mcp_credentials WHERE owner_id=? AND server_id=?",
                (owner_id, server_id),
            ).fetchone()
            created_at = str(current["created_at"]) if current is not None else now
            conn.execute(
                """
                INSERT INTO remote_mcp_credentials(
                    owner_id,server_id,auth_type,secret_cipher,fingerprint,created_at,updated_at
                ) VALUES(?,?,'bearer',?,?,?,?)
                ON CONFLICT(owner_id,server_id) DO UPDATE SET
                    auth_type='bearer',secret_cipher=excluded.secret_cipher,
                    fingerprint=excluded.fingerprint,updated_at=excluded.updated_at
                """,
                (owner_id, server_id, cipher_text, fingerprint, created_at, now),
            )
            self._revoke_server_leases(conn, owner_id, server_id, now)
        metadata = self.metadata(owner_id, server_id)
        if metadata is None:
            raise RuntimeError("Remote MCP credential write failed")
        return metadata

    def get_bearer(
        self,
        owner_id: str,
        server_id: str,
        *,
        expected_revision: str | None = None,
    ) -> str | None:
        self.init()
        expected = None if expected_revision is None else str(expected_revision)
        if self.registry.get(owner_id, server_id) is None:
            if expected:
                raise RuntimeError("Remote MCP credential owner/server disappeared")
            return None
        with self.registry.db() as conn:
            row = conn.execute(
                """
                SELECT auth_type,secret_cipher,updated_at FROM remote_mcp_credentials
                WHERE owner_id=? AND server_id=?
                """,
                (owner_id, server_id),
            ).fetchone()
        if row is None:
            if expected:
                raise RuntimeError("Remote MCP credential disappeared")
            return None
        if str(row["auth_type"]) != "bearer":
            raise RuntimeError("Remote MCP credential type is unsupported")
        revision = str(row["updated_at"])
        if expected is not None and revision != expected:
            raise RuntimeError("Remote MCP credential revision changed")
        try:
            token = self._cipher().decrypt(str(row["secret_cipher"]).encode("ascii")).decode("ascii")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise RuntimeError("Remote MCP credential cannot be decrypted") from exc
        return _clean_bearer(token)

    def delete(self, owner_id: str, server_id: str) -> bool:
        self.init()
        now = _now()
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "DELETE FROM remote_mcp_credentials WHERE owner_id=? AND server_id=?",
                (owner_id, server_id),
            )
            if cursor.rowcount:
                self._revoke_server_leases(conn, owner_id, server_id, now)
        return bool(cursor.rowcount)

    def delete_server(self, owner_id: str, server_id: str) -> bool:
        self.init()
        now = _now()
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM remote_mcp_servers WHERE owner_id=? AND id=?",
                (owner_id, server_id),
            ).fetchone()
            if row is None:
                return False
            self._revoke_server_leases(conn, owner_id, server_id, now)
            conn.execute(
                "DELETE FROM remote_mcp_credentials WHERE owner_id=? AND server_id=?",
                (owner_id, server_id),
            )
            cursor = conn.execute(
                "DELETE FROM remote_mcp_servers WHERE owner_id=? AND id=?",
                (owner_id, server_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Remote MCP atomic delete failed")
        return True

    def delete_owner(self, owner_id: str) -> int:
        self.init()
        with self.registry.db() as conn:
            cursor = conn.execute("DELETE FROM remote_mcp_credentials WHERE owner_id=?", (owner_id,))
        return max(0, int(cursor.rowcount))


@lru_cache
def remote_mcp_credential_store() -> RemoteMcpCredentialStore:
    store = RemoteMcpCredentialStore()
    store.init()
    return store
