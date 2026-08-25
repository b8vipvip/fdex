from pathlib import Path

from app.config import Settings
from app.memory_erasure import MemoryErasureService


class NoNetwork:
    pass


def test_direct_compatibility_scope_is_always_considered(tmp_path: Path) -> None:
    service = MemoryErasureService(
        Settings(fdex_memory_data_dir=str(tmp_path / "memory")),
        qdrant_client=NoNetwork(),
        registry_path=tmp_path / "erasure.sqlite3",
    )
    assert len(service.account_ids_for_user("usr_1234567890abcdef12345678")) == 1
