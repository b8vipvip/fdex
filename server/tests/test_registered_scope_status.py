from pathlib import Path

from app.config import Settings
from app.memory_erasure import MemoryErasureService
from app.memory_scope_registry import MemoryScopeRegistry


class NoNetwork:
    pass


def test_registered_device_scopes_expand_account_memory_status(tmp_path: Path) -> None:
    settings = Settings(fdex_memory_data_dir=str(tmp_path / "memory"))
    scopes = MemoryScopeRegistry(tmp_path / "memory" / "scopes.sqlite3")
    user = "usr_1234567890abcdef12345678"
    scopes.register(user, "a" * 64)
    scopes.register(user, "b" * 64)
    service = MemoryErasureService(
        settings,
        qdrant_client=NoNetwork(),
        registry_path=tmp_path / "erasure.sqlite3",
        scope_registry=scopes,
    )
    assert service.status(user)["memory_scopes"] == 3
