from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Callable

from app.agent_runtime import AgentTask, AgentRuntimeError
from app.agent_tasks import agent_task_store
from app.codex_app_server import CodexServerRequestDenied
from app.codex_interaction_store import CodexInteractionStore, codex_interaction_store
from app.codex_item_store import codex_item_store

_SUPPORTED = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
}


def interaction_kind(method: str) -> str:
    return {
        "item/commandExecution/requestApproval": "command_approval",
        "item/fileChange/requestApproval": "file_change_approval",
        "item/permissions/requestApproval": "permissions_approval",
        "item/tool/requestUserInput": "user_input",
    }.get(method, "unsupported")


def _public_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "interactionId": str(row.get("id") or ""),
        "method": str(row.get("method") or ""),
        "kind": interaction_kind(str(row.get("method") or "")),
        "threadId": str(row.get("thread_id") or ""),
        "turnId": str(row.get("turn_id") or ""),
        "itemId": str(row.get("item_id") or ""),
        "approvalId": str(row.get("approval_id") or ""),
        "state": str(row.get("state") or ""),
        "blocking": bool(row.get("blocking")),
        "request": row.get("request") if isinstance(row.get("request"), dict) else {},
        "responseSummary": row.get("response_summary") if isinstance(row.get("response_summary"), dict) else {},
        "error": str(row.get("error") or "")[:2000],
        "createdAt": str(row.get("created_at") or ""),
        "updatedAt": str(row.get("updated_at") or ""),
    }


async def publish_interaction_event(owner_id: str, task_id: str, row: dict[str, Any], phase: str) -> None:
    await asyncio.to_thread(
        codex_item_store().record_notification,
        owner_id=owner_id,
        task_id=task_id,
        method=f"fdex/interaction/{phase}",
        params=_public_event(row),
    )


