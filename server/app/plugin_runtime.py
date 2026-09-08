from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.agent_projects import agent_project_store
from app.web_workspace import web_workspace_store


RISK_ORDER = {"read": 1, "write": 2, "dangerous": 3}
VALID_GRANT_MODES = {"none", "read", "write"}


@dataclass(frozen=True)
class PluginTool:
    name: str
    risk: str
    description: str


@dataclass(frozen=True)
class PluginDefinition:
    id: str
    name: str
    category: str
    description: str
    connect_path: str
    implementation: str
    tools: tuple[PluginTool, ...]


CATALOG: tuple[PluginDefinition, ...] = (
    PluginDefinition(
        "github", "GitHub", "代码仓库", "仓库、代码、Issue、PR 与 Actions。现有 FDEX GitHub App 已迁入统一插件层。",
        "/account/github", "native",
        (
            PluginTool("github.installation.repositories", "read", "读取当前 GitHub App 安装范围内的仓库元数据"),
            PluginTool("github.repository.read", "read", "读取仓库文件和提交信息"),
            PluginTool("github.repository.write", "write", "修改仓库文件或提交代码"),
            PluginTool("github.pull_request.write", "write", "创建或更新 Pull Request"),
            PluginTool("github.repository.delete", "dangerous", "删除或执行高风险仓库操作"),
        ),
    ),
    PluginDefinition("gitlab", "GitLab", "代码仓库", "项目、代码、Issue、Merge Request 与 CI/CD。", "", "roadmap", (
        PluginTool("gitlab.repository.read", "read", "读取项目与代码"),
        PluginTool("gitlab.repository.write", "write", "写入项目代码"),
        PluginTool("gitlab.merge_request.write", "write", "创建或更新 Merge Request"),
    )),
    PluginDefinition("gitee", "Gitee", "代码仓库", "国内代码仓库、Issue、Pull Request 与流水线。", "", "roadmap", (
        PluginTool("gitee.repository.read", "read", "读取仓库与代码"),
        PluginTool("gitee.repository.write", "write", "写入仓库代码"),
        PluginTool("gitee.pull_request.write", "write", "创建或更新 Pull Request"),
    )),
    PluginDefinition("feishu", "飞书", "沟通与知识", "群消息、文档、多维表格与任务回写。", "", "roadmap", (
        PluginTool("feishu.message.read", "read", "读取授权范围内消息"),
        PluginTool("feishu.document.read", "read", "读取文档"),
        PluginTool("feishu.message.send", "write", "发送消息"),
        PluginTool("feishu.document.write", "write", "创建或更新文档"),
    )),
    PluginDefinition("notion", "Notion", "沟通与知识", "页面、数据库、知识检索与内容维护。", "", "roadmap", (
        PluginTool("notion.page.search", "read", "搜索页面"),
        PluginTool("notion.page.read", "read", "读取页面"),
        PluginTool("notion.page.write", "write", "创建或更新页面"),
    )),
    PluginDefinition("google-drive", "Google Drive", "沟通与知识", "Drive、Docs、Sheets 与 Slides 文档知识源。", "", "roadmap", (
        PluginTool("drive.file.search", "read", "搜索 Drive 文件"),
        PluginTool("drive.file.read", "read", "读取文件"),
        PluginTool("drive.file.write", "write", "创建或更新文件"),
    )),
    PluginDefinition("linear", "Linear", "项目管理", "Issue、Project、Initiative 与研发任务状态回写。", "", "roadmap", (
        PluginTool("linear.issue.read", "read", "读取 Issue"),
        PluginTool("linear.issue.write", "write", "创建或更新 Issue"),
    )),
    PluginDefinition("jira", "Jira", "项目管理", "Issue、项目、状态流转与开发协作。", "", "roadmap", (
        PluginTool("jira.issue.read", "read", "读取 Issue"),
        PluginTool("jira.issue.write", "write", "创建或更新 Issue"),
    )),
    PluginDefinition("vercel", "Vercel", "部署与运维", "部署、构建状态、项目和环境。", "", "roadmap", (
        PluginTool("vercel.deployment.read", "read", "读取部署状态"),
        PluginTool("vercel.deployment.create", "write", "创建部署"),
        PluginTool("vercel.production.promote", "dangerous", "提升到生产环境"),
    )),
    PluginDefinition("cloudflare", "Cloudflare", "部署与运维", "DNS、Workers、Pages、域名和边缘配置。", "", "roadmap", (
        PluginTool("cloudflare.zone.read", "read", "读取域名和 DNS 状态"),
        PluginTool("cloudflare.zone.write", "write", "修改 DNS 或 Worker 配置"),
        PluginTool("cloudflare.zone.delete", "dangerous", "执行高风险删除操作"),
    )),
    PluginDefinition("ssh", "SSH", "部署与运维", "服务器文件、命令、systemd 与日志。", "", "roadmap", (
        PluginTool("ssh.file.read", "read", "读取服务器文件"),
        PluginTool("ssh.command.read", "read", "执行只读诊断命令"),
        PluginTool("ssh.file.write", "write", "修改服务器文件"),
        PluginTool("ssh.command.write", "write", "执行变更命令"),
        PluginTool("ssh.command.dangerous", "dangerous", "执行高风险系统命令"),
    )),
    PluginDefinition("docker", "Docker", "部署与运维", "容器、Compose、镜像、日志和生命周期。", "", "roadmap", (
        PluginTool("docker.inspect", "read", "查看容器和镜像状态"),
        PluginTool("docker.logs", "read", "读取容器日志"),
        PluginTool("docker.compose.apply", "write", "应用 Compose 变更"),
        PluginTool("docker.remove", "dangerous", "删除容器、镜像或卷"),
    )),
    PluginDefinition("sentry", "Sentry", "可观测性", "错误、Issue、Release 与 Trace。", "", "roadmap", (
        PluginTool("sentry.issue.read", "read", "读取错误与 Issue"),
        PluginTool("sentry.issue.update", "write", "更新 Issue 状态"),
    )),
    PluginDefinition("datadog", "Datadog", "可观测性", "Logs、Metrics、Trace、Monitor 与事件。", "", "roadmap", (
        PluginTool("datadog.telemetry.read", "read", "读取日志、指标和 Trace"),
        PluginTool("datadog.monitor.write", "write", "创建或更新 Monitor"),
    )),
    PluginDefinition("grafana", "Grafana", "可观测性", "Dashboard、Prometheus/Loki 数据与告警。", "", "roadmap", (
        PluginTool("grafana.query.read", "read", "查询指标和日志"),
        PluginTool("grafana.dashboard.write", "write", "创建或修改 Dashboard"),
    )),
)


