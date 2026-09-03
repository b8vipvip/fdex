from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError, AgentTaskCancelled, FdexAgentRuntime
from app.agent_tasks import TaskRunBusy, agent_task_store
from app.codex_app_server import CodexAppServerClient, CodexRpcError, CodexServerRequestDenied
from app.codex_engine import (
    _PROVIDER_ID,
    _DEVELOPER_INSTRUCTIONS,
    _codex_home,
    _codex_thread_config,
    _commit_and_publish,
    _launch_args,
    _safe_event_message,
    _safe_process_env,
    _task_network_allowed,
    _turn_error_text,
    resolve_codex_runtime,
    select_codex_provider,
)
from app.codex_host_store import CodexHostStore, codex_host_store
from app.config import fresh_settings


def _thread_from_result(result: Any, method: str) -> dict[str, Any]:
    thread = result.get("thread") if isinstance(result, dict) else None
    if not isinstance(thread, dict) or not str(thread.get("id") or "").strip():
        raise AgentRuntimeError(f"Codex {method} returned no thread id")
    return thread


def _turn_from_result(result: Any, method: str) -> dict[str, Any]:
    turn = result.get("turn") if isinstance(result, dict) else None
    if not isinstance(turn, dict) or not str(turn.get("id") or "").strip():
        raise AgentRuntimeError(f"Codex {method} returned no turn id")
    return turn


def _thread_common_params(
    *,
    provider: Any,
    worktree: Path,
    codex_home: Path,
    allow_network: bool,
) -> dict[str, Any]:
    return {
        "model": provider.model,
        "modelProvider": _PROVIDER_ID,
        "cwd": str(worktree),
        "approvalPolicy": "never",
        "sandbox": "workspace-write",
        "config": _codex_thread_config(codex_home, allow_network=allow_network),
        "developerInstructions": _DEVELOPER_INSTRUCTIONS,
    }


def thread_start_params(*, provider: Any, worktree: Path, codex_home: Path, allow_network: bool) -> dict[str, Any]:
    return {
        **_thread_common_params(
            provider=provider,
            worktree=worktree,
            codex_home=codex_home,
            allow_network=allow_network,
        ),
        "ephemeral": False,
    }


def thread_resume_params(
    thread_id: str,
    *,
    provider: Any,
    worktree: Path,
    codex_home: Path,
    allow_network: bool,
) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        **_thread_common_params(
            provider=provider,
            worktree=worktree,
            codex_home=codex_home,
            allow_network=allow_network,
        ),
    }


def thread_fork_params(
    thread_id: str,
    *,
    provider: Any,
    worktree: Path,
    codex_home: Path,
    allow_network: bool,
    last_turn_id: str = "",
) -> dict[str, Any]:
    payload = {
        "threadId": thread_id,
        **_thread_common_params(
            provider=provider,
            worktree=worktree,
            codex_home=codex_home,
            allow_network=allow_network,
        ),
        "ephemeral": False,
    }
    if last_turn_id:
        payload["lastTurnId"] = last_turn_id
    return payload


def turn_start_params(thread_id: str, prompt: str) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "clientUserMessageId": uuid.uuid4().hex,
        "input": [{"type": "text", "text": prompt, "text_elements": []}],
        "approvalPolicy": "never",
    }


def turn_steer_params(thread_id: str, turn_id: str, text: str) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "expectedTurnId": turn_id,
        "clientUserMessageId": uuid.uuid4().hex,
        "input": [{"type": "text", "text": text, "text_elements": []}],
    }


def _parent_commit_base(runtime: FdexAgentRuntime, task: Any) -> tuple[Path, Path, str] | None:
    parent_id = str(getattr(task, "parent_task_id", "") or "")
    if not parent_id:
        return None
    parent = agent_task_store().get(task.owner_id, parent_id)
    if parent is None:
        return None
    if parent.get("project_id") != task.project_id:
        return None
    commit = str(parent.get("commit_sha") or "").strip()
    if not commit:
        return None
    source, worktree_root, _ = runtime._source_and_worktrees(task)
    try:
        runtime._run_command(("git", "cat-file", "-e", f"{commit}^{{commit}}"), cwd=source)
    except Exception:
        return None
    return source, worktree_root, commit


