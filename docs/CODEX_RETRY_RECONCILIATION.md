# Codex Retry Chain Reconciliation / Codex 重试链崩溃恢复

Phase 7.41 closes the process-crash gap left after Phase 7.38 bounded retry, Phase 7.39 logical-task projection and Phase 7.40 atomic task lineage.

Phase 7.41 用于补齐 Phase 7.38 有界重试、Phase 7.39 逻辑任务投影和 Phase 7.40 原子任务血缘之后仍存在的 **Worker 进程崩溃恢复缺口**。

## 中文

### 1. 问题

正常 Coding Agent 执行会在 logical root 的 `AgentTaskStore.run_lock(root_task_id)` 下覆盖整条自动重试链。Phase 7.38 的 retry child 还有自己的 task lock。Linux `flock` 在 Worker 退出后由内核自动释放，因此“锁不会永久死掉”；但此前没有任何调度器在锁释放后主动寻找 `auto_retry` 孤儿任务。

结果是：Worker 如果在自动 retry child 已持久化后退出，数据库可能永久留下：

```text
root.status = running
child.task_kind = auto_retry
child.status = queued | running
```

Thread/Turn 层已有 Phase 7.21 orphan reconciliation，但它不会重新调度 AgentTask。

### 2. Phase 7.41 恢复原则

Phase 7.41 新增后台 `codex_retry_reconciler`。每 15 秒扫描：

```text
task_kind = auto_retry
status IN (queued, running)
```

扫描是内部跨 owner 调度查询，不改变任何用户/API owner scope。真正接管前必须先取得 logical root 的 crash-safe `flock`。因此多个 Uvicorn Worker 可以同时运行 reconciler，但同一 logical root 只有一个 Worker 能进入恢复路径。

### 3. 唯一允许自动 replay 的状态

只有同时满足以下条件才自动接管：

1. `child.task_kind == auto_retry`；
2. child 仍为 `queued`；
3. logical root 存在、owner 一致、仍为 `running`；
4. root 没有 cancellation；
5. Phase 7.40 主表 lineage 与 Phase 7.39 audit 完全一致：root、parent、attempt index 都匹配；
6. retry audit 仍为 `queued`；
7. `started_at` 为空；
8. `provider_id == 0`；
9. 当前 attempt 没有 durable Codex Turn；
10. 原 backoff 已到期。

满足后，reconciler 调用 `FdexAgentLoop.run_from_retry_child()`。它不会创建另一套 retry policy，而是重新进入与正常执行完全相同的 `_drive_chain()`。

### 4. 为什么 running attempt 不自动 replay

即使数据库里暂时还没有 Turn，只要 attempt 已 `running`、retry audit 已 `started` 或 Provider 已被记录，就说明它已经越过 Provider/Host start boundary。

Phase 7.33/7.38 的不变量是：

> started Codex Host / Turn / task / worktree 内不得切换 Provider。

进程死亡后重新启动 Host 时，如果没有一个独立、不可变的 Provider pin，就不能证明新 Host 一定复用原 Provider。因此 Phase 7.41 不把“没有 Turn”误当成“绝对安全”。它会以 `ATTEMPT_ALREADY_STARTED` fail-closed。

如果已有属于这个 physical attempt 的 durable Turn，则状态进一步升级为 `SIDE_EFFECT_UNKNOWN`：模型可能已执行 command/file/tool side effect，因此绝不自动 replay。

### 5. audit 缺失也不猜

Phase 7.40 能保证 child 身份在主 `agent_tasks` 行里原子存在，即使 Worker 死在 `codex_retry_attempts` 写入之前也不会把 child 暴露成普通用户任务。

但 Phase 7.41 不会因此猜测丢失的 Provider exclusion、trigger reason 或 backoff。主 lineage 存在而 retry audit 缺失时：

```text
RECOVERY_METADATA_MISSING
```

reconciler 只补一个最小审计行用于说明恢复失败，然后将 child/root fail-closed。

### 6. cancellation 与 terminal root

- root 已 `succeeded / failed / canceled`：遗留 child 只会被取消，不会执行；
- root 已收到 cancel：child 与 root 都按取消路径收口；
- root 缺失或 lineage 不一致：fail-closed；
- backoff 未到：保持 queued，等待下次 tick。

### 7. 直接运行内部 child 被禁止

`FdexAgentLoop.run(task_id)` 现在明确拒绝 `task_kind=auto_retry`。

因此即使 owner 从显式 retry-chain audit API 得到 child task ID，也不能把它通过普通 `/run` 路径伪装成一个新的 logical root。内部 child 只能由：

- 正常 Phase 7.38 root execution chain；或
- Phase 7.41 在取得 root lease 后的 orphan recovery

执行。

### 8. 不改变的边界

Phase 7.41 不改变：

- Codex-only Agent Core；
- Phase 7.33 fresh-full compatibility；
- Phase 7.37 structured health retry signal；
- Phase 7.38 最多 2 次自动 retry；
- 每次 retry 的新 AgentTask / worktree / Host boundary；
- side-effect replay suppression；
- GitHub commit/push/PR 仍由 FDEX 持有；
- Coding Agent 不回退到 `client_ai`。

## English summary

Phase 7.41 adds a durable background reconciler for orphaned automatic retry attempts. Every server worker may scan the shared task database, but recovery is serialized by the logical-root crash-safe `flock`. The reconciler only reclaims an `auto_retry` child that is still queued, whose immutable Phase 7.40 lineage matches its Phase 7.39 audit row, whose audit has not selected a Provider or crossed Host start, whose physical attempt has no durable Codex Turn, and whose original backoff has elapsed.

A started attempt is never blindly replayed. Provider/Host-start evidence yields `ATTEMPT_ALREADY_STARTED`; a durable Turn yields `SIDE_EFFECT_UNKNOWN`. Missing audit metadata yields `RECOVERY_METADATA_MISSING`. All of these fail closed because replay would otherwise guess Provider intent or side-effect state. Normal user/manual-retry/resume/fork tasks are never scanned by this reconciler.

Recovered queued children re-enter the existing `FdexAgentLoop._drive_chain()` rather than introducing a second retry engine. Directly invoking `FdexAgentLoop.run()` on an internal `auto_retry` task is rejected, preventing an internal attempt from being detached from its logical-root execution lease.
