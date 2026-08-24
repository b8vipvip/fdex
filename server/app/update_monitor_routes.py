from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import fresh_settings
from app.security import is_admin
from app.update_monitor import update_task_status

router = APIRouter(prefix="/admin", include_in_schema=False)


@router.get("/update/status", response_model=None)
def update_status(request: Request) -> JSONResponse:
    if not is_admin(request):
        return JSONResponse({"detail": "未登录"}, status_code=401)
    return JSONResponse(update_task_status(fresh_settings()))
