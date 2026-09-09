from __future__ import annotations

import re
from functools import wraps
from typing import Any, Callable
from urllib.parse import quote

from app import plugin_code_hosts, plugin_mcp_gateway


_MAX_TREE_ENTRIES = 4000
_WORKFLOW_BRANCH_RE = re.compile(r"^fdex/[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_installed = False


def _tree_path(value: str) -> str:
    clean = (value or "").strip().strip("/")
    if not clean:
        return ""
    if len(clean) > 1000 or "\x00" in clean:
        raise ValueError("目录路径无效")
    if any(part in {"", ".", ".."} for part in clean.split("/")):
        raise ValueError("目录路径无效")
    return clean


def _workflow_branch(value: str) -> str:
    clean = plugin_code_hosts._branch(value)
    if not _WORKFLOW_BRANCH_RE.fullmatch(clean):
        raise ValueError("Coding Agent 工作分支必须使用 fdex/ 前缀且只包含安全 Git 分支字符")
    if ".." in clean or "//" in clean or "@{" in clean or clean.endswith(("/", ".")):
        raise ValueError("Coding Agent 工作分支名称无效")
    if any(part.startswith(".") or part.endswith(".") for part in clean.split("/")):
        raise ValueError("Coding Agent 工作分支名称无效")
    return clean


def _normalize_tree_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
    path = str(raw.get("path") or "").strip().strip("/")
    if not path:
        return None
    provider_type = str(raw.get("type") or "").strip().lower()
    item_type = "tree" if provider_type in {"tree", "dir", "directory"} else "file" if provider_type in {"blob", "file"} else provider_type
    size_raw = raw.get("size")
    try:
        size = int(size_raw) if size_raw is not None and str(size_raw).strip() else None
    except (TypeError, ValueError):
        size = None
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "type": item_type,
        "sha": str(raw.get("id") or raw.get("sha") or "")[:160],
        "mode": str(raw.get("mode") or "")[:32],
        "size": size,
    }


