from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.codex_host_guard import (
    CodexThreadBusy,
    reconcile_orphaned_thread,
    settle_orphaned_controls,
    thread_lock,
)
from app.codex_host_runtime import (
    thread_fork_params,
    thread_resume_params,
    thread_start_params,
    turn_start_params,
    turn_steer_params,
)
from app.codex_host_store import CodexHostStore


OWNER = "usr_phase721_owner"
OTHER = "usr_phase721_other"
TASK1 = "1" * 32
TASK2 = "2" * 32
TASK3 = "3" * 32
THREAD1 = "019-phase721-thread-root"
THREAD2 = "019-phase721-thread-fork"
TURN1 = "019-phase721-turn-one"


def _provider() -> SimpleNamespace:
    return SimpleNamespace(model="gpt-5.3-codex", provider_id=7, name="provider-seven")


def test_official_lifecycle_payloads_use_current_app_server_field_names(tmp_path: Path) -> None:
    provider = _provider()
    worktree = tmp_path / "worktree"
    codex_home = tmp_path / "codex-home"

    start = thread_start_params(provider=provider, worktree=worktree, codex_home=codex_home, allow_network=False)
    assert start["model"] == "gpt-5.3-codex"
    assert start["modelProvider"] == "fdex"
    assert start["cwd"] == str(worktree)
    assert start["approvalPolicy"] == "never"
    assert start["sandbox"] == "workspace-write"
    assert start["ephemeral"] is False
    assert start["config"]["sandbox_workspace_write"]["network_access"] is False

    resume = thread_resume_params(
        THREAD1,
        provider=provider,
        worktree=worktree,
        codex_home=codex_home,
        allow_network=True,
    )
    assert resume["threadId"] == THREAD1
    assert resume["config"]["sandbox_workspace_write"]["network_access"] is True

    fork = thread_fork_params(
        THREAD1,
        provider=provider,
        worktree=worktree,
        codex_home=codex_home,
        allow_network=False,
        last_turn_id=TURN1,
    )
    assert fork["threadId"] == THREAD1
    assert fork["lastTurnId"] == TURN1
    assert fork["ephemeral"] is False

    turn = turn_start_params(THREAD1, "continue the task")
    assert turn["threadId"] == THREAD1
    assert turn["clientUserMessageId"]
    assert turn["input"] == [{"type": "text", "text": "continue the task", "text_elements": []}]

    steer = turn_steer_params(THREAD1, TURN1, "do not change public API")
    assert steer["threadId"] == THREAD1
    assert steer["expectedTurnId"] == TURN1
    assert steer["clientUserMessageId"]
    assert steer["input"][0]["text"] == "do not change public API"


def test_thread_turn_mapping_survives_task_continuation(tmp_path: Path) -> None:
    store = CodexHostStore(tmp_path / "codex-host.db")
    store.upsert_thread(
        owner_id=OWNER,
        task_id=TASK1,
        thread_id=THREAD1,
        project_id=10,
        runtime_version="0.147.0",
        provider_id=7,
        provider_name="provider-seven",
        model="gpt-5.3-codex",
        worktree="/tmp/fdex-one",
    )
    store.bind_task(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, relation="start")
    store.record_turn_started(
        owner_id=OWNER,
        task_id=TASK1,
        thread_id=THREAD1,
        turn_id=TURN1,
        input_preview="first turn",
        client_user_message_id="client-one",
    )
    running = store.task_state(OWNER, TASK1)
    assert running is not None
    assert running["thread"]["current_turn_id"] == TURN1
    assert running["thread"]["status"] == "running"

    store.record_turn_completed(
        owner_id=OWNER,
        thread_id=THREAD1,
        turn_id=TURN1,
        status="completed",
    )
    store.bind_task(
        owner_id=OWNER,
        task_id=TASK2,
        thread_id=THREAD1,
        relation="resume",
        source_task_id=TASK1,
    )

    original = store.task_state(OWNER, TASK1)
    continuation = store.task_state(OWNER, TASK2)
    assert original is not None and continuation is not None
    assert original["binding"]["thread_id"] == THREAD1
    assert continuation["binding"]["thread_id"] == THREAD1
    assert continuation["binding"]["relation"] == "resume"
    assert continuation["binding"]["source_task_id"] == TASK1
    assert continuation["thread"]["last_completed_turn_id"] == TURN1
    assert continuation["thread"]["status"] == "idle"


