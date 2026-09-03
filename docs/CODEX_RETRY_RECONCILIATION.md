# Codex Retry Chain Reconciliation / Codex 重试链崩溃恢复

Phase 7.41 closes the worker-crash gaps left after Phase 7.38 bounded retry, Phase 7.39 logical-task projection and Phase 7.40 atomic task lineage.

Phase 7.41 补齐 Phase 7.38 有界重试、Phase 7.39 逻辑任务投影和 Phase 7.40 原子任务血缘之后仍存在的 **Worker 进程崩溃恢复窗口**。

## 中文

### 1. 为什么只持久化 child 还不够

正常 Coding Agent 执行会在 logical root 的 `AgentTaskStore.run_lock(root_task_id)` 下覆盖整条自动重试链；每个 physical retry child 还有自己的 task lock。Linux `flock` 会在 Worker 退出后由内核释放，但锁释放本身不会重新调度任务。

Phase 7.40 已解决“child 已创建、retry audit 尚未写入时 child 身份丢失”的问题，但完整 retry 流程仍有多个 crash point：

```text
failed attempt
  -> structured retry decision
  -> source terminalize / worktree discard
  -> create child
  -> attach child audit
  -> backoff
  -> start Provider / Host / Turn
  -> child terminal result
  -> project result back to logical root
```

如果只扫描 `auto_retry child`，Worker 死在“decision 已做出、child 尚未创建”时仍会留下永久 `root=running`。

### 2. Durable retry transition journal

Phase 7.41 新增 `codex_retry_transitions`。当 Phase 7.38 判断 `decision.retry == true` 时，FDEX 在同一个 SQLite `BEGIN IMMEDIATE` 事务中同时写入：

- 当前 physical attempt 的失败 decision；
- `source_attempt_task_id`；
- `source_attempt_index`；
- `next_attempt_index`；
- structured `decision_code / decision_reason`；
- 原始 backoff；
- task-local Provider exclusions。

这个 transaction **发生在 source terminalize、worktree discard 和 child create 之前**。

因此一旦系统已经承诺“要执行下一次 retry”，重启后不需要重新解析错误字符串，也不需要猜当时的 Provider/backoff 决策。

### 3. 正常状态机写入顺序

```text
decide retry
    ↓
atomic: attempt decision + retry transition plan
    ↓
terminalize failed child if needed
    ↓
discard failed worktree
    ↓
create auto_retry child
    ↓
attach child_task_id to transition
    ↓
write/verify child attempt audit
    ↓
wait original backoff
    ↓
Provider preflight / Host / Turn
```

`FdexAgentLoop._drive_chain()` 和后台 reconciler 使用同一套状态机；reconciler 不是第二个 retry engine。

### 4. Root-centric reconciliation

每个 Server Worker 每 15 秒扫描：

```text
logical_root.status = running
AND logical_root.id = logical_root.logical_root_id
AND structured retry attempt audit exists
```

扫描是内部跨 owner 调度查询，不改变任何用户/API owner scope。真正处理前必须成功获取 logical root 的 crash-safe `flock`。正常执行中的 root 已持有该锁，因此其他 Worker 只会得到 `busy`；只有原 Worker 已退出后，reconciler 才能成为新的唯一执行者。

普通 `queued` 用户任务不会被扫描。`user / manual_retry / resume / fork` root 如果已经进入 Codex 且 Worker 死亡，可以被 **fail-closed 收口**，但不会自动重放原始 started attempt。

### 5. 可以恢复的 crash windows

#### A. transition 已提交，child 尚未创建

reconciler 使用 transition 中的 exact next index、backoff 和 exclusions 完成 source cleanup，然后创建计划中的 `auto_retry` child。

#### B. child 主记录已创建，但 transition attach 尚未完成

Phase 7.40 主表 lineage 能定位唯一的 `(root, parent, attempt_index)` child。reconciler 将它 attach 到 transition，不会再创建第二个 child。

#### C. child 已创建，但 Phase 7.39 child audit 尚未写入

此时 **不会 fail-closed**。transition 已经保存完整 policy metadata，所以 child audit 可以从 transition 精确重建。只有 transition 本身不存在时，FDEX 才拒绝推断。

#### D. 7.40 升级遗留 queued child

如果升级前已经存在：

- immutable `auto_retry` lineage；
- Phase 7.39 structured child audit；
- audit 尚未 Provider/Host started；

