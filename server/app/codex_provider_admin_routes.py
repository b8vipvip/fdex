from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.codex_engine import resolve_codex_runtime, select_codex_provider_from
from app.codex_process_isolation import codex_process_isolation_status
from app.codex_provider_rollout import provider_rollout_rows
from app.codex_provider_smoke import run_codex_provider_smoke
from app.config import SERVER_DIR
from app.provider_manager import provider_store
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf

router = APIRouter(prefix="/admin/agent/codex-providers", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        **extra,
    }


async def _background_smoke(provider_id: int) -> None:
    # The result is durably written to codex-provider-compatibility.db by the runner. Exceptions
    # before a record can be created are intentionally swallowed here because the admin POST has
    # already preflighted Provider/Runtime/cgroup requirements; a later refresh still shows no
    # fresh compatibility proof rather than falsely unlocking rollout.
    try:
        await run_codex_provider_smoke(int(provider_id))
    except Exception:
        return


@router.get("", response_class=HTMLResponse, response_model=None)
def codex_provider_rollout_page(request: Request) -> Response:
    if not is_admin(request):
        return _login_redirect()
    return templates.TemplateResponse(
        "codex_provider_rollout.html",
        _ctx(
            request,
            rollout=provider_rollout_rows(),
            isolation=codex_process_isolation_status(),
        ),
    )


@router.post("/{provider_id}/smoke", response_model=None)
def start_codex_provider_smoke(
    request: Request,
    background_tasks: BackgroundTasks,
    provider_id: int,
    csrf_token: str = Form(...),
) -> Response:
    if not is_admin(request):
        return _login_redirect()
    verify_csrf(request, csrf_token)
    try:
        provider = provider_store().get(int(provider_id), include_secret=True)
        runtime = resolve_codex_runtime()
        isolation = codex_process_isolation_status()
        if not bool(isolation.get("enforced")):
            raise ValueError(
                "Phase 7.32 production process-tree isolation 未生效，拒绝把当前机器上的 smoke 作为 rollout 证据："
                + str(isolation.get("reason") or "unknown reason")
            )
        spec = select_codex_provider_from([provider])
        if spec is None:
            raise ValueError("该供应商未完整配置 Responses 协议、API Key、Base URL 或文本模型")
        background_tasks.add_task(_background_smoke, int(provider_id))
        write_audit(
            request,
            "codex_provider_smoke_started",
            provider_id=int(provider_id),
            provider_name=spec.name,
            model=spec.model,
            runtime_version=runtime.version,
            runtime_source=runtime.source,
        )
        set_flash(
            request,
            f"已启动 {spec.name} / {spec.model} 的真实 Codex full smoke。测试在隔离 scratch workspace 中执行，刷新本页查看最终兼容等级。",
            "success",
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        write_audit(
            request,
            "codex_provider_smoke_started",
            success=False,
            provider_id=int(provider_id),
            error=str(exc),
        )
        set_flash(request, f"无法启动 Codex Provider smoke：{exc}", "error")
    return RedirectResponse("/admin/agent/codex-providers", status_code=303)