def _ensure_codex_worktree(runtime: FdexAgentRuntime, task: Any) -> Path:
    if task.worktree:
        existing = Path(task.worktree).expanduser().resolve()
        if existing.is_dir():
            return existing
        raise AgentRuntimeError("agent task worktree disappeared")

    inherited = _parent_commit_base(runtime, task)
    if inherited is None:
        return runtime._ensure_worktree(task)

    source, worktree_root, base_ref = inherited
    worktree_root.mkdir(parents=True, exist_ok=True)
    branch = f"fdex-agent/{task.owner_id[:20]}-{task.id[:12]}"
    path = (worktree_root / task.id).resolve()
    if path.exists():
        raise AgentRuntimeError("agent worktree path already exists")
    runtime._run_command(("git", "worktree", "add", "-b", branch, str(path), base_ref), cwd=source)
    task.branch = branch
    task.worktree = str(path)
    task.emit("workspace.ready", f"Prepared isolated continuation worktree from {base_ref[:12]} on {branch}")
    return path


def _thread_metadata(
    *,
    task: Any,
    thread_id: str,
    runtime_spec: Any,
    provider: Any,
    worktree: Path,
    status: str,
    parent_thread_id: str = "",
    forked_from_turn_id: str = "",
    root_task_id: str = "",
) -> dict[str, Any]:
    return {
        "owner_id": task.owner_id,
        "task_id": task.id,
        "thread_id": thread_id,
        "project_id": task.project_id,
        "status": status,
        "runtime_version": runtime_spec.version,
        "provider_id": provider.provider_id,
        "provider_name": provider.name,
        "model": provider.model,
        "worktree": str(worktree),
        "parent_thread_id": parent_thread_id,
        "forked_from_turn_id": forked_from_turn_id,
        "root_task_id": root_task_id or task.id,
        "metadata": {"protocol": "codex-app-server-jsonrpc-v2"},
    }


async def _handle_steer_controls(
    *,
    client: CodexAppServerClient,
    store: CodexHostStore,
    task: Any,
    thread_id: str,
    turn_id: str,
) -> str:
    current_turn_id = turn_id
    controls = await asyncio.to_thread(
        store.claim_controls,
        owner_id=task.owner_id,
        thread_id=thread_id,
        actions=("steer",),
        limit=8,
    )
    for control in controls:
        control_id = int(control["id"])
        payload = control.get("payload") if isinstance(control.get("payload"), dict) else {}
        text = str(payload.get("text") or "").strip()
        if not text:
            await asyncio.to_thread(
                store.finish_control,
                owner_id=task.owner_id,
                control_id=control_id,
                state="rejected",
                error="steer text is empty",
            )
            continue
        try:
            result = await client.request(
                "turn/steer",
                turn_steer_params(thread_id, current_turn_id, text),
                timeout=20.0,
            )
            returned = str(result.get("turnId") or "") if isinstance(result, dict) else ""
            if returned:
                current_turn_id = returned
                await asyncio.to_thread(
                    store.update_thread_state,
                    task.owner_id,
                    thread_id,
                    current_turn_id=current_turn_id,
                    status="running",
                )
            await asyncio.to_thread(
                store.finish_control,
                owner_id=task.owner_id,
                control_id=control_id,
                state="succeeded",
                result={"turnId": current_turn_id},
            )
            task.emit("codex.turn_steered", f"Applied steer request to Codex turn {current_turn_id}")
        except Exception as exc:
            await asyncio.to_thread(
                store.finish_control,
                owner_id=task.owner_id,
                control_id=control_id,
                state="failed",
                error=str(exc),
            )
            task.emit("codex.control_failed", f"Steer request failed: {str(exc)[:500]}")
    return current_turn_id


