from __future__ import annotations

from app.agent_runtime import AgentRuntimeError

DYNAMIC_TOOL_ACTIVATION_ALLOWED = False
DYNAMIC_TOOL_POLICY_REASON = (
    "Dynamic Tool 在 Phase 7.30 保持禁用。当前 FDEX bundled fallback rust-v0.147.0 "
    "虽然已有 DynamicToolSpec 协议类型，但其 ThreadStartParams 尚未提供 dynamicTools 字段；"
    "FDEX 不会向旧 Runtime 注入未知 experimental 字段。新 Runtime 的 Dynamic Tool 又会通过 "
    "app-server server request 回调宿主执行工具，因此在 FDEX 建立 owner-scoped Dynamic Tool "
    "registry/executor、明确工具来源与权限，并完成 Phase 7.32 Codex 整个进程树外层隔离前，"
    "thread/start 必须继续省略 dynamicTools。"
)


class DynamicToolPolicyError(AgentRuntimeError):
    pass


def dynamic_tool_policy() -> dict[str, object]:
    return {
        "allowed": DYNAMIC_TOOL_ACTIVATION_ALLOWED,
        "runtime_fallback": "rust-v0.147.0",
        "activation_field": "thread/start.dynamicTools",
        "reason": DYNAMIC_TOOL_POLICY_REASON,
        "requires": [
            "runtime capability detection",
            "owner-scoped dynamic tool registry",
            "explicit per-tool authority",
            "bounded client executor",
            "Phase 7.32 process-tree isolation",
        ],
    }


def assert_dynamic_tool_activation_blocked() -> None:
    raise DynamicToolPolicyError(DYNAMIC_TOOL_POLICY_REASON)
