from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.responses import Response

from app.audit import write_audit
from app.codex_subagent_governance import (
    CodexSubAgentSettings,
    clear_subagent_settings_cache,
    codex_subagent_policy,
    fresh_subagent_settings,
)
from app.config import SERVER_DIR, fresh_settings
from app.env_manager import write_env
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf
from app.system_info import schedule_service_restart

router = APIRouter(prefix="/subagents", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "settings": fresh_settings(),
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        **extra,
    }


@router.get("", response_class=HTMLResponse, response_model=None)
def subagent_settings_page(request: Request) -> Response:
    if not is_admin(request):
        return _login_redirect()
    subagent = fresh_subagent_settings()
    return templates.TemplateResponse(
        "agent_subagent_settings.html",
        _ctx(
            request,
            subagent=subagent,
            policy=codex_subagent_policy(subagent),
        ),
    )


@router.post("", response_model=None)
def save_subagent_settings(
    request: Request,
    csrf_token: str = Form(...),
    fdex_agent_subagents_enabled: str | None = Form(None),
    max_concurrent: int = Form(...),
    rollout_budget_tokens: int = Form(...),
    wait_min_ms: int = Form(...),
    wait_default_ms: int = Form(...),
    wait_max_ms: int = Form(...),
    sampling_token_weight: float = Form(...),
    prefill_token_weight: float = Form(...),
) -> Response:
    if not is_admin(request):
        return _login_redirect()
    verify_csrf(request, csrf_token)
    before = fresh_subagent_settings()
    try:
        requested = CodexSubAgentSettings(
            fdex_agent_subagents_enabled=fdex_agent_subagents_enabled == "true",
            fdex_agent_subagent_max_concurrent=max_concurrent,
            fdex_agent_subagent_rollout_budget_tokens=rollout_budget_tokens,
            fdex_agent_subagent_wait_min_ms=wait_min_ms,
            fdex_agent_subagent_wait_default_ms=wait_default_ms,
            fdex_agent_subagent_wait_max_ms=wait_max_ms,
            fdex_agent_subagent_sampling_token_weight=sampling_token_weight,
            fdex_agent_subagent_prefill_token_weight=prefill_token_weight,
        )
    except ValidationError as exc:
        set_flash(request, f"Sub-Agent 治理设置无效：{exc.errors()[0].get('msg', str(exc))}", "error")
        return RedirectResponse("/admin/agent/subagents", status_code=303)

    write_env(
        {
            "FDEX_AGENT_SUBAGENTS_ENABLED": "true" if requested.fdex_agent_subagents_enabled else "false",
            "FDEX_AGENT_SUBAGENT_MAX_CONCURRENT": str(requested.fdex_agent_subagent_max_concurrent),
            "FDEX_AGENT_SUBAGENT_ROLLOUT_BUDGET_TOKENS": str(
                requested.fdex_agent_subagent_rollout_budget_tokens
            ),
            "FDEX_AGENT_SUBAGENT_WAIT_MIN_MS": str(requested.fdex_agent_subagent_wait_min_ms),
            "FDEX_AGENT_SUBAGENT_WAIT_DEFAULT_MS": str(requested.fdex_agent_subagent_wait_default_ms),
            "FDEX_AGENT_SUBAGENT_WAIT_MAX_MS": str(requested.fdex_agent_subagent_wait_max_ms),
            "FDEX_AGENT_SUBAGENT_SAMPLING_TOKEN_WEIGHT": f"{requested.fdex_agent_subagent_sampling_token_weight:g}",
            "FDEX_AGENT_SUBAGENT_PREFILL_TOKEN_WEIGHT": f"{requested.fdex_agent_subagent_prefill_token_weight:g}",
        }
    )
    clear_subagent_settings_cache()
    write_audit(
        request,
        "save_codex_subagent_governance",
        enabled=requested.fdex_agent_subagents_enabled,
        previous_enabled=before.fdex_agent_subagents_enabled,
        max_concurrent=requested.fdex_agent_subagent_max_concurrent,
        previous_max_concurrent=before.fdex_agent_subagent_max_concurrent,
        rollout_budget_tokens=requested.fdex_agent_subagent_rollout_budget_tokens,
        previous_rollout_budget_tokens=before.fdex_agent_subagent_rollout_budget_tokens,
    )
    try:
        task = schedule_service_restart(fresh_settings())
        write_audit(request, "restart_after_codex_subagent_governance", task=task)
        set_flash(
            request,
            "Codex Sub-Agent 治理已保存；服务将在约 2 秒后重启，新的官方 Thread 将使用新上限。",
        )
    except (ValueError, RuntimeError) as exc:
        write_audit(
            request,
            "restart_after_codex_subagent_governance",
            success=False,
            error=str(exc),
        )
        set_flash(
            request,
            f"Sub-Agent 设置已保存，但自动重启失败：{exc}。请在版本与维护页面手动重启服务。",
            "error",
        )
    return RedirectResponse("/admin/agent/subagents", status_code=303)
