from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.employee_agent_tools import collect_employee_tool_context
from app.user_app_routes import _ask_employee, _capture_knowledge, _now, _owner, _store_for
from app.user_portal_routes import _current_user, _verify_csrf

router = APIRouter(prefix="/account", include_in_schema=False)

_GITHUB_FACT_HINTS = (
    "github",
    "git hub",
    "仓库",
    "repository",
    "repo",
    "代码库",
    "项目库",
)
_GITHUB_INVENTORY_HINTS = (
    "几个",
    "哪些",
    "列表",
    "列出",
    "查看",
    "状态",
    "情况",
    "有什么",
    "有哪些",
    "多少",
    "inventory",
    "list",
    "status",
)
_GITHUB_EXECUTION_HINTS = (
    "修改",
    "改代码",
    "写代码",
    "修复",
    "删除",
    "创建",
    "新增",
    "提交",
    "commit",
    "push",
    "pull request",
    " pr ",
    "合并",
    "merge",
    "测试",
    "运行",
    "执行",
    "构建",
    "build",
    "checkout",
    "clone",
    "分支",
    "branch",
    "读取文件",
    "查看文件",
    "代码内容",
    "源码",
)


def _json_error(message: str, *, status_code: int = 502) -> JSONResponse:
    clean = (message or "智体暂时无法回复").strip()[:1200]
    return JSONResponse({"ok": False, "error": clean}, status_code=status_code)


def _tool_events(request: Request) -> list[dict[str, object]]:
    raw = request.scope.get("fdex_employee_tool_events")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)][:20]


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = f" {text.casefold()} "
    return any(item.casefold() in lowered for item in needles)


def _direct_github_inventory_answer(
    request: Request,
    owner_id: str,
    employee: dict[str, object],
    message: str,
    attachment: UploadFile | None,
) -> str:
    """Answer deterministic GitHub metadata questions before entering Codex.

    Repository inventory/permission metadata is a FDEX GitHub App host fact, not a model decision and
    not a repository worktree operation. It must therefore remain available even when the configured
    Codex model provider cannot pass the production full smoke. Any request that also asks to inspect
    files, run commands, edit code, test, commit, push or create a PR still fails closed into the real
    Coding Agent path; this helper never becomes a legacy/general-AI fallback.
    """
    if not bool(employee.get("coding_agent")):
        return ""
    if attachment is not None and attachment.filename:
        return ""
    clean = (message or "").strip()
    if not clean:
        return ""
    if not (_contains_any(clean, _GITHUB_FACT_HINTS) and _contains_any(clean, _GITHUB_INVENTORY_HINTS)):
        return ""
    if _contains_any(clean, _GITHUB_EXECUTION_HINTS):
        return ""

    context = collect_employee_tool_context(owner_id, employee, clean)
    if not context.answer_prefix.strip():
        return ""
    request.scope["fdex_employee_tool_events"] = [dict(item) for item in context.events[:20]]
    return context.answer_prefix.strip()


@router.post("/chat/employee/{employee_id}/send-json", response_model=None)
async def employee_chat_send_json(
    employee_id: int,
    request: Request,
    csrf_token: str = Form(...),
    message: str = Form(""),
    attachment: UploadFile | None = File(default=None),
) -> JSONResponse:
    user = _current_user(request)
    if user is None:
        return _json_error("登录状态已失效，请重新登录", status_code=401)

    owner_id = _owner(user)
    store = _store_for(user)
    try:
        _verify_csrf(request, csrf_token)
        employee = store.get(owner_id, "employee", employee_id)
        if not message.strip() and (attachment is None or not attachment.filename):
            return _json_error("请输入消息或选择附件", status_code=400)

        history = store.list(owner_id, "message", parent_id=employee_id, limit=500)
        display = message.strip()
        if attachment is not None and attachment.filename:
            display = (display + f"\n[附件：{Path(attachment.filename).name[:200]}]").strip()

        user_message = store.create(
            owner_id,
            "message",
            {
                "employee_id": employee_id,
                "role": "user",
                "content": display,
                "created_at": _now(),
            },
            parent_id=employee_id,
            sort_key=_now(),
        )

        try:
            answer = _direct_github_inventory_answer(request, owner_id, employee, message, attachment)
            if not answer:
                answer = await _ask_employee(request, owner_id, employee, message, history, attachment)
        except HTTPException as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "error": str(exc.detail)[:1200],
                    "user_message": user_message,
                    "tool_events": _tool_events(request),
                },
                status_code=exc.status_code if 400 <= exc.status_code < 600 else 502,
            )
        except ValueError as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "error": str(exc)[:1200],
                    "user_message": user_message,
                    "tool_events": _tool_events(request),
                },
                status_code=400,
            )

        tool_events = _tool_events(request)
        assistant_message = store.create(
            owner_id,
            "message",
            {
                "employee_id": employee_id,
                "role": "assistant",
                "content": answer,
                "tool_events": tool_events,
                "created_at": _now(),
            },
            parent_id=employee_id,
            sort_key=_now(),
        )
        _capture_knowledge(owner_id, employee, display, answer)
        return JSONResponse(
            {
                "ok": True,
                "user_message": user_message,
                "assistant_message": assistant_message,
                "tool_events": tool_events,
            }
        )
    except HTTPException as exc:
        return _json_error(str(exc.detail), status_code=exc.status_code)
    except KeyError:
        return _json_error("智体不存在或已被删除", status_code=404)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)
