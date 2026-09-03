from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.audit import write_audit
from app.codex_agent_health import codex_agent_health_snapshot, run_codex_agent_health_check
from app.security import is_admin, verify_csrf

router = APIRouter(prefix="/admin/agent/health", include_in_schema=False)


def _unauthorized() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "admin authentication required"}, status_code=401)


@router.get(".json", response_model=None)
def agent_health_json(request: Request) -> Response:
    if not is_admin(request):
        return _unauthorized()
    response = JSONResponse({"ok": True, "health": codex_agent_health_snapshot()})
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@router.post("/check", response_model=None)
async def agent_health_check(
    request: Request,
    csrf_token: str = Form(...),
) -> Response:
    if not is_admin(request):
        return _unauthorized()
    verify_csrf(request, csrf_token)
    try:
        health = await run_codex_agent_health_check(force_host=True)
        write_audit(
            request,
            "codex_agent_health_manual_check",
            state=str(health.get("state") or ""),
            code=str(health.get("code") or ""),
            duration_ms=int(health.get("duration_ms") or 0),
        )
        response = JSONResponse({"ok": True, "health": health})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response
    except Exception as exc:
        write_audit(
            request,
            "codex_agent_health_manual_check",
            success=False,
            error=str(exc)[:700],
        )
        response = JSONResponse(
            {"ok": False, "error": str(exc)[:700], "health": codex_agent_health_snapshot()},
            status_code=503,
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response
