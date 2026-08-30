from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from app.agent_projects import agent_project_store
from app.agent_runtime import AgentRuntimeError
from app.codex_app_server import CodexAppServerClient, CodexRpcError, CodexServerRequestDenied
from app.codex_engine import (
    _codex_home,
    _launch_args,
    _safe_process_env,
    resolve_codex_runtime,
    select_codex_provider,
)
from app.config import fresh_settings

T = TypeVar("T")

PLUGIN_MUTATION_BLOCK_REASON = (
    "Plugin 安装、卸载、Marketplace 写入及 share 写操作在 Phase 7.30 保持禁用；"
    "它们可能把本地 command/stdio 能力引入多租户 Center，必须等 Phase 7.32 "
    "完成 Codex 整个进程树的 cgroup v2 / PID / tree-kill 外层隔离后再开放。"
)


class CodexCapabilityError(AgentRuntimeError):
    pass


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _project_cwd(owner_id: str, project_id: int | None) -> tuple[Path, dict[str, Any] | None]:
    home = _codex_home(owner_id)
    if project_id is None:
        return home, None
    try:
        project = agent_project_store().get_project(owner_id, int(project_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise CodexCapabilityError("Coding Agent 项目不存在或不属于当前账号") from exc
    if not bool(project.get("enabled")):
        raise CodexCapabilityError("Coding Agent 项目未启用")
    repo, _worktrees = agent_project_store().project_paths(owner_id, int(project_id))
    # Capability inventory must not clone/fetch a repository as a side effect. If the project
    # has not been materialized yet, use the owner CODEX_HOME and surface that fact in the UI.
    if (repo / ".git").is_dir():
        resolved = repo.resolve()
        owner_root = agent_project_store().owner_root(owner_id).resolve()
        if resolved != owner_root and owner_root not in resolved.parents:
            raise CodexCapabilityError("项目能力扫描目录越过当前账号沙箱")
        return resolved, project
    return home, project


def _flatten_skills(result: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    skills: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in _list(_dict(result).get("data")):
        row = _dict(entry)
        cwd = str(row.get("cwd") or "")
        for raw in _list(row.get("skills")):
            skill = _dict(raw)
            path = str(skill.get("path") or "").strip()
            name = str(skill.get("name") or "").strip()
            if not path or not name:
                continue
            skills.append(
                {
                    "name": name[:160],
                    "description": str(skill.get("description") or "")[:1500],
                    "short_description": str(skill.get("shortDescription") or "")[:500],
                    "path": path,
                    "scope": str(skill.get("scope") or "")[:80],
                    "enabled": bool(skill.get("enabled", True)),
                    "plugin_id": str(skill.get("pluginId") or "")[:200],
                    "cwd": cwd,
                }
            )
        for raw in _list(row.get("errors")):
            item = _dict(raw)
            errors.append(
                {
                    "cwd": cwd,
                    "path": str(item.get("path") or "")[:1000],
                    "message": str(item.get("message") or item.get("error") or raw)[:1500],
                }
            )
    return skills, errors


def _flatten_hooks(result: Any) -> tuple[list[dict[str, Any]], list[str]]:
    hooks: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    for entry in _list(_dict(result).get("data")):
        row = _dict(entry)
        cwd = str(row.get("cwd") or "")
        for raw in _list(row.get("hooks")):
            hook = _dict(raw)
            hooks.append(
                {
                    "cwd": cwd,
                    "event": str(hook.get("event") or hook.get("eventName") or "")[:120],
                    "source": str(hook.get("source") or "")[:120],
                    "trust": str(hook.get("trustStatus") or "")[:120],
                    "handler": str(
                        hook.get("handlerType")
                        or hook.get("handler")
                        or hook.get("command")
                        or ""
                    )[:1200],
                    "raw": hook,
                }
            )
        for warning in _list(row.get("warnings")):
            diagnostics.append(str(warning)[:1500])
        for raw in _list(row.get("errors")):
            item = _dict(raw)
            diagnostics.append(str(item.get("message") or item.get("error") or raw)[:1500])
    return hooks, diagnostics


def _flatten_marketplaces(result: Any) -> tuple[list[dict[str, Any]], list[str]]:
    markets: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    payload = _dict(result)
    for raw in _list(payload.get("marketplaces")):
        market = _dict(raw)
        plugins: list[dict[str, Any]] = []
        for item in _list(market.get("plugins")):
            plugin = _dict(item)
            name = str(plugin.get("name") or "").strip()
            if not name:
                continue
            plugins.append(
                {
                    "id": str(plugin.get("id") or "")[:240],
                    "name": name[:200],
                    "description": str(plugin.get("description") or "")[:1500],
                    "version": str(plugin.get("version") or "")[:120],
                    "installed": bool(plugin.get("installed", False)),
                    "enabled": bool(plugin.get("enabled", True)),
                    "raw": plugin,
                }
            )
        markets.append(
            {
                "name": str(market.get("name") or "")[:200],
                "path": str(market.get("path") or ""),
                "plugins": plugins,
            }
        )
    for raw in _list(payload.get("marketplaceLoadErrors")):
        item = _dict(raw)
        diagnostics.append(
            f"{str(item.get('marketplacePath') or '')[:700]} · "
            f"{str(item.get('message') or raw)[:1200]}"
        )
    return markets, diagnostics


async def _with_client(
    owner_id: str,
    project_id: int | None,
    operation: Callable[[CodexAppServerClient, Path], Awaitable[T]],
) -> T:
    provider = select_codex_provider()
    if provider is None:
        raise CodexCapabilityError(
            "没有可供 Codex 能力控制面使用的 Responses 供应商；请先在供应商管理配置已启用的 Responses 模型"
        )
    runtime = resolve_codex_runtime()
    codex_home = _codex_home(owner_id)
    cwd, _project = _project_cwd(owner_id, project_id)

    async def deny_server_request(method: str, _params: dict[str, Any]) -> Any:
        raise CodexServerRequestDenied(f"FDEX capability control denies server request {method}")

    client = CodexAppServerClient(
        _launch_args(runtime.path, provider),
        env=_safe_process_env(codex_home, provider.api_key),
        cwd=cwd,
        client_version=fresh_settings().app_version,
        request_timeout=30.0,
        server_request_handler=deny_server_request,
        experimental_api=True,
    )
    try:
        async with client:
            return await operation(client, cwd)
    except CodexRpcError as exc:
        raise CodexCapabilityError(str(exc)) from exc


async def capability_inventory(
    owner_id: str,
    *,
    project_id: int | None = None,
    force_reload: bool = False,
) -> dict[str, Any]:
    async def operation(client: CodexAppServerClient, cwd: Path) -> dict[str, Any]:
        cwd_text = str(cwd)
        skills_result = await client.request(
            "skills/list",
            {"cwds": [cwd_text], "forceReload": bool(force_reload)},
            timeout=30.0,
        )
        hooks_result = await client.request("hooks/list", {"cwds": [cwd_text]}, timeout=30.0)
        # Phase 7.30 deliberately limits plugin discovery to local marketplaces and forbids
        # remote catalog refetch so opening the control page cannot create new network egress.
        plugin_result = await client.request(
            "plugin/list",
            {
                "cwds": [cwd_text],
                "marketplaceKinds": ["local"],
                "forceRefetch": False,
            },
            timeout=30.0,
        )
        installed_result = await client.request(
            "plugin/installed",
            {"cwds": [cwd_text], "installSuggestionPluginNames": []},
            timeout=30.0,
        )
        skills, skill_errors = _flatten_skills(skills_result)
        hooks, hook_diagnostics = _flatten_hooks(hooks_result)
        marketplaces, plugin_diagnostics = _flatten_marketplaces(plugin_result)
        installed_marketplaces, installed_diagnostics = _flatten_marketplaces(installed_result)
        return {
            "cwd": cwd_text,
            "skills": skills,
            "skill_errors": skill_errors,
            "hooks": hooks,
            "hook_diagnostics": hook_diagnostics,
            "marketplaces": marketplaces,
            "installed_marketplaces": installed_marketplaces,
            "plugin_diagnostics": plugin_diagnostics + installed_diagnostics,
            "plugin_mutation_allowed": False,
            "plugin_mutation_reason": PLUGIN_MUTATION_BLOCK_REASON,
        }

    inventory = await _with_client(owner_id, project_id, operation)
    _cwd, project = _project_cwd(owner_id, project_id)
    inventory["project"] = project
    return inventory


async def set_skill_enabled(
    owner_id: str,
    *,
    path: str,
    enabled: bool,
    project_id: int | None = None,
) -> dict[str, Any]:
    requested = str(path or "").strip()
    if not requested or not Path(requested).is_absolute():
        raise CodexCapabilityError("Skill path 必须来自官方 skills/list 返回的绝对路径")

    async def operation(client: CodexAppServerClient, cwd: Path) -> dict[str, Any]:
        result = await client.request(
            "skills/list",
            {"cwds": [str(cwd)], "forceReload": True},
            timeout=30.0,
        )
        skills, _errors = _flatten_skills(result)
        matches = [item for item in skills if str(item.get("path") or "") == requested]
        if len(matches) != 1:
            raise CodexCapabilityError(
                "Skill 不在当前账号/项目的官方 skills/list 清单中，已拒绝写入配置"
            )
        await client.request(
            "skills/config/write",
            {"path": requested, "enabled": bool(enabled)},
            timeout=30.0,
        )
        return {**matches[0], "enabled": bool(enabled)}

    return await _with_client(owner_id, project_id, operation)


async def read_local_plugin(
    owner_id: str,
    *,
    marketplace_path: str,
    plugin_name: str,
    project_id: int | None = None,
) -> dict[str, Any]:
    requested_market = str(marketplace_path or "").strip()
    requested_name = str(plugin_name or "").strip()
    if not requested_market or not requested_name:
        raise CodexCapabilityError("Plugin marketplace path 和名称不能为空")

    async def operation(client: CodexAppServerClient, cwd: Path) -> dict[str, Any]:
        result = await client.request(
            "plugin/list",
            {
                "cwds": [str(cwd)],
                "marketplaceKinds": ["local"],
                "forceRefetch": False,
            },
            timeout=30.0,
        )
        marketplaces, _diagnostics = _flatten_marketplaces(result)
        exact = [
            market
            for market in marketplaces
            if market.get("path") == requested_market
            and any(plugin.get("name") == requested_name for plugin in market.get("plugins", []))
        ]
        if len(exact) != 1:
            raise CodexCapabilityError(
                "Plugin 不在当前账号/项目的本地官方 plugin/list 清单中，已拒绝读取"
            )
        detail = await client.request(
            "plugin/read",
            {
                "marketplacePath": requested_market,
                "remoteMarketplaceName": None,
                "pluginName": requested_name,
            },
            timeout=30.0,
        )
        plugin = _dict(_dict(detail).get("plugin"))
        # Keep UI/log payload bounded. Full command-like plugin configuration remains visible only
        # through the official result object and is never executed by this control plane.
        return {
            "name": str(plugin.get("name") or requested_name)[:200],
            "description": str(plugin.get("description") or "")[:5000],
            "path": str(plugin.get("path") or "")[:1200],
            "raw": plugin,
        }

    return await _with_client(owner_id, project_id, operation)


def assert_plugin_mutation_blocked(action: str) -> None:
    clean = str(action or "plugin mutation").strip()[:120]
    raise CodexCapabilityError(f"{clean} 已被 FDEX 安全策略阻止。{PLUGIN_MUTATION_BLOCK_REASON}")