async def _wait_for_compaction(
    *,
    client: CodexAppServerClient,
    store: CodexHostStore,
    task: Any,
    thread_id: str,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    await asyncio.to_thread(store.update_thread_state, task.owner_id, thread_id, status="compacting", current_turn_id="")
    await client.request("thread/compact/start", {"threadId": thread_id}, timeout=30.0)
    task.emit("codex.compact_started", f"Compacting Codex thread {thread_id}")
    compact_turn_id = ""
    started_recorded = False
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            method, params = await client.next_notification(timeout=1.0)
        except CodexRpcError as exc:
            if exc.code == -32002:
                continue
            raise
        if method == "turn/started":
            turn = params.get("turn")
            if isinstance(turn, dict) and str(turn.get("id") or ""):
                compact_turn_id = str(turn["id"])
                await asyncio.to_thread(
                    store.record_turn_started,
                    owner_id=task.owner_id,
                    task_id=task.id,
                    thread_id=thread_id,
                    turn_id=compact_turn_id,
                    kind="compact",
                    input_preview="[thread compact]",
                )
                started_recorded = True
        elif method == "turn/completed":
            turn = params.get("turn")
            if not isinstance(turn, dict) or not str(turn.get("id") or ""):
                continue
            completed_id = str(turn["id"])
            if compact_turn_id and completed_id != compact_turn_id:
                continue
            compact_turn_id = completed_id
            if not started_recorded:
                await asyncio.to_thread(
                    store.record_turn_started,
                    owner_id=task.owner_id,
                    task_id=task.id,
                    thread_id=thread_id,
                    turn_id=compact_turn_id,
                    kind="compact",
                    input_preview="[thread compact]",
                )
            status = str(turn.get("status") or "")
            error = _turn_error_text(turn.get("error"))
            await asyncio.to_thread(
                store.record_turn_completed,
                owner_id=task.owner_id,
                thread_id=thread_id,
                turn_id=compact_turn_id,
                status=status or "completed",
                error=error,
            )
            if status != "completed":
                raise AgentRuntimeError(f"Codex compact turn {status or 'failed'}: {error or 'no detail'}")
            task.emit("codex.compact_completed", f"Compacted Codex thread {thread_id}")
            return {"threadId": thread_id, "turnId": compact_turn_id, "status": status}
    await asyncio.to_thread(store.update_thread_state, task.owner_id, thread_id, status="failed", current_turn_id="")
    raise AgentRuntimeError("Codex thread compaction timed out")


async def _handle_compact_controls(
    *,
    client: CodexAppServerClient,
    store: CodexHostStore,
    task: Any,
    thread_id: str,
) -> None:
    controls = await asyncio.to_thread(
        store.claim_controls,
        owner_id=task.owner_id,
        thread_id=thread_id,
        actions=("compact",),
        limit=4,
    )
    for control in controls:
        control_id = int(control["id"])
        try:
            result = await _wait_for_compaction(
                client=client,
                store=store,
                task=task,
                thread_id=thread_id,
            )
            await asyncio.to_thread(
                store.finish_control,
                owner_id=task.owner_id,
                control_id=control_id,
                state="succeeded",
                result=result,
            )
        except Exception as exc:
            await asyncio.to_thread(
                store.finish_control,
                owner_id=task.owner_id,
                control_id=control_id,
                state="failed",
                error=str(exc),
            )
            task.emit("codex.control_failed", f"Compact request failed: {str(exc)[:500]}")


async def run_codex_task(runtime: FdexAgentRuntime, task_id: str) -> None:
    """Run one durable Codex Turn, starting/resuming/forking its official Thread as needed."""
    task = await runtime.get_task(task_id)
    if task is None:
        raise AgentRuntimeError("task not found")
    provider = select_codex_provider()
    if provider is None:
        raise AgentRuntimeError("没有可供 Codex 使用的 Responses 供应商")
    runtime_spec = resolve_codex_runtime()
    store = codex_host_store()

    task.status = "running"
    task.emit(
        "engine.selected",
        f"Official OpenAI Codex native app-server {runtime_spec.version} · {provider.name} / {provider.model}",
    )
    try:
        await runtime._raise_if_cancelled(task)
        worktree = await asyncio.to_thread(_ensure_codex_worktree, runtime, task)
        initial_head = await asyncio.to_thread(runtime._run_command, ("git", "rev-parse", "HEAD"), cwd=worktree)
        codex_home = await asyncio.to_thread(_codex_home, task.owner_id)
        allow_network = await asyncio.to_thread(_task_network_allowed, task)
        binding = await asyncio.to_thread(store.task_binding, task.owner_id, task.id)

        async def on_notification(method: str, params: dict[str, Any]) -> None:
            event = _safe_event_message(method, params)
            if event is not None:
                task.emit(*event)

        async def on_server_request(method: str, _params: dict[str, Any]) -> Any:
            task.emit("codex.server_request_denied", f"Denied unsupported interactive request: {method}")
            raise CodexServerRequestDenied(f"FDEX policy denies interactive request {method}")

        client = CodexAppServerClient(
            _launch_args(runtime_spec.path, provider),
            env=_safe_process_env(codex_home, provider.api_key),
            cwd=worktree,
            client_version=fresh_settings().app_version,
            request_timeout=30.0,
            notification_handler=on_notification,
            server_request_handler=on_server_request,
            experimental_api=True,
        )
        task.emit(
            "codex.started",
            "Starting native official Codex app-server in the isolated task worktree "
            f"(workspace network={'enabled' if allow_network else 'disabled'})",
        )

        final_parts: list[str] = []
        final_item_text = ""
        turn_status = ""
        turn_error = ""
        thread_id = ""
        turn_id = ""

        async with client:
            relation = str(binding.get("relation") or "") if binding else ""
            if binding and relation == "fork":
                source_thread_id = str(binding["thread_id"])
                source_thread = await asyncio.to_thread(store.get_thread, task.owner_id, source_thread_id)
                if source_thread is None:
                    raise AgentRuntimeError("Codex fork source thread is missing")
                fork_from_turn = str(source_thread.get("last_completed_turn_id") or "")
                fork_result = await client.request(
                    "thread/fork",
                    thread_fork_params(
                        source_thread_id,
                        provider=provider,
                        worktree=worktree,
                        codex_home=codex_home,
                        allow_network=allow_network,
                        last_turn_id=fork_from_turn,
                    ),
                )
                thread_obj = _thread_from_result(fork_result, "thread/fork")
                thread_id = str(thread_obj["id"])
                await asyncio.to_thread(
                    store.upsert_thread,
                    **_thread_metadata(
                        task=task,
                        thread_id=thread_id,
                        runtime_spec=runtime_spec,
                        provider=provider,
                        worktree=worktree,
                        status="ready",
                        parent_thread_id=source_thread_id,
                        forked_from_turn_id=fork_from_turn,
                        root_task_id=str(source_thread.get("root_task_id") or task.id),
                    ),
                )
                await asyncio.to_thread(
                    store.bind_task,
                    owner_id=task.owner_id,
                    task_id=task.id,
                    thread_id=thread_id,
                    relation="forked",
                    source_task_id=str(binding.get("source_task_id") or ""),
                )
                task.emit("codex.thread_forked", f"Forked Codex thread {source_thread_id} → {thread_id}")
            elif binding:
                thread_id = str(binding["thread_id"])
                resume_result = await client.request(
                    "thread/resume",
                    thread_resume_params(
                        thread_id,
                        provider=provider,
                        worktree=worktree,
                        codex_home=codex_home,
                        allow_network=allow_network,
                    ),
                )
                thread_obj = _thread_from_result(resume_result, "thread/resume")
                returned_id = str(thread_obj["id"])
                if returned_id != thread_id:
                    raise AgentRuntimeError("Codex thread/resume returned a different thread id")
                existing = await asyncio.to_thread(store.get_thread, task.owner_id, thread_id)
                await asyncio.to_thread(
                    store.upsert_thread,
                    **_thread_metadata(
                        task=task,
                        thread_id=thread_id,
                        runtime_spec=runtime_spec,
                        provider=provider,
                        worktree=worktree,
                        status="ready",
                        parent_thread_id=str((existing or {}).get("parent_thread_id") or ""),
                        forked_from_turn_id=str((existing or {}).get("forked_from_turn_id") or ""),
                        root_task_id=str((existing or {}).get("root_task_id") or task.id),
                    ),
                )
                task.emit("codex.thread_resumed", f"Resumed Codex thread {thread_id}")
            else:
                thread_result = await client.request(
                    "thread/start",
                    thread_start_params(
                        provider=provider,
                        worktree=worktree,
                        codex_home=codex_home,
                        allow_network=allow_network,
                    ),
                )
                thread_obj = _thread_from_result(thread_result, "thread/start")
                thread_id = str(thread_obj["id"])
                await asyncio.to_thread(
                    store.upsert_thread,
                    **_thread_metadata(
                        task=task,
                        thread_id=thread_id,
                        runtime_spec=runtime_spec,
                        provider=provider,
                        worktree=worktree,
                        status="ready",
                    ),
                )
                await asyncio.to_thread(
                    store.bind_task,
                    owner_id=task.owner_id,
                    task_id=task.id,
                    thread_id=thread_id,
                    relation="start",
                )
                task.emit("codex.thread_started", f"Codex thread {thread_id}")

            payload = turn_start_params(thread_id, task.prompt)
            turn_result = await client.request("turn/start", payload)
            turn_obj = _turn_from_result(turn_result, "turn/start")
            turn_id = str(turn_obj["id"])
            await asyncio.to_thread(
                store.record_turn_started,
                owner_id=task.owner_id,
                task_id=task.id,
                thread_id=thread_id,
                turn_id=turn_id,
                kind="turn",
                input_preview=task.prompt,
                client_user_message_id=str(payload["clientUserMessageId"]),
            )
            task.emit("codex.turn_started", f"Codex turn {turn_id}")

            try:
                while True:
                    await runtime._raise_if_cancelled(task)
                    turn_id = await _handle_steer_controls(
                        client=client,
                        store=store,
                        task=task,
                        thread_id=thread_id,
                        turn_id=turn_id,
                    )
                    try:
                        method, params = await client.next_notification(timeout=0.75)
                    except CodexRpcError as exc:
                        if exc.code == -32002:
                            continue
                        raise
                    if method == "item/agentMessage/delta" and str(params.get("turnId") or "") in {"", turn_id}:
                        delta = str(params.get("delta") or "")
                        if delta:
                            final_parts.append(delta)
                    elif method == "item/completed" and str(params.get("turnId") or "") in {"", turn_id}:
                        item = params.get("item")
                        if isinstance(item, dict) and str(item.get("type") or "") == "agentMessage":
                            text = str(item.get("text") or "").strip()
                            if text:
                                final_item_text = text
                    elif method == "turn/completed":
                        completed = params.get("turn")
                        if not isinstance(completed, dict) or str(completed.get("id") or "") != turn_id:
                            continue
                        turn_status = str(completed.get("status") or "")
                        turn_error = _turn_error_text(completed.get("error"))
                        await asyncio.to_thread(
                            store.record_turn_completed,
                            owner_id=task.owner_id,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            status=turn_status or "failed",
                            error=turn_error,
                        )
                        break
            except AgentTaskCancelled:
                if thread_id and turn_id:
                    try:
                        await client.request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": turn_id},
                            timeout=10.0,
                        )
                    except Exception:
                        pass
                    try:
                        await asyncio.to_thread(
                            store.record_turn_completed,
                            owner_id=task.owner_id,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            status="interrupted",
                            error="FDEX task cancellation requested",
                        )
                    except Exception:
                        pass
                raise

            # Compact is serialized after the active model turn.  A request can be inserted by
            # any Uvicorn worker while this worker owns the stdio app-server process.
            await _handle_compact_controls(client=client, store=store, task=task, thread_id=thread_id)

        if turn_status != "completed":
            raise AgentRuntimeError(
                f"Codex turn {turn_status or 'ended without completion'}: {turn_error or 'no additional error detail'}"
            )
        final_response = final_item_text or "".join(final_parts).strip() or "Codex 已完成任务。"
        await asyncio.to_thread(
            _commit_and_publish,
            runtime,
            task,
            worktree,
            initial_head.strip(),
            final_response,
        )
        if task.pr_url:
            final_response += f"\n\nPull Request: {task.pr_url}"
        elif task.pushed:
            final_response += f"\n\n已推送分支：{task.branch}"
        await runtime.complete_task(task_id, final_response)
    except AgentTaskCancelled:
        return
    except Exception as exc:
        binding = await asyncio.to_thread(store.task_binding, task.owner_id, task.id)
        if binding:
            try:
                await asyncio.to_thread(
                    store.update_thread_state,
                    task.owner_id,
                    str(binding["thread_id"]),
                    status="failed",
                    current_turn_id="",
                )
            except Exception:
                pass
        await runtime.fail_task(task_id, str(exc))


