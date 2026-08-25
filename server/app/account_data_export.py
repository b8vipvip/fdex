from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent_projects import agent_project_store
from app.central_auth import CentralAuthStore, central_auth_store
from app.config import fresh_settings
from app.fdex_memory import MemoryScope
from app.memory_erasure import memory_erasure_status
from app.memory_scope_registry import MemoryScopeRegistry, memory_scope_registry


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _memory_account_ids(user_id: str, scopes: MemoryScopeRegistry) -> list[str]:
    raw = [user_id, *scopes.scopes_for_user(user_id)]
    return list(dict.fromkeys(MemoryScope(value).account_id for value in raw if value))


def _remote_history(user_id: str, scopes: MemoryScopeRegistry, *, limit: int = 50000) -> list[dict[str, object]]:
    memory_db = Path(fresh_settings().fdex_memory_data_dir).expanduser().resolve() / "mempalace-raw.sqlite3"
    if not memory_db.exists():
        return []
    account_ids = _memory_account_ids(user_id, scopes)
    if not account_ids:
        return []
    conn = sqlite3.connect(memory_db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mempalace_drawers'"
        ).fetchone()
        if table is None:
            return []
        placeholders = ",".join("?" for _ in account_ids)
        rows = conn.execute(
            f"""
            SELECT account_id,vault_id,wing,room,role,conversation_id,employee_id,
                   source,content,created_at
            FROM mempalace_drawers
            WHERE account_id IN ({placeholders})
            ORDER BY created_at,rowid
            LIMIT ?
            """,
            (*account_ids, max(1, min(int(limit), 50000))),
        ).fetchall()
        return [
            {
                "memory_scope": str(row["account_id"]),
                "vault": str(row["vault_id"]),
                "wing": str(row["wing"]),
                "room": str(row["room"]),
                "role": str(row["role"]),
                "conversation_id": str(row["conversation_id"]),
                "employee_id": str(row["employee_id"] or ""),
                "source": str(row["source"]),
                "content": str(row["content"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def _public_sessions(store: CentralAuthStore, user_id: str) -> list[dict[str, object]]:
    store.init()
    now = _now()
    with store.db() as conn:
        rows = conn.execute(
            """
            SELECT id,device_name,client_ip,user_agent,created_at,updated_at,last_seen_at,
                   access_expires_at,refresh_expires_at,revoked_at
            FROM user_sessions WHERE user_id=? ORDER BY created_at,id
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "device_name": str(row["device_name"] or ""),
            "client_ip": str(row["client_ip"] or ""),
            "user_agent": str(row["user_agent"] or ""),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "last_seen_at": str(row["last_seen_at"] or ""),
            "access_expires_at": str(row["access_expires_at"]),
            "refresh_expires_at": str(row["refresh_expires_at"]),
            "revoked_at": str(row["revoked_at"] or ""),
            "active_at_export": not bool(row["revoked_at"]) and str(row["refresh_expires_at"]) > now,
        }
        for row in rows
    ]


def build_account_export(
    user_id: str,
    *,
    auth_store: CentralAuthStore | None = None,
    scope_registry: MemoryScopeRegistry | None = None,
) -> dict[str, object]:
    """Build a portable user export without operational secrets.

    Deliberately excluded: access/refresh tokens and hashes, password hashes, reset codes,
    GitHub token ciphertext/plaintext, provider secrets, embeddings and sandbox cache files.
    Android adds its per-user local SQLite business data to this server snapshot before the
    user saves the final JSON file through the system document picker.
    """
    store = auth_store or central_auth_store()
    scopes = scope_registry or memory_scope_registry()
    user = store.get_user(user_id)
    projects = agent_project_store()
    return {
        "schema_version": 1,
        "generated_at": _now(),
        "account": user,
        "sessions": _public_sessions(store, user_id),
        "security_events": store.security_events(user_id, limit=100),
        "github_connections": projects.list_connections(user_id),
        "coding_agent_projects": projects.list_projects(user_id, enabled_only=False),
        "long_term_memory": {
            "status": memory_erasure_status(user_id),
            "registered_device_scopes": scopes.scope_count(user_id),
            "history": _remote_history(user_id, scopes),
        },
        "excluded_secrets": [
            "password_hash",
            "access_token",
            "refresh_token",
            "access_hash",
            "refresh_hash",
            "password_reset_code",
            "github_token",
            "github_token_cipher",
            "provider_api_keys",
            "embeddings",
        ],
    }
