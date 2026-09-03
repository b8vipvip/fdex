# Agent Task Kind and Retry Lineage / Agent 任务类型与重试血缘

## 中文

Phase 7.40 把 Coding Agent 的“任务身份”从 Phase 7.39 的投影层前移到 `agent_tasks` 主记录。目标不是改变 Codex 的执行、Provider 选择或重试资格，而是消除自动重试 child 创建与 retry ledger 写入之间的崩溃窗口。

### 1. 两类真相

FDEX 现在明确区分两类持久化真相：

- **任务身份 / lineage 真相：`agent_tasks`**
  - `task_kind`
  - `logical_root_id`
  - `attempt_index`
  - `parent_task_id`
- **重试运行审计真相：`codex_retry_attempts`**
  - 实际 Provider / Model
  - structured health trigger
  - backoff
  - retry-scoped Provider exclusions
  - final decision code / reason
  - attempt timestamps / error

`codex_retry_attempts` 不再决定一个 AgentTask 是不是内部任务；它只是补充运行审计。

### 2. task_kind

Phase 7.40 的合法任务类型：

| task_kind | 含义 | 用户任务历史默认可见 | logical_root_id |
| --- | --- | --- | --- |
| `user` | 用户直接创建的新逻辑任务 | 是 | 自己的 task id |
| `auto_retry` | Phase 7.38 自动恢复产生的物理 attempt | 否 | 原逻辑 root id |
| `manual_retry` | 用户显式点击/调用 Retry 创建的新逻辑任务 | 是 | 自己的 task id |
| `resume` | 官方 Codex Thread resume 创建的新逻辑任务 | 是 | 自己的 task id |
| `fork` | 官方 Codex Thread fork 创建的新逻辑任务 | 是 | 自己的 task id |

`attempt_index` 只用于同一个逻辑任务内部的自动重试：root 为 `0`，自动 retry child 为 `1..N`。所有用户可见的新逻辑任务都从 `attempt_index=0` 开始。

### 3. 原子创建边界

`FdexAgentRuntime.create_task()` 在构造 `AgentTask` 时就确定 task kind 与 lineage，然后才触发第一个 `task.created` 事件。由于 `task.created` 同时是第一次主表持久化，所以自动 retry child 的第一条 durable row 已经包含：

```text
task_kind = auto_retry
logical_root_id = <root task id>
attempt_index = 1..N
parent_task_id = <previous attempt id>
```

不存在“先以普通 user task 创建，稍后再由 retry ledger 标记为内部”的中间状态。

### 4. Crash-window 语义

允许出现以下故障序列：

```text
create auto-retry AgentTask  -> durable main row
worker crash
retry audit row              -> 未写入
```

此时系统仍必须满足：

- 普通任务历史不会显示该 child；
- `active_count`、workspace cleanup、direct task lookup 仍能看到真实 child；
- `list_execution_lineage()` 能从 `agent_tasks` 找到 root + child；
- retry-chain projection 会生成 `audit_pending=true` 的有界占位 attempt；
- 不解析 `task.error`、`429`、`timeout` 或 event 文本来猜身份。

### 5. 迁移

旧 Phase 7.39 数据库升级时：

1. `agent_tasks` 增加 `task_kind / logical_root_id / attempt_index`；
2. 已存在的 `codex_retry_attempts.attempt_index > 0` 反向回填为 `task_kind=auto_retry`；
3. 其余旧任务默认保持 `task_kind=user`，并把 `logical_root_id` 设为自己的 task id；
4. 迁移是幂等的，不使用 human-readable error/event 文本。

因此部署升级不会把历史自动重试 child 重新暴露到用户任务列表。

### 6. 不变量

Phase 7.40 不改变以下既有规则：

- Coding-Agent-enabled 智体只使用官方 OpenAI Codex Core；
- 没有 Legacy/Auto Agent core，也没有 `client_ai` fallback；
- Phase 7.33 fresh-full compatibility 仍决定 Provider 是否可进入生产 Codex Host；
- Phase 7.37 structured health 仍是自动 retry 的唯一健康信号；
- Phase 7.38 bounded retry 仍最多 2 次，且每次使用新 task/worktree/Host；
- started Host/Turn 内绝不切换 Provider；
- changed-files/commit/push/PR side-effect boundary 之后禁止 whole-task 自动 replay；
- GitHub commit/push/PR authority 始终属于 FDEX，而不是 Codex。

---

## English

Phase 7.40 moves Coding Agent task identity from the Phase 7.39 projection layer into the primary `agent_tasks` record. It does not change Codex execution, Provider selection, or retry eligibility. Its purpose is to close the crash window between creating an automatic retry child and writing the richer retry ledger row.

### 1. Two sources of truth

FDEX now separates two durable concerns:

- **Identity and lineage authority: `agent_tasks`** — `task_kind`, `logical_root_id`, `attempt_index`, `parent_task_id`.
- **Retry execution audit: `codex_retry_attempts`** — actual Provider/Model, structured health trigger, backoff, retry-scoped exclusions, final decision, timestamps and bounded error.

The retry ledger no longer determines whether an AgentTask is internal.

### 2. Task kinds

Valid task kinds are `user`, `auto_retry`, `manual_retry`, `resume`, and `fork`. Only `auto_retry` is hidden from ordinary task history. A visible manual retry/resume/fork is a new logical task with its own `logical_root_id` and `attempt_index=0`; automatic attempts keep the original logical root and use positive attempt indexes.

### 3. Atomic creation boundary

`FdexAgentRuntime.create_task()` assigns kind and lineage before the first `task.created` event. Because that event performs the first durable task write, an automatic retry child is never durably born as a normal user task.

### 4. Crash-window semantics

If a worker dies after the retry child main row is durable but before the retry audit row exists, normal history still hides the child, execution/accounting/cleanup still see it, lineage still finds it, and retry-chain projection surfaces a bounded `audit_pending=true` placeholder. No human-readable error or event text is parsed to infer identity.

### 5. Migration

Existing Phase 7.39 databases receive the new columns. Any task backed by a retry-ledger row with `attempt_index > 0` is backfilled as `auto_retry`; other legacy tasks become self-rooted `user` tasks. Migration is idempotent and text-independent.

### 6. Preserved invariants

Codex-only execution, fresh-full Provider gating, structured-health-only retry decisions, bounded new-worktree retries, no Provider switching inside a started Host/Turn, side-effect replay suppression, and FDEX-owned GitHub publication authority remain unchanged.
