from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import account_cleanup
from app.config import Settings
from app.fdex_memory import MemoryScope
from app.memory_erasure import MemoryErasureError, MemoryErasureService


class FakeQdrant:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, *, json: dict[str, object]):
        self.calls.append((url, json))
        return SimpleNamespace(status_code=self.status_code)


class FakeAgents:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.deleted: list[str] = []

    def delete(self, agent_id: str):
        self.deleted.append(agent_id)
        if self.error is not None:
            raise self.error
        return {}


class FakeLetta:
    def __init__(self, agents: FakeAgents) -> None:
        self.agents = agents


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        fdex_memory_data_dir=str(tmp_path / "memory"),
        fdex_memory_qdrant_url="http://qdrant.invalid",
        fdex_memory_qdrant_collection="fdex-test",
        fdex_memory_qdrant_timeout_seconds=2,
        fdex_letta_base_url="http://letta.invalid",
        fdex_letta_timeout_seconds=2,
    )


def _seed_raw(path: Path, rows: list[tuple[str, str]]) -> None:
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
        for index, (account_id, point_id) in enumerate(rows):
            conn.execute(
                "INSERT INTO mempalace_drawers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"drawer-{index}", point_id, account_id, "default", "wing", "conversation",
                    "user", "conv", "1", "fdex:conv", f"secret-{index}", f"hash-{index}",
                    "2026-08-25T00:00:00+00:00",
                ),
            )


def _count_account(path: Path, account_id: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM mempalace_drawers WHERE account_id=?", (account_id,)).fetchone()[0])


def test_erasure_removes_only_target_mempalace_qdrant_and_letta(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    target = "usr_1234567890abcdef12345678"
    other = "usr_abcdef1234567890abcdef12"
    target_scope = MemoryScope(target)
    other_scope = MemoryScope(other)
    raw = Path(settings.fdex_memory_data_dir) / "mempalace-raw.sqlite3"
    _seed_raw(
        raw,
        [
            (target_scope.account_id, "11111111-1111-1111-1111-111111111111"),
            (target_scope.account_id, "22222222-2222-2222-2222-222222222222"),
            (other_scope.account_id, "33333333-3333-3333-3333-333333333333"),
        ],
    )
    state = Path(settings.fdex_memory_data_dir) / "letta-agent.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "agents": {
                    target_scope.storage_key: "agent-target",
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
        registry_path=tmp_path / "registry.sqlite3",
    )

    report = __import__("asyncio").run(service.erase_account(target))

    assert report.mempalace_rows == 2
    assert report.qdrant_points == 2
    assert report.letta_agents == 1
    assert _count_account(raw, target_scope.account_id) == 0
    assert _count_account(raw, other_scope.account_id) == 1
    assert agents.deleted == ["agent-target"]
    remaining = json.loads(state.read_text(encoding="utf-8"))["agents"]
    assert target_scope.storage_key not in remaining
    assert remaining[other_scope.storage_key] == "agent-other"
    assert len(qdrant.calls) == 1
    assert qdrant.calls[0][1]["points"] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    registry = service.registry.get(service.account_hash(target))
    assert registry is not None
    assert registry["phase"] == "completed"
    assert registry["completed_at"]
    assert registry["last_error"] == ""


def test_qdrant_failure_is_fail_closed_and_keeps_raw_history(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    target = "usr_1234567890abcdef12345678"
    scope = MemoryScope(target)
    raw = Path(settings.fdex_memory_data_dir) / "mempalace-raw.sqlite3"
    _seed_raw(raw, [(scope.account_id, "11111111-1111-1111-1111-111111111111")])
    service = MemoryErasureService(
        settings,
        qdrant_client=FakeQdrant(503),
        registry_path=tmp_path / "registry.sqlite3",
    )

    with pytest.raises(MemoryErasureError, match="mempalace_qdrant_delete_server_error"):
        __import__("asyncio").run(service.erase_account(target))

    assert _count_account(raw, scope.account_id) == 1
    registry = service.registry.get(service.account_hash(target))
    assert registry is not None
    assert registry["phase"] == "failed"
    assert registry["last_error"] == "mempalace_qdrant_delete_server_error"


def test_missing_letta_agent_is_treated_as_already_erased(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    target = "usr_1234567890abcdef12345678"
    scope = MemoryScope(target)
    state = Path(settings.fdex_memory_data_dir) / "letta-agent.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"schema_version": 1, "agents": {scope.storage_key: "agent-gone"}}), encoding="utf-8")

    class MissingAgentError(RuntimeError):
        status_code = 404

    agents = FakeAgents(MissingAgentError("gone"))
    service = MemoryErasureService(
        settings,
        qdrant_client=FakeQdrant(),
        letta_client_factory=lambda: FakeLetta(agents),
        registry_path=tmp_path / "registry.sqlite3",
    )

    report = __import__("asyncio").run(service.erase_account(target))
    assert report.letta_agents == 1
    assert json.loads(state.read_text(encoding="utf-8"))["agents"] == {}


def test_account_cleanup_erases_memory_before_agent_resources(monkeypatch) -> None:
    order: list[str] = []

    async def fake_memory(user_id: str) -> dict[str, object]:
        order.append("memory")
        return {"completed": True, "account_hash": "hash"}

    def fake_agents(user_id: str) -> dict[str, int]:
        order.append("agent")
        return {"projects": 1, "github_connections": 2, "owner_directories": 3}

    monkeypatch.setattr(account_cleanup, "erase_account_memory", fake_memory)
    monkeypatch.setattr(account_cleanup, "_purge_agent_resources_only", fake_agents)

    result = account_cleanup.purge_owned_agent_resources("usr_1234567890abcdef12345678")
    assert order == ["memory", "agent"]
    assert result["memory"]["completed"] is True
    assert result["projects"] == 1
