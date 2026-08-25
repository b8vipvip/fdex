from pathlib import Path

from app.memory_erasure import MemoryErasureRegistry


def test_erasure_registry_tracks_memory_scope_count(tmp_path: Path) -> None:
    registry = MemoryErasureRegistry(tmp_path / "registry.sqlite3")
    registry.begin("a" * 64, memory_scopes=4)
    row = registry.get("a" * 64)
    assert row is not None
    assert row["memory_scopes"] == 4
    assert row["phase"] == "started"
