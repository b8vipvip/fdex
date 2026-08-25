from pathlib import Path

from app.memory_scope_registry import MemoryScopeRegistry


def test_unknown_scope_is_not_misassigned(tmp_path: Path) -> None:
    registry = MemoryScopeRegistry(tmp_path / "scopes.sqlite3")
    assert registry.owner_hash_for_scope("z" * 64) == ""
    assert registry.write_blocked("z" * 64) is False
