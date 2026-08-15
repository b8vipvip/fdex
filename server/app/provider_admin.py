from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.config import SERVER_DIR, fresh_settings
from app.multimodal_service import probe_specialized_capabilities
from app.provider_manager import KEY_PATH, DB_PATH, probe_provider, provider_stats, provider_store
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf

router = APIRouter(prefix="/admin/providers", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _guard(request: Request) -> RedirectResponse | None:
    return None if is_admin(request) else RedirectResponse("/admin/login", status_code=303)


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    return {
        "request": request,
        "settings": fresh_settings(),
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        **extra,
    }


def _models(value: str) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in re.split(r"[,，;；\n]+", value or "") if x.strip()))


def _protocols(form: Any) -> list[str]:
    values = [str(x) for x in form.getlist("protocol_order")]
    allowed = {"chat", "responses", "legacy"}
    result = [x for x in values if x in allowed]
    return result or ["chat", "responses", "legacy"]


def _redirect(edit: int | None = None) -> RedirectResponse:
    return RedirectResponse(f"/admin/providers?edit={edit}" if edit else "/admin/providers", status_code=303)


@router.get("", response_class=HTMLResponse, response_model=None)
def providers_page(request: Request, edit: int | None = None) -> Response:
    if redirect := _guard(request):
        return redirect
    store = provider_store()
    providers = store.list()
    edit_provider: dict[str, Any] | None = None
    if edit:
        try:
            edit_provider = store.get(edit)
        except KeyError:
            set_flash(request, "要编辑的供应商不存在。", "error")
            return _redirect()
    return templates.TemplateResponse(
        "providers.html",
        _ctx(
            request,
            providers=providers,
            provider_stats=provider_stats(),
            edit_provider=edit_provider,
            provider_db_path=str(DB_PATH),
            provider_key_path=str(KEY_PATH),
        ),
    )


@router.post("/save", response_model=None)
async def save_provider(request: Request) -> Response:
    if redirect := _guard(request):
        return redirect
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token") or ""))
    provider_id = int(str(form.get("provider_id") or "0") or 0)
    values = {
        "name": str(form.get("name") or "").strip(),
        "base_url": str(form.get("base_url") or "").strip(),
        "api_key": str(form.get("api_key") or "").strip(),
        "enabled": form.get("enabled") is not None,
        "priority": int(str(form.get("priority") or "100")),
        "main_text_model": str(form.get("main_text_model") or "").strip(),
        "backup_text_models": _models(str(form.get("backup_text_models") or "")),
        "main_vision_model": str(form.get("main_vision_model") or "").strip(),
        "backup_vision_models": _models(str(form.get("backup_vision_models") or "")),
        "main_image_model": str(form.get("main_image_model") or "").strip(),
        "backup_image_models": _models(str(form.get("backup_image_models") or "")),
        "main_audio_model": str(form.get("main_audio_model") or "").strip(),
        "backup_audio_models": _models(str(form.get("backup_audio_models") or "")),
        "audio_protocol": str(form.get("audio_protocol") or "auto").strip().lower(),
        "audio_voice": str(form.get("audio_voice") or "alloy").strip(),
        "audio_format": str(form.get("audio_format") or "wav").strip().lower(),
        "protocol_order": _protocols(form),
        "timeout_seconds": int(str(form.get("timeout_seconds") or "60")),
        "auto_test_enabled": form.get("auto_test_enabled") is not None,
        "auto_test_interval_hours": int(str(form.get("auto_test_interval_hours") or "12")),
    }
    if not values["name"] or len(str(values["name"])) > 100:
        set_flash(request, "供应商名称不能为空且不能超过 100 个字符。", "error")
        return _redirect(provider_id or None)
    if not str(values["base_url"]).startswith(("http://", "https://")):
        set_flash(request, "BaseUrl 必须是 HTTP/HTTPS 地址。", "error")
        return _redirect(provider_id or None)
    if not 1 <= int(values["priority"]) <= 10000:
        set_flash(request, "优先级必须在 1 到 10000 之间。", "error")
        return _redirect(provider_id or None)
    if not 5 <= int(values["timeout_seconds"]) <= 600:
        set_flash(request, "请求超时必须在 5 到 600 秒之间。", "error")
        return _redirect(provider_id or None)
    if not 1 <= int(values["auto_test_interval_hours"]) <= 720:
        set_flash(request, "自动深测周期必须在 1 到 720 小时之间。", "error")
        return _redirect(provider_id or None)
    if values["audio_protocol"] not in {"auto", "chat_audio", "speech", "realtime"}:
        set_flash(request, "语音调用方式无效。", "error")
        return _redirect(provider_id or None)
    if values["audio_format"] not in {"mp3", "opus", "aac", "flac", "wav", "pcm"}:
        set_flash(request, "语音输出格式无效。", "error")
        return _redirect(provider_id or None)
    if not values["audio_voice"] or len(str(values["audio_voice"])) > 80:
        set_flash(request, "语音 Voice 不能为空且不能超过 80 个字符。", "error")
        return _redirect(provider_id or None)

    store = provider_store()
    try:
        if provider_id:
            item = store.update(provider_id, **values)
            if form.get("clear_api_key") is not None:
                store.clear_key(provider_id)
                item = store.get(provider_id)
            action = "update_ai_provider"
        else:
            item = store.create(**values)
            action = "create_ai_provider"
    except (KeyError, ValueError, RuntimeError) as exc:
        write_audit(request, "save_ai_provider", success=False, error=str(exc), provider_id=provider_id)
        set_flash(request, f"供应商保存失败：{exc}", "error")
        return _redirect(provider_id or None)

    write_audit(
        request,
        action,
        provider_id=item["id"],
        provider_name=item["name"],
        api_key_changed=bool(values["api_key"]) or form.get("clear_api_key") is not None,
        enabled=item["enabled"],
        priority=item["priority"],
        main_text_model=item["main_text_model"],
        main_image_model=item["main_image_model"],
        main_audio_model=item["main_audio_model"],
    )
    set_flash(request, f"供应商“{item['name']}”已保存。")
    return _redirect()