7.41 可以从这份已有 audit 建立兼容 transition，然后继续恢复。仍然不读取 human error/event text。

#### E. backoff 中 Worker 死亡

backoff 以 durable transition 的 `created_at + backoff_seconds` 为准。未到期时 child 保持 queued；到期后才能执行。

#### F. child 已 terminal，但 root 尚未投影

如果 child 已 durable `succeeded`，reconciler **不重新运行 Codex**，而是调用现有 `complete_logical_root_from_retry()` 把结果、Git metadata 和最终状态投影回 root。

如果 child 已 durable `failed/canceled` 且没有下一 transition，则只收口 logical root，同样不会 replay child。

### 6. 绝不自动 replay 的状态

只要新的 child 已越过 Provider/Host start boundary：

- main task 已 `running`；或
- audit 有 `started_at`；或
- `provider_id > 0`；或
- transition state 已 `started`；

就不能重新启动该 physical attempt。原因是 Phase 7.33/7.38 要求：

> 已启动的 Codex Host / Turn / task / worktree 内不得切换 Provider。

Worker 死亡后如果重新启动 Host，当前没有独立于 attempt audit 的不可变 Provider process pin 可以证明一定复用原 Provider，所以统一 `ATTEMPT_ALREADY_STARTED` fail-closed。

如果该 physical attempt 已有 durable Codex Turn，则风险更高，状态为 `SIDE_EFFECT_UNKNOWN`，因为 command/file/network/tool side effect 可能已经发生。

### 7. 没有 transition 时不猜

如果 root/attempt 已经进入 `running`，但 Worker 在 durable retry transition 提交之前退出：

```text
ORPHAN_ATTEMPT_NO_TRANSITION
```

FDEX 会终止 orphan root，而不是重新运行这个 started attempt，也不会根据 `429`、`timeout`、event 文本等推导 retry。

这仍然是 fail-closed，但不会再留下永久 `running` 任务。

### 8. Retry budget 与 lineage 仍是硬边界

transition 必须满足：

```text
next_attempt_index = source_attempt_index + 1
1 <= next_attempt_index <= MAX_AUTO_RETRIES
```

当前 `MAX_AUTO_RETRIES = 2`。任何超预算、重复 attempt index、parent/root 不一致、transition-child 冲突或 audit-policy 不一致都会 fail-closed。

### 9. 内部 child 不能脱离 root 运行

`FdexAgentLoop.run(task_id)` 明确拒绝 `task_kind=auto_retry`。

内部 child 只能由：

- 正常 Phase 7.38 logical-root execution chain；或
- Phase 7.41 在取得 logical-root lease 后的 orphan reconciliation

执行。即使 owner 从显式 retry-chain audit API 得到 child ID，也不能把它通过普通 `/run` 路径变成新的 logical root。

### 10. 不改变的边界

Phase 7.41 不改变：

- official OpenAI Codex 是唯一 Coding Agent Core；
- Phase 7.33 fresh-full compatibility；
- Phase 7.37 structured health retry signal；
- Phase 7.38 original + 最多 2 个 retry child；
- 每次 retry 使用新的 AgentTask / worktree / Host boundary；
- Provider 不在 started Host 内切换；
- GitHub commit/push/PR authority 仍由 FDEX 持有；
- Coding Agent 不回退到 `client_ai`。

## English summary

Phase 7.41 adds a durable retry-transition journal and a logical-root-centric crash reconciler. A retryable Phase 7.38 decision and the exact next-attempt intent are committed atomically before source terminalization, worktree cleanup or child creation. The transition records the source/next attempt indices, structured decision, original backoff and task-local Provider exclusions.

Every worker may scan running logical roots, but the crash-safe root `flock` remains the sole execution authority. The reconciler can safely finish a planned-but-not-created child, attach a child created just before a crash, reconstruct a missing child audit from the durable transition, adopt an already-audited pre-7.41 queued child, respect the remaining backoff, and project an already-terminal child outcome back to the root without rerunning Codex.

A Provider/Host-started physical attempt is never replayed. Such a state fails closed as `ATTEMPT_ALREADY_STARTED`; durable Turn evidence becomes `SIDE_EFFECT_UNKNOWN`. If the worker dies before any durable retry transition exists, the orphan started attempt is terminalized as `ORPHAN_ATTEMPT_NO_TRANSITION` rather than being guessed or left permanently running. Retry budget, immutable task lineage, Provider immutability, Codex-only execution and FDEX-owned Git publication remain unchanged.
