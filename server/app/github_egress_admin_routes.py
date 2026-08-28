from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.audit import write_audit
from app.config import SERVER_DIR, fresh_settings, get_settings
from app.env_manager import read_env, write_env
from app.github_app import GitHubAppError
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
from app.github_egress_probe import probe_github_egress_network
from app.github_vless_pool import (
    active_vless_node,
    add_vless_node,
    delete_vless_node,
    disable_all_vless_nodes,
    disable_vless_node,
    edit_vless_node,
    ensure_legacy_vless_node,
    get_vless_node,
    list_vless_nodes,
    mark_active_vless_node,
)
from app.security import ensure_csrf_token, is_admin, pop_flash, set_flash, verify_csrf

router = APIRouter(prefix="/github-egress", include_in_schema=False)
templates = Jinja2Templates(directory=str(SERVER_DIR / "app" / "templates"))

_MANAGED_ENV_KEYS = (
    "FDEX_GITHUB_EGRESS_MODE",
    "FDEX_GITHUB_VLESS_URI",
    "FDEX_GITHUB_VLESS_ACTIVE_NODE_ID",
    "FDEX_GITHUB_XRAY_BINARY",
    "FDEX_GITHUB_XRAY_LOCAL_PORT",
    "FDEX_GITHUB_XRAY_PROXY_USER",
    "FDEX_GITHUB_XRAY_PROXY_PASSWORD",
    "FDEX_GITHUB_HTTP_PROXY",
    "FDEX_GITHUB_CONNECT_TIMEOUT_SECONDS",
    "FDEX_GITHUB_READ_TIMEOUT_SECONDS",
    "FDEX_GITHUB_RETRY_ATTEMPTS",
)


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


def _snapshot(values: dict[str, str]) -> dict[str, str]:
    return {key: str(values.get(key) or "") for key in _MANAGED_ENV_KEYS}


def _restore_snapshot(snapshot: dict[str, str]) -> None:
    write_env(snapshot)
    get_settings.cache_clear()
    try:
        if snapshot.get("FDEX_GITHUB_EGRESS_MODE") == "managed_vless" and snapshot.get("FDEX_GITHUB_VLESS_URI"):
            apply_managed_egress(force_restart=True)
        else:
            stop_managed_egress()
    except Exception:
        # The original configuration may itself have been unhealthy. Restoration is best-effort,
        # but secrets and mode are still returned to their previous persisted state.
        pass


def _activate_node(
    node: dict[str, Any],
    *,
    binary_setting: str,
    port: int,
    extra_updates: dict[str, str] | None = None,
) -> tuple[dict[str, Any], object]:
    values = read_env()
    uri = str(node.get("uri") or "").strip()
    node_id = str(node.get("id") or "").strip()
    if not node_id:
        raise ValueError("VLESS 节点 ID 无效")
    parse_vless_uri(uri)
    resolved_setting = (binary_setting or values.get("FDEX_GITHUB_XRAY_BINARY") or "xray").strip()
    resolve_xray_binary(resolved_setting)
    username, password = make_managed_credentials(values)
    selected_proxy = managed_proxy_url(port, username, password)
    previous = _snapshot(values)
    updates = {
        "FDEX_GITHUB_EGRESS_MODE": "managed_vless",
        "FDEX_GITHUB_VLESS_URI": uri,
        "FDEX_GITHUB_VLESS_ACTIVE_NODE_ID": node_id,
        "FDEX_GITHUB_XRAY_BINARY": resolved_setting,
        "FDEX_GITHUB_XRAY_LOCAL_PORT": str(port),
        "FDEX_GITHUB_XRAY_PROXY_USER": username,
        "FDEX_GITHUB_XRAY_PROXY_PASSWORD": password,
        "FDEX_GITHUB_HTTP_PROXY": selected_proxy,
    }
    if extra_updates:
        updates.update(extra_updates)
    backup = write_env(updates)
    get_settings.cache_clear()
    try:
        status = apply_managed_egress(force_restart=True)
    except GitHubEgressError:
        _restore_snapshot(previous)
        raise
    mark_active_vless_node(node_id)
    return status, backup