CATALOG_BY_ID = {item.id: item for item in CATALOG}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def plugin_definition(plugin_id: str) -> PluginDefinition:
    clean = (plugin_id or "").strip().lower()
    if clean not in CATALOG_BY_ID:
        raise ValueError("未知插件")
    return CATALOG_BY_ID[clean]


def _github_connection_status(owner_id: str) -> dict[str, Any]:
    try:
        store = agent_project_store()
        connections = [
            item for item in store.list_connections(owner_id)
            if str(item.get("auth_type") or "") == "github_app" and not bool(item.get("needs_reconnect"))
        ]
    except (KeyError, ValueError, RuntimeError, OSError):
        connections = []
    accounts = [str(item.get("login") or item.get("name") or "").strip() for item in connections]
    return {
        "connected": bool(connections),
        "connection_count": len(connections),
        "account_label": "、".join(item for item in accounts if item)[:240],
    }


def plugin_connection_status(owner_id: str, plugin_id: str) -> dict[str, Any]:
    definition = plugin_definition(plugin_id)
    if definition.id == "github":
        status = _github_connection_status(owner_id)
        return {**status, "connectable": True, "state": "connected" if status["connected"] else "available"}
    # Connection credentials for later adapters must live in their dedicated encrypted connector stores,
    # not in the generic Web workspace records. The catalog is intentionally visible before those adapters
    # arrive so UI, grants, audit and Tool Router contracts stay stable.
    return {"connected": False, "connection_count": 0, "account_label": "", "connectable": False, "state": "roadmap"}


