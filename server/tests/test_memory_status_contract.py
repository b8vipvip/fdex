from pathlib import Path

from app.config import Settings
from app.memory_erasure import MemoryErasureService
from app.memory_scope_registry import MemoryScopeRegistry


class NoNetwork:
    pass


def test_memory_status_is_idle_before_first_clear(tmp_path: Path) -> None:
    settings = Settings(fdex_memory_data_dir=str(tmp_path / "memory"))
    scopes = MemoryScopeRegistry(tmp_path / "memory" / "scopes.sqlite3")
    service = MemoryErasureService(
        settings,
        qdrant_client=NoNetwork(),
        registry_path=tmp_path / "erasure.sqlite3",
        scope_registry=scopes,
    )
    status = service.status("usr_1234567890abcdef12345678")
    assert status["phase"] == "idle"
    assert status["last_error"] == ""
    assert status["memory_scopes"] == 1
