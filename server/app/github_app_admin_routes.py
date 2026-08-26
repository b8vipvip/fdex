from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.config import SERVER_DIR, fresh_settings
from app.env_manager import write_env
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
    return {
        "name": "FDEX",
        "url": base + "/account/github",
        "redirect_url": base + "/admin/github-app/manifest/callback",
        "callback_urls": [base + "/account/github/app/oauth/callback"],
        "setup_url": base + "/account/github/app/setup",
        "setup_on_update": True,
        "public": False,
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
        _ctx(request, app_settings_url=app_settings_url, install_url=install_url),
    )


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
        "github_app_manifest_started",
        {
            "public_base_url": cfg.public_base_url,
            "callback_url": cfg.public_base_url.rstrip("/") + "/admin/github-app/manifest/callback",
        },
    )
    return templates.TemplateResponse(
        "github_app_manifest_post.html",
        {
            "request": request,
            "github_manifest_url": _MANIFEST_ENDPOINT,
            "state": state,
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
        write_audit(
            "github_app_manifest_completed",
            {
                "app_id": app_id,
                "slug": slug,
                "client_id_suffix": client_id[-6:],
                "private_key_path": str(pem_path),
            },
        )
        set_flash(
            request,
            f"FDEX GitHub App 已初始化：{slug}。现在用户中心会出现“安装 / 连接 GitHub App”按钮。",
            "success",
        )
    except (httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
        write_audit("github_app_manifest_failed", {"error": str(exc)[:500]})
        set_flash(request, f"GitHub App 初始化失败：{exc}", "error")
    return RedirectResponse("/admin/github-app", status_code=303)
