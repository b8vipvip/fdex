from pathlib import Path

from app.memory_scope_registry import MemoryScopeRegistry


def test_memory_scope_owner_registry_does_not_store_raw_user_id(tmp_path: Path) -> None:
    registry = MemoryScopeRegistry(tmp_path / "registry.sqlite3")
    user_id = "usr_1234567890abcdef12345678"
    registry.register(user_id, "a" * 64)
    raw = (tmp_path / "registry.sqlite3").read_bytes()
    assert user_id.encode() not in raw
