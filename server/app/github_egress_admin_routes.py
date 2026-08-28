from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.config import SERVER_DIR, fresh_settings, get_settings
from app.env_manager import read_env, write_env
from app.github_app import GitHubAppClient, GitHubAppError
from app.github_egress import (
    GitHubEgressError,
    apply_managed_egress,
    egress_mode,
    make_managed_credentials,
    managed_egress_status,
    managed_proxy_url,
    parse_vless_uri,
    probe_managed_proxy_auth,
    resolve_xray_binary,
    stop_managed_egress,
)
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf

router = APIRouter(prefix="/github-egress", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))


def _guard() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def _ctx(request: Request, **extra: object) -> dict[str, object]:
    cfg = fresh_settings()
    return {
        "request": request,
        "settings": cfg,
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        **extra,
    }


def _float_setting(value: str, name: str, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} 必须在 {minimum:g}-{maximum:g} 秒之间")
    return parsed


def _int_setting(value: str, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} 必须在 {minimum}-{maximum} 之间")
    return parsed


def _validate_http_proxy(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        return ""
    if len(clean) > 1000:
        raise ValueError("HTTP(S) 代理地址过长")
    parsed = urlsplit(clean)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("自定义代理必须是完整的 http:// 或 https:// 地址")
    return clean


@router.get("", response_class=HTMLResponse, response_model=None)
def github_egress_page(request: Request) -> Response:
    if not is_admin(request):
        return _guard()
    values = read_env()
    status = managed_egress_status()
    return templates.TemplateResponse(
        "github_egress.html",
        _ctx(
            request,
            status=status,
            effective_mode=egress_mode(values, fresh_settings()),
            vless_configured=bool((values.get("FDEX_GITHUB_VLESS_URI") or "").strip()),
            custom_proxy_configured=bool(fresh_settings().fdex_github_http_proxy.strip())
            and status.get("mode") == "http_proxy",
        ),
    )


@router.post("/save", response_model=None)
def save_github_egress(
    request: Request,
    csrf_token: str = Form(...),
    mode: str = Form("direct"),
    vless_uri: str = Form(""),
    xray_binary: str = Form("xray"),
    xray_local_port: str = Form("18188"),
    http_proxy: str = Form(""),
    connect_timeout_seconds: str = Form("10"),
    read_timeout_seconds: str = Form("60"),
    retry_attempts: str = Form("3"),
    clear_saved_vless: str | None = Form(None),
) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)

    values = read_env()
    cfg = fresh_settings()
    clean_mode = (mode or "").strip().lower()
    if clean_mode not in {"direct", "managed_vless", "http_proxy"}:
        set_flash(request, "GitHub 出站模式无效", "error")
        return RedirectResponse("/admin/github-egress", status_code=303)

    try:
        connect_timeout = _float_setting(connect_timeout_seconds, "GitHub 连接超时", 2, 120)
        read_timeout = _float_setting(read_timeout_seconds, "GitHub 读取超时", 5, 300)
        retries = _int_setting(retry_attempts, "GitHub 重试次数", 1, 5)
        port = _int_setting(xray_local_port, "Xray 本地端口", 1024, 65535)
        if port == int(cfg.fdex_port):
            raise ValueError("Xray 本地端口不能与 FDEX 服务端口相同")
    except ValueError as exc:
        write_audit(request, "save_github_egress", success=False, error=str(exc))
        set_flash(request, str(exc), "error")
        return RedirectResponse("/admin/github-egress", status_code=303)

    updates: dict[str, str] = {
        "FDEX_GITHUB_EGRESS_MODE": clean_mode,
        "FDEX_GITHUB_CONNECT_TIMEOUT_SECONDS": f"{connect_timeout:g}",
        "FDEX_GITHUB_READ_TIMEOUT_SECONDS": f"{read_timeout:g}",
        "FDEX_GITHUB_RETRY_ATTEMPTS": str(retries),
        "FDEX_GITHUB_XRAY_LOCAL_PORT": str(port),
    }

    old_vless = (values.get("FDEX_GITHUB_VLESS_URI") or "").strip()
    old_proxy = cfg.fdex_github_http_proxy.strip()
    vless_changed = False
    proxy_changed = False

    try:
        if clear_saved_vless and clean_mode == "managed_vless":
            raise ValueError("托管 VLESS 模式不能同时清除 VLESS 配置")

        if clean_mode == "direct":
            updates["FDEX_GITHUB_HTTP_PROXY"] = ""
            if clear_saved_vless:
                updates.update(
                    {
                        "FDEX_GITHUB_VLESS_URI": "",
                        "FDEX_GITHUB_XRAY_PROXY_USER": "",
                        "FDEX_GITHUB_XRAY_PROXY_PASSWORD": "",
                    }
                )
                vless_changed = bool(old_vless)

        elif clean_mode == "http_proxy":
            proxy_input = _validate_http_proxy(http_proxy)
            current_mode = egress_mode(values, cfg)
            if proxy_input:
                selected_proxy = proxy_input
            elif current_mode == "http_proxy" and old_proxy:
                selected_proxy = old_proxy
            else:
                raise ValueError("切换到自定义 HTTP(S) 代理时必须填写代理地址")
            updates["FDEX_GITHUB_HTTP_PROXY"] = selected_proxy
            if clear_saved_vless:
                updates.update(
                    {
                        "FDEX_GITHUB_VLESS_URI": "",
                        "FDEX_GITHUB_XRAY_PROXY_USER": "",
                        "FDEX_GITHUB_XRAY_PROXY_PASSWORD": "",
                    }
                )
                vless_changed = bool(old_vless)

        else:
            uri = (vless_uri or "").strip() or old_vless
            if not uri:
                raise ValueError("托管 VLESS 模式需要填写 vless:// 分享链接")
            parse_vless_uri(uri)
            binary_setting = (xray_binary or values.get("FDEX_GITHUB_XRAY_BINARY") or "xray").strip()
            # Validate the executable before changing the live GitHub path.
            resolve_xray_binary(binary_setting)
            username, password = make_managed_credentials(values)
            selected_proxy = managed_proxy_url(port, username, password)
            updates.update(
                {
                    "FDEX_GITHUB_VLESS_URI": uri,
                    "FDEX_GITHUB_XRAY_BINARY": binary_setting,
                    "FDEX_GITHUB_XRAY_PROXY_USER": username,
                    "FDEX_GITHUB_XRAY_PROXY_PASSWORD": password,
                    "FDEX_GITHUB_HTTP_PROXY": selected_proxy,
                }
            )
            vless_changed = uri != old_vless

        proxy_changed = updates.get("FDEX_GITHUB_HTTP_PROXY", old_proxy) != old_proxy
    except ValueError as exc:
        write_audit(request, "save_github_egress", success=False, mode=clean_mode, error=str(exc))
        set_flash(request, str(exc), "error")
        return RedirectResponse("/admin/github-egress", status_code=303)

    backup = write_env(updates)
    get_settings.cache_clear()
    apply_error = ""
    try:
        if clean_mode == "managed_vless":
            apply_managed_egress(force_restart=True)
        else:
            stop_managed_egress()
    except GitHubEgressError as exc:
        apply_error = str(exc)

    after_values = read_env()
    write_audit(
        request,
        "save_github_egress",
        success=not bool(apply_error),
        mode=clean_mode,
        proxy_changed=proxy_changed,
        proxy_configured=bool(fresh_settings().fdex_github_http_proxy.strip()),
        vless_changed=vless_changed,
        vless_configured=bool((after_values.get("FDEX_GITHUB_VLESS_URI") or "").strip()),
        xray_local_port=port,
        connect_timeout_seconds=connect_timeout,
        read_timeout_seconds=read_timeout,
        retry_attempts=retries,
        backup=str(backup) if backup else "",
        apply_error=apply_error[:500],
    )
    if apply_error:
        set_flash(
            request,
            "配置已安全保存，但 FDEX 专用 Xray 启动失败。为避免静默绕过代理，GitHub 请求不会自动回退直连："
            + apply_error,
            "error",
        )
    else:
        message = {
            "direct": "GitHub 出站已恢复服务器直连；未修改系统代理，服务器其它服务网络不受影响。",
            "http_proxy": "GitHub 已切换到 FDEX 专用 HTTP(S) 代理；只影响 FDEX 的 GitHub 请求和 Git 子进程。",
            "managed_vless": "托管 VLESS 已保存并启动。Xray 只监听 127.0.0.1，启用随机认证并仅放行 GitHub 域名。",
        }[clean_mode]
        set_flash(request, message, "success")
    return RedirectResponse("/admin/github-egress", status_code=303)


