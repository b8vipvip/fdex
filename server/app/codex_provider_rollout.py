from __future__ import annotations

from typing import Any

from app.codex_provider_compatibility import (
    COMPATIBILITY_MAX_AGE_HOURS,
    codex_provider_compatibility_store,
)
from app.provider_manager import provider_store

_installed = False


def _engine_module():
    import app.codex_engine as engine

    return engine


def rollout_selection(runtime: Any | None = None) -> dict[str, Any]:
    """Choose the first priority Provider with a fresh full Codex compatibility proof.

    Skipping an unverified/unfresh higher-priority Provider is the only automatic Provider
    failover FDEX performs for Codex. It happens before a user Host starts and therefore before a
    worktree can be modified. Once a Codex Host/Turn starts, FDEX never switches Providers inside
    that task; a failure terminalizes the task and Retry creates a fresh task/worktree boundary.
    """
    engine = _engine_module()
    runtime = runtime or engine.resolve_codex_runtime()
    compatibility = codex_provider_compatibility_store()
    diagnostics: list[dict[str, Any]] = []
    selected = None

    for provider in provider_store().list(enabled_only=True, include_secret=True):
        spec = engine.select_codex_provider_from([provider])
        if spec is None:
            diagnostics.append(
                {
                    "provider_id": int(provider.get("id") or 0),
                    "provider_name": str(provider.get("name") or ""),
                    "eligible": False,
                    "reason": "未完整配置 Responses 协议、API Key、Base URL 或文本模型",
                    "level": "none",
                }
            )
            continue
        status = compatibility.evaluate(
            provider,
            runtime,
            required_level="full",
            max_age_hours=COMPATIBILITY_MAX_AGE_HOURS,
        )
        diagnostics.append(
            {
                "provider_id": spec.provider_id,
                "provider_name": spec.name,
                "model": spec.model,
                "eligible": bool(status.get("valid")),
                "reason": str(status.get("reason") or ""),
                "level": str(status.get("level") or "none"),
                "age_hours": status.get("age_hours"),
            }
        )
        if bool(status.get("valid")):
            selected = spec
            break

    return {
        "provider": selected,
        "runtime": runtime,
        "diagnostics": diagnostics,
        "required_level": "full",
        "max_age_hours": COMPATIBILITY_MAX_AGE_HOURS,
    }


def select_verified_codex_provider():
    try:
        return rollout_selection().get("provider")
    except Exception:
        return None


def codex_rollout_runtime_status() -> dict[str, object]:
    engine = _engine_module()
    runtime = None
    selection: dict[str, Any] = {}
    reason = ""
    try:
        runtime = engine.resolve_codex_runtime()
        selection = rollout_selection(runtime)
    except Exception as exc:
        reason = str(exc)
    provider = selection.get("provider") if selection else None
    diagnostics = list(selection.get("diagnostics") or []) if selection else []
    if runtime is not None and provider is None and not reason:
        configured = [item for item in diagnostics if int(item.get("provider_id") or 0) > 0]
        if not configured:
            reason = "没有已启用且声明 Responses 协议、API Key 和文本模型完整的供应商"
        else:
            first = configured[0]
            reason = (
                "没有 fresh full-compatible Codex Provider；请在管理员 Codex Provider Rollout 页面执行真实 smoke。"
                f" 当前首选：{first.get('provider_name') or first.get('provider_id')} · {first.get('reason') or '未验证'}"
            )
    return {
        "ready": bool(runtime is not None and provider is not None),
        "sdk_version": "native-jsonrpc",
        "runtime_version": getattr(runtime, "version", "") if runtime else "",
        "runtime_source": getattr(runtime, "source", "") if runtime else "",
        "runtime_path": getattr(runtime, "path", "") if runtime else "",
        "protocol": "codex-app-server-jsonrpc-v2",
        "provider_id": getattr(provider, "provider_id", None) if provider else None,
        "provider_name": getattr(provider, "name", "") if provider else "",
        "model": getattr(provider, "model", "") if provider else "",
        "reason": reason,
        "rollout_required_level": "full",
        "rollout_max_age_hours": COMPATIBILITY_MAX_AGE_HOURS,
        "rollout_diagnostics": diagnostics,
    }


def provider_rollout_rows() -> dict[str, Any]:
    engine = _engine_module()
    compatibility = codex_provider_compatibility_store()
    try:
        runtime = engine.resolve_codex_runtime()
        runtime_error = ""
    except Exception as exc:
        runtime = None
        runtime_error = str(exc)
    rows: list[dict[str, Any]] = []
    for provider in provider_store().list(include_secret=True):
        public = {key: value for key, value in provider.items() if key != "api_key"}
        spec = engine.select_codex_provider_from([provider])
        if runtime is None or spec is None:
            status = {
                "valid": False,
                "level": "none",
                "reason": runtime_error
                or "未完整配置 Responses 协议、API Key、Base URL 或文本模型",
                "record": compatibility.get(int(provider["id"])),
            }
        else:
            status = compatibility.evaluate(
                provider,
                runtime,
                required_level="full",
                max_age_hours=COMPATIBILITY_MAX_AGE_HOURS,
            )
        rows.append({"provider": public, "spec": spec, "compatibility": status})
    return {
        "runtime": runtime,
        "runtime_error": runtime_error,
        "rows": rows,
        "required_level": "full",
        "max_age_hours": COMPATIBILITY_MAX_AGE_HOURS,
    }


def install_codex_provider_rollout_runtime() -> None:
    """Install the Provider proof gate at every already-imported Codex launch/status seam.

    Phase 7.36 no longer has an Agent engine switch. This gate now decides only whether the
    mandatory Codex Host is ready to start. Failure is terminal/fail-closed and never selects a
    different Agent core.
    """
    global _installed
    if _installed:
        return
    engine = _engine_module()
    engine.select_codex_provider = select_verified_codex_provider
    engine.codex_runtime_status = codex_rollout_runtime_status

    # These modules imported Codex functions into module globals before the rollout installer runs.
    # Rebind every such reference once, before FastAPI starts serving requests, so all user Hosts,
    # capability-control Hosts and admin readiness surfaces enforce the same fresh-full proof.
    import app.agent_admin_routes as agent_admin
    import app.codex_capability_control as capability
    import app.codex_host_runtime as host
    import app.codex_runtime_admin_routes as runtime_admin

    agent_admin.codex_runtime_status = codex_rollout_runtime_status
    capability.select_codex_provider = select_verified_codex_provider
    host.select_codex_provider = select_verified_codex_provider
    runtime_admin.codex_runtime_status = codex_rollout_runtime_status
    _installed = True
