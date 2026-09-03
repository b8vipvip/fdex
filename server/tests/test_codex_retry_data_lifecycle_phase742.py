from __future__ import annotations

from pathlib import Path

from app.agent_tasks import AgentTaskStore
from app.codex_retry_chain_store import CodexRetryChainStore
from app.codex_retry_data_lifecycle import delete_owner_retry_task_graph

OWNER_A = "usr_phase742_owner_a"
OWNER_B = "usr_phase742_owner_b"
ROOT_A = "a" * 32
ROOT_B = "b" * 32


def _stores(tmp_path: Path, monkeypatch):
    from app import codex_retry_chain_store as chain_module
    from app import codex_retry_data_lifecycle as lifecycle

    tasks = AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")
    tasks.init()
    monkeypatch.setattr(chain_module, "agent_task_store", lambda: tasks)
    monkeypatch.setattr(lifecycle, "agent_task_store", lambda: tasks)
    chain = CodexRetryChainStore()
    chain.init()
    return tasks, chain


def _seed_task(tasks: AgentTaskStore, *, owner_id: str, task_id: str) -> None:
    now = "2026-09-03T00:00:00+00:00"
    with tasks.db() as conn:
        conn.execute(
            """
            INSERT INTO agent_tasks(
                id,owner_id,prompt,status,task_kind,logical_root_id,attempt_index,created_at,updated_at
            ) VALUES(?,?,'phase742 erasure seed','failed','user',?,0,?,?)
            """,
            (task_id, owner_id, task_id, now, now),
        )


def _seed_retry_graph(
    tasks: AgentTaskStore,
    chain: CodexRetryChainStore,
    *,
    owner_id: str,
    task_id: str,
    provider_id: int,
) -> None:
    _seed_task(tasks, owner_id=owner_id, task_id=task_id)
    chain.record_queued(
        owner_id=owner_id,
        root_task_id=task_id,
        attempt_task_id=task_id,
        parent_task_id="",
        attempt_index=0,
    )
    chain.record_started(
        owner_id=owner_id,
        root_task_id=task_id,
        attempt_task_id=task_id,
        parent_task_id="",
        attempt_index=0,
        provider_id=provider_id,
        provider_name=f"provider-{provider_id}",
        model="codex-model",
    )
    chain.record_retry_plan(
        owner_id=owner_id,
        root_task_id=task_id,
        source_attempt_task_id=task_id,
        source_attempt_index=0,
        next_attempt_index=1,
        decision_code="PROVIDER_UNREACHABLE",
        decision_reason="structured retry metadata must be erased with owner data",
        error="transient provider failure",
        backoff_seconds=2.0,
        excluded_provider_ids={provider_id},
    )


def _count(tasks: AgentTaskStore, table: str, owner_id: str) -> int:
    with tasks.db() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE owner_id=?", (owner_id,)).fetchone()[0])


def test_phase742_owner_erasure_atomically_removes_transition_attempt_and_task(monkeypatch, tmp_path: Path) -> None:
    tasks, chain = _stores(tmp_path, monkeypatch)
    _seed_retry_graph(tasks, chain, owner_id=OWNER_A, task_id=ROOT_A, provider_id=41)
    _seed_retry_graph(tasks, chain, owner_id=OWNER_B, task_id=ROOT_B, provider_id=42)

    result = delete_owner_retry_task_graph(OWNER_A)

    assert result == {
        "agent_tasks": 1,
        "codex_retry_attempts": 1,
        "codex_retry_transitions": 1,
    }
    assert _count(tasks, "agent_tasks", OWNER_A) == 0
    assert _count(tasks, "codex_retry_attempts", OWNER_A) == 0
    assert _count(tasks, "codex_retry_transitions", OWNER_A) == 0

    # Owner scope is part of the erasure authority. Another tenant's retry journal must survive.
    assert _count(tasks, "agent_tasks", OWNER_B) == 1
    assert _count(tasks, "codex_retry_attempts", OWNER_B) == 1
    assert _count(tasks, "codex_retry_transitions", OWNER_B) == 1


def test_phase742_erasure_is_compatible_with_phase740_database_without_transition_table(monkeypatch, tmp_path: Path) -> None:
    from app import codex_retry_data_lifecycle as lifecycle

    tasks = AgentTaskStore(tmp_path / "agent-tasks.db", tmp_path / "locks")
    tasks.init()
    monkeypatch.setattr(lifecycle, "agent_task_store", lambda: tasks)
    _seed_task(tasks, owner_id=OWNER_A, task_id=ROOT_A)
    now = "2026-09-03T00:00:00+00:00"
    with tasks.db() as conn:
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "codex_retry_transitions" not in tables
        conn.execute(
            """
            INSERT INTO codex_retry_attempts(
                attempt_task_id,owner_id,root_task_id,parent_task_id,attempt_index,state,
                created_at,updated_at
            ) VALUES(?,?,?,'',0,'failed',?,?)
            """,
            (ROOT_A, OWNER_A, ROOT_A, now, now),
        )

    result = delete_owner_retry_task_graph(OWNER_A)

    assert result == {
        "agent_tasks": 1,
        "codex_retry_attempts": 1,
        "codex_retry_transitions": 0,
    }
    assert _count(tasks, "agent_tasks", OWNER_A) == 0
    assert _count(tasks, "codex_retry_attempts", OWNER_A) == 0


def test_phase742_unknown_owner_is_a_safe_noop(monkeypatch, tmp_path: Path) -> None:
    tasks, chain = _stores(tmp_path, monkeypatch)
    _seed_retry_graph(tasks, chain, owner_id=OWNER_B, task_id=ROOT_B, provider_id=42)

    result = delete_owner_retry_task_graph("usr_phase742_missing")

    assert result == {
        "agent_tasks": 0,
        "codex_retry_attempts": 0,
        "codex_retry_transitions": 0,
    }
    assert _count(tasks, "agent_tasks", OWNER_B) == 1
    assert _count(tasks, "codex_retry_attempts", OWNER_B) == 1
    assert _count(tasks, "codex_retry_transitions", OWNER_B) == 1


def test_phase742_account_cleanup_uses_atomic_retry_task_graph_erasure() -> None:
    source = (Path(__file__).parents[1] / "app" / "account_cleanup.py").read_text(encoding="utf-8")

    assert "delete_owner_retry_task_graph" in source
    assert "retry_task_cleanup = delete_owner_retry_task_graph(clean)" in source
    assert '"agent_tasks": retry_task_cleanup["agent_tasks"]' in source
    assert '"codex_retry_attempts": retry_task_cleanup["codex_retry_attempts"]' in source
    assert '"codex_retry_transitions": retry_task_cleanup["codex_retry_transitions"]' in source
    assert "task_count = agent_task_store().delete_owner(clean)" not in source
