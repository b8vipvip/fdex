# Codex Bounded Retry Controller / Codex 有界自动重试控制器

## 中文

Phase 7.38 在 Phase 7.37 结构化健康监控之上增加 **bounded automatic retry**。它不是新的 Agent 引擎，也不是错误字符串重试器。Coding-Agent-enabled 智体仍然只使用官方 OpenAI Codex Core；自动恢复只决定是否创建一个新的 Codex AgentTask/worktree 尝试。

### 不变量

```text
failed Codex attempt
    -> refresh Phase 7.37 structured health
    -> retryable transient state?
       no  -> terminal fail-closed
       yes -> side-effect-free + budget available?
              no  -> terminal fail-closed
              yes -> NEW AgentTask + NEW worktree
                    -> optional fresh-full Provider reselection
                    -> NEW Codex Host / Turn
```

绝对禁止：

- 在已经启动的 Codex Host/Turn 内切换 Provider；
- 回退到 Legacy Agent；
- 回退到普通 `client_ai`；
- 通过解析第三方错误字符串里的 `429`、`timeout` 等关键词决定重试；
- 在已经越过 FDEX commit/push/PR 边界后自动 replay；
- 用实时 `/models` 探针替代 Phase 7.33 fresh-full compatibility seal。

### 自动重试预算

- 原始 attempt：1 次；
- 自动 retry：最多 2 次；
- 默认 backoff：第 1 次 2 秒，第 2 次 8 秒；
- 第 3 个自动 retry 请求直接返回 `RETRY_LIMIT_REACHED`，不会再执行健康探针或创建任务。

因此一次逻辑任务最多产生：

```text
1 original attempt + 2 retry children = 3 Codex attempts
```

每个 retry child 都有新的 `AgentTask.id`、独立 worktree 和自己的 cross-worker run lock。逻辑根任务在整个恢复链完成前保持非 terminal；最终成功或失败只在根任务上提交一次用户可见结果。

### 可自动恢复的结构化状态

当前允许：

- `PROVIDER_RATE_LIMITED`
- `PROVIDER_UNREACHABLE`
- `HOST_UNAVAILABLE`

这些 code 来自 Phase 7.37 健康快照，而不是 task.error 文本。

### 不自动重试的硬阻断

包括：

- `AGENT_DISABLED`
- `RUNTIME_UNAVAILABLE`
- `PROCESS_ISOLATION_UNAVAILABLE`
- `PROVIDER_CONFIG_INVALID`
- `SMOKE_MISSING`
- `SMOKE_EXPIRED`
- `FINGERPRINT_MISMATCH`
- `COMPATIBILITY_INSUFFICIENT`
- `SMOKE_FAILED`
- `SIDE_EFFECT_BOUNDARY_REACHED`
- `HEALTH_CHECK_UNAVAILABLE`
- `RETRY_LIMIT_REACHED`

Phase 7.37 的 `/models` 401/403 仍是 advisory `PROVIDER_AUTH_PROBE_FAILED`，不会因为“看起来像鉴权故障”就触发自动 replay。

### Provider reselection

当失败 attempt 对应的 Provider 出现结构化 transient live failure 时，控制器检查是否存在另一个：

1. Phase 7.33 compatibility `eligible=true`；
2. fresh `full`；
3. live state 为 `ok` 或 `reachable`。

只有同时满足时，失败 Provider 才会通过 task-local `ContextVar` 在**新的 retry task**里临时排除。Rollout selector 随后正常重新选择候选；新 Provider仍必须通过原来的 fresh-full seal。

如果没有经过证明的健康替代 Provider，控制器不会强行切换，而是在 backoff 后允许新的 task 再使用同一个仍被 fresh-full seal 接受的 Provider。

这个排除上下文不会写入全局 Provider 配置，也不会影响其它账号、其它任务或正在运行的 Host。

### Thread / 上下文连续性

如果失败 attempt 或它的父链上存在一个拥有 `last_completed_turn_id` 的官方 Codex Thread，retry child 会绑定为 `relation=fork`。运行时随后调用官方 `thread/fork`，从最近一个**已完成 Turn** 创建新 Thread。

失败、未完成的 Turn 不作为恢复 checkpoint。若找不到可证明的 completed Turn，retry child 启动全新的 Thread。

媒体/附件继续通过现有 `parent_task_id` 继承机制复制到新 task，仍受 owner/task 范围检查。

### Commit / 发布边界

自动 replay 只允许在当前 attempt 尚未产生以下任何状态时执行：

```text
commit_sha
pushed
pr_url
changed_files
```

一旦 FDEX 已确认文件变化并进入 commit/publish 结果路径，自动 replay 停止并 fail closed，避免重复 side effects。失败 attempt 的无提交 worktree 可以被安全释放；其本地 retry branch 在边界验证后尝试删除。

### Logical root

Web 智体聊天、REST `/api/agent/tasks/{id}/run` 与用户 Agent Center 都继续观察原始 root task ID。

当 child retry 成功：

- child 保留为 durable audit attempt；
- root 复制最终 changed-files/commit/push/PR 结果；
- root 的 Codex task binding 指向最终成功 child 的 Thread；
- root 最终变为 `succeeded`。

当预算耗尽或遇到硬阻断：

- 当前 child 变为 terminal；
- root 最终只 terminalize 一次；
- 不重新打开任何已经 terminal 的 task。

### Cancellation

用户始终取消 logical root。Phase 7.38 在 active retry attempt scope 中把 root 的 durable `cancel_requested` 传递到正在运行的 child，并继续使用原有 `_raise_if_cancelled` 安全边界。Backoff 中检测到取消时，尚未启动的 child 会直接 canceled，不再启动 Host。

### Manual Retry

现有用户手动“重试”仍然独立存在。手动重试创建新的 task，并开始新的自动重试预算；它不会修改或复活原 terminal task。

## English

Phase 7.38 adds a bounded automatic recovery controller on top of Phase 7.37 structured health. It never introduces another Agent core, never falls back to generic AI, and never parses human error strings to infer retryability.

The original Codex attempt may be followed by at most two automatic retry children with 2s/8s backoff. Every retry is a new AgentTask/worktree/Host boundary. Provider reselection is allowed only before that new Host starts and only when a healthy alternative already has its own fresh `full` Phase 7.33 proof. Provider identity stays immutable inside a started Turn.

Automatic replay is restricted to structured transient states (`PROVIDER_RATE_LIMITED`, `PROVIDER_UNREACHABLE`, `HOST_UNAVAILABLE`) and to attempts that have not crossed FDEX's commit/publish boundary. Runtime, process-isolation, smoke/fingerprint/configuration failures and unavailable health evidence fail closed.

When a safe completed Codex checkpoint exists, the retry child uses official `thread/fork` from the nearest completed Turn. The logical root task remains the stable user-facing task ID and is terminalized only once after the bounded recovery chain succeeds or definitively fails.