def catalog_snapshot(owner_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in CATALOG:
        status = plugin_connection_status(owner_id, item.id)
        rows.append({
            **asdict(item),
            **status,
            "tools": [asdict(tool) for tool in item.tools],
        })
    return rows


def _grant_records(owner_id: str, employee_id: int) -> list[dict[str, Any]]:
    return web_workspace_store().list(owner_id, "plugin_grant", parent_id=int(employee_id), include_deleted=True, limit=500)


def explicit_agent_grant(owner_id: str, employee_id: int, plugin_id: str) -> dict[str, Any] | None:
    clean = plugin_definition(plugin_id).id
    return next((row for row in _grant_records(owner_id, employee_id) if str(row.get("plugin_id") or "") == clean), None)


def effective_agent_grant(owner_id: str, employee: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    definition = plugin_definition(plugin_id)
    explicit = explicit_agent_grant(owner_id, int(employee["id"]), definition.id)
    if explicit is not None:
        mode = str(explicit.get("mode") or "none")
        source = "explicit"
    elif definition.id == "github":
        # Backward compatibility: before the Plugin Runtime existed, every active 智体 could use
        # deterministic GitHub reads and Coding Agent 智体 could reach existing project write paths.
        # Keep that behavior until the user explicitly changes this 智体's plugin grant.
        mode = "write" if bool(employee.get("coding_agent")) else "read"
        source = "legacy-default"
    else:
        mode = "none"
        source = "default"
    if mode not in VALID_GRANT_MODES:
        mode = "none"
    return {"plugin_id": definition.id, "mode": mode, "source": source}


def save_agent_grant(owner_id: str, employee_id: int, plugin_id: str, mode: str) -> dict[str, Any]:
    definition = plugin_definition(plugin_id)
    clean_mode = (mode or "").strip().lower()
    if clean_mode not in VALID_GRANT_MODES:
        raise ValueError("插件权限只能是 none/read/write")
    store = web_workspace_store()
    employee = store.get(owner_id, "employee", int(employee_id), include_deleted=True)
    existing = explicit_agent_grant(owner_id, int(employee_id), definition.id)
    payload = {
        "employee_id": int(employee_id),
        "plugin_id": definition.id,
        "mode": clean_mode,
        "employee_name": str(employee.get("name") or "")[:80],
        "updated_at": _now(),
    }
    if existing is None:
        return store.create(owner_id, "plugin_grant", payload, parent_id=int(employee_id), sort_key=f"{definition.id}:{employee_id}")
    return store.upsert(
        owner_id,
        "plugin_grant",
        int(existing["id"]),
        payload,
        parent_id=int(employee_id),
        sort_key=f"{definition.id}:{employee_id}",
        deleted=False,
    )


def _tool(plugin_id: str, tool_name: str) -> PluginTool:
    definition = plugin_definition(plugin_id)
    match = next((tool for tool in definition.tools if tool.name == tool_name), None)
    if match is None:
        raise ValueError("插件未声明该 Tool")
    return match


def authorize_plugin_tool(
    owner_id: str,
    employee: dict[str, Any],
    plugin_id: str,
    tool_name: str,
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    definition = plugin_definition(plugin_id)
    tool = _tool(definition.id, tool_name)
    status = plugin_connection_status(owner_id, definition.id)
    if not status.get("connected"):
        raise PermissionError(f"{definition.name} 插件尚未连接")
    grant = effective_agent_grant(owner_id, employee, definition.id)
    mode = str(grant.get("mode") or "none")
    max_risk = {"none": 0, "read": 1, "write": 2}.get(mode, 0)
    needed = RISK_ORDER[tool.risk]
    if tool.risk == "dangerous":
        if mode != "write":
            raise PermissionError(f"智体没有 {definition.name} 写权限")
        if not confirmed:
            raise PermissionError("高风险插件操作必须由用户本次明确确认")
    elif max_risk < needed:
        raise PermissionError(f"智体没有执行 {tool.name} 所需的 {tool.risk} 权限")
    return {"plugin": definition, "tool": tool, "grant": grant, "connection": status}


def audit_plugin_action(
    owner_id: str,
    employee: dict[str, Any],
    plugin_id: str,
    tool_name: str,
    *,
    status: str,
    summary: str = "",
    risk: str = "",
) -> dict[str, Any]:
    definition = plugin_definition(plugin_id)
    tool = _tool(definition.id, tool_name)
    return web_workspace_store().create(
        owner_id,
        "plugin_audit",
        {
            "employee_id": int(employee.get("id") or 0),
            "employee_name": str(employee.get("name") or "")[:80],
            "plugin_id": definition.id,
            "plugin_name": definition.name,
            "tool": tool.name,
            "risk": risk or tool.risk,
            "status": (status or "unknown")[:40],
            "summary": (summary or "")[:1000],
            "created_at": _now(),
        },
        parent_id=int(employee.get("id") or 0) or None,
        sort_key=_now(),
    )


def run_plugin_tool(
    owner_id: str,
    employee: dict[str, Any],
    plugin_id: str,
    tool_name: str,
    executor: Callable[[], Any],
    *,
    confirmed: bool = False,
) -> Any:
    decision = authorize_plugin_tool(owner_id, employee, plugin_id, tool_name, confirmed=confirmed)
    try:
        result = executor()
    except Exception as exc:
        audit_plugin_action(
            owner_id, employee, plugin_id, tool_name,
            status="failed", summary=f"{type(exc).__name__}: {str(exc)[:700]}", risk=decision["tool"].risk,
        )
        raise
    audit_plugin_action(
        owner_id, employee, plugin_id, tool_name,
        status="completed", summary="Tool 执行完成", risk=decision["tool"].risk,
    )
    return result


def plugin_audit_rows(owner_id: str, limit: int = 100) -> list[dict[str, Any]]:
    return web_workspace_store().list(owner_id, "plugin_audit", newest_first=True, limit=limit)
