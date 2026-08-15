from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.responses import Response

from app.audit import write_audit
from app.realtime_diagnostics import DIAGNOSTIC_FILE
from app.security import is_admin, set_flash

router = APIRouter(prefix="/admin/diagnostics", include_in_schema=False)


@router.get("/realtime-voice/download", response_model=None)
def download_realtime_voice_diagnostics(request: Request) -> Response:
    if not is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)

    if not DIAGNOSTIC_FILE.exists() or not DIAGNOSTIC_FILE.is_file():
        write_audit(request, "download_realtime_voice_diagnostics", success=False, reason="log_not_created")
        set_flash(request, "实时语音诊断日志尚未生成。请先用最新版 App 完成一次实时语音通话，再回来下载。", "error")
        return RedirectResponse("/admin/maintenance#realtime-diagnostics", status_code=303)

    size = DIAGNOSTIC_FILE.stat().st_size
    write_audit(request, "download_realtime_voice_diagnostics", size=size)
    return FileResponse(
        path=str(DIAGNOSTIC_FILE),
        filename="fdex-realtime-voice.log",
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store, private"},
    )
