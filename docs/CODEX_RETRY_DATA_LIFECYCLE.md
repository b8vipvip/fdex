# Codex Retry Data Lifecycle / Codex 重试数据生命周期

Phase 7.42 closes the account-erasure gap introduced by the Phase 7.41 durable retry-transition journal.

Phase 7.42 用于补齐 Phase 7.41 引入持久化 retry transition journal 之后的账号数据擦除缺口。

## 中文

### 问题

Phase 7.41 新增 `codex_retry_transitions`，用于在真正创建下一次 retry child 之前持久化：

- source attempt / next attempt；
- structured retry decision；
- backoff；
- task-local Provider exclusions；
- child task identity；
- transition state。

这些数据与 `codex_retry_attempts`、`agent_tasks` 位于同一个 SQLite 数据库中。

此前账号注销最终只通过 `AgentTaskStore.delete_owner()` 删除：

```text
codex_retry_attempts
agent_tasks
```

并不会删除 Phase 7.41 新表 `codex_retry_transitions`。因此一个已经注销的 owner 仍可能留下 root/source task id、retry 原因、backoff、Provider exclusions 等持久化元数据。

### Phase 7.42 删除权威

新增 `delete_owner_retry_task_graph(owner_id)`，作为账号擦除阶段的 retry/task 持久化权威。

它在同一个共享 SQLite 连接中执行：

```text
BEGIN IMMEDIATE
  DELETE codex_retry_transitions WHERE owner_id = ?
  DELETE codex_retry_attempts    WHERE owner_id = ?
  DELETE agent_tasks             WHERE owner_id = ?
COMMIT
```

删除前分别统计三层记录，删除后再次验证 owner 计数全部为 0。如果无法收敛则抛出错误，使账号注销 fail-closed，而不是报告成功后留下 retry journal。

### 兼容旧数据库

Phase 7.40 数据库不存在 `codex_retry_transitions`。Phase 7.42 不要求为了注销账号先升级出这张表：

- 若 transition 表存在，则统计并删除；
- 若不存在，则 transition count 为 0；
- `codex_retry_attempts` 与 `agent_tasks` 仍正常删除。

因此 rolling upgrade / downgrade 环境里的旧数据库仍可完成 owner erasure。

### Owner 隔离

删除条件始终是精确 `owner_id`。测试同时创建 owner A 与 owner B 的 task / attempt / transition，删除 A 后验证 B 三层数据全部保留。

### Account cleanup 回显

账号注销结果现在显式包含：

```text
agent_tasks
codex_retry_attempts
codex_retry_transitions
```

因此运维审计可以看到每层实际删除数量，而不再只看到表面的 AgentTask 数量。

### 不改变的边界

Phase 7.42 不改变：

- Codex-only Agent Core；
- Phase 7.33 Provider compatibility；
- Phase 7.38 bounded retry policy；
- Phase 7.41 crash recovery；
- retry budget / Provider switching boundary；
- Codex Host / Item / Interaction / Task Input 的独立删除顺序；
- worktree、sandbox、CODEX_HOME 的文件系统擦除；
- GitHub App Installation 撤销顺序。

它只修复账号删除时 retry 持久化图的完整性。

## English

Phase 7.42 makes the Phase 7.41 retry journal part of the authoritative account-erasure graph. `delete_owner_retry_task_graph(owner_id)` removes `codex_retry_transitions`, `codex_retry_attempts`, and `agent_tasks` for one owner in a single `BEGIN IMMEDIATE` SQLite transaction, verifies that all three projections converge to zero, and returns per-layer deletion counts to account cleanup.

The transition table is optional for Phase 7.40 database compatibility. If it does not exist, erasure still removes attempts and tasks without creating or requiring a migration-only table. Owner-scoped regressions prove that deleting one tenant never removes another tenant's task, attempt, or transition records.
