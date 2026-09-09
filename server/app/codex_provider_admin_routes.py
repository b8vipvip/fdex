from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.codex_engine import resolve_codex_runtime, select_codex_provider_from
from app.codex_process_isolation import codex_process_isolation_status
from app.codex_provider_rollout import provider_rollout_rows
from app.codex_provider_smoke import run_codex_provider_smoke
from app.codex_provider_smoke_runs import codex_provider_smoke_run_store
from app.config import SERVER_DIR, fresh_settings
from app.provider_manager import provider_store
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf

router = APIRouter(prefix="/admin/agent/codex-providers", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))
logger = logging.getLogger("fdex.codex_provider_smoke")
_ADMIN_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _admin_time(value: object) -> str:
    """Render persisted UTC timestamps in the console's Beijing-time convention."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_ADMIN_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


templates.env.filters["admin_time"] = _admin_time


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


def _rollout_with_smoke_runs() -> dict[str, object]:
    rollout = provider_rollout_rows()
    runs = codex_provider_smoke_run_store()
    active = False
    for row in list(rollout.get("rows") or []):
        provider = row.get("provider") if isinstance(row, dict) else None
        provider_id = int(provider.get("id") or 0) if isinstance(provider, dict) else 0
        run = runs.get(provider_id) if provider_id > 0 else None
        row["smoke_run"] = run
        if run is not None and bool(run.get("active")):
            active = True
    rollout["smoke_active"] = active
    return rollout


async def _smoke_heartbeat(provider_id: int, run_id: str, stopped: asyncio.Event) -> None:
    runs = codex_provider_smoke_run_store()
    while not stopped.is_set():
        runs.update(
            int(provider_id),
            run_id,
            status="running",
            stage="full-smoke",
            detail="官方 Codex app-server 正在执行 wire → tools → MCP → subagent 真实验证",
        )
        try:
            await asyncio.wait_for(stopped.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            continue


async def _background_smoke(provider_id: int, run_id: str) -> None:
    runs = codex_provider_smoke_run_store()
    stopped = asyncio.Event()
    heartbeat = asyncio.create_task(
        _smoke_heartbeat(int(provider_id), run_id, stopped),
        name=f"fdex-codex-provider-smoke-heartbeat-{provider_id}",
    )
    try:
        result = await run_codex_provider_smoke(int(provider_id))
        ok = bool(result.get("ok"))
        level = str(result.get("level") or "none")
        error = str(result.get("error") or "")
        runs.finish(
            int(provider_id),
            run_id,
            status="passed" if ok else "failed",
            stage=level,
            detail=(
                "full smoke 已完成并写入新的兼容记录"
                if ok
                else f"full smoke 已结束，compatibility={level}"
            ),
            error=error,
        )
        logger.info(
            "Codex Provider smoke finished provider_id=%s run_id=%s ok=%s level=%s",
            provider_id,
            run_id,
            ok,
            level,
        )
    except Exception as exc:
        runs.finish(
            int(provider_id),
            run_id,
            status="failed",
            stage="crashed",
            detail="full smoke 在写入兼容结果前异常退出",
            error=str(exc),
        )
        logger.exception(
            "Codex Provider smoke crashed before a compatibility record was written provider_id=%s run_id=%s",
            provider_id,
            run_id,
        )
    finally:
        stopped.set()
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


@router.get("", response_class=HTMLResponse, response_model=None)
def codex_provider_rollout_page(request: Request) -> Response:
    if not is_admin(request):
        return _login_redirect()
    return templates.TemplateResponse(
        "codex_provider_rollout.html",
        _ctx(
            request,
            rollout=_rollout_with_smoke_runs(),
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
        if spec.model.strip().lower().startswith("gpt-5.6") and runtime.version.strip() == "0.147.0":
            raise ValueError(
                "当前 Codex Runtime=0.147.0；该版本存在 GPT-5.6 Code Mode/exec host 回归，"
                "会在 chat2api 已返回 custom_tool_call 后阻断本地工具执行和 continuation。"
                "请先在 Codex Runtime 管理页升级到当前官方稳定版，再执行 full smoke。"
            )
        run = codex_provider_smoke_run_store().begin(
            int(provider_id),
            runtime_version=runtime.version,
            runtime_source=runtime.source,
            model=spec.model,
        )
        run_id = str(run.get("run_id") or "")
        if not run_id:
            raise RuntimeError("无法创建 Codex Provider smoke 运行记录")
        background_tasks.add_task(_background_smoke, int(provider_id), run_id)
        write_audit(
            request,
            "codex_provider_smoke_started",
            provider_id=int(provider_id),
            provider_name=spec.name,
            model=spec.model,
            runtime_version=runtime.version,
            runtime_source=runtime.source,
            smoke_run_id=run_id,
        )
        set_flash(
            request,
            f"已启动 {spec.name} / {spec.model} 的真实 Codex full smoke（Runtime {runtime.version} / {runtime.source}）。"
            "页面会自动刷新并显示本次开始时间、已用时和最终结果；旧兼容记录在新测试完成前仅作为“上次完成”保留。",
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
