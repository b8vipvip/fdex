from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any

from app.agent_runtime import AgentRuntimeError
from app.codex_app_server import CodexAppServerClient, CodexRpcError, CodexServerRequestDenied
from app.codex_engine import (
    _PROVIDER_ID,
    _codex_thread_config,
    _launch_args,
    _safe_process_env,
    resolve_codex_runtime,
    select_codex_provider_from,
)
from app.codex_process_isolation import codex_process_isolation_status
from app.codex_provider_compatibility import (
    codex_provider_compatibility_store,
    provider_runtime_fingerprint,
)
from app.config import SERVER_DIR, fresh_settings
from app.provider_manager import provider_store


class CodexProviderSmokeError(AgentRuntimeError):
    pass


def _safe_error(exc: BaseException | str, api_key: str = "") -> str:
    text = str(exc).strip() if not isinstance(exc, BaseException) else (str(exc).strip() or type(exc).__name__)
    if api_key:
        text = text.replace(api_key, "***")
    return text.replace("\r", " ").replace("\n", " ")[:2000]


def _thread_id(result: Any) -> str:
    thread = result.get("thread") if isinstance(result, dict) else None
    value = str(thread.get("id") or "") if isinstance(thread, dict) else ""
    if not value:
        raise CodexProviderSmokeError("Codex thread/start returned no thread id")
    return value


def _turn_id(result: Any) -> str:
    turn = result.get("turn") if isinstance(result, dict) else None
    value = str(turn.get("id") or "") if isinstance(turn, dict) else ""
    if not value:
        raise CodexProviderSmokeError("Codex turn/start returned no turn id")
    return value