async def create_codex_continuation(
    runtime: FdexAgentRuntime,
    *,
    owner_id: str,
    source_task_id: str,
    prompt: str,
    fork: bool,
) -> Any:
    source = await runtime.get_task(source_task_id)
    if source is None or source.owner_id != owner_id:
        raise AgentRuntimeError("source task not found")
    if source.status not in {"succeeded", "failed", "canceled"}:
        raise AgentRuntimeError("resume/fork requires a terminal source task")
    if source.project_id is None:
        raise AgentRuntimeError("resume/fork requires a configured Agent project")
    binding = await asyncio.to_thread(codex_host_store().task_binding, owner_id, source_task_id)
    if binding is None:
        raise AgentRuntimeError("source task has no persisted Codex thread")
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise AgentRuntimeError("continuation prompt is required")
    child = await runtime.create_task(
        clean_prompt,
        owner_id=owner_id,
        project_id=source.project_id,
        parent_task_id=source.id,
        task_kind="fork" if fork else "resume",
    )
    await asyncio.to_thread(
        codex_host_store().bind_task,
        owner_id=owner_id,
        task_id=child.id,
        thread_id=str(binding["thread_id"]),
        relation="fork" if fork else "resume",
        source_task_id=source.id,
    )
    child.emit(
        "codex.thread_fork_requested" if fork else "codex.thread_resume_requested",
        f"Continuation linked to Codex thread {binding['thread_id']}",
    )
    return child