def list_code_host_tree(
    owner_id: str,
    plugin_id: str,
    repo_full_name: str,
    *,
    path: str = "",
    ref: str = "",
    recursive: bool = False,
) -> dict[str, Any]:
    """List a bounded repository tree without exposing provider credentials."""
    connection = plugin_code_hosts._connection(owner_id, plugin_id)
    clean_plugin = plugin_code_hosts._plugin(plugin_id)
    clean_repo = plugin_code_hosts._repo(clean_plugin, repo_full_name)
    clean_path = _tree_path(path)
    clean_ref = plugin_code_hosts._branch(ref or "HEAD")
    base_url = str(connection["base_url"])
    token = str(connection["token"])
    entries: list[dict[str, Any]] = []
    truncated = False

    if clean_plugin == "gitlab":
        endpoint = f"/projects/{quote(clean_repo, safe='')}/repository/tree"
        for page in range(1, 41):
            payload = plugin_code_hosts._json(
                plugin_code_hosts._request(
                    clean_plugin,
                    base_url,
                    token,
                    "GET",
                    endpoint,
                    params={
                        "ref": clean_ref,
                        "path": clean_path,
                        "recursive": "true" if recursive else "false",
                        "per_page": 100,
                        "page": page,
                    },
                )
            )
            if not isinstance(payload, list):
                raise plugin_code_hosts.CodeHostPluginError("GitLab 目录树返回格式无效")
            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                item = _normalize_tree_entry(raw)
                if item is not None:
                    entries.append(item)
                if len(entries) >= _MAX_TREE_ENTRIES:
                    truncated = True
                    break
            if truncated or len(payload) < 100:
                break
            if page == 40:
                truncated = True
    else:
        owner_name, repo_name = clean_repo.split("/", 1)
        endpoint = (
            f"/repos/{quote(owner_name, safe='')}/{quote(repo_name, safe='')}/git/trees/"
            f"{quote(clean_ref, safe='')}"
        )
        payload = plugin_code_hosts._json(
            plugin_code_hosts._request(
                clean_plugin,
                base_url,
                token,
                "GET",
                endpoint,
                params={"recursive": 1},
            )
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
            raise plugin_code_hosts.CodeHostPluginError("Gitee 目录树返回格式无效")
        prefix = f"{clean_path}/" if clean_path else ""
        for raw in payload["tree"]:
            if not isinstance(raw, dict):
                continue
            item = _normalize_tree_entry(raw)
            if item is None:
                continue
            item_path = str(item["path"])
            if clean_path:
                if not item_path.startswith(prefix):
                    continue
                relative = item_path[len(prefix):]
            else:
                relative = item_path
            if not relative or (not recursive and "/" in relative):
                continue
            entries.append(item)
            if len(entries) >= _MAX_TREE_ENTRIES:
                truncated = True
                break
        truncated = truncated or bool(payload.get("truncated"))

    return {
        "plugin_id": clean_plugin,
        "repository": clean_repo,
        "ref": clean_ref,
        "path": clean_path,
        "recursive": bool(recursive),
        "count": len(entries),
        "truncated": truncated,
        "entries": entries,
    }


def create_code_host_branch(
    owner_id: str,
    plugin_id: str,
    repo_full_name: str,
    *,
    branch: str,
    source_ref: str,
) -> dict[str, Any]:
    """Create one isolated fdex/* branch from an existing branch/tag/commit."""
    connection = plugin_code_hosts._connection(owner_id, plugin_id)
    clean_plugin = plugin_code_hosts._plugin(plugin_id)
    clean_repo = plugin_code_hosts._repo(clean_plugin, repo_full_name)
    clean_branch = _workflow_branch(branch)
    clean_source = plugin_code_hosts._branch(source_ref)
    if clean_source == clean_branch:
        raise ValueError("工作分支不能从自身创建")
    base_url = str(connection["base_url"])
    token = str(connection["token"])

    if clean_plugin == "gitlab":
        endpoint = f"/projects/{quote(clean_repo, safe='')}/repository/branches"
        payload = plugin_code_hosts._json(
            plugin_code_hosts._request(
                clean_plugin,
                base_url,
                token,
                "POST",
                endpoint,
                json_body={"branch": clean_branch, "ref": clean_source},
            )
        )
    else:
        owner_name, repo_name = clean_repo.split("/", 1)
        endpoint = f"/repos/{quote(owner_name, safe='')}/{quote(repo_name, safe='')}/branches"
        payload = plugin_code_hosts._json(
            plugin_code_hosts._request(
                clean_plugin,
                base_url,
                token,
                "POST",
                endpoint,
                json_body={"refs": clean_source, "branch_name": clean_branch},
            )
        )
    if not isinstance(payload, dict):
        raise plugin_code_hosts.CodeHostPluginError("创建工作分支返回格式无效")
    commit = payload.get("commit") if isinstance(payload.get("commit"), dict) else {}
    return {
        "plugin_id": clean_plugin,
        "repository": clean_repo,
        "branch": str(payload.get("name") or payload.get("branch_name") or clean_branch)[:180],
        "source_ref": clean_source,
        "commit_sha": str(commit.get("id") or commit.get("sha") or payload.get("sha") or "")[:160],
        "protected": bool(payload.get("protected")),
        "web_url": str(payload.get("web_url") or payload.get("html_url") or "")[:800],
    }


def workflow_write_code_host_file(
    owner_id: str,
    plugin_id: str,
    repo_full_name: str,
    path: str,
    content: str,
    *,
    branch: str,
    message: str,
) -> dict[str, Any]:
    """Fail closed against direct default/protected-branch style writes from Coding Agent MCP."""
    clean_branch = _workflow_branch(branch)
    return plugin_code_hosts.write_code_host_file(
        owner_id,
        plugin_id,
        repo_full_name,
        path,
        content,
        branch=clean_branch,
        message=message,
    )


def workflow_create_code_host_pull_request(
    owner_id: str,
    plugin_id: str,
    repo_full_name: str,
    *,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str = "",
) -> dict[str, Any]:
    source = _workflow_branch(source_branch)
    target = plugin_code_hosts._branch(target_branch)
    if source == target:
        raise ValueError("PR/MR 源分支和目标分支不能相同")
    if target.startswith("fdex/"):
        raise ValueError("PR/MR 目标分支必须是非 fdex/ 工作分支")
    return plugin_code_hosts.create_code_host_pull_request(
        owner_id,
        plugin_id,
        repo_full_name,
        source_branch=source,
        target_branch=target,
        title=title,
        description=description,
    )


def _tool_definitions() -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for plugin_id, repo_label in (("gitlab", "namespace/project"), ("gitee", "owner/repo")):
        definitions[f"{plugin_id}_list_tree"] = {
            "plugin_id": plugin_id,
            "runtime_tool": f"{plugin_id}.repository.read",
            "risk": "read",
            "description": "List a bounded repository directory tree before reading or editing files.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repository": {"type": "string", "description": repo_label},
                    "path": {"type": "string", "description": "optional directory path; defaults to repository root"},
                    "ref": {"type": "string", "description": "branch/tag/commit; defaults to HEAD"},
                    "recursive": {"type": "boolean", "description": "include descendants; defaults to false"},
                },
                "required": ["repository"],
                "additionalProperties": False,
            },
        }
        definitions[f"{plugin_id}_create_branch"] = {
            "plugin_id": plugin_id,
            "runtime_tool": f"{plugin_id}.repository.write",
            "risk": "write",
            "description": "Create an isolated fdex/* work branch from an existing branch/tag/commit before editing.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repository": {"type": "string", "description": repo_label},
                    "branch": {"type": "string", "description": "new branch name; must start with fdex/"},
                    "source_ref": {"type": "string", "description": "existing source branch/tag/commit"},
                },
                "required": ["repository", "branch", "source_ref"],
                "additionalProperties": False,
            },
        }
    return definitions