async def _run_turn(
    client: CodexAppServerClient,
    thread_id: str,
    prompt: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    payload = {
        "threadId": thread_id,
        "clientUserMessageId": uuid.uuid4().hex,
        "input": [{"type": "text", "text": prompt, "text_elements": []}],
        "approvalPolicy": "never",
    }
    turn_id = _turn_id(await client.request("turn/start", payload, timeout=min(timeout, 60.0)))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(15.0, timeout)
    item_types: list[str] = []
    mcp_tools: list[str] = []
    collab_tools: list[str] = []
    completed_collab_tools: list[str] = []
    subagent_activities: list[str] = []
    final_parts: list[str] = []
    final_item_text = ""
    status = ""
    error = ""

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise CodexProviderSmokeError(f"Codex smoke turn timed out after {timeout:.0f}s")
        try:
            method, params = await client.next_notification(timeout=min(1.0, remaining))
        except CodexRpcError as exc:
            if exc.code == -32002:
                continue
            raise

        event_turn_id = str(params.get("turnId") or "")
        if method in {"item/started", "item/completed"} and event_turn_id in {"", turn_id}:
            item = params.get("item")
            if isinstance(item, dict):
                item_type = str(item.get("type") or "")
                if item_type:
                    item_types.append(item_type)
                if item_type == "mcpToolCall":
                    mcp_tools.append(f"{str(item.get('server') or '')}:{str(item.get('tool') or '')}")
                if item_type == "collabAgentToolCall":
                    tool = str(item.get("tool") or "")
                    if tool:
                        collab_tools.append(tool)
                    if method == "item/completed" and str(item.get("status") or "") == "completed" and tool:
                        completed_collab_tools.append(tool)
                if item_type == "subAgentActivity":
                    subagent_activities.append(str(item.get("kind") or ""))
                if method == "item/completed" and item_type == "agentMessage":
                    text = str(item.get("text") or "").strip()
                    if text:
                        final_item_text = text
        elif method == "item/agentMessage/delta" and event_turn_id in {"", turn_id}:
            delta = str(params.get("delta") or "")
            if delta:
                final_parts.append(delta)
        elif method == "turn/completed":
            turn = params.get("turn")
            if not isinstance(turn, dict) or str(turn.get("id") or "") != turn_id:
                continue
            status = str(turn.get("status") or "")
            raw_error = turn.get("error")
            if isinstance(raw_error, dict):
                error = str(raw_error.get("message") or raw_error)[:1200]
            elif raw_error is not None:
                error = str(raw_error)[:1200]
            break

    if status != "completed":
        raise CodexProviderSmokeError(f"Codex smoke turn {status or 'failed'}: {error or 'no detail'}")
    return {
        "turn_id": turn_id,
        "text": final_item_text or "".join(final_parts).strip(),
        "item_types": item_types,
        "mcp_tools": mcp_tools,
        "collab_tools": collab_tools,
        "completed_collab_tools": completed_collab_tools,
        "subagent_activities": subagent_activities,
    }


def _require_marker(stage: str, result: dict[str, Any], marker: str) -> None:
    if marker not in str(result.get("text") or ""):
        raise CodexProviderSmokeError(f"{stage} turn completed but final response did not contain the required marker")


async def run_codex_provider_smoke(provider_id: int) -> dict[str, Any]:
    """Run a real official app-server Provider smoke without touching a user repository.

    The smoke deliberately uses an isolated scratch workspace, the same sanitized Provider env,
    the same Phase 7.31 CLI governance and the same Phase 7.32 process-tree isolation as production.
    It never commits, pushes, creates a PR, or reuses a user's durable Codex Thread.
    """
    store = provider_store()
    provider = store.get(int(provider_id), include_secret=True)
    runtime = resolve_codex_runtime()
    isolation = codex_process_isolation_status()
    if not bool(isolation.get("enforced")):
        raise CodexProviderSmokeError(
            "Phase 7.32 production process-tree isolation 未生效，拒绝生成 rollout compatibility 证据："
            + str(isolation.get("reason") or "unknown reason")
        )
    spec = select_codex_provider_from([provider])
    if spec is None:
        raise CodexProviderSmokeError(
            "供应商必须配置 Responses 协议、API Key、有效 Base URL 和文本模型后才能执行 Codex smoke"
        )
    fingerprint = provider_runtime_fingerprint(provider, runtime)
    compatibility = codex_provider_compatibility_store()
    started = perf_counter()
    smoke_id = uuid.uuid4().hex
    root = (SERVER_DIR / "data" / "codex-provider-smoke" / smoke_id).resolve()
    workspace = root / "workspace"
    codex_home = root / "codex-home"
    workspace.mkdir(parents=True, exist_ok=False)
    codex_home.mkdir(parents=True, exist_ok=False)
    for path in (root, workspace, codex_home):
        try:
            path.chmod(0o700)
        except OSError:
            pass

    marker_root = f"FDEX_CODEX_SMOKE_{smoke_id[:16]}"
    mcp_marker = marker_root + "_MCP"
    capability = compatibility.issue_smoke_capability(mcp_marker, lifetime_seconds=900)
    level = "none"
    error = ""
    evidence: dict[str, Any] = {
        "wire": False,
        "tools": False,
        "mcp": False,
        "subagent": False,
        "reasoning": False,
        "runtime": runtime.version,
        "process_isolation": True,
    }

    async def deny_server_request(method: str, _params: dict[str, Any]) -> Any:
        raise CodexServerRequestDenied(f"Provider smoke denies interactive server request {method}")

    settings = fresh_settings()
    stage_timeout = max(45.0, min(180.0, float(provider.get("timeout_seconds") or 60) * 2.0))
    config = _codex_thread_config(codex_home, allow_network=False)
    config["model_reasoning_effort"] = "medium"
    config["model_reasoning_summary"] = "auto"
    config["mcp_servers"] = {
        "fdex_smoke": {
            "url": f"http://127.0.0.1:{int(settings.fdex_port)}/internal/codex-provider-smoke-mcp/{capability}",
            "enabled": True,
            "required": True,
            "startup_timeout_sec": 20,
            "tool_timeout_sec": 30,
            "enabled_tools": ["fdex_smoke_echo"],
            "default_tools_approval_mode": "approve",
        }
    }
    client = CodexAppServerClient(
        _launch_args(runtime.path, spec),
        env=_safe_process_env(codex_home, spec.api_key),
        cwd=workspace,
        client_version=settings.app_version,
        request_timeout=max(30.0, min(stage_timeout, 120.0)),
        server_request_handler=deny_server_request,
        experimental_api=True,
    )

    try:
        async with client:
            thread = await client.request(
                "thread/start",
                {
                    "model": spec.model,
                    "modelProvider": _PROVIDER_ID,
                    "cwd": str(workspace),
                    "approvalPolicy": "never",
                    "sandbox": "workspace-write",
                    "config": config,
                    "developerInstructions": (
                        "This is an FDEX Provider compatibility smoke in an empty scratch workspace. "
                        "Follow each stage literally. Never inspect paths outside the current workspace, "
                        "never access credentials, and never use network tools except the explicitly configured "
                        "fdex_smoke MCP server when the prompt asks for it."
                    ),
                    "ephemeral": True,
                },
                timeout=45.0,
            )
            thread_id = _thread_id(thread)

            wire_marker = marker_root + "_WIRE"
            wire = await _run_turn(
                client,
                thread_id,
                (
                    "Reason briefly about this instruction, but do not use shell, MCP, or collaboration tools. "
                    f"Then reply with this exact marker and no other requirement: {wire_marker}"
                ),
                timeout=stage_timeout,
            )
            _require_marker("wire", wire, wire_marker)
            evidence["wire"] = True
            evidence["reasoning"] = "reasoning" in set(wire.get("item_types") or [])
            evidence["wire_item_types"] = sorted(set(wire.get("item_types") or []))
            level = "wire"

            tool_marker = marker_root + "_TOOLS"
            tool_file = workspace / "fdex_codex_provider_smoke.txt"
            tools = await _run_turn(
                client,
                thread_id,
                (
                    "Use a shell command in the current workspace to create fdex_codex_provider_smoke.txt "
                    f"with exactly this text followed by a newline: {tool_marker}. Then use a shell command to "
                    f"read that file and finally reply with {tool_marker}. Do not use MCP or sub-agents in this stage."
                ),
                timeout=stage_timeout,
            )
            _require_marker("tools", tools, tool_marker)
            if not tool_file.is_file() or tool_file.read_text(encoding="utf-8").strip() != tool_marker:
                raise CodexProviderSmokeError("tool turn did not create the required scratch file with exact content")
            tool_types = set(tools.get("item_types") or [])
            if not ({"commandExecution", "fileChange"} & tool_types):
                raise CodexProviderSmokeError("tool turn completed without commandExecution/fileChange evidence")
            evidence["tools"] = True
            evidence["tool_item_types"] = sorted(tool_types)
            evidence["reasoning"] = bool(evidence["reasoning"] or "reasoning" in tool_types)
            level = "tools"

            mcp = await _run_turn(
                client,
                thread_id,
                (
                    "You must call the MCP tool fdex_smoke_echo from server fdex_smoke exactly once with JSON "
                    f'{{"marker":"{mcp_marker}"}}. Use the returned tool text as evidence, then reply with '
                    f"{mcp_marker}. Do not use shell commands or sub-agents in this stage."
                ),
                timeout=stage_timeout,
            )
            _require_marker("MCP", mcp, mcp_marker)
            cap_state = compatibility.smoke_capability(capability)
            if cap_state is None or int(cap_state.get("call_count") or 0) < 1:
                raise CodexProviderSmokeError("MCP turn completed without a call reaching the FDEX smoke capability")
            if str(cap_state.get("last_argument") or "") != mcp_marker:
                raise CodexProviderSmokeError("MCP smoke tool was called with the wrong marker")
            mcp_types = set(mcp.get("item_types") or [])
            if "mcpToolCall" not in mcp_types:
                raise CodexProviderSmokeError("MCP turn has no official mcpToolCall Item evidence")
            evidence["mcp"] = True
            evidence["mcp_tools"] = list(mcp.get("mcp_tools") or [])[:20]
            evidence["reasoning"] = bool(evidence["reasoning"] or "reasoning" in mcp_types)

            agent_marker = marker_root + "_SUBAGENT"
            subagent = await _run_turn(
                client,
                thread_id,
                (
                    "Use the official collaboration spawnAgent tool to delegate one read-only reasoning subtask. "
                    f"Tell the child to return exactly {agent_marker}. You must then call the official wait tool "
                    "and wait for child activity/completion before producing your final response. After waiting, "
                    f"reply with {agent_marker}. Do not use shell or MCP in this stage."
                ),
                timeout=stage_timeout,
            )
            _require_marker("sub-agent", subagent, agent_marker)
            completed_collab = list(subagent.get("completed_collab_tools") or [])
            if "spawnAgent" not in completed_collab:
                raise CodexProviderSmokeError(
                    "sub-agent turn has no completed official collabAgentToolCall spawnAgent evidence"
                )
            if "wait" not in completed_collab:
                raise CodexProviderSmokeError(
                    "sub-agent turn spawned a child but has no completed official wait collaboration evidence"
                )
            sub_types = set(subagent.get("item_types") or [])
            if "collabAgentToolCall" not in sub_types:
                raise CodexProviderSmokeError("sub-agent turn has no collabAgentToolCall Item evidence")
            if "subAgentActivity" not in sub_types:
                raise CodexProviderSmokeError(
                    "sub-agent spawn completed without the official subAgentActivity lifecycle Item"
                )
            evidence["subagent"] = True
            evidence["collab_tools"] = completed_collab[:20]
            evidence["subagent_activities"] = list(subagent.get("subagent_activities") or [])[:20]
            evidence["reasoning"] = bool(evidence["reasoning"] or "reasoning" in sub_types)
            if not bool(evidence["reasoning"]):
                raise CodexProviderSmokeError(
                    "full smoke completed tools/MCP/sub-agent stages but no official reasoning Item was observed"
                )
            level = "full"
    except Exception as exc:
        error = _safe_error(exc, spec.api_key)
    finally:
        compatibility.revoke_smoke_capability(capability)
        try:
            shutil.rmtree(root)
        except OSError:
            pass

    latency_ms = int((perf_counter() - started) * 1000)
    record = compatibility.record(
        spec.provider_id,
        fingerprint=fingerprint,
        level=level,
        runtime_version=runtime.version,
        runtime_source=runtime.source,
        model=spec.model,
        base_url=spec.base_url,
        latency_ms=latency_ms,
        evidence=evidence,
        error=error,
    )
    return {
        "ok": level == "full" and not error,
        "level": level,
        "provider_id": spec.provider_id,
        "provider_name": spec.name,
        "model": spec.model,
        "runtime_version": runtime.version,
        "runtime_source": runtime.source,
        "latency_ms": latency_ms,
        "evidence": evidence,
        "error": error,
        "record": record,
    }
