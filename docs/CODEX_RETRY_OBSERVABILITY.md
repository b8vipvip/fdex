# Codex Retry Chain Observability / Codex 重试链可观测性

## 中文

Phase 7.39 是 Phase 7.38 有界自动恢复之上的**投影与可观测层**。它不新增 Agent 引擎、不改变重试资格、不扩大权限，也不改变 Provider 切换边界。

核心区分：

```text
Logical Task / logical root
    用户创建、取消、查看最终结果的稳定任务身份

Execution Attempt
    实际运行一次 Codex Host/Turn 的物理 AgentTask
    Attempt #0 = 原始执行
    Attempt #1/#2 = Phase 7.38 内部自动重试
```

### 为什么需要这一层

Phase 7.38 正确地把每次自动 replay 建成新的 AgentTask、worktree 和 Host 边界。这样才能避免在失败 Turn 内偷偷换 Provider 或重放副作用。但如果直接把这些物理 task 当成普通任务展示，会产生三个产品问题：

1. 一个用户任务可能在任务历史里变成两三条重复记录；
2. logical root 仍为 `running` 时，真正的 Codex Thread/Item/approval 已经属于 retry child，页面却可能继续看旧 root；
3. 审批接口严格校验 `interaction.task_id`，如果浏览器继续用 root task id 提交 child 的审批，会得到 `Codex interaction not found`。

Phase 7.39 只解决这些投影问题，不修改 Phase 7.38 的安全决策。

### 结构化 attempt ledger

服务端在 Agent task SQLite 中新增 `codex_retry_attempts`：

- `owner_id`
- `root_task_id`
- `attempt_task_id`
- `parent_task_id`
- `attempt_index`
- `state`
- `provider_id/provider_name/model`
- `trigger_code/trigger_reason`
- `backoff_seconds`
- `excluded_provider_ids`
- `decision_code/decision_reason`
- bounded `error`
- started/completed/created/updated timestamps

这个表是审计/投影数据，不是执行真相。真实任务状态、worktree、commit/push/PR 权限仍由 `AgentTask`、Codex Host 与 FDEX publication boundary 决定。

禁止通过以下方式识别内部 retry：

- 解析 `task.error`；
- 搜索 `429` / `timeout` 文本；
- 解析 `retry.auto_attempt` 的自然语言 message；
- 猜测 parent_task_id 就一定代表自动重试。

只有结构化 ledger 中 `attempt_index > 0` 的 attempt 才属于 Phase 7.38 内部自动恢复链。手动 Retry、Resume、Fork 仍是普通用户可见任务。

### 普通任务历史

`AgentTaskStore.list()` 默认排除 ledger 中 `attempt_index > 0` 的内部 attempt。

因此 Web、旧版 Android/API 客户端和 host-owned task-status facts 默认继续看到一个用户级 logical task，而不是 root + retry child 的重复列表。

这只是列表投影：

- `get/get_any` 仍能直接读取内部 child；
- `active_count` 仍统计 child；
- `list_releasable` 仍清理 child 的真实 worktree；
- owner/account 删除仍删除完整 retry ledger 与全部 AgentTask。

### Effective Execution Attempt

对于 logical root，页面按以下顺序解析当前执行身份：

```text
active_attempt_task_id
  -> latest_attempt_task_id
  -> logical root task_id
```

因此自动 retry 期间：

- 用户仍停留在 root task URL；
- Cancel 仍取消 root，由 Phase 7.38 传播到 active child；
- 手动 Retry / Resume / Fork 仍基于 logical root；
- Steer / Compact 路由到 effective execution attempt；
- Codex Thread/Turn 状态来自 effective attempt；
- Item snapshot / SSE 来自 effective attempt；
- approval / requestUserInput / MCP response POST 使用 effective attempt id。

如果用户直接打开内部 retry child 的 Web 详情 URL，FDEX 重定向回 logical root。内部 child 没有第二个用户级任务身份。

### Web 展示

root 详情页展示 `Codex 自动恢复链`，每个 attempt 显示：

- Attempt #0/#1/#2；
- 原始执行或自动重试；
- queued/running/succeeded/failed/blocked/canceled；
- 实际 Provider / Model；
- 触发下一次 retry 的结构化 health code；
- backoff；
- retry task 临时排除的 Provider id；
- 最终 retry decision code/reason；
- bounded error 与时间戳。

页面在 logical root 为 `queued/running` 时持续刷新，即使 root 已经有旧 Codex session；否则 retry child 启动后页面可能停留在旧 Thread。

### API

普通 `GET /api/agent/tasks` 的 response shape 不改变，并通过 `AgentTaskStore.list()` 默认只返回 logical/user-visible tasks。

新增：

```text
GET /api/agent/tasks/{task_id}/retry-chain
```

返回：

```json
{
  "task_id": "requested task",
  "logical_task_id": "root task",
  "execution_task_id": "active or latest attempt",
  "retry_chain": {
    "root_task_id": "...",
    "attempt_count": 2,
    "retry_count": 1,
    "active_attempt_task_id": "...",
    "latest_attempt_task_id": "...",
    "attempts": []
  }
}
```

owner scope 仍由现有 Agent API authentication 决定。该 projection 不包含 API key、Provider secret 或 worktree 路径。

### 不变量

Phase 7.39 绝不改变以下 Phase 7.38 / 7.36 安全规则：

- Coding-Agent-enabled 智体只使用官方 OpenAI Codex Core；
- 不存在 Legacy/Auto Agent fallback；
- 不回退普通 `client_ai`；
- retry eligibility 只来自 Phase 7.37 structured health；
- Provider 只允许在新的 retry task/Host **启动前**重新选择；
- 已启动 Host/Turn 内 Provider 不可变；
- fresh-full Phase 7.33 proof 仍是 Provider eligibility gate；
- 已跨 commit/push/PR 副作用边界不自动 replay；
- 不完整失败 Turn 不是 Thread recovery checkpoint；
- GitHub authority 仍由 FDEX 持有，Codex 不接触 Installation token。

## English

Phase 7.39 is a projection and observability layer on top of the Phase 7.38 bounded retry controller. It introduces no new Agent core and changes no retry or authorization decision.

A **logical task** is the stable user-facing root. An **execution attempt** is one physical AgentTask/worktree/Codex Host run. Attempt 0 is the original execution; attempts 1 and 2 are internal bounded retries.

The structured `codex_retry_attempts` ledger records attempt identity, Provider/Model, structured health trigger, backoff, retry-scoped Provider exclusions and the final decision. Normal task history hides internal attempts while direct audit, active accounting and worktree cleanup still include them.

Web task detail keeps the logical root URL but projects Thread/Turn, Item/SSE, approval, requestUserInput, Steer and Compact onto the active/latest execution attempt. The authenticated Agent API exposes the same chain through `GET /api/agent/tasks/{task_id}/retry-chain` without changing the legacy task-list response shape.

All Codex-only, fresh-full, no-in-Turn-Provider-switch, no-generic-fallback and side-effect boundaries remain unchanged.
