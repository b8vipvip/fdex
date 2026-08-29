from __future__ import annotations

import asyncio
import uuid
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
        await publish_interaction_event(self.task.owner_id, self.task.id, row, "pending")
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
                    await asyncio.to_thread(
                        self.store.terminalize,
                        owner_id=self.task.owner_id,
                        interaction_id=interaction_id,
                        state="cancelled",
                        error="FDEX task cancellation requested",
                    )
                    raise CodexServerRequestDenied("FDEX task was cancelled while interaction was pending")
                if not self.transport_alive():
                    await asyncio.to_thread(
                        self.store.terminalize,
                        owner_id=self.task.owner_id,
                        interaction_id=interaction_id,
                        state="interrupted",
                        error="Codex app-server transport exited while interaction was pending",
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
            await asyncio.to_thread(
                self.store.terminalize,
                owner_id=self.task.owner_id,
                interaction_id=interaction_id,
                state="interrupted",
                error="FDEX Host coroutine was cancelled",
            )
            raise

        await asyncio.to_thread(
            self.store.terminalize,
            owner_id=self.task.owner_id,
            interaction_id=interaction_id,
            state="expired",
            error="Codex interaction exceeded FDEX one-hour user-response window",
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