def _deactivate_active_node(node_id: str) -> None:
    node = get_vless_node(node_id)
    if node is None:
        raise KeyError("VLESS 节点不存在")
    if node.get("enabled"):
        write_env(
            {
                "FDEX_GITHUB_EGRESS_MODE": "direct",
                "FDEX_GITHUB_HTTP_PROXY": "",
                "FDEX_GITHUB_VLESS_URI": "",
                "FDEX_GITHUB_VLESS_ACTIVE_NODE_ID": "",
            }
        )
        get_settings.cache_clear()
        stop_managed_egress()
    disable_vless_node(node_id)


@router.get("", response_class=HTMLResponse, response_model=None)
def github_egress_page(request: Request) -> Response:
    if not is_admin(request):
        return _guard()
    values = read_env()
    ensure_legacy_vless_node(values)
    status = managed_egress_status()
    nodes = list_vless_nodes()
    active = next((node for node in nodes if node.get("enabled")), None)
    return templates.TemplateResponse(
        "github_egress.html",
        _ctx(
            request,
            status=status,
            effective_mode=egress_mode(values, fresh_settings()),
            vless_nodes=nodes,
            active_vless=active,
            custom_proxy_configured=bool(fresh_settings().fdex_github_http_proxy.strip())
            and status.get("mode") == "http_proxy",
        ),
    )


@router.post("/save", response_model=None)
def save_github_egress(
    request: Request,
    csrf_token: str = Form(...),
    mode: str = Form("direct"),
    vless_uri: str = Form(""),  # Phase 7.16 form compatibility; the new UI uses the node list.
    xray_binary: str = Form("xray"),
    xray_local_port: str = Form("18188"),
    http_proxy: str = Form(""),
    connect_timeout_seconds: str = Form("10"),
    read_timeout_seconds: str = Form("60"),
    retry_attempts: str = Form("3"),
    clear_saved_vless: str | None = Form(None),  # retained for old cached forms
) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)

    values = read_env()
    ensure_legacy_vless_node(values)
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

    common = {
        "FDEX_GITHUB_CONNECT_TIMEOUT_SECONDS": f"{connect_timeout:g}",
        "FDEX_GITHUB_READ_TIMEOUT_SECONDS": f"{read_timeout:g}",
        "FDEX_GITHUB_RETRY_ATTEMPTS": str(retries),
        "FDEX_GITHUB_XRAY_LOCAL_PORT": str(port),
        "FDEX_GITHUB_XRAY_BINARY": (xray_binary or values.get("FDEX_GITHUB_XRAY_BINARY") or "xray").strip(),
    }
    backup: object = None
    apply_error = ""
    imported_node_id = ""

    try:
        if clean_mode == "managed_vless":
            # Old cached Phase 7.16 pages may still POST a one-off link. Import it into the pool
            # instead of discarding it, then activate it using the same transactional path.
            clean_legacy_uri = (vless_uri or "").strip()
            node: dict[str, Any] | None
            if clean_legacy_uri:
                public = add_vless_node("导入的 VLESS 节点", clean_legacy_uri)
                imported_node_id = str(public["id"])
                node = get_vless_node(imported_node_id)
            else:
                node = active_vless_node()
            if node is None:
                raise ValueError("托管 VLESS 模式需要先在下方代理列表中添加并启用一个节点")
            _, backup = _activate_node(
                node,
                binary_setting=common["FDEX_GITHUB_XRAY_BINARY"],
                port=port,
                extra_updates={key: value for key, value in common.items() if key != "FDEX_GITHUB_XRAY_BINARY"},
            )
        elif clean_mode == "http_proxy":
            proxy_input = _validate_http_proxy(http_proxy)
            current_mode = egress_mode(values, cfg)
            old_proxy = cfg.fdex_github_http_proxy.strip()
            if proxy_input:
                selected_proxy = proxy_input
            elif current_mode == "http_proxy" and old_proxy:
                selected_proxy = old_proxy
            else:
                raise ValueError("切换到自定义 HTTP(S) 代理时必须填写代理地址")
            backup = write_env(
                {
                    **common,
                    "FDEX_GITHUB_EGRESS_MODE": "http_proxy",
                    "FDEX_GITHUB_HTTP_PROXY": selected_proxy,
                    "FDEX_GITHUB_VLESS_URI": "",
                    "FDEX_GITHUB_VLESS_ACTIVE_NODE_ID": "",
                }
            )
            get_settings.cache_clear()
            stop_managed_egress()
            disable_all_vless_nodes()
        else:
            backup = write_env(
                {
                    **common,
                    "FDEX_GITHUB_EGRESS_MODE": "direct",
                    "FDEX_GITHUB_HTTP_PROXY": "",
                    "FDEX_GITHUB_VLESS_URI": "",
                    "FDEX_GITHUB_VLESS_ACTIVE_NODE_ID": "",
                }
            )
            get_settings.cache_clear()
            stop_managed_egress()
            disable_all_vless_nodes()
    except (GitHubEgressError, KeyError, ValueError) as exc:
        apply_error = str(exc)

    write_audit(
        request,
        "save_github_egress",
        success=not bool(apply_error),
        mode=clean_mode,
        proxy_configured=bool(fresh_settings().fdex_github_http_proxy.strip()),
        imported_node=bool(imported_node_id),
        xray_local_port=port,
        connect_timeout_seconds=connect_timeout,
        read_timeout_seconds=read_timeout,
        retry_attempts=retries,
        backup=str(backup) if backup else "",
        apply_error=apply_error[:500],
    )
    if apply_error:
        set_flash(request, f"GitHub 出站配置未能生效：{apply_error}", "error")
    else:
        message = {
            "direct": "GitHub 出站已恢复服务器直连；服务器其它服务网络不受影响。",
            "http_proxy": "GitHub 已切换到 FDEX 专用 HTTP(S) 代理；只影响 FDEX 的 GitHub 请求和 Git 子进程。",
            "managed_vless": "托管 VLESS 已应用当前启用节点。Xray 仅监听 127.0.0.1，并保持 GitHub 域名白名单隔离。",
        }[clean_mode]
        set_flash(request, message, "success")
    return RedirectResponse("/admin/github-egress", status_code=303)