@router.post("/test", response_model=None)
def test_github_egress(request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        network = GitHubAppClient().network_probe()
        targets = network.get("targets") if isinstance(network.get("targets"), list) else []
        parts: list[str] = []
        all_ok = bool(targets)
        audit_targets: list[dict[str, object]] = []
        for item in targets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "GitHub")
            ok = bool(item.get("ok"))
            all_ok = all_ok and ok
            status_code = int(item.get("status_code") or 0)
            elapsed_ms = int(item.get("elapsed_ms") or 0)
            error = str(item.get("error") or "")
            parts.append(
                f"{name}: HTTP {status_code} · {elapsed_ms} ms"
                if ok
                else f"{name}: {error or '连接失败'} · {elapsed_ms} ms"
            )
            audit_targets.append(
                {
                    "name": name,
                    "ok": ok,
                    "status_code": status_code,
                    "elapsed_ms": elapsed_ms,
                    "error": error[:80],
                }
            )
        isolation = probe_managed_proxy_auth()
        if isolation.get("applicable"):
            all_ok = all_ok and bool(isolation.get("ok"))
            parts.append("隔离验证: " + str(isolation.get("detail") or "未知"))
        write_audit(
            request,
            "test_github_egress",
            success=all_ok,
            mode=managed_egress_status().get("mode"),
            targets=audit_targets,
            isolation_ok=isolation.get("ok"),
        )
        set_flash(
            request,
            ("GitHub 专用出口测试通过：" if all_ok else "GitHub 专用出口测试异常：")
            + "；".join(parts),
            "success" if all_ok else "error",
        )
    except (GitHubAppError, GitHubEgressError, RuntimeError, ValueError) as exc:
        write_audit(request, "test_github_egress", success=False, error=str(exc)[:500])
        set_flash(request, f"GitHub 专用出口测试失败：{exc}", "error")
    return RedirectResponse("/admin/github-egress", status_code=303)


@router.post("/restart", response_model=None)
def restart_managed_vless(request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        status = apply_managed_egress(force_restart=True)
        write_audit(
            request,
            "restart_github_managed_vless",
            unit_state=status.get("unit_state"),
            listener_ready=status.get("listener_ready"),
        )
        set_flash(request, "FDEX 专用 Xray 已重新加载并启动。", "success")
    except GitHubEgressError as exc:
        write_audit(request, "restart_github_managed_vless", success=False, error=str(exc)[:500])
        set_flash(request, f"无法重启 FDEX 专用 Xray：{exc}", "error")
    return RedirectResponse("/admin/github-egress", status_code=303)