def test_fork_keeps_thread_lineage_and_task_binding(tmp_path: Path) -> None:
    store = CodexHostStore(tmp_path / "codex-host.db")
    root = store.upsert_thread(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, project_id=10)
    store.bind_task(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, relation="start")
    store.record_turn_started(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, turn_id=TURN1)
    store.record_turn_completed(owner_id=OWNER, thread_id=THREAD1, turn_id=TURN1, status="completed")

    fork = store.upsert_thread(
        owner_id=OWNER,
        task_id=TASK3,
        thread_id=THREAD2,
        project_id=10,
        parent_thread_id=THREAD1,
        forked_from_turn_id=TURN1,
        root_task_id=str(root["root_task_id"]),
    )
    store.bind_task(
        owner_id=OWNER,
        task_id=TASK3,
        thread_id=THREAD2,
        relation="forked",
        source_task_id=TASK1,
    )
    state = store.task_state(OWNER, TASK3)
    assert state is not None
    assert fork["parent_thread_id"] == THREAD1
    assert fork["forked_from_turn_id"] == TURN1
    assert state["thread"]["root_task_id"] == TASK1
    assert state["binding"]["relation"] == "forked"


def test_cross_worker_control_queue_claims_once_and_records_result(tmp_path: Path) -> None:
    store = CodexHostStore(tmp_path / "codex-host.db")
    store.upsert_thread(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, project_id=10)
    store.bind_task(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, relation="start")
    store.record_turn_started(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, turn_id=TURN1)

    control = store.enqueue_control(
        owner_id=OWNER,
        task_id=TASK1,
        thread_id=THREAD1,
        action="steer",
        payload={"text": "keep compatibility", "expectedTurnId": TURN1},
    )
    claimed = store.claim_controls(owner_id=OWNER, thread_id=THREAD1, actions=("steer",), limit=10)
    assert [item["id"] for item in claimed] == [control["id"]]
    assert claimed[0]["state"] == "processing"
    assert store.claim_controls(owner_id=OWNER, thread_id=THREAD1, actions=("steer",), limit=10) == []

    store.finish_control(
        owner_id=OWNER,
        control_id=int(control["id"]),
        state="succeeded",
        result={"turnId": TURN1},
    )
    finished = store.get_control(OWNER, int(control["id"]))
    assert finished is not None
    assert finished["state"] == "succeeded"
    assert finished["result"] == {"turnId": TURN1}


def test_thread_flock_prevents_two_workers_from_using_same_thread(tmp_path: Path) -> None:
    store = CodexHostStore(tmp_path / "codex-host.db")
    store.upsert_thread(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, project_id=10)
    with thread_lock(store, OWNER, THREAD1):
        with pytest.raises(CodexThreadBusy, match="already using"):
            with thread_lock(store, OWNER, THREAD1):
                pass


def test_orphan_recovery_terminalizes_turn_and_controls_after_dead_worker(tmp_path: Path) -> None:
    store = CodexHostStore(tmp_path / "codex-host.db")
    store.upsert_thread(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, project_id=10)
    store.bind_task(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, relation="start")
    store.record_turn_started(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, turn_id=TURN1)
    control = store.enqueue_control(
        owner_id=OWNER,
        task_id=TASK1,
        thread_id=THREAD1,
        action="steer",
        payload={"text": "late steer"},
    )
    claimed = store.claim_controls(owner_id=OWNER, thread_id=THREAD1, actions=("steer",), limit=1)
    assert claimed and claimed[0]["state"] == "processing"

    with thread_lock(store, OWNER, THREAD1):
        reconcile_orphaned_thread(store, OWNER, THREAD1)

    state = store.task_state(OWNER, TASK1)
    assert state is not None
    assert state["thread"]["status"] == "interrupted"
    assert state["thread"]["current_turn_id"] == ""
    assert state["turns"][0]["status"] == "interrupted"
    recovered = store.get_control(OWNER, int(control["id"]))
    assert recovered is not None
    assert recovered["state"] == "failed"
    assert store.active_count(OWNER) == 0