@router.post("/nodes/add", response_model=None)
def add_vless_proxy_node(
    request: Request,
    csrf_token: str = Form(...),
    node_name: str = Form(""),
    vless_uri: str = Form(...),
) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        node = add_vless_node(node_name, vless_uri)
        write_audit(request, "add_github_vless_node", node_id=node["id"])
        set_flash(request, f"VLESS 节点“{node['name']}”已加入代理列表，默认处于停用状态。", "success")
    except ValueError as exc:
        write_audit(request, "add_github_vless_node", success=False, error=str(exc))
        set_flash(request, str(exc), "error")
    return RedirectResponse("/admin/github-egress#vless-pool", status_code=303)


@router.post("/nodes/{node_id}/edit", response_model=None)
def edit_vless_proxy_node(
    node_id: str,
    request: Request,
    csrf_token: str = Form(...),
    node_name: str = Form(""),
    vless_uri: str = Form(""),
) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    original = get_vless_node(node_id)
    if original is None:
        set_flash(request, "VLESS 节点不存在", "error")
        return RedirectResponse("/admin/github-egress#vless-pool", status_code=303)
    try:
        edited = edit_vless_node(node_id, node_name, vless_uri)
        updated_raw = get_vless_node(node_id)
        if updated_raw is None:
            raise KeyError("VLESS 节点不存在")
        if original.get("enabled") and str(original.get("uri")) != str(updated_raw.get("uri")):
            status = managed_egress_status()
            try:
                _activate_node(
                    updated_raw,
                    binary_setting=str(status.get("xray_binary_setting") or "xray"),
                    port=int(status.get("local_port") or 18188),
                )
            except (GitHubEgressError, ValueError):
                edit_vless_node(node_id, str(original.get("name") or ""), str(original.get("uri") or ""))
                raise
        write_audit(request, "edit_github_vless_node", node_id=node_id, uri_changed=bool((vless_uri or "").strip()))
        set_flash(request, f"VLESS 节点“{edited['name']}”已更新。", "success")
    except (GitHubEgressError, KeyError, ValueError) as exc:
        write_audit(request, "edit_github_vless_node", success=False, node_id=node_id, error=str(exc)[:500])
        set_flash(request, f"无法更新 VLESS 节点：{exc}", "error")
    return RedirectResponse("/admin/github-egress#vless-pool", status_code=303)


