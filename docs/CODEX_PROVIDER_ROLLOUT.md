# Codex Provider Compatibility & Rollout / Codex Provider 兼容性与上线封印

## 中文

Phase 7.33 解决生产上线前最后一个代码层缺口：**“供应商普通聊天接口可用”不等于“该供应商能够完整承载官方 Codex Host”。**

FDEX 将通用 Provider 健康状态与 Codex compatibility 完全分离。只有部署中的真实 FDEX Center 使用当前 Provider 凭据、当前有效模型候选、当前官方 Runtime、当前 Multi-Agent governance、当前 cgroup 资源策略完成真实 `full` smoke，Provider 才能进入生产 Codex selector。

`FDEX_AGENT_ENGINE=legacy` 仍是默认值。Phase 7.33 建立的是可验证 rollout gate，不会因为代码合并、CI 绿色或配置中勾选 Responses 就自动切换到 Codex。

## 1. 为什么普通 Provider 测试不够

通用 Provider 测试主要证明：

- Base URL 可访问；
- API Key 能鉴权；
- 普通文本模型能返回正文；
- 某些 vision/image/audio 端点可用。

官方 Codex Host 还依赖真实 Responses/app-server 交互、reasoning Item、command/file-change 生命周期、MCP tool call 与 Multi-Agent V2 collaboration Item。仅在 `protocol_order` 中声明 `responses` 不能证明这些能力。

因此生产 `codex_runtime_status.ready` 不再以“存在一个配置完整的 Responses Provider”为充分条件，而要求 selector 实际找到 fresh full-compatible Provider。

## 2. 独立兼容性账本

兼容记录：

```text
server/data/codex-provider-compatibility.db
```

数据库不保存 Provider API Key 明文。Key 只以不可逆 SHA-256 成分参与整体 fingerprint，使凭据轮换会立即让旧 smoke 失效，又不会复制密钥到 compatibility ledger。

最终 fingerprint schema 为 **v2**，绑定：

- Provider ID；
- Base URL；
- API Key identity hash；
- 主文本模型；
- **完整有效文本模型候选顺序**，包括 `main_text_model` 为空而由 `backup_text_models` 承担 Codex 的合法配置；
- protocol order；
- timeout；
- Codex Runtime path / version / source；
- Phase 7.31 Multi-Agent / rollout-budget CLI governance；
- Phase 7.32 Memory / CPU / PID 资源上限；
- FDEX app version。

上述任一输入变化，旧记录 fingerprint 都不再匹配，必须重新 smoke。v2 升级本身也会让旧 schema 结果失效，这是预期的 fail-closed 行为。

默认 freshness：**168 小时（7 天）**。

## 3. 兼容等级

### `none`

未测试、Host 建立前失败，或没有达到更高等级。

### `wire`

必须通过真实官方链路：

```text
codex app-server initialize
→ thread/start
→ turn/start
→ official notifications
→ turn/completed
```

并返回随机 smoke marker。普通 HTTP 文本响应不能得到 `wire`。

### `tools`

在 `wire` 基础上，模型必须在 FDEX 创建的隔离 scratch workspace 中真实执行工具：

- 官方 Item 至少出现 `commandExecution` 或 `fileChange`；
- 指定 scratch 文件真实存在；
- 文件内容精确匹配随机 marker。

模型只在最终文字中声称“已修改文件”不会通过。

### `full`

生产 selector 要求 `full`。除 `wire` 与 `tools` 外，还必须同时满足：

- 至少观察到官方 `reasoning` Item；
- FDEX 一次性 loopback MCP capability 被真实调用；
- 官方 Item 出现 `mcpToolCall`；
- MCP 服务端记录 exact marker 参数；
- 官方 `collabAgentToolCall` 中出现 **completed `spawnAgent`**；
- 官方 `collabAgentToolCall` 中出现 **completed `wait`**；
- 官方 Item 出现 `subAgentActivity`。

因此 `full` 不是“模型说自己调用了 MCP/子 Agent”，而是多侧可验证的官方事件与真实副作用证据。

## 4. Smoke workspace 与安全边界

每次真实 smoke 创建随机隔离目录：

```text
server/data/codex-provider-smoke/<random>/
  workspace/
  codex-home/
```

它使用与生产一致的：

- official `codex app-server`；
- sanitized Provider process environment；
- Phase 7.31 operator-owned governance overrides；
- Phase 7.32 transient systemd/cgroup process-tree isolation；
- `workspace-write` sandbox；
- web search disabled；
- 不向 shell 继承 Provider/API/GitHub secrets。

Smoke 不使用用户仓库，不 commit、不 push、不创建 PR，不复用用户 durable Thread。完成或失败后，MCP capability 被撤销，scratch workspace 与临时 `CODEX_HOME` 被清理。

