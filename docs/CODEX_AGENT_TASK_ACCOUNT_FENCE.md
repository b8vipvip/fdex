# Agent Task Account-Operation Fence / Agent 任务账号操作栅栏

Phase 7.43 closes the check-then-create race between Coding Agent task creation and FDEX account data operations.

Phase 7.43 用于关闭 Coding Agent 创建任务与 FDEX 账号数据操作之间的 check-then-create 竞态。

## 中文

### 问题

账号删除、长期记忆清理和数据导出已经共享 `account_operation(user_id, operation)` 的跨 Worker `flock`。部分 Agent HTTP/Web 入口也会先读取 `account_operation_status()`，在 busy 时拒绝新请求。

但“先读 status，再创建 AgentTask”不是原子操作：

```text
request A: account_operation_status() -> not busy
request B: acquire account_delete flock
request A: runtime.create_task() -> first durable AgentTask write
```

如果 task creation 本身不取得同一把锁，A 仍可能在账号清理已经开始后写入新的 owner 数据。Web 智体/Coding Agent 聊天、manual retry、resume/fork、auto-retry 等直接调用 `FdexAgentRuntime.create_task()` 的路径也不能靠某一个 HTTP 路由的 pre-check 获得统一保证。

### Phase 7.43 权威边界

真实 Central FDEX owner（`usr_*`）的 `FdexAgentRuntime.create_task()` 现在复用同一个：

```text
account_operation(owner_id, "agent_task_create")
```

并在锁内完成：

1. 删除 tombstone 检查；
2. project enabled / owner scope 验证；
3. `task.created` 的第一持久化写入。

随后才释放 account-operation flock，并把 durable task 放入当前 Worker 的热缓存。

因此锁序形成明确二选一：

```text
create wins first
    -> first AgentTask row already durable
    -> later account delete sees active task and fails closed

account operation wins first
    -> create_task cannot acquire flock
    -> no AgentTask row is written
```

账号删除完成后，`mark_account_deleted()` 在释放 delete flock 前写入 one-way tombstone。即使一个旧 HTTP/realtime 请求早先已经拿到了 owner ID，后续 `create_task()` 在拿到 flock 后仍会因 tombstone 拒绝，不会重新创建已删除身份的数据。

### 为什么修在 Runtime 而不是路由

Runtime 是所有 AgentTask 的 first-write seam，包括：

- Agent REST API；
- Web Agent 页面；
- Coding-Agent-enabled 智体聊天；
- manual retry；
- resume / fork；
- Phase 7.38 auto-retry child；
- 后续任何直接复用 `FdexAgentRuntime.create_task()` 的入口。

路由上的 `account_operation_status()` 仍保留用于更友好的 409/UI 提示，但它不再承担一致性权威。

### Legacy/bootstrap owner

`local` 等非 `usr_*` owner 不是 Central FDEX identity，没有账号删除 tombstone，因此保留原创建语义，不强行套用 Central account-operation lock。

### 不改变的边界

Phase 7.43 不改变：

- Codex-only Agent Core；
- Provider 选择与 fresh-full compatibility；
- Phase 7.38 retry eligibility / budget；
- Phase 7.41 crash reconciliation；
- Phase 7.42 retry-journal erasure；
- task/worktree/Host retry boundary；
- GitHub commit/push/PR authority；
- Android 行为。

它只把账号数据操作与 AgentTask first durable write 串行化。

## English

Phase 7.43 makes `FdexAgentRuntime.create_task()` the authoritative account-operation fence for every real Central FDEX owner. Route-level `account_operation_status()` checks remain advisory UX, while the runtime acquires the same cross-worker `account_operation(owner, "agent_task_create")` flock, checks the deletion tombstone, validates project scope, and performs the first durable `task.created` write before releasing that lock.

This eliminates both sides of the race: if task creation wins first, account deletion later observes the durable active task and fails closed; if account deletion/export/memory erasure wins first, task creation cannot write. A deletion tombstone prevents stale requests from recreating AgentTask data after the account has been removed. Internal automatic retries and user-visible manual retry/resume/fork paths inherit the same fence because they all pass through the runtime creation seam.