async def queue_codex_steer(*, owner_id: str, task_id: str, text: str) -> dict[str, Any]:
    store = codex_host_store()
    binding = await asyncio.to_thread(store.task_binding, owner_id, task_id)
    if binding is None:
        raise AgentRuntimeError("task has no persisted Codex thread")
    thread_id = str(binding["thread_id"])
    thread = await asyncio.to_thread(store.get_thread, owner_id, thread_id)
    if thread is None or str(thread.get("status") or "") != "running" or not str(thread.get("current_turn_id") or ""):
        raise AgentRuntimeError("Codex thread has no active turn to steer")
    clean = text.strip()
    if not clean:
        raise AgentRuntimeError("steer text is required")
    if len(clean) > 12000:
        raise AgentRuntimeError("steer text exceeds 12000 characters")
    return await asyncio.to_thread(
        store.enqueue_control,
        owner_id=owner_id,
        task_id=task_id,
        thread_id=thread_id,
        action="steer",
        payload={"text": clean, "expectedTurnId": str(thread["current_turn_id"])},
    )


async def compact_codex_thread(runtime: FdexAgentRuntime, *, owner_id: str, task_id: str) -> dict[str, Any]:
    store = codex_host_store()
    binding = await asyncio.to_thread(store.task_binding, owner_id, task_id)
    if binding is None:
        raise AgentRuntimeError("task has no persisted Codex thread")
    thread_id = str(binding["thread_id"])
    thread = await asyncio.to_thread(store.get_thread, owner_id, thread_id)
    if thread is None:
        raise AgentRuntimeError("Codex thread not found")
    control = await asyncio.to_thread(
        store.enqueue_control,
        owner_id=owner_id,
        task_id=task_id,
        thread_id=thread_id,
        action="compact",
        payload={},
    )
    if str(thread.get("status") or "") in {"running", "compacting"}:
        return control

    row = await asyncio.to_thread(agent_task_store().get, owner_id, task_id)
    if row is None:
        raise AgentRuntimeError("task not found")
    task = runtime._task_from_record(row)
    worktree = Path(str(thread.get("worktree") or task.worktree or "")).expanduser().resolve()
    if not worktree.is_dir():
        await asyncio.to_thread(
            store.finish_control,
            owner_id=owner_id,
            control_id=int(control["id"]),
            state="rejected",
            error="task worktree was released; resume/fork a continuation before compacting",
        )
        raise AgentRuntimeError("task worktree was released; resume/fork a continuation before compacting")

    provider = select_codex_provider()
    if provider is None:
        raise AgentRuntimeError("没有可供 Codex 使用的 Responses 供应商")
    runtime_spec = resolve_codex_runtime()
    codex_home = await asyncio.to_thread(_codex_home, owner_id)
    allow_network = await asyncio.to_thread(_task_network_allowed, task)

    try:
        with agent_task_store().run_lock(task_id):
            client = CodexAppServerClient(
                _launch_args(runtime_spec.path, provider),
                env=_safe_process_env(codex_home, provider.api_key),
                cwd=worktree,
                client_version=fresh_settings().app_version,
                request_timeout=30.0,
                server_request_handler=lambda method, _params: (_ for _ in ()).throw(
                    CodexServerRequestDenied(f"FDEX policy denies interactive request {method}")
                ),
                experimental_api=True,
            )
            async with client:
                result = await client.request(
                    "thread/resume",
                    thread_resume_params(
                        thread_id,
                        provider=provider,
                        worktree=worktree,
                        codex_home=codex_home,
                        allow_network=allow_network,
                    ),
                )
                returned = _thread_from_result(result, "thread/resume")
                if str(returned["id"]) != thread_id:
                    raise AgentRuntimeError("Codex thread/resume returned a different thread id")
                claimed = await asyncio.to_thread(
                    store.claim_controls,
                    owner_id=owner_id,
                    thread_id=thread_id,
                    actions=("compact",),
                    limit=4,
                )
                for item in claimed:
                    try:
                        compact_result = await _wait_for_compaction(
                            client=client,
                            store=store,
                            task=task,
                            thread_id=thread_id,
                        )
                        await asyncio.to_thread(
                            store.finish_control,
                            owner_id=owner_id,
                            control_id=int(item["id"]),
                            state="succeeded",
                            result=compact_result,
                        )
                    except Exception as exc:
                        await asyncio.to_thread(
                            store.finish_control,
                            owner_id=owner_id,
                            control_id=int(item["id"]),
                            state="failed",
                            error=str(exc),
                        )
                        raise
    except TaskRunBusy:
        return control
    return (await asyncio.to_thread(store.get_control, owner_id, int(control["id"]))) or control