def install_code_host_workflow_tools() -> None:
    """Extend the Phase 7.43 Plugin MCP gateway without duplicating its lease/auth/audit path."""
    global _installed
    if _installed:
        return

    plugin_mcp_gateway._TOOL_DEFINITIONS.update(_tool_definitions())
    for tool_name in ("gitlab_write_file", "gitee_write_file"):
        spec = plugin_mcp_gateway._TOOL_DEFINITIONS[tool_name]
        spec["description"] = "Create or update one UTF-8 file on an existing isolated fdex/* work branch."
        spec["inputSchema"]["properties"]["branch"]["description"] = "existing fdex/* work branch"
    for tool_name in ("gitlab_create_merge_request", "gitee_create_pull_request"):
        spec = plugin_mcp_gateway._TOOL_DEFINITIONS[tool_name]
        spec["description"] = "Open MR/PR from an fdex/* work branch into a non-fdex target branch."
        spec["inputSchema"]["properties"]["source_branch"]["description"] = "existing fdex/* work branch"

    original_executor = plugin_mcp_gateway._tool_executor
    original_safe_result = plugin_mcp_gateway._safe_result

    @wraps(original_executor)
    def workflow_executor(owner_id: str, tool_name: str, arguments: dict[str, Any]) -> Callable[[], Any]:
        if tool_name in {"gitlab_list_tree", "gitee_list_tree"}:
            plugin_id = str(plugin_mcp_gateway._TOOL_DEFINITIONS[tool_name]["plugin_id"])
            repository = plugin_mcp_gateway._arg(arguments, "repository", limit=400)
            path = plugin_mcp_gateway._arg(arguments, "path", required=False, limit=1000)
            ref = plugin_mcp_gateway._arg(arguments, "ref", required=False, limit=180)
            recursive_raw = arguments.get("recursive", False)
            if not isinstance(recursive_raw, bool):
                raise ValueError("参数 recursive 必须是布尔值")
            return lambda: list_code_host_tree(
                owner_id,
                plugin_id,
                repository,
                path=path,
                ref=ref,
                recursive=recursive_raw,
            )
        if tool_name in {"gitlab_create_branch", "gitee_create_branch"}:
            plugin_id = str(plugin_mcp_gateway._TOOL_DEFINITIONS[tool_name]["plugin_id"])
            repository = plugin_mcp_gateway._arg(arguments, "repository", limit=400)
            branch = plugin_mcp_gateway._arg(arguments, "branch", limit=180)
            source_ref = plugin_mcp_gateway._arg(arguments, "source_ref", limit=180)
            return lambda: create_code_host_branch(
                owner_id,
                plugin_id,
                repository,
                branch=branch,
                source_ref=source_ref,
            )
        if tool_name in {"gitlab_write_file", "gitee_write_file"}:
            plugin_id = str(plugin_mcp_gateway._TOOL_DEFINITIONS[tool_name]["plugin_id"])
            repository = plugin_mcp_gateway._arg(arguments, "repository", limit=400)
            path = plugin_mcp_gateway._arg(arguments, "path", limit=1000)
            content = plugin_mcp_gateway._arg(arguments, "content", required=False, limit=2 * 1024 * 1024)
            branch = plugin_mcp_gateway._arg(arguments, "branch", limit=180)
            message = plugin_mcp_gateway._arg(arguments, "message", limit=500)
            return lambda: workflow_write_code_host_file(
                owner_id,
                plugin_id,
                repository,
                path,
                content,
                branch=branch,
                message=message,
            )
        if tool_name in {"gitlab_create_merge_request", "gitee_create_pull_request"}:
            plugin_id = str(plugin_mcp_gateway._TOOL_DEFINITIONS[tool_name]["plugin_id"])
            repository = plugin_mcp_gateway._arg(arguments, "repository", limit=400)
            source = plugin_mcp_gateway._arg(arguments, "source_branch", limit=180)
            target = plugin_mcp_gateway._arg(arguments, "target_branch", limit=180)
            title = plugin_mcp_gateway._arg(arguments, "title", limit=300)
            description = plugin_mcp_gateway._arg(arguments, "description", required=False, limit=10000)
            return lambda: workflow_create_code_host_pull_request(
                owner_id,
                plugin_id,
                repository,
                source_branch=source,
                target_branch=target,
                title=title,
                description=description,
            )
        return original_executor(owner_id, tool_name, arguments)

    @wraps(original_safe_result)
    def workflow_safe_result(value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("entries"), list):
            return {
                "plugin_id": value.get("plugin_id"),
                "repository": value.get("repository"),
                "ref": value.get("ref"),
                "path": value.get("path"),
                "recursive": bool(value.get("recursive")),
                "count": int(value.get("count") or 0),
                "truncated": bool(value.get("truncated")),
                "entries": value.get("entries")[:_MAX_TREE_ENTRIES],
            }
        if isinstance(value, dict) and "source_ref" in value and "branch" in value:
            return {
                key: value.get(key)
                for key in ("plugin_id", "repository", "branch", "source_ref", "commit_sha", "protected", "web_url")
            }
        return original_safe_result(value)

    plugin_mcp_gateway._tool_executor = workflow_executor
    plugin_mcp_gateway._safe_result = workflow_safe_result
    _installed = True
