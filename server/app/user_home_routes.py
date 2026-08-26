from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.user_portal_routes import _current_user, _login_redirect
from app.web_workspace import web_workspace_store

router = APIRouter(prefix="/account", include_in_schema=False)


@router.get("", response_model=None)
def account_home(request: Request) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    try:
        preferred = str(web_workspace_store().preferences(owner_id).get("default_home") or "messages")
    except (ValueError, OSError):
        preferred = "messages"
    if preferred not in {"messages", "knowledge", "discover", "me"}:
        preferred = "messages"
    return RedirectResponse(f"/account/{preferred}", status_code=303)
