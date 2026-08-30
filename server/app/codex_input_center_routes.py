from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.agent_tasks import agent_task_store
from app.codex_capability_routes import router as codex_capability_router
from app.codex_task_inputs import codex_task_input_store
from app.config import SERVER_DIR
from app.user_portal_routes import _ctx, _current_user, _login_redirect

router = APIRouter(prefix="/account/agent", include_in_schema=False)
router.include_router(codex_capability_router)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


@router.get("/inputs", response_class=HTMLResponse, response_model=None)
def codex_input_center(request: Request) -> Response:
    user = _current_user(request)
    if user is None:
        return _login_redirect(request)
    owner_id = str(user["id"])
    tasks = agent_task_store().list(owner_id, status="queued", limit=100)
    store = codex_task_input_store()
    rows = [
        {
            **task,
            "input_count": len(store.list(owner_id, str(task["id"]))),
        }
        for task in tasks
    ]
    return templates.TemplateResponse(
        "user_agent_input_center.html",
        _ctx(request, user, tasks=rows),
    )
