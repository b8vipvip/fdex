from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.admin_routes import _ctx, templates
from app.client_runtime_logs import client_runtime_log_store
from app.config import fresh_settings
from app.security import is_admin
from app.system_info import service_logs

router = APIRouter(prefix="/admin", include_in_schema=False)


def _guard(request: Request) -> RedirectResponse | None:
    return None if is_admin(request) else RedirectResponse("/admin/login", status_code=303)


def _filters(owner: str, platform: str, level: str, component: str, q: str, limit: int) -> dict[str, object]:
    return {
        "owner_id": (owner or "").strip()[:100],
        "platform": (platform or "").strip().lower()[:40],
        "level": (level or "").strip().lower()[:20],
        "component": (component or "").strip()[:120],
        "query": (q or "").strip()[:120],
        "limit": max(1, min(int(limit), 5000)),
    }


@router.get("/client-logs", response_class=HTMLResponse, response_model=None)
def client_logs_page(
    request: Request,
    owner: str = "",
    platform: str = "",
    level: str = "",
    component: str = "",
    q: str = "",
    limit: int = 300,
) -> Response:
    if redirect := _guard(request):
        return redirect
    filters = _filters(owner, platform, level, component, q, limit)
    store = client_runtime_log_store()
    logs = store.list(**filters)
    return templates.TemplateResponse(
        "client_logs.html",
        _ctx(
            request,
            logs=logs,
            owners=store.owners(),
            platforms=store.platforms(),
            components=store.components(),
            filters=filters,
        ),
    )


def _text_export(rows: list[dict[str, object]]) -> str:
    lines = [
        "FDEX Client Runtime Logs",
        f"Exported-At: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"Entries: {len(rows)}",
        "",
    ]
    for item in rows:
        details = json.dumps(item.get("details") or {}, ensure_ascii=False, separators=(",", ":"))
        lines.append(
            f"{item.get('received_at') or ''} [{item.get('level') or ''}] "
            f"owner={item.get('owner_id') or ''} platform={item.get('platform') or ''} "
            f"device={item.get('device_name') or ''} app={item.get('app_version') or ''} "
            f"component={item.get('component') or ''} event={item.get('event') or ''} "
            f"client_time={item.get('client_time') or ''} message={item.get('message') or ''} details={details}"
        )
    return "\n".join(lines) + "\n"


@router.get("/client-logs/export", response_model=None)
def export_client_logs(
    request: Request,
    format: str = "txt",
    owner: str = "",
    platform: str = "",
    level: str = "",
    component: str = "",
    q: str = "",
    limit: int = 5000,
) -> Response:
    if redirect := _guard(request):
        return redirect
    filters = _filters(owner, platform, level, component, q, limit)
    rows = client_runtime_log_store().list(**filters)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if format.strip().lower() == "json":
        payload = json.dumps(
            {
                "format": "fdex-client-runtime-logs-v1",
                "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "filters": filters,
                "entries": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        return Response(
            payload,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="fdex-client-logs-{stamp}.json"'},
        )
    return Response(
        _text_export(rows),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="fdex-client-logs-{stamp}.log"'},
    )


@router.get("/logs/export", response_model=None)
def export_service_runtime_logs(request: Request, lines: int = 1000) -> Response:
    if redirect := _guard(request):
        return redirect
    requested_lines = max(1, min(int(lines), 5000))
    settings = fresh_settings()
    payload = service_logs(settings, requested_lines)
    if payload and not payload.endswith("\n"):
        payload += "\n"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Response(
        payload,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="fdex-server-logs-{stamp}.log"'},
    )
