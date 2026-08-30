# Codex Sub-Agent Governance / Codex 子 Agent 治理

## 中文

Phase 7.31 使用 OpenAI Codex 官方 Multi-Agent V2，不在 FDEX 中实现第二套 Sub-Agent 调度循环。FDEX Center 负责向每个官方 `codex app-server` 注入不可由租户放宽的治理配置，并继续保留账号、项目、GitHub、审批与操作系统级进程树安全边界。

### 控制项

- `FDEX_AGENT_SUBAGENTS_ENABLED`：是否启用官方 Multi-Agent V2。
- `FDEX_AGENT_SUBAGENT_MAX_CONCURRENT`：单个官方 Session 的最大同时线程数，包含 root。默认 4，即 root + 最多 3 个同时打开的子 Agent。
- `FDEX_AGENT_SUBAGENT_ROLLOUT_BUDGET_TOKENS`：root 与所有子 Agent 共享的 weighted-token 总预算，默认 80000。
- `FDEX_AGENT_SUBAGENT_WAIT_MIN_MS` / `WAIT_DEFAULT_MS` / `WAIT_MAX_MS`：官方 `wait_agent` 的等待范围。
- `FDEX_AGENT_SUBAGENT_SAMPLING_TOKEN_WEIGHT` / `PREFILL_TOKEN_WEIGHT`：官方 rollout budget 的 token 权重。

管理员可在 `/admin/agent/subagents` 修改这些设置。保存后 FDEX 写入服务端 `.env` 并重启；租户自己的 `CODEX_HOME/config.toml` 不能放宽这些上限，因为 FDEX 通过官方 CLI `--config` 高优先级覆盖注入。

### Provider 与模型边界

FDEX 固定 `expose_spawn_agent_model_overrides=false`。子 Agent 继承当前 Thread 已由 FDEX 供应商池选择的 Provider/model，不能通过 `spawn_agent` 请求另一个模型绕过 FDEX Provider 策略。

### 层级与事件

Multi-Agent V2 使用官方 canonical task path（例如 `/root/reviewer`）和 Codex `AgentControl` 管理 spawn、message、follow-up、wait、interrupt、list。FDEX 不维护第二棵私有 Agent 树。

官方 app-server 的所有 Item/Event 继续进入 Phase 7.22 的 schema-light `codex_events` / `codex_items`，仍然按 FDEX owner/task 隔离并支持 SSE 重连恢复。

### 兼容性

FDEX 当前 bundled fallback 为 `openai-codex-cli-bin==0.147.0`。Phase 7.31 CI 不只检查配置字符串，还直接调用该 bundled 官方 Runtime 的 `app-server --help`，要求它成功解析 Multi-Agent V2 和 rollout budget CLI overrides。

### Phase 7.32 外层资源边界

Phase 7.32 在 Multi-Agent 治理之外补上操作系统级边界：真实 FDEX Provider Codex Host 通过独立 transient systemd service 运行，整个 `codex app-server` 及其子 Agent、shell/command、stdio MCP helper、可执行 Plugin 后代共同受 cgroup v2 `MemoryMax`、`CPUQuota`、`TasksMax` 约束，并使用 `KillMode=control-group` 做整树终止。

这意味着 Phase 7.31 的“官方并发/共享 token budget”与 Phase 7.32 的“操作系统资源/进程生命周期”是互补的两层治理，而不是互相替代。Phase 7.32 只在该隔离真实生效时开放经过 inventory 再验证的本地 Plugin install/uninstall；Marketplace 写入、远程 catalog 安装与 `plugin/share/*` 仍关闭。

详见 `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`。

---

## English

Phase 7.31 uses the official OpenAI Codex Multi-Agent V2 implementation. FDEX does not implement a second sub-agent scheduler. FDEX Center injects operator-owned governance into each official `codex app-server` while retaining the account, project, GitHub, approval, and operating-system process-tree boundaries outside Codex.

### Controls

- `FDEX_AGENT_SUBAGENTS_ENABLED`: enable official Multi-Agent V2.
- `FDEX_AGENT_SUBAGENT_MAX_CONCURRENT`: maximum concurrently open threads per official session, including the root. The default is 4, so the root can have at most three concurrently open child agents.
- `FDEX_AGENT_SUBAGENT_ROLLOUT_BUDGET_TOKENS`: shared weighted-token budget for the root and every child agent. Default: 80000.
- `FDEX_AGENT_SUBAGENT_WAIT_MIN_MS` / `WAIT_DEFAULT_MS` / `WAIT_MAX_MS`: bounds for official `wait_agent` calls.
- `FDEX_AGENT_SUBAGENT_SAMPLING_TOKEN_WEIGHT` / `PREFILL_TOKEN_WEIGHT`: official rollout-budget token weights.

Administrators can change these values at `/admin/agent/subagents`. FDEX persists them to the server `.env` and restarts the service. Tenant `CODEX_HOME/config.toml` state cannot loosen the Center limits because FDEX injects higher-precedence official CLI `--config` overrides.

### Provider and model boundary

FDEX forces `expose_spawn_agent_model_overrides=false`. Spawned agents inherit the Provider/model already selected by the FDEX provider pool for the current thread and cannot use `spawn_agent` as a provider-policy bypass.

### Hierarchy and events

Multi-Agent V2 uses official canonical task paths such as `/root/reviewer` and Codex `AgentControl` for spawn, messaging, follow-up, waiting, interruption, and listing. FDEX does not maintain a parallel private agent tree.

All official app-server Item/Event notifications continue through the Phase 7.22 schema-light `codex_events` / `codex_items` store, preserving FDEX owner/task isolation and SSE reconnect recovery.

### Compatibility

The current FDEX bundled fallback is `openai-codex-cli-bin==0.147.0`. Phase 7.31 CI does more than inspect generated strings: it invokes that exact bundled official Runtime with `app-server --help` and requires the Multi-Agent V2 and rollout-budget CLI overrides to parse successfully.

### Phase 7.32 outer resource boundary

Phase 7.32 adds the operating-system boundary below Multi-Agent governance. Real FDEX-provider Codex Hosts run in independent transient systemd services, so `codex app-server` and descendant sub-agents, shell/command processes, stdio MCP helpers, and executable Plugins share cgroup v2 `MemoryMax`, `CPUQuota`, and `TasksMax` limits and are terminated as one tree with `KillMode=control-group`.

Phase 7.31's official concurrency/shared-token controls and Phase 7.32's OS resource/lifecycle controls are complementary layers. Phase 7.32 permits only inventory-revalidated local Plugin install/uninstall when that isolation is actually enforced; Marketplace writes, remote-catalog installs, and `plugin/share/*` remain closed.

See `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`.
