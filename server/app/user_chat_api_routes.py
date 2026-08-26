from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.user_app_routes import _ask_employee, _capture_knowledge, _now, _owner, _store_for
from app.user_portal_routes import _current_user, _verify_csrf

router = APIRouter(prefix="/account", include_in_schema=False)


def _json_error(message: str, *, status_code: int = 502) -> JSONResponse:
    clean = (message or "AI 员工暂时无法回复").strip()[:1200]
    return JSONResponse({"ok": False, "error": clean}, status_code=status_code)


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
            answer = await _ask_employee(request, owner_id, employee, message, history, attachment)
        except HTTPException as exc:
            # The user's message is intentionally durable even when the upstream AI fails. The
            # browser can show the exact server-side routing failure immediately instead of making
            # the user refresh and wonder whether the message was submitted.
            return JSONResponse(
                {
                    "ok": False,
                    "error": str(exc.detail)[:1200],
                    "user_message": user_message,
                },
                status_code=exc.status_code if 400 <= exc.status_code < 600 else 502,
            )
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)[:1200], "user_message": user_message},
                status_code=400,
            )

        assistant_message = store.create(
            owner_id,
            "message",
            {
                "employee_id": employee_id,
                "role": "assistant",
                "content": answer,
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
            }
        )
    except HTTPException as exc:
        return _json_error(str(exc.detail), status_code=exc.status_code)
    except KeyError:
        return _json_error("AI 员工不存在或已被删除", status_code=404)
    except ValueError as exc:
        return _json_error(str(exc), status_code=400)