管理员入口还要求 Phase 7.32 process isolation `enforced=true`；否则拒绝把本机测试写成 rollout evidence。

## 5. 内置 MCP smoke capability

内部地址：

```text
/internal/codex-provider-smoke-mcp/<capability>
```

安全规则：

- 必须是直接 TCP loopback caller；
- 复用生产 Remote MCP Gateway 的 hardened direct-loopback 判定；
- 除要求 peer address 是 loopback 外，还拒绝 `Forwarded`、`Via`、`X-Forwarded-*`、`X-Real-IP` 等反代标记，避免公网请求经同机 Nginx 后伪装成 `127.0.0.1`；
- capability 是高熵短时随机值；
- SQLite 只存 capability token SHA-256；
- request body 有严格上限；
- 只暴露一个无副作用的 `fdex_smoke_echo`；
- tool argument 必须精确匹配当前随机 marker；
- 服务端独立记录 call count 与最后参数。

所以 MCP full evidence 同时依赖 Codex 官方 `mcpToolCall` 和 FDEX 服务端实际接收到的 capability 调用，单边记录不足以通过。

## 6. Provider 选择与安全 Failover

生产 selector 按 Provider priority 检查：

1. Provider 必须能形成有效 Codex ProviderSpec（Responses / Key / Base URL / 至少一个有效文本模型候选）；
2. 当前 fingerprint 必须与 ledger 一致；
3. 记录必须在 freshness 窗口内；
4. compatibility 至少为 `full`；
5. 最近 smoke 不能有 terminal error。

### 允许的 failover

只有在**用户 Codex Host 尚未启动**时，FDEX 才能跳过 stale / unverified / incompatible 的高优先级 Provider，选择下一个 fresh full-compatible Provider。

这是安全的，因为此时还没有 Provider-specific Turn，也没有被前一个 Provider 修改过的 worktree。

### 禁止的 failover

一旦 `codex app-server` / Turn 开始，Provider 失败即终止当前任务。FDEX 不会在同一个可能已经发生文件修改或 MCP side effect 的 worktree 中切换另一个 Provider 继续生成。

该规则避免：

- 两个模型对半完成修改持有不同假设；
- 新 Provider 把旧 Provider 残留状态误当自己的上下文；
- 重复 command/MCP side effects；
- 不可审计的跨 Provider“续写”。

### Retry 是新的安全边界

Retry 创建新的 FDEX task/worktree boundary，可重新执行 Provider selector。`FDEX_AGENT_ENGINE=auto` 也只允许在 Codex 尚未 ready / Host 尚未开始时回退 legacy；已开始的 Codex 失败不能再被 legacy 在同一 worktree 中接管。

## 7. 所有启动/控制入口使用同一 rollout gate

Phase 7.33 最终安全审查发现，某些模块在 `main.py` 安装 rollout runtime 之前就把旧的 `codex_runtime_status` / `select_codex_provider` import 到模块全局。如果只 patch `codex_engine`，管理员控制面可能仍拿到旧的“配置完整即 ready”结果。

最终 installer 因此显式 rebind：

- `codex_engine.codex_runtime_status`；
- `codex_engine.select_codex_provider`；
- `agent_admin_routes.codex_runtime_status`；
- `codex_runtime_admin_routes.codex_runtime_status`；
- `codex_capability_control.select_codex_provider`；
- `codex_host_runtime.select_codex_provider`。

因此：

- `/admin/agent` 页面；
- 管理员把 engine 切到 `codex` 的 POST；
- Runtime 状态页；
- capability-control 短生命周期 Hosts；
- 用户真实 task Hosts

都受同一个 fresh-full gate 约束，不存在配置-only readiness 旁路。

## 8. 管理入口

管理员页面：

```text
/admin/agent/codex-providers
```

展示：

- 当前 Runtime；
- Phase 7.32 process isolation；
- Provider priority / model / Responses 配置；
- compatibility level；
- fingerprint/freshness 失效原因；
- wire / tools / MCP / subagent / reasoning 实证；
- last checked / latency / Runtime version；
- real full smoke 操作。

页面只展示 masked key。真实 smoke 会产生上游模型调用成本，因此 UI 明确要求管理员确认。

## 9. CI 与真实生产验证的区别

GitHub CI 能验证：

- ledger/fingerprint 逻辑；
- secret 不落盘；
- freshness 和配置漂移失效；
- backup-only model candidate 漂移会失效旧证明；
- reverse-proxy-resistant loopback MCP 行为；
- completed spawn + completed wait + subAgentActivity evidence classifier；
- safe pre-start Provider selection；
- no mid-task fallback；
- Admin/Runtime/capability/user-Host gate wiring；
- FastAPI / Android regressions。