@router.post("/{provider_id}/delete", response_model=None)
def delete_provider(
    request: Request,
    provider_id: int,
    csrf_token: str = Form(...),
    confirm: str = Form(""),
) -> Response:
    if redirect := _guard(request):
        return redirect
    verify_csrf(request, csrf_token)
    if confirm != "delete":
        set_flash(request, "请确认后再删除供应商。", "error")
        return _redirect()
    store = provider_store()
    try:
        item = store.get(provider_id)
        store.delete(provider_id)
    except KeyError:
        set_flash(request, "供应商不存在或已被删除。", "error")
        return _redirect()
    write_audit(request, "delete_ai_provider", provider_id=provider_id, provider_name=item["name"])
    set_flash(request, f"供应商“{item['name']}”已删除。")
    return _redirect()


@router.post("/{provider_id}/test", response_model=None)
async def test_provider(
    request: Request,
    provider_id: int,
    csrf_token: str = Form(...),
    mode: str = Form("ordinary"),
) -> Response:
    if redirect := _guard(request):
        return redirect
    verify_csrf(request, csrf_token)
    store = provider_store()
    try:
        item = store.get(provider_id)
        if mode == "specialized":
            result = await probe_specialized_capabilities(provider_id)
            parts: list[str] = []
            for key, label in (("vision", "视觉"), ("image_generation", "图片"), ("audio", "语音")):
                value = result.get(key, {})
                if value.get("skipped"):
                    parts.append(f"{label}=未配置")
                elif value.get("ok"):
                    parts.append(f"{label}=可用({value.get('model') or '-'})")
                else:
                    parts.append(f"{label}=失败")
            ok = any(bool(result.get(key, {}).get("ok")) for key in ("vision", "image_generation", "audio"))
            write_audit(
                request,
                "test_ai_provider_specialized",
                success=ok,
                provider_id=provider_id,
                provider_name=item["name"],
                result=result,
            )
            set_flash(
                request,
                f"{item['name']} 专项能力测试：" + "，".join(parts),
                "success" if ok else "error",
            )
            return _redirect()

        normalized_mode = "deep" if mode == "deep" else "ordinary"
        result = await probe_provider(provider_id, mode=normalized_mode)
    except (KeyError, RuntimeError) as exc:
        write_audit(request, "test_ai_provider", success=False, provider_id=provider_id, mode=mode, error=str(exc))
        set_flash(request, f"测试失败：{exc}", "error")
        return _redirect()

    write_audit(
        request,
        "test_ai_provider",
        success=bool(result.get("ok")),
        provider_id=provider_id,
        provider_name=item["name"],
        mode=normalized_mode,
        latency_ms=result.get("latency_ms", 0),
        usable_models=len(result.get("models", [])),
    )
    label = "深度测试" if normalized_mode == "deep" else "普通测试"
    set_flash(
        request,
        f"{item['name']} {label}：{result.get('message', '完成')}（耗时 {result.get('latency_ms', 0)} ms）",
        "success" if result.get("ok") else "error",
    )
    return _redirect()
