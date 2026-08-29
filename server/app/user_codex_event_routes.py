from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.responses import Response

from app.agent_tasks import agent_task_store
from app.codex_item_store import codex_item_store
from app.user_portal_routes import _current_user

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


@router.get("/{task_id}/codex/snapshot", response_model=None)
async def codex_item_snapshot(task_id: str, request: Request) -> Response:
    owner_id, _task, error = _scope(request, task_id)
    if error is not None:
        return error
    store = codex_item_store()
    items, latest_seq = await asyncio.gather(
        asyncio.to_thread(store.list_items, owner_id, task_id, limit=300),
        asyncio.to_thread(store.latest_seq, owner_id, task_id),
    )
    return JSONResponse(
        {
            "task_id": task_id,
            "latest_seq": latest_seq,
            "items": items,
        },
        headers={"Cache-Control": "no-store"},
    )


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
