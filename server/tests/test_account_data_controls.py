from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import account_operations
from app.account_operations import AccountOperationBusy, account_operation, account_operation_status
from app.config import Settings
from app.fdex_memory import MemoryScope
from app.memory_erasure import MemoryErasureService
from app.memory_scope_registry import MemoryScopeRegistry


class FakeQdrant:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def post(self, url: str, *, json: dict[str, object]):
        self.deleted.extend(str(value) for value in json.get("points", []))
        return SimpleNamespace(status_code=200)


class FakeAgents:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, agent_id: str):
        self.deleted.append(agent_id)
        return {}


class FakeLetta:
    def __init__(self, agents: FakeAgents) -> None:
        self.agents = agents


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        app_dir=str(tmp_path / "app"),
        fdex_memory_data_dir=str(tmp_path / "memory"),
        fdex_memory_qdrant_url="http://qdrant.invalid",
        fdex_memory_qdrant_collection="fdex-test",
        fdex_memory_qdrant_timeout_seconds=2,
        fdex_letta_base_url="http://letta.invalid",
        fdex_letta_timeout_seconds=2,
    )


def _seed_raw(path: Path, rows: list[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE mempalace_drawers (
                drawer_id TEXT PRIMARY KEY,
                point_id TEXT NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                vault_id TEXT NOT NULL,
                wing TEXT NOT NULL,
                room TEXT NOT NULL,
                role TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                employee_id TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        for index, (account_id, point_id, content) in enumerate(rows):
            conn.execute(
                "INSERT INTO mempalace_drawers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"drawer-{index}", point_id, account_id, "default", "wing", "conversation",
                    "user", "conv", "1", "fdex:conv", content, f"hash-{index}",
                    "2026-08-25T00:00:00+00:00",
                ),
            )


def _contents(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [str(row[0]) for row in conn.execute("SELECT content FROM mempalace_drawers ORDER BY rowid").fetchall()]


def test_scope_registry_maps_multiple_bound_device_scopes_to_one_account(tmp_path: Path) -> None:
    registry = MemoryScopeRegistry(tmp_path / "scope.sqlite3")
    user = "usr_1234567890abcdef12345678"
    first = "a" * 64
    second = "b" * 64

    assert registry.register(user, first) == first
    assert registry.register(user, second) == second
    assert registry.scopes_for_user(user) == [first, second]
    assert registry.scope_count(user) == 2
    assert registry.owner_hash_for_scope(first) == registry.owner_hash_for_scope(second)

    other = "usr_abcdef1234567890abcdef12"
    with pytest.raises(RuntimeError, match="ownership conflict"):
        registry.register(other, first)


def test_erasure_covers_every_registered_device_scope_and_preserves_other_account(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    registry = MemoryScopeRegistry(tmp_path / "memory" / "memory-scope-owners.sqlite3")
    target = "usr_1234567890abcdef12345678"
    other = "usr_abcdef1234567890abcdef12"
    bound_one = "1" * 64
    bound_two = "2" * 64
    registry.register(target, bound_one)
    registry.register(target, bound_two)

    first_scope = MemoryScope(bound_one)
    second_scope = MemoryScope(bound_two)
    other_scope = MemoryScope(other)
    raw = Path(settings.fdex_memory_data_dir) / "mempalace-raw.sqlite3"
    _seed_raw(
        raw,
        [
            (first_scope.account_id, "11111111-1111-1111-1111-111111111111", "device-one"),
            (second_scope.account_id, "22222222-2222-2222-2222-222222222222", "device-two"),
            (other_scope.account_id, "33333333-3333-3333-3333-333333333333", "other-user"),
        ],
    )
    state = Path(settings.fdex_memory_data_dir) / "letta-agent.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "agents": {
                    first_scope.storage_key: "agent-one",
                    second_scope.storage_key: "agent-two",
                    other_scope.storage_key: "agent-other",
                },
            }
        ),
        encoding="utf-8",
    )
    qdrant = FakeQdrant()
    agents = FakeAgents()
    service = MemoryErasureService(
        settings,
        qdrant_client=qdrant,
        letta_client_factory=lambda: FakeLetta(agents),
        registry_path=tmp_path / "erasure.sqlite3",
        scope_registry=registry,
    )

    report = asyncio.run(service.erase_account(target))

    assert report.memory_scopes == 3  # direct Phase 7.2 compatibility + two bound device scopes
    assert report.mempalace_rows == 2
    assert report.qdrant_points == 2
    assert report.letta_agents == 2
    assert set(qdrant.deleted) == {
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    }
    assert set(agents.deleted) == {"agent-one", "agent-two"}
    assert _contents(raw) == ["other-user"]
    remaining = json.loads(state.read_text(encoding="utf-8"))["agents"]
    assert list(remaining.values()) == ["agent-other"]


def test_cross_worker_account_operation_lock_exposes_busy_status_without_clobbering_metadata(tmp_path: Path, monkeypatch) -> None:
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setattr(account_operations, "_lock_dir", lambda: lock_dir)
    user = "usr_1234567890abcdef12345678"

    with account_operation(user, "account_delete") as held:
        status = account_operation_status(user)
        assert held.operation == "account_delete"
        assert status.busy is True
        assert status.operation == "account_delete"
        assert status.started_at == held.started_at
        with pytest.raises(AccountOperationBusy) as blocked:
            with account_operation(user, "memory_clear"):
                pass
        assert blocked.value.status.operation == "account_delete"
        # A failed second acquisition must not truncate the holder's metadata.
        assert account_operation_status(user).operation == "account_delete"

    assert account_operation_status(user).busy is False
