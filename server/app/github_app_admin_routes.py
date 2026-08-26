from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.config import SERVER_DIR, fresh_settings, get_settings
from app.env_manager import write_env
from app.github_app import GitHubAppClient, GitHubAppError
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf

router = APIRouter(prefix="/admin/github-app", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))

_MANIFEST_STATE = "fdex_github_app_manifest_state"
_MANIFEST_ENDPOINT = "https://github.com/settings/apps/new"
_GITHUB_API = "https://api.github.com"


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
        "callback_url": cfg.public_base_url.rstrip("/") + "/account/github/app/oauth/callback",
        "setup_url": cfg.public_base_url.rstrip("/") + "/account/github/app/setup",
        "manifest_callback_url": cfg.public_base_url.rstrip("/") + "/admin/github-app/manifest/callback",
        **extra,
    }


def _manifest(cfg) -> dict[str, object]:
    base = cfg.public_base_url.rstrip("/")
    host = (urlsplit(base).hostname or "fdex").replace(".", "-")
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:6]
    app_name = f"FDEX-{host}-{digest}"[:34].rstrip("-")
    return {
        "name": app_name,
        "description": "FDEX user-owned GitHub repository integration for Coding Agent",
        "url": base + "/account/github",
        "redirect_url": base + "/admin/github-app/manifest/callback",
        "callback_urls": [base + "/account/github/app/oauth/callback"],
        "setup_url": base + "/account/github/app/setup",
        "setup_on_update": True,
        # FDEX is a multi-user center service. Public here means other GitHub accounts may
        # install this App; it does not publish it to Marketplace and does not bypass each
        # user's explicit repository selection on GitHub's official installation page.
        "public": True,
        # FDEX intentionally performs an owner-bound OAuth+PKCE proof before installation;
        # keeping this false preserves setup_url as the installation completion callback.
        "request_oauth_on_install": False,
        "hook_attributes": {
            "url": base + "/api/github/app/webhook",
            "active": False,
        },
        "default_permissions": {
            "contents": "write",
            "pull_requests": "write",
            "metadata": "read",
        },
        "default_events": [],
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


def _validate_proxy(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        return ""
    if len(clean) > 1000:
        raise ValueError("GitHub 出站代理地址过长")
    parsed = urlsplit(clean)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("GitHub 出站代理必须是完整的 http:// 或 https:// 地址")
    return clean


@router.get("", response_class=HTMLResponse, response_model=None)
def github_app_settings(request: Request) -> Response:
    if not is_admin(request):
        return _guard()
    cfg = fresh_settings()
    app_settings_url = (
        f"https://github.com/settings/apps/{cfg.fdex_github_app_slug.strip()}"
        if cfg.fdex_github_app_slug.strip()
        else ""
    )
    install_url = (
        f"https://github.com/apps/{cfg.fdex_github_app_slug.strip()}/installations/new"
        if cfg.fdex_github_app_slug.strip()
        else ""
    )
    return templates.TemplateResponse(
        "github_app_settings.html",
        _ctx(
            request,
            app_settings_url=app_settings_url,
            install_url=install_url,
            github_proxy_configured=bool(cfg.fdex_github_http_proxy.strip()),
        ),
    )


@router.post("/network", response_model=None)
def github_network_settings(
    request: Request,
    csrf_token: str = Form(...),
    http_proxy: str = Form(""),
    clear_proxy: str | None = Form(None),
    connect_timeout_seconds: str = Form("10"),
    read_timeout_seconds: str = Form("60"),
    retry_attempts: str = Form("3"),
) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    current = fresh_settings()
    try:
        connect_timeout = _float_setting(connect_timeout_seconds, "GitHub 连接超时", 2, 120)
        read_timeout = _float_setting(read_timeout_seconds, "GitHub 读取超时", 5, 300)
        retries = _int_setting(retry_attempts, "GitHub 重试次数", 1, 5)
        proxy = _validate_proxy(http_proxy)
    except ValueError as exc:
        write_audit(request, "save_github_network", success=False, error=str(exc))
        set_flash(request, str(exc), "error")
        return RedirectResponse("/admin/github-app#network", status_code=303)

    updates: dict[str, str] = {
        "FDEX_GITHUB_CONNECT_TIMEOUT_SECONDS": f"{connect_timeout:g}",
        "FDEX_GITHUB_READ_TIMEOUT_SECONDS": f"{read_timeout:g}",
        "FDEX_GITHUB_RETRY_ATTEMPTS": str(retries),
    }
    proxy_changed = False
    if clear_proxy:
        updates["FDEX_GITHUB_HTTP_PROXY"] = ""
        proxy_changed = bool(current.fdex_github_http_proxy.strip())
    elif proxy:
        updates["FDEX_GITHUB_HTTP_PROXY"] = proxy
        proxy_changed = proxy != current.fdex_github_http_proxy.strip()

    backup = write_env(updates)
    get_settings.cache_clear()
    after = fresh_settings()
    # Never audit or render the proxy URL because it may contain credentials.
    write_audit(
        request,
        "save_github_network",
        proxy_changed=proxy_changed,
        proxy_configured=bool(after.fdex_github_http_proxy.strip()),
        connect_timeout_seconds=after.fdex_github_connect_timeout_seconds,
        read_timeout_seconds=after.fdex_github_read_timeout_seconds,
        retry_attempts=after.fdex_github_retry_attempts,
        backup=str(backup) if backup else "",
    )
    set_flash(
        request,
        "GitHub 网络出口配置已保存。新 OAuth / GitHub App 请求会立即使用新配置，无需重启服务。",
        "success",
    )
    return RedirectResponse("/admin/github-app#network", status_code=303)


@router.post("/network/test", response_model=None)
def github_network_test(request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        result = GitHubAppClient().network_probe()
        targets = result.get("targets") if isinstance(result.get("targets"), list) else []
        descriptions: list[str] = []
        all_ok = bool(targets)
        audit_targets: list[dict[str, object]] = []
        for item in targets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "GitHub")
            ok = bool(item.get("ok"))
            all_ok = all_ok and ok
            elapsed = int(item.get("elapsed_ms") or 0)
            status = int(item.get("status_code") or 0)
            error = str(item.get("error") or "")
            if ok:
                descriptions.append(f"{name}: HTTP {status} · {elapsed} ms")
            else:
                descriptions.append(f"{name}: {error or '连接失败'} · {elapsed} ms")
            audit_targets.append(
                {"name": name, "ok": ok, "status_code": status, "elapsed_ms": elapsed, "error": error[:80]}
            )
        write_audit(
            request,
            "test_github_network",
            success=all_ok,
            proxy_configured=bool(result.get("proxy_configured")),
            targets=audit_targets,
        )
        prefix = "GitHub 网络连通正常" if all_ok else "GitHub 网络连通异常"
        set_flash(request, f"{prefix}：{'；'.join(descriptions) or '没有测试结果'}", "success" if all_ok else "error")
    except (GitHubAppError, ValueError, RuntimeError) as exc:
        write_audit(request, "test_github_network", success=False, error=str(exc)[:500])
        set_flash(request, f"GitHub 网络测试失败：{exc}", "error")
    return RedirectResponse("/admin/github-app#network", status_code=303)


@router.post("/manifest/start", response_class=HTMLResponse, response_model=None)
def github_app_manifest_start(request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    cfg = fresh_settings()
    if not cfg.public_base_url.lower().startswith("https://"):
        set_flash(request, "GitHub App Manifest 初始化要求 PUBLIC_BASE_URL 使用 HTTPS", "error")
        return RedirectResponse("/admin/github-app", status_code=303)

    state = secrets.token_urlsafe(32)
    request.session[_MANIFEST_STATE] = state
    manifest = json.dumps(_manifest(cfg), ensure_ascii=False, separators=(",", ":"))
    write_audit(
        request,
        "github_app_manifest_started",
        public_base_url=cfg.public_base_url,
        callback_url=cfg.public_base_url.rstrip("/") + "/admin/github-app/manifest/callback",
    )
    # GitHub documents the manifest-flow CSRF state as a query parameter on the registration
    # endpoint. Keep it out of the POST body so GitHub reliably echoes it to redirect_url.
    github_manifest_url = f"{_MANIFEST_ENDPOINT}?{urlencode({'state': state})}"
    return templates.TemplateResponse(
        "github_app_manifest_post.html",
        {
            "request": request,
            "github_manifest_url": github_manifest_url,
            "manifest": manifest,
        },
    )


@router.get("/manifest/callback", response_model=None)
def github_app_manifest_callback(request: Request, code: str = "", state: str = "") -> Response:
    if not is_admin(request):
        return _guard()
    expected = str(request.session.pop(_MANIFEST_STATE, "") or "")
    if not expected or not state or not hmac.compare_digest(expected, state):
        set_flash(request, "GitHub App Manifest state 校验失败，请从后台重新初始化", "error")
        return RedirectResponse("/admin/github-app", status_code=303)
    clean_code = (code or "").strip()
    if not clean_code:
        set_flash(request, "GitHub 没有返回 Manifest 转换 code", "error")
        return RedirectResponse("/admin/github-app", status_code=303)

    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.post(
                f"{_GITHUB_API}/app-manifests/{clean_code}/conversions",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "fdex-github-app-bootstrap",
                },
            )
        if response.status_code not in {200, 201}:
            detail = response.text[:500].replace("\n", " ").strip()
            raise RuntimeError(f"GitHub HTTP {response.status_code}{': ' + detail if detail else ''}")
        payload = response.json()
        app_id = str(payload.get("id") or "").strip()
        slug = str(payload.get("slug") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        client_secret = str(payload.get("client_secret") or "").strip()
        pem = str(payload.get("pem") or "").strip()
        if not all((app_id, slug, client_id, client_secret, pem)):
            raise RuntimeError("GitHub Manifest 返回的 App 身份信息不完整")

        cfg = fresh_settings()
        secrets_dir = Path(cfg.app_dir).expanduser().resolve() / "server" / "data" / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(secrets_dir, 0o700)
        except OSError:
            pass
        pem_path = secrets_dir / "fdex-github-app.pem"
        temp_path = secrets_dir / f".fdex-github-app-{secrets.token_hex(6)}.tmp"
        temp_path.write_text(pem.rstrip() + "\n", encoding="utf-8")
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, pem_path)
        try:
            os.chmod(pem_path, 0o600)
        except OSError:
            pass

        write_env(
            {
                "FDEX_GITHUB_APP_ID": app_id,
                "FDEX_GITHUB_APP_SLUG": slug,
                "FDEX_GITHUB_APP_CLIENT_ID": client_id,
                "FDEX_GITHUB_APP_CLIENT_SECRET": client_secret,
                "FDEX_GITHUB_APP_PRIVATE_KEY_PATH": str(pem_path),
                "FDEX_GITHUB_APP_PRIVATE_KEY_B64": "",
            }
        )
        get_settings.cache_clear()
        write_audit(
            request,
            "github_app_manifest_completed",
            app_id=app_id,
            slug=slug,
            client_id_suffix=client_id[-6:],
            private_key_path=str(pem_path),
        )
        set_flash(
            request,
            f"FDEX GitHub App 已初始化：{slug}。现在用户中心会出现“安装 / 连接 FDEX GitHub App”按钮。",
            "success",
        )
    except (httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
        write_audit(
            request,
            "github_app_manifest_failed",
            success=False,
            error=str(exc)[:500],
        )
        set_flash(request, f"GitHub App 初始化失败：{exc}", "error")
    return RedirectResponse("/admin/github-app", status_code=303)
