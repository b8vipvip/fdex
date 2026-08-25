from pathlib import Path

from app.memory_scope_registry import MemoryScopeRegistry


def test_scope_registry_is_content_free(tmp_path: Path) -> None:
    registry = MemoryScopeRegistry(tmp_path / "scopes.sqlite3")
    user = "usr_1234567890abcdef12345678"
    scope = "a" * 64
    registry.register(user, scope)

    with registry._connect() as conn:
        row = conn.execute("SELECT * FROM memory_scope_owners").fetchone()
        columns = {str(item[1]) for item in conn.execute("PRAGMA table_info(memory_scope_owners)").fetchall()}
    assert row is not None
    assert columns == {"scope_key", "owner_hash", "first_seen_at", "last_seen_at"}
    assert "usr_" not in "|".join(str(value) for value in row)
