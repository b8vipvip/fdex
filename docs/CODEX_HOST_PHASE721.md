# FDEX Codex Host Phase 7.21 / Durable Thread & Turn Lifecycle

## 目标

Phase 7.20 让 FDEX 成为官方 Codex `app-server` 的原生 Host，但一次 FDEX Coding Agent 任务完成后 stdio 进程会退出，`thread_id` / `turn_id` 仅存在于进程内。

Phase 7.21 把官方 Codex Thread/Turn 变成 FDEX 可恢复、可审计、按 `user_id` 隔离的持久资源，并实现：

- `thread/resume`
- `thread/fork`
- `turn/steer`
- `thread/compact/start`

## 持久模型

独立数据库：`server/data/codex-host.db`。

### `codex_threads`

保存官方 Thread 身份、父 Thread、fork 截止 Turn、当前/最后完成 Turn、Runtime/Provider/Model、当前 worktree 与状态。

### `codex_task_threads`

保存 FDEX Task 到 Codex Thread 的关系。一个 Thread 可以跨多个 continuation task，因此不能把 `thread_id` 直接塞进 `agent_tasks` 做一对一字段。

关系类型：

- `start`
- `resume`
- `fork`
- `forked`

### `codex_turns`

保存每个官方 Turn 对应的 FDEX task、状态、输入摘要、开始/结束时间和错误。

### `codex_controls`

跨 Uvicorn worker 的 Host 控制队列。`steer` / `compact` 先持久化，再由真正持有 Codex stdio 子进程的 worker claim 并执行，避免依赖 HTTP 请求恰好落在同一个 Python worker。

## Resume

FDEX 不会重新打开一个已经 terminal 的 AgentTask，而是创建 continuation child task：

1. child 记录 `parent_task_id`；
2. child worktree 优先从 parent task 的已验证 Commit 创建；
3. `codex_task_threads` 将 child 映射到原 Thread，关系为 `resume`；
4. child 执行时调用官方 `thread/resume`；
5. 再在同一 Thread 上调用 `turn/start`。

这样 AgentTask 仍保持不可逆的 terminal 语义，同时 Codex Thread 可以持续演化。

## Fork

Fork 同样创建 child task，并先把 source Thread 记录为 fork intent。真正拥有隔离 worktree 的执行 worker 调用：

`thread/fork(threadId, lastTurnId, cwd, model/provider/sandbox/config...)`

成功后将 child 重新绑定到返回的新 Thread，并持久化：

- `parent_thread_id`
- `forked_from_turn_id`
- `root_task_id`

## Steer

活动 Turn 的 steer 不允许由任意 Web worker直接操作 stdio：

1. Web 写入 `codex_controls(action=steer)`；
2. 运行 Turn 的 worker 每 750ms 以内检查控制队列；
3. claim 后调用官方 `turn/steer`，并传 `expectedTurnId`；
4. 结果写回 control row。

因此 FDEX_WORKERS > 1 时也不需要 sticky session。

## Compact

如果 Thread 正在执行 Turn，compact 进入控制队列并在 Turn 完成后串行执行。

如果 Thread idle，FDEX 可以短暂启动官方 app-server、`thread/resume` 后执行 `thread/compact/start`，并持久记录 compact Turn。

## 账号隔离与删除

所有 Thread/Turn/Control 查询都必须带 `owner_id`。账号注销时：

- 有 running/compacting Thread 或 pending/processing control -> fail closed；
- 无活动操作后删除该 owner 的全部 Codex Host 数据。

## 后续阶段

Phase 7.22 在此持久总线上增加完整 Item 事件存储和实时 UI；Phase 7.23 将 server-initiated approval/requestUserInput 也建模为持久交互请求；后续 MCP/Hooks/Plugins、Sub-Agent 以及 cgroup Runtime 管理继续复用同一 Host 所有权边界。