@router.post("/nodes/{node_id}/enable", response_model=None)
def enable_vless_proxy_node(request: Request, node_id: str, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    node = get_vless_node(node_id)
    if node is None:
        set_flash(request, "VLESS 节点不存在", "error")
        return RedirectResponse("/admin/github-egress#vless-pool", status_code=303)
    status = managed_egress_status()
    try:
        applied, _ = _activate_node(
            node,
            binary_setting=str(status.get("xray_binary_setting") or "xray"),
            port=int(status.get("local_port") or 18188),
        )
        write_audit(
            request,
            "enable_github_vless_node",
            node_id=node_id,
            unit_state=applied.get("unit_state"),
            listener_ready=applied.get("listener_ready"),
        )
        set_flash(request, f"已启用 VLESS 节点“{node['name']}”，其它节点自动切换为停用。", "success")
    except (GitHubEgressError, ValueError) as exc:
        write_audit(request, "enable_github_vless_node", success=False, node_id=node_id, error=str(exc)[:500])
        set_flash(request, f"无法启用该 VLESS 节点：{exc}", "error")
    return RedirectResponse("/admin/github-egress#vless-pool", status_code=303)


@router.post("/nodes/{node_id}/disable", response_model=None)
def disable_vless_proxy_node(request: Request, node_id: str, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        node = get_vless_node(node_id)
        if node is None:
            raise KeyError("VLESS 节点不存在")
        _deactivate_active_node(node_id)
        write_audit(request, "disable_github_vless_node", node_id=node_id)
        set_flash(
            request,
            f"VLESS 节点“{node['name']}”已停用。若它原本是当前节点，GitHub 出站已安全恢复服务器直连。",
            "success",
        )
    except KeyError as exc:
        write_audit(request, "disable_github_vless_node", success=False, node_id=node_id, error=str(exc))
        set_flash(request, str(exc), "error")
    return RedirectResponse("/admin/github-egress#vless-pool", status_code=303)


@router.post("/nodes/{node_id}/delete", response_model=None)
def delete_vless_proxy_node(request: Request, node_id: str, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        node = get_vless_node(node_id)
        if node is None:
            raise KeyError("VLESS 节点不存在")
        if node.get("enabled"):
            _deactivate_active_node(node_id)
        removed = delete_vless_node(node_id)
        write_audit(request, "delete_github_vless_node", node_id=node_id, was_enabled=bool(node.get("enabled")))
        set_flash(request, f"VLESS 节点“{removed['name']}”已删除。", "success")
    except KeyError as exc:
        write_audit(request, "delete_github_vless_node", success=False, node_id=node_id, error=str(exc))
        set_flash(request, str(exc), "error")
    return RedirectResponse("/admin/github-egress#vless-pool", status_code=303)


@router.post("/test", response_model=None)
def test_github_egress(request: Request, csrf_token: str = Form(...)) -> Response:
    if not is_admin(request):
        return _guard()
    verify_csrf(request, csrf_token)
    try:
        network = probe_github_egress_network()
        targets = network.get("targets") if isinstance(network.get("targets"), list) else []
        parts: list[str] = []
        all_ok = bool(targets)
        audit_targets: list[dict[str, object]] = []
        for item in targets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "GitHub")
            ok = bool(item.get("ok"))
            reachable = bool(item.get("reachable"))
            all_ok = all_ok and ok
            status_code = int(item.get("status_code") or 0)
            elapsed_ms = int(item.get("elapsed_ms") or 0)
            error = str(item.get("error") or "")
            if ok:
                parts.append(f"{name}: HTTP {status_code} · {elapsed_ms} ms")
            elif reachable:
                parts.append(f"{name}: HTTP {status_code} · {error or '响应不可用'} · {elapsed_ms} ms")
            else:
                parts.append(f"{name}: {error or '连接失败'} · {elapsed_ms} ms")
            audit_targets.append(
                {
                    "name": name,
                    "ok": ok,
                    "reachable": reachable,
                    "status_code": status_code,
                    "elapsed_ms": elapsed_ms,
                    "error": error[:120],
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
            ("GitHub 专用出口功能测试通过：" if all_ok else "GitHub 专用出口功能测试异常：")
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
