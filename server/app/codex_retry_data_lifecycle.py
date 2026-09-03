from __future__ import annotations

from typing import Any

from app.agent_tasks import _owner, agent_task_store


def delete_owner_retry_task_graph(owner_id: str) -> dict[str, int]:
    """Atomically erase one owner's durable Agent retry/task graph.

    Phase 7.41 introduced ``codex_retry_transitions`` in the same SQLite database as
    ``codex_retry_attempts`` and ``agent_tasks``. Account erasure must therefore remove all three
    projections together; otherwise a deleted owner can leave retry decision/backoff/provider
    metadata behind even after the AgentTask rows are gone.

    The transition table is optional for rolling downgrade/upgrade compatibility with Phase 7.40
    databases. Unknown owners are valid no-op erasures. This function intentionally does not touch
    filesystem worktrees/CODEX_HOME or Host/Item/Input databases; ``account_cleanup`` owns those
    resources and calls this helper only after active-task/Host guards have passed.
    """

    clean = _owner(owner_id)
    store = agent_task_store()
    store.init()
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        task_count = int(
            conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE owner_id=?", (clean,)).fetchone()[0]
        )
        attempt_count = 0
        transition_count = 0
        if "codex_retry_attempts" in tables:
            attempt_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM codex_retry_attempts WHERE owner_id=?",
                    (clean,),
                ).fetchone()[0]
            )
        if "codex_retry_transitions" in tables:
            transition_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM codex_retry_transitions WHERE owner_id=?",
                    (clean,),
                ).fetchone()[0]
            )
            conn.execute("DELETE FROM codex_retry_transitions WHERE owner_id=?", (clean,))
        if "codex_retry_attempts" in tables:
            conn.execute("DELETE FROM codex_retry_attempts WHERE owner_id=?", (clean,))
        conn.execute("DELETE FROM agent_tasks WHERE owner_id=?", (clean,))

        remaining_tasks = int(
            conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE owner_id=?", (clean,)).fetchone()[0]
        )
        remaining_attempts = 0
        remaining_transitions = 0
        if "codex_retry_attempts" in tables:
            remaining_attempts = int(
                conn.execute(
                    "SELECT COUNT(*) FROM codex_retry_attempts WHERE owner_id=?",
                    (clean,),
                ).fetchone()[0]
            )
        if "codex_retry_transitions" in tables:
            remaining_transitions = int(
                conn.execute(
                    "SELECT COUNT(*) FROM codex_retry_transitions WHERE owner_id=?",
                    (clean,),
                ).fetchone()[0]
            )
        if remaining_tasks or remaining_attempts or remaining_transitions:
            raise RuntimeError("Codex retry/task owner erasure did not converge")

    return {
        "agent_tasks": task_count,
        "codex_retry_attempts": attempt_count,
        "codex_retry_transitions": transition_count,
    }