def interaction_item_projection(owner_id: str, task_id: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """Recover the exact Item identified by an interaction without a bounded list scan."""
    thread_id = str(row.get("thread_id") or "")
    turn_id = str(row.get("turn_id") or "")
    item_id = str(row.get("item_id") or "")
    if not thread_id or not turn_id or not item_id:
        return None
    store = codex_item_store()
    store.init()
    with store.db() as conn:
        item = conn.execute(
            """
            SELECT item_type,status,payload_json,delta_text FROM codex_items
            WHERE owner_id=? AND task_id=? AND thread_id=? AND turn_id=? AND item_id=?
            """,
            (owner_id, task_id, thread_id, turn_id, item_id),
        ).fetchone()
    if item is None:
        return None
    try:
        payload = json.loads(str(item["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    return {
        "item_type": str(item["item_type"] or "unknown"),
        "status": str(item["status"] or ""),
        "payload": payload if isinstance(payload, dict) else {},
        "delta_text": str(item["delta_text"] or ""),
    }


def _scope_root(worktree: str) -> Path:
    clean = str(worktree or "").strip()
    if not clean:
        raise AgentRuntimeError("Codex approval cannot be granted before the task worktree is known")
    root = Path(clean).expanduser().resolve()
    if not root.is_dir():
        raise AgentRuntimeError("Codex approval worktree is unavailable")
    return root


def _within_root(root: Path, value: Any) -> bool:
    clean = str(value or "").strip()
    if not clean or any(ord(ch) < 32 for ch in clean):
        return False
    candidate = Path(clean).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    return resolved == root or root in resolved.parents


def _filesystem_permission_paths(profile: dict[str, Any]) -> list[str]:
    file_system = profile.get("fileSystem")
    if file_system is None:
        return []
    if not isinstance(file_system, dict):
        raise AgentRuntimeError("Codex filesystem permission request has an invalid shape")
    unknown = {str(key) for key, value in file_system.items() if value is not None} - {
        "read",
        "write",
        "globScanMaxDepth",
        "entries",
    }
    if unknown:
        raise AgentRuntimeError("Codex filesystem permission request contains unsupported fields")
    paths: list[str] = []
    for key in ("read", "write"):
        values = file_system.get(key)
        if values is None:
            continue
        if not isinstance(values, list):
            raise AgentRuntimeError("Codex filesystem permission roots have an invalid shape")
        paths.extend(str(value) for value in values)
    entries = file_system.get("entries")
    if entries is not None:
        if not isinstance(entries, list):
            raise AgentRuntimeError("Codex filesystem permission entries have an invalid shape")
        for entry in entries:
            if not isinstance(entry, dict):
                raise AgentRuntimeError("Codex filesystem permission entry is invalid")
            path_spec = entry.get("path")
            if not isinstance(path_spec, dict) or str(path_spec.get("type") or "") != "path":
                # glob_pattern and special paths cannot be proven owner/worktree scoped by a
                # generic server path check, so FDEX fails closed instead of broadening access.
                raise AgentRuntimeError("FDEX does not grant glob/special filesystem escalation")
            paths.append(str(path_spec.get("path") or ""))
    return paths


def enforce_response_policy(
    row: dict[str, Any],
    action: str,
    *,
    allow_network: bool,
    worktree: str,
    item_projection: dict[str, Any] | None = None,
) -> None:
    """Keep FDEX project/worktree authority above a human Codex approval click.

    Human approval answers an interactive Codex question; it is not authority to escape the FDEX
    tenant boundary. Positive command/file/permission decisions are therefore narrowed to actions
    that FDEX can prove remain inside the task worktree and project network policy.
    """
    method = str(row.get("method") or "")
    clean_action = str(action or "").strip()
    if clean_action in {"decline", "cancel", "deny"} or method == "item/tool/requestUserInput":
        return
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    root = _scope_root(worktree)

    if method == "item/commandExecution/requestApproval":
        cwd = request.get("cwd")
        if cwd and not _within_root(root, cwd):
            raise AgentRuntimeError("FDEX policy blocks command approval outside the task worktree")
        kind = str(request.get("kind") or "command")
        if kind == "writeStdin":
            if clean_action != "accept":
                raise AgentRuntimeError("writeStdin approval only supports one-time accept in FDEX")
            return
        network_context = request.get("networkApprovalContext")
        network_amendments = request.get("proposedNetworkPolicyAmendments")
        if network_context is not None or network_amendments is not None:
            if not allow_network:
                raise AgentRuntimeError("FDEX project policy has network access disabled")
            return
        # A regular on-request command prompt without network context normally represents an
        # unsandboxed/escalated retry. Until a separate outer filesystem namespace is available,
        # allowing it could expose FDEX service files, so only decline/cancel is accepted.
        raise AgentRuntimeError(
            "FDEX blocks unsandboxed command escalation; only scoped network approval or writeStdin can be allowed"
        )

    if method == "item/fileChange/requestApproval":
        grant_root = request.get("grantRoot")
        if grant_root and not _within_root(root, grant_root):
            raise AgentRuntimeError("FDEX policy blocks file-change grantRoot outside the task worktree")
        payload = item_projection.get("payload") if isinstance(item_projection, dict) else None
        if not isinstance(payload, dict) or payload.get("fdex_truncated"):
            raise AgentRuntimeError("FDEX cannot verify the file-change Item; approval fails closed")
        changes = payload.get("changes")
        if not isinstance(changes, list) or not changes:
            raise AgentRuntimeError("FDEX cannot verify file-change paths; approval fails closed")
        for change in changes:
            if not isinstance(change, dict) or not _within_root(root, change.get("path")):
                raise AgentRuntimeError("FDEX policy blocks file changes outside the task worktree")
        if clean_action == "acceptForSession" and not grant_root:
            raise AgentRuntimeError("session-wide file approval requires an explicit in-worktree grantRoot")
        return

    if method == "item/permissions/requestApproval":
        permissions = request.get("permissions")
        if not isinstance(permissions, dict):
            raise AgentRuntimeError("Codex permission request has an invalid shape")
        unknown = {str(key) for key, value in permissions.items() if value is not None} - {"network", "fileSystem"}
        if unknown:
            raise AgentRuntimeError("Codex permission request contains unsupported fields")
        if permissions.get("network") is not None and not allow_network:
            raise AgentRuntimeError("FDEX project policy has network access disabled")
        for path in _filesystem_permission_paths(permissions):
            if not _within_root(root, path):
                raise AgentRuntimeError("FDEX policy blocks filesystem permission outside the task worktree")
        return

    raise AgentRuntimeError("unsupported Codex interaction method")


class CodexInteractionBroker:
    """Translate supported app-server requests into durable FDEX user interactions."""

    def __init__(
        self,
        *,
        task: AgentTask,
        store: CodexInteractionStore | None = None,
        host_session_id: str | None = None,
        transport_alive: Callable[[], bool] | None = None,
        max_wait_seconds: float = 3600.0,
    ) -> None:
        self.task = task
        self.store = store or codex_interaction_store()
        self.host_session_id = host_session_id or uuid.uuid4().hex
        self.transport_alive = transport_alive or (lambda: True)
        self.max_wait_seconds = max(30.0, float(max_wait_seconds))

    async def _cancel_requested(self) -> bool:
        if self.task.cancel_requested:
            return True
        try:
            requested = await asyncio.to_thread(agent_task_store().cancel_requested, self.task.id)
        except (KeyError, ValueError):
            # If the durable task record vanished while an approval is pending, fail closed.
            return True
        if requested:
            self.task.cancel_requested = True
        return bool(requested)

    async def _terminalize(self, interaction_id: str, state: str, error: str) -> None:
        await asyncio.to_thread(
            self.store.terminalize,
            owner_id=self.task.owner_id,
            interaction_id=interaction_id,
            state=state,
            error=error,
        )
        current = await asyncio.to_thread(self.store.get, self.task.owner_id, interaction_id)
        if current is not None:
            try:
                await publish_interaction_event(self.task.owner_id, self.task.id, current, state)
            except Exception:
                # The interaction row is authoritative. A cleanup notification must never undo
                # terminalization or strand encrypted answer material if the Item event bus fails.
                pass

    async def handle(self, request_id: int | str, method: str, params: dict[str, Any]) -> Any:
        if method not in _SUPPORTED:
            self.task.emit("codex.server_request_denied", f"Denied unsupported interactive request: {method}")
            raise CodexServerRequestDenied(f"FDEX policy denies interactive request {method}")

        row = await asyncio.to_thread(
            self.store.create,
            owner_id=self.task.owner_id,
            task_id=self.task.id,
            host_session_id=self.host_session_id,
            rpc_id=request_id,
            method=method,
            params=params,
        )
        interaction_id = str(row["id"])
        try:
            await publish_interaction_event(self.task.owner_id, self.task.id, row, "pending")
        except Exception as exc:
            await self._terminalize(
                interaction_id,
                "failed",
                f"FDEX could not publish the pending interaction: {exc}",
            )
            raise CodexServerRequestDenied("FDEX could not publish the Codex interaction") from exc
        self.task.emit(
            "codex.interaction_pending",
            f"Codex requires {interaction_kind(method)} approval/input ({interaction_id[:10]})",
        )

        # The current protocol explicitly says isBlocking is authoritative and autoResolutionMs
        # is deprecated. A non-blocking request must not stall the Codex turn waiting on a browser.
        if method == "item/tool/requestUserInput" and not bool(row.get("blocking")):
            answered = await asyncio.to_thread(
                self.store.submit_response,
                owner_id=self.task.owner_id,
                interaction_id=interaction_id,
                response={"answers": {}},
                summary={"resolution": "nonblocking-empty"},
            )
            await publish_interaction_event(self.task.owner_id, self.task.id, answered, "answered")

        deadline = asyncio.get_running_loop().time() + self.max_wait_seconds
        try:
            while asyncio.get_running_loop().time() < deadline:
                if await self._cancel_requested():
                    await self._terminalize(
                        interaction_id,
                        "cancelled",
                        "FDEX task cancellation requested",
                    )
                    raise CodexServerRequestDenied("FDEX task was cancelled while interaction was pending")
                if not self.transport_alive():
                    await self._terminalize(
                        interaction_id,
                        "interrupted",
                        "Codex app-server transport exited while interaction was pending",
                    )
                    raise CodexServerRequestDenied("Codex transport closed while interaction was pending")

                response = await asyncio.to_thread(
                    self.store.claim_response,
                    owner_id=self.task.owner_id,
                    interaction_id=interaction_id,
                    host_session_id=self.host_session_id,
                )
                if response is not None:
                    current = await asyncio.to_thread(self.store.get, self.task.owner_id, interaction_id)
                    if current is not None:
                        await publish_interaction_event(self.task.owner_id, self.task.id, current, "responded")
                    self.task.emit(
                        "codex.interaction_resolved",
                        f"Resolved Codex {interaction_kind(method)} ({interaction_id[:10]})",
                    )
                    return response
                current = await asyncio.to_thread(self.store.get, self.task.owner_id, interaction_id)
                if current is None:
                    raise CodexServerRequestDenied("Codex interaction disappeared")
                state = str(current.get("state") or "")
                if state not in {"pending", "answered"}:
                    raise CodexServerRequestDenied(
                        f"Codex interaction ended before a response was delivered: {state}"
                    )
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            await self._terminalize(
                interaction_id,
                "interrupted",
                "FDEX Host coroutine was cancelled",
            )
            raise

        await self._terminalize(
            interaction_id,
            "expired",
            "Codex interaction exceeded FDEX one-hour user-response window",
        )
        raise CodexServerRequestDenied("Codex interaction timed out waiting for user response")


def approval_response(method: str, decision: str) -> tuple[dict[str, Any], dict[str, Any]]:
    clean = (decision or "").strip()
    if method == "item/commandExecution/requestApproval":
        allowed = {"accept", "acceptForSession", "decline", "cancel"}
    elif method == "item/fileChange/requestApproval":
        allowed = {"accept", "acceptForSession", "decline", "cancel"}
    else:
        raise AgentRuntimeError("interaction is not a command/file approval")
    if clean not in allowed:
        raise AgentRuntimeError("invalid Codex approval decision")
    return {"decision": clean}, {"decision": clean}


def permissions_response(row: dict[str, Any], action: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(row.get("method") or "") != "item/permissions/requestApproval":
        raise AgentRuntimeError("interaction is not a permissions approval")
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    clean = (action or "").strip()
    if clean == "deny":
        return {"permissions": {}, "scope": "turn"}, {"decision": "deny", "scope": "turn"}
    if clean not in {"grant_turn", "grant_session"}:
        raise AgentRuntimeError("invalid Codex permission decision")
    requested = request.get("permissions") if isinstance(request.get("permissions"), dict) else {}
    granted = {key: value for key, value in requested.items() if value is not None}
    scope = "session" if clean == "grant_session" else "turn"
    return {"permissions": granted, "scope": scope}, {"decision": "grant", "scope": scope}


def user_input_response(row: dict[str, Any], values: dict[str, list[str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(row.get("method") or "") != "item/tool/requestUserInput":
        raise AgentRuntimeError("interaction is not requestUserInput")
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    questions = request.get("questions") if isinstance(request.get("questions"), list) else []
    answers: dict[str, dict[str, list[str]]] = {}
    summary: dict[str, Any] = {"answeredQuestionIds": [], "secretQuestionIds": []}
    known_ids: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("id") or "")
        if not question_id or question_id in known_ids:
            continue
        known_ids.add(question_id)
        submitted = [str(value)[:12000] for value in values.get(question_id, []) if str(value).strip()]
        if not submitted:
            # The official response map allows omitted question ids. Keep unanswered questions
            # absent rather than inventing an empty string that can carry different semantics.
            continue
        answers[question_id] = {"answers": submitted[:50]}
        summary["answeredQuestionIds"].append(question_id)
        if bool(question.get("isSecret")):
            summary["secretQuestionIds"].append(question_id)
    unknown = sorted(set(values) - known_ids)
    if unknown:
        raise AgentRuntimeError("requestUserInput contained answers for unknown question ids")
    return {"answers": answers}, summary
