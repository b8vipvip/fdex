from __future__ import annotations

import hashlib
import hmac
import json
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
_MAX_SECRET_BYTES = 16384


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


def _clean_oauth_secret(value: str, *, required: bool = False) -> str:
    secret = str(value or "")
    if not secret and not required:
        return ""
    if not secret:
        raise ValueError("OAuth secret 不能为空")
    if len(secret.encode("utf-8")) > _MAX_SECRET_BYTES:
        raise ValueError("OAuth secret 超过 16 KiB 上限")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in secret):
        raise ValueError("OAuth secret 包含控制字符")
    return secret


class RemoteMcpCredentialStore:
    """FDEX-held owner/server Remote MCP secrets.

    ``lease_revision`` is intentionally different from the row ``updated_at`` for OAuth grants.
    A static Bearer rotation is an authorization change and therefore changes the lease revision.
    OAuth access-token refresh is only maintenance of the same authorization grant and keeps the
    grant revision stable, so an otherwise-authorized long running task does not lose its lease
    every time a short-lived access token refreshes. Reauthorization/revocation creates a new grant
    revision and invalidates old leases.
    """

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
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:16]

    def seal_text(self, value: str) -> str:
        clean = _clean_oauth_secret(value, required=True)
        return self._cipher().encrypt(clean.encode("utf-8")).decode("ascii")

    def open_text(self, value: str) -> str:
        try:
            plain = self._cipher().decrypt(str(value).encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise RuntimeError("Remote MCP encrypted secret cannot be decrypted") from exc
        return _clean_oauth_secret(plain, required=True)

    def _seal_json(self, value: dict[str, Any]) -> str:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("Remote MCP OAuth credential bundle exceeds 64 KiB")
        return self._cipher().encrypt(encoded.encode("utf-8")).decode("ascii")

    def _open_json(self, value: str) -> dict[str, Any]:
        try:
            plain = self._cipher().decrypt(str(value).encode("ascii")).decode("utf-8")
            payload = json.loads(plain)
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Remote MCP OAuth credential bundle cannot be decrypted") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Remote MCP OAuth credential bundle is invalid")
        return payload

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
                conn.execute("BEGIN IMMEDIATE")
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
                        grant_revision TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY(owner_id,server_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_remote_mcp_credentials_owner
                        ON remote_mcp_credentials(owner_id,updated_at DESC);
                    """
                )
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(remote_mcp_credentials)").fetchall()
                }
                if "grant_revision" not in columns:
                    conn.execute(
                        "ALTER TABLE remote_mcp_credentials ADD COLUMN grant_revision TEXT NOT NULL DEFAULT ''"
                    )
                conn.execute(
                    """
                    UPDATE remote_mcp_credentials
                    SET grant_revision=updated_at
                    WHERE grant_revision=''
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
                SELECT owner_id,server_id,auth_type,fingerprint,created_at,updated_at,grant_revision
                FROM remote_mcp_credentials WHERE owner_id=? AND server_id=?
                """,
                (owner_id, server_id),
            ).fetchone()
        if row is None:
            return None
        grant_revision = str(row["grant_revision"] or row["updated_at"])
        return {
            "owner_id": str(row["owner_id"]),
            "server_id": str(row["server_id"]),
            "auth_type": str(row["auth_type"]),
            "fingerprint": str(row["fingerprint"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "grant_revision": grant_revision,
            "lease_revision": grant_revision,
        }

    def revision(self, owner_id: str, server_id: str) -> str:
        metadata = self.metadata(owner_id, server_id)
        return str(metadata.get("lease_revision") or "") if metadata else ""

    def list_metadata(self, owner_id: str) -> dict[str, dict[str, Any]]:
        self.init()
        server_ids = {str(row["id"]) for row in self.registry.list(owner_id)}
        if not server_ids:
            return {}
        with self.registry.db() as conn:
            rows = conn.execute(
                """
                SELECT owner_id,server_id,auth_type,fingerprint,created_at,updated_at,grant_revision
                FROM remote_mcp_credentials WHERE owner_id=? ORDER BY server_id
                """,
                (owner_id,),
            ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            server_id = str(row["server_id"])
            if server_id not in server_ids:
                continue
            grant_revision = str(row["grant_revision"] or row["updated_at"])
            result[server_id] = {
                "owner_id": str(row["owner_id"]),
                "server_id": server_id,
                "auth_type": str(row["auth_type"]),
                "fingerprint": str(row["fingerprint"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "grant_revision": grant_revision,
                "lease_revision": grant_revision,
            }
        return result

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
                    owner_id,server_id,auth_type,secret_cipher,fingerprint,created_at,updated_at,grant_revision
                ) VALUES(?,?,'bearer',?,?,?,?,?)
                ON CONFLICT(owner_id,server_id) DO UPDATE SET
                    auth_type='bearer',secret_cipher=excluded.secret_cipher,
                    fingerprint=excluded.fingerprint,updated_at=excluded.updated_at,
                    grant_revision=excluded.grant_revision
                """,
                (owner_id, server_id, cipher_text, fingerprint, created_at, now, now),
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
                SELECT auth_type,secret_cipher,updated_at,grant_revision FROM remote_mcp_credentials
                WHERE owner_id=? AND server_id=?
                """,
                (owner_id, server_id),
            ).fetchone()
        if row is None:
            if expected:
                raise RuntimeError("Remote MCP credential disappeared")
            return None
        if str(row["auth_type"]) != "bearer":
            raise RuntimeError("Remote MCP credential type is not static bearer")
        revision = str(row["grant_revision"] or row["updated_at"])
        if expected is not None and revision != expected:
            raise RuntimeError("Remote MCP credential revision changed")
        try:
            token = self._cipher().decrypt(str(row["secret_cipher"]).encode("ascii")).decode("ascii")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise RuntimeError("Remote MCP credential cannot be decrypted") from exc
        return _clean_bearer(token)

    def set_oauth_grant(
        self,
        owner_id: str,
        server_id: str,
        *,
        access_token: str,
        refresh_token: str = "",
        expires_at: str = "",
        scope: str = "",
        token_type: str = "Bearer",
    ) -> dict[str, Any]:
        self.init()
        access = _clean_bearer(access_token)
        refresh = _clean_oauth_secret(refresh_token)
        token_type_clean = str(token_type or "Bearer").strip()
        if token_type_clean.casefold() != "bearer":
            raise ValueError("Remote MCP OAuth 仅支持 Bearer access token")
        now = _now()
        grant_revision = f"oauth:{now}:{uuid.uuid4().hex}"
        payload = {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": str(expires_at or ""),
            "scope": str(scope or "")[:4096],
            "token_type": "Bearer",
        }
        cipher_text = self._seal_json(payload)
        fingerprint = self._fingerprint(access)
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
                    owner_id,server_id,auth_type,secret_cipher,fingerprint,created_at,updated_at,grant_revision
                ) VALUES(?,?,'oauth',?,?,?,?,?)
                ON CONFLICT(owner_id,server_id) DO UPDATE SET
                    auth_type='oauth',secret_cipher=excluded.secret_cipher,
                    fingerprint=excluded.fingerprint,updated_at=excluded.updated_at,
                    grant_revision=excluded.grant_revision
                """,
                (owner_id, server_id, cipher_text, fingerprint, created_at, now, grant_revision),
            )
            self._revoke_server_leases(conn, owner_id, server_id, now)
        metadata = self.metadata(owner_id, server_id)
        if metadata is None:
            raise RuntimeError("Remote MCP OAuth credential write failed")
        return metadata

    def get_oauth_grant(
        self,
        owner_id: str,
        server_id: str,
        *,
        expected_revision: str | None = None,
    ) -> dict[str, Any] | None:
        self.init()
        expected = None if expected_revision is None else str(expected_revision)
        if self.registry.get(owner_id, server_id) is None:
            if expected:
                raise RuntimeError("Remote MCP OAuth owner/server disappeared")
            return None
        with self.registry.db() as conn:
            row = conn.execute(
                """
                SELECT auth_type,secret_cipher,updated_at,grant_revision FROM remote_mcp_credentials
                WHERE owner_id=? AND server_id=?
                """,
                (owner_id, server_id),
            ).fetchone()
        if row is None:
            if expected:
                raise RuntimeError("Remote MCP OAuth grant disappeared")
            return None
        if str(row["auth_type"]) != "oauth":
            raise RuntimeError("Remote MCP credential type is not OAuth")
        revision = str(row["grant_revision"] or row["updated_at"])
        if expected is not None and revision != expected:
            raise RuntimeError("Remote MCP OAuth grant revision changed")
        payload = self._open_json(str(row["secret_cipher"]))
        payload["access_token"] = _clean_bearer(str(payload.get("access_token") or ""))
        payload["refresh_token"] = _clean_oauth_secret(str(payload.get("refresh_token") or ""))
        payload["grant_revision"] = revision
        payload["updated_at"] = str(row["updated_at"])
        return payload

    def refresh_oauth_grant(
        self,
        owner_id: str,
        server_id: str,
        *,
        expected_revision: str,
        access_token: str,
        refresh_token: str = "",
        expires_at: str = "",
        scope: str = "",
        token_type: str = "Bearer",
    ) -> dict[str, Any]:
        """Refresh short-lived OAuth tokens without changing the authorization grant revision."""
        self.init()
        access = _clean_bearer(access_token)
        refresh = _clean_oauth_secret(refresh_token)
        if str(token_type or "Bearer").strip().casefold() != "bearer":
            raise ValueError("Remote MCP OAuth 仅支持 Bearer access token")
        now = _now()
        payload = {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": str(expires_at or ""),
            "scope": str(scope or "")[:4096],
            "token_type": "Bearer",
        }
        cipher_text = self._seal_json(payload)
        fingerprint = self._fingerprint(access)
        with self.registry.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT auth_type,grant_revision FROM remote_mcp_credentials
                WHERE owner_id=? AND server_id=?
                """,
                (owner_id, server_id),
            ).fetchone()
            if row is None or str(row["auth_type"]) != "oauth":
                raise RuntimeError("Remote MCP OAuth grant disappeared during refresh")
            revision = str(row["grant_revision"])
            if not revision or revision != str(expected_revision):
                raise RuntimeError("Remote MCP OAuth grant changed during refresh")
            conn.execute(
                """
                UPDATE remote_mcp_credentials
                SET secret_cipher=?,fingerprint=?,updated_at=?
                WHERE owner_id=? AND server_id=? AND auth_type='oauth' AND grant_revision=?
                """,
                (cipher_text, fingerprint, now, owner_id, server_id, revision),
            )
        metadata = self.metadata(owner_id, server_id)
        if metadata is None:
            raise RuntimeError("Remote MCP OAuth refresh write failed")
        return metadata

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
            tables = {
                str(item[0])
                for item in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "remote_mcp_oauth_flows" in tables:
                conn.execute(
                    "DELETE FROM remote_mcp_oauth_flows WHERE owner_id=? AND server_id=?",
                    (owner_id, server_id),
                )
            if "remote_mcp_oauth_configs" in tables:
                conn.execute(
                    "DELETE FROM remote_mcp_oauth_configs WHERE owner_id=? AND server_id=?",
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
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("DELETE FROM remote_mcp_credentials WHERE owner_id=?", (owner_id,))
            tables = {
                str(item[0])
                for item in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "remote_mcp_oauth_flows" in tables:
                conn.execute("DELETE FROM remote_mcp_oauth_flows WHERE owner_id=?", (owner_id,))
            if "remote_mcp_oauth_configs" in tables:
                conn.execute("DELETE FROM remote_mcp_oauth_configs WHERE owner_id=?", (owner_id,))
        return max(0, int(cursor.rowcount))


@lru_cache
def remote_mcp_credential_store() -> RemoteMcpCredentialStore:
    store = RemoteMcpCredentialStore()
    store.init()
    return store