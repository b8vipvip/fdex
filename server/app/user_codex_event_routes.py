from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import AsyncIterator, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.responses import Response

from app.agent_runtime import AgentRuntimeError
from app.agent_tasks import agent_task_store
from app.codex_interaction_store import codex_interaction_store
from app.codex_interactions import (
    approval_response,
    permissions_response,
    publish_interaction_event,
    user_input_response,
)
from app.codex_item_store import codex_item_store
from app.user_portal_routes import _current_user, _flash, _verify_csrf

router = APIRouter(prefix="/account/agent/tasks", include_in_schema=False)


def _scope(request: Request, task_id: str) -> tuple[str, dict[str, object] | None, Response | None]:
    user = _current_user(request)
    if user is None:
        return "", None, PlainTextResponse("unauthorized", status_code=401)
    owner_id = str(user["id"])
    try:
        task = agent_task_store().get(owner_id, task_id)
    except ValueError:
        task = None
    if task is None:
        return owner_id, None, PlainTextResponse("not found", status_code=404)
    return owner_id, task, None


def _interaction_for_task(owner_id: str, task_id: str, interaction_id: str) -> dict[str, Any]:
    row = codex_interaction_store().get(owner_id, interaction_id)
    if row is None or str(row.get("task_id") or "") != task_id:
        raise KeyError("Codex interaction not found")
    return row


def _wants_json(request: Request) -> bool:
    return request.query_params.get("format") == "json" or "application/json" in request.headers.get("accept", "")


@router.get("/{task_id}/codex/snapshot", response_model=None)
async def codex_item_snapshot(task_id: str, request: Request) -> Response:
    owner_id, _task, error = _scope(request, task_id)
    if error is not None:
        return error
    item_store = codex_item_store()
    interaction_store = codex_interaction_store()
    # A previous worker may have died after persisting a pending request. Once Phase 7.21 has
    # reconciled its Thread away from running/compacting, surface that request as interrupted
    # instead of presenting a button that can no longer reach any stdio Host.
    await asyncio.to_thread(interaction_store.interrupt_orphans, owner_id)
    items, latest_seq, interactions = await asyncio.gather(
        asyncio.to_thread(item_store.list_items, owner_id, task_id, limit=300),
        asyncio.to_thread(item_store.latest_seq, owner_id, task_id),
        asyncio.to_thread(interaction_store.list_for_task, owner_id, task_id, limit=100),
    )
    return JSONResponse(
        {
            "task_id": task_id,
            "latest_seq": latest_seq,
            "items": items,
            "interactions": interactions,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{task_id}/codex/interactions", response_model=None)
async def codex_interactions_snapshot(task_id: str, request: Request) -> Response:
    owner_id, _task, error = _scope(request, task_id)
    if error is not None:
        return error
    store = codex_interaction_store()
    await asyncio.to_thread(store.interrupt_orphans, owner_id)
    rows = await asyncio.to_thread(store.list_for_task, owner_id, task_id, limit=100)
    return JSONResponse({"task_id": task_id, "interactions": rows}, headers={"Cache-Control": "no-store"})


@router.post("/{task_id}/codex/interactions/{interaction_id}/respond", response_model=None)
async def codex_interaction_respond(task_id: str, interaction_id: str, request: Request) -> Response:
    owner_id, _task, error = _scope(request, task_id)
    if error is not None:
        return error
    try:
        form = await request.form()
        _verify_csrf(request, str(form.get("csrf_token") or ""))
        row = await asyncio.to_thread(_interaction_for_task, owner_id, task_id, interaction_id)
        if str(row.get("state") or "") != "pending":
            raise AgentRuntimeError("Codex interaction is no longer pending")
        method = str(row.get("method") or "")
        action = str(form.get("action") or "").strip()
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            response, summary = approval_response(method, action)
        elif method == "item/permissions/requestApproval":
            response, summary = permissions_response(row, action)
        elif method == "item/tool/requestUserInput":
            values: dict[str, list[str]] = {}
            for key, value in form.multi_items():
                key_text = str(key)
                if not key_text.startswith("q:"):
                    continue
                question_id = key_text[2:]
                if not question_id:
                    continue
                values.setdefault(question_id, []).append(str(value))
            response, summary = user_input_response(row, values)
        else:
            raise AgentRuntimeError("unsupported Codex interaction method")
        updated = await asyncio.to_thread(
            codex_interaction_store().submit_response,
            owner_id=owner_id,
            interaction_id=interaction_id,
            response=response,
            summary=summary,
        )
        await publish_interaction_event(owner_id, task_id, updated, "answered")
        if _wants_json(request):
            return JSONResponse(
                {"ok": True, "interaction": updated},
                headers={"Cache-Control": "no-store"},
            )
        _flash(request, "已提交 Codex 审批/回答，正在交给持有该 Host 的 worker", "success")
        return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)
    except (KeyError, ValueError, AgentRuntimeError) as exc:
        if _wants_json(request):
            return JSONResponse(
                {"ok": False, "error": str(exc)},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        _flash(request, str(exc), "error")
        return RedirectResponse(f"/account/agent/tasks/{task_id}", status_code=303)


@router.get("/{task_id}/codex/events", response_model=None)
async def codex_item_events(task_id: str, request: Request, after: int = 0) -> Response:
    owner_id, _task, error = _scope(request, task_id)
    if error is not None:
        return error
    try:
        header_seq = int(request.headers.get("last-event-id", "0") or "0")
    except ValueError:
        header_seq = 0
    cursor = max(0, int(after), header_seq)

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        store = codex_item_store()
        started = monotonic()
        last_heartbeat = started
        while monotonic() - started < 600:
            if await request.is_disconnected():
                return
            events = await asyncio.to_thread(store.list_events, owner_id, task_id, after_seq=cursor, limit=200)
            if events:
                for event in events:
                    cursor = int(event["seq"])
                    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: codex\ndata: {payload}\n\n"
                last_heartbeat = monotonic()
                continue
            if monotonic() - last_heartbeat >= 15:
                yield ": fdex-codex-heartbeat\n\n"
                last_heartbeat = monotonic()
            await asyncio.sleep(0.5)
        yield "event: reconnect\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
