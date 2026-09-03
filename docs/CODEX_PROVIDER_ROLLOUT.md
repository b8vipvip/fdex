# Codex Provider Compatibility & Rollout / Codex Provider 兼容性与上线封印

## 中文

Phase 7.33 建立的 Provider compatibility ledger 继续保留，因为“供应商普通聊天接口可用”并不等于“该供应商能够完整承载官方 Codex Host”。Phase 7.36 改变的是它的职责：**rollout gate 现在只决定强制 Codex 执行是否可以安全启动，不再参与 Agent 引擎选择。**

Coding Agent 已经是 Codex-only：

```text
coding_agent=true
    -> FDEX owner/project/worktree boundary
    -> fresh-full Codex rollout gate
        -> ready: official codex app-server Thread/Turn
        -> not ready: task fail-closed
```

不存在 Legacy/Auto Agent fallback，也不会把 Coding Agent 任务转给普通 `client_ai`。

## 1. 为什么普通 Provider 测试不够

通用 Provider 健康测试主要证明 Base URL、API Key 和普通文本调用可用。官方 Codex Host 还依赖真实 Responses/app-server 交互、reasoning Item、command/file-change 生命周期、MCP tool call 与 Multi-Agent collaboration Item。

因此生产 `codex_runtime_status.ready` 必须找到 fresh full-compatible Provider，而不是只检查 `protocol_order` 中是否声明 `responses`。

## 2. 独立兼容性账本

兼容记录存放在：

```text
server/data/codex-provider-compatibility.db
```

数据库不保存 Provider API Key 明文。Fingerprint v2 绑定 Provider ID/Base URL、API-key identity hash、完整有效文本模型候选顺序、protocol order、timeout、Codex Runtime path/version/source、Multi-Agent governance、Memory/CPU/PID 资源上限以及 FDEX app version。

任一输入变化都会让旧证明失效。默认 freshness 为 168 小时。

## 3. 兼容等级

`wire` 要求真实官方 `initialize -> thread/start -> turn/start -> turn/completed`；`tools` 还要求官方 command/file-change 事件和可验证 scratch-file 副作用；生产要求 `full`，并继续验证 reasoning、loopback MCP、`mcpToolCall`、completed `spawnAgent`、completed `wait` 与 `subAgentActivity`。

模型只在文字中声称“执行过”不会提升兼容等级。

## 4. Smoke workspace 与安全边界

每次 smoke 使用随机 scratch workspace/CODEX_HOME，不使用用户仓库，不 commit/push/PR，不复用用户 durable Thread。真实 Host 仍使用 sanitized process environment、systemd/cgroup v2 process-tree isolation、workspace-write sandbox 与禁用 web search 的默认策略。

只有 Phase 7.32 process isolation `enforced=true` 时，管理员 smoke 才能写入 rollout evidence。

## 5. Provider 选择与 Failover

Codex Host 启动前，selector 可以按 priority 跳过 stale/unverified/incompatible Provider，选择下一个 fresh full-compatible Provider。这是唯一允许的自动 Provider failover。

一旦官方 Codex Host/Turn 开始，Provider 失败就终止该任务；不会在同一 worktree 中切换另一个 Provider。Retry 创建新的 FDEX task/worktree boundary，再重新执行 selector。

Phase 7.36 删除了旧的“Codex 不 ready 就切换其他 Agent Core”的语义。Provider 列表全部不可用时，Coding Agent 任务直接失败并返回 rollout 原因。

## 6. 所有 Codex 启动入口使用同一 Gate

`install_codex_provider_rollout_runtime()` 继续 rebind：

- `codex_engine.codex_runtime_status`；
- `codex_engine.select_codex_provider`；
- `agent_admin_routes.codex_runtime_status`；
- `codex_runtime_admin_routes.codex_runtime_status`；
- `codex_capability_control.select_codex_provider`；
- `codex_host_runtime.select_codex_provider`。

因此管理员状态页、Runtime 管理、capability-control Host 和用户真实 Coding Agent Host 都使用同一 fresh-full proof。

## 7. 管理入口

管理员页面：

```text
/admin/agent/codex-providers
```

展示 Runtime、进程树隔离、Provider priority/model/Responses 配置、compatibility level、fingerprint/freshness 失效原因、真实 evidence、last checked/latency 和 full smoke 操作。

Agent 设置页已经不再提供 `legacy|auto|codex` 下拉框。只有“启用 Coding Agent”开关；开启后执行核心固定为官方 Codex。

## 8. CI 与生产验证

GitHub CI 能验证 ledger/fingerprint、secret 不落盘、freshness、配置漂移、MCP loopback、Multi-Agent evidence classifier、pre-start Provider selection、no mid-task fallback 以及 gate wiring。

CI 不能证明真实生产 Provider 是 full-compatible，因为 CI 不持有生产 Provider secret。实际 Center 仍必须运行 full smoke。

## 9. 生产启用步骤

1. 部署最新 `main`；
2. 确认 Phase 7.32 systemd/cgroup isolation 为 enforced；
3. 打开 `/admin/agent/codex-providers`；
4. 对计划用于 Coding Agent 的 Provider 执行 full smoke；
5. 确认至少一个 Provider 显示 fresh `full`；
6. 启用 Coding Agent；
7. Provider key/model/endpoint、Runtime、governance、resource limits 或 app version 变化后重新 smoke。

如果第 5 步不满足，Coding Agent 任务 fail-closed，不会转到旧 Agent 或普通 AI。

## 10. 旧环境变量迁移

`FDEX_AGENT_ENGINE` 已从正式 Settings、管理员 UI 与 `.env.example` 移除。旧服务器 `.env` 中如果仍残留该变量，Pydantic Settings 会因为 `extra="ignore"` 而忽略它；部署维护时可以直接删除。

## English

FDEX now treats the Phase 7.33 compatibility ledger as a **Codex readiness gate**, not an engine-rollout selector. Coding Agent execution is Codex-only. A task starts the official Codex Host only when an official Runtime and at least one fresh full-compatible Provider are available; otherwise the task fails closed. There is no legacy/auto Agent fallback and no transfer to ordinary `client_ai`.

Provider failover remains allowed only before a Host starts. Once a Codex Turn starts, Provider failure terminalizes the task. Retry creates a fresh task/worktree boundary and re-runs Provider selection. Production still requires a real full smoke on the deployed Center because CI cannot prove compatibility for production Provider credentials.