def test_host_exit_settles_late_controls_instead_of_blocking_account_forever(tmp_path: Path) -> None:
    store = CodexHostStore(tmp_path / "codex-host.db")
    store.upsert_thread(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, project_id=10)
    store.bind_task(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, relation="start")
    steer = store.enqueue_control(
        owner_id=OWNER,
        task_id=TASK1,
        thread_id=THREAD1,
        action="steer",
        payload={"text": "too late"},
    )
    compact = store.enqueue_control(
        owner_id=OWNER,
        task_id=TASK1,
        thread_id=THREAD1,
        action="compact",
        payload={},
    )
    changed = settle_orphaned_controls(
        store,
        OWNER,
        THREAD1,
        reason="host closed",
    )
    assert changed == 2
    assert store.get_control(OWNER, int(steer["id"]))["state"] == "rejected"  # type: ignore[index]
    assert store.get_control(OWNER, int(compact["id"]))["state"] == "failed"  # type: ignore[index]
    assert store.active_count(OWNER) == 0


def test_owner_scope_blocks_cross_account_thread_access_and_cleanup(tmp_path: Path) -> None:
    store = CodexHostStore(tmp_path / "codex-host.db")
    store.upsert_thread(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, project_id=10)
    store.bind_task(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, relation="start")

    assert store.get_thread(OTHER, THREAD1) is None
    assert store.task_binding(OTHER, TASK1) is None
    with pytest.raises(ValueError, match="owner mismatch"):
        store.upsert_thread(owner_id=OTHER, task_id=TASK2, thread_id=THREAD1, project_id=10)

    counts = store.delete_owner(OWNER)
    assert counts == {"threads": 1, "task_bindings": 1, "turns": 0, "controls": 0}
    assert store.get_thread(OWNER, THREAD1) is None


def test_cleanup_refuses_active_turn_or_control(tmp_path: Path) -> None:
    store = CodexHostStore(tmp_path / "codex-host.db")
    store.upsert_thread(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, project_id=10)
    store.bind_task(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, relation="start")
    store.record_turn_started(owner_id=OWNER, task_id=TASK1, thread_id=THREAD1, turn_id=TURN1)
    assert store.active_count(OWNER) == 1
    with pytest.raises(ValueError, match="active operations"):
        store.delete_owner(OWNER)


def test_phase721_is_wired_into_runtime_portal_and_account_erasure() -> None:
    root = Path(__file__).resolve().parents[2]
    loop = (root / "server/app/agent_loop.py").read_text(encoding="utf-8")
    entry = (root / "server/app/codex_host_entry.py").read_text(encoding="utf-8")
    routes = (root / "server/app/user_agent_task_routes.py").read_text(encoding="utf-8")
    template = (root / "server/app/templates/user_agent.html").read_text(encoding="utf-8")
    cleanup = (root / "server/app/account_cleanup.py").read_text(encoding="utf-8")
    guard = (root / "server/app/codex_host_guard.py").read_text(encoding="utf-8")

    # Phase 7.23 inserts only the task-scoped interaction entry in front of the existing 7.21
    # guard. The Thread flock/capture implementation remains the execution authority underneath.
    assert "from app.codex_host_entry import run_codex_task" in loop
    assert "from app.codex_host_guard import run_codex_task as guarded_run_codex_task" in entry
    assert "flock" in guard
    for path in ("/codex/resume", "/codex/fork", "/codex/steer", "/codex/compact"):
        assert path in routes
        assert path in template
    assert "codex_host_store().delete_owner(clean)" in cleanup
    assert "codex_host_store().active_count(clean)" in cleanup