Phase 7.33 最终 PR head `94da1865f29caf043f756348a265a1a9378036a8` 的 FastAPI 结果为 **437 passed / 2 pre-existing skipped**；新增 7.33 security regressions 均真实执行而非 skip。Android unit 与 Debug APK 同 head 成功。Squash merge 后 `main@080c4ba962ce72cd43f0ee0802aef8050b290748` 的独立 Build and Test run `33307653585` 也再次通过 FastAPI、Android unit 和 Debug APK。

GitHub CI **不能证明部署环境里的真实 Provider 是 full-compatible**，因为 CI 不持有生产 Provider secret，也不应该持有。

因此：

- Phase 7.33 代码已验收 ≠ 生产 Provider 已验收；
- 实际 Center 必须运行 full smoke；
- 没有 matching fresh full record 时 Codex selector 保持 not ready；
- `FDEX_AGENT_ENGINE=legacy` 不因代码合并自动改变。

## 10. 生产启用步骤

部署接受后的 `main` 后：

1. 保持 `FDEX_AGENT_ENGINE=legacy`；
2. 确认 Phase 7.32 systemd/cgroup isolation 为 enforced；
3. 打开 `/admin/agent/codex-providers`；
4. 对计划用于 Codex 的真实 Provider 执行 full smoke；
5. 确认目标 Provider 显示 fresh `full`；
6. 再由管理员显式决定是否调整 engine rollout mode；
7. Provider key/model/endpoint、Runtime、governance、resource limits 或 app version 改变后重新 smoke。

## 11. Plugin 安全边界不变

Phase 7.33 不改变 Phase 7.32 的 Plugin 结论。Executable Plugin install/uninstall 继续 fail-closed，直到建立独立 filesystem/execution sandbox，并验证本地 stdio Plugin process 无法读取 Center/service-host 敏感文件。

---

## English

Phase 7.33 closes the repository-side production rollout gap: **a generally healthy Provider is not automatically a Codex-compatible Provider.**

A Provider is eligible for production Codex selection only after the deployed FDEX Center records a fresh `full` smoke using the current credentials, effective text-model candidate ordering, official Runtime, Multi-Agent governance and cgroup resource policy. `FDEX_AGENT_ENGINE=legacy` remains the default.

### Compatibility ledger and fingerprint v2

Records live in `server/data/codex-provider-compatibility.db`. Provider API-key plaintext is never copied into the ledger; only a SHA-256 identity component contributes to the outer fingerprint.

Fingerprint v2 binds Provider identity/endpoint, credential identity, protocol settings, timeout, the **complete effective text-model candidate ordering including backup-only configurations**, Runtime path/version/source, Phase 7.31 governance, Phase 7.32 resource limits and FDEX app version. Any drift invalidates prior evidence.

Default freshness is 168 hours.

### Compatibility levels

- `wire`: real official app-server initialize/thread/turn completion with the required random marker.
- `tools`: official command/file-change evidence plus an actual verified scratch-file side effect.
- `full`: additionally requires official reasoning, a real loopback MCP call with matching server-side evidence, official `mcpToolCall`, completed `spawnAgent`, completed `wait`, and official `subAgentActivity`.

Production selection requires fresh `full` evidence.

### Hardened MCP smoke

The built-in MCP endpoint accepts direct loopback callers only and reuses the production Remote MCP Gateway's reverse-proxy-resistant loopback check. Forwarding/proxy marker headers are rejected even when the TCP peer appears as localhost. Capabilities are short-lived, stored only as hashes and expose one read-only exact-marker echo tool.

### Failover semantics

FDEX may skip stale/unverified Providers only before a user Codex Host starts. Once a Host/Turn starts, Provider failure terminalizes the task; there is no cross-Provider continuation in a potentially modified worktree. Retry creates a fresh task/worktree boundary and may perform Provider selection again.

### One rollout gate everywhere

The final installer rebinds every early-imported readiness/Provider-selection seam used by admin engine switching, Runtime status, capability-control Hosts and user task Hosts. No configuration-only readiness path remains.

### CI is not production Provider proof

The final PR head passed FastAPI with 437 passed / 2 pre-existing skipped, plus Android unit and Debug APK. The merged `main@080c4ba962ce72cd43f0ee0802aef8050b290748` independently passed the same Build and Test workflow. CI still cannot prove a deployed Provider is full-compatible because it does not hold production Provider credentials.

After deployment, run the real full smoke from `/admin/agent/codex-providers`; only a fresh full record unlocks that Provider for Codex selection. Keep the legacy default until that deployed-provider verification and an explicit operator rollout decision are complete.
