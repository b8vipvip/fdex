# Codex Provider Compatibility & Rollout / Codex Provider 兼容性与上线封印

## 中文

Phase 7.33 解决最后一个生产上线缺口：**“供应商普通聊天接口可用”不等于“该供应商能够完整承载官方 Codex Host”。**

FDEX 因此把通用 Provider 健康检查与 Codex compatibility 完全分离。供应商只有在部署中的 FDEX Center 上，以当前官方 Runtime、当前模型、当前 API Key、当前 Multi-Agent governance 和当前 cgroup 资源策略完成真实 full smoke 后，才允许进入 Codex Provider 选择器。

生产默认 `FDEX_AGENT_ENGINE=legacy` 仍保持不变。Phase 7.33 建立的是可验证的 rollout gate，不会因为 CI 或配置声明自动把默认切到 Codex。

## 1. 为什么现有 Provider 测试不够

通用 Provider 测试主要回答：

- Base URL 是否可访问；
- API Key 是否有效；
- 普通文本模型是否能返回内容；
- 某些 vision/image/audio 端点是否可用。

官方 Codex Host 还要求供应商真正兼容 Responses streaming、reasoning Items、工具调用生命周期、command/file-change、MCP 与 Multi-Agent 协作事件。仅在 `protocol_order` 中声明 `responses` 不能证明这些能力。

因此 Phase 7.33 不再把“配置中包含 Responses”作为生产 Codex ready 的充分条件。

## 2. 独立兼容性账本

兼容记录写入：

```text
server/data/codex-provider-compatibility.db
```

该数据库不保存 Provider API Key 明文。API Key 只以 SHA-256 成分进入整体 fingerprint，使密钥轮换可以立即让旧 smoke 失效，同时不会把密钥复制到兼容记录。

fingerprint 绑定：

- Provider ID；
- Base URL；
- API Key 的不可逆 SHA-256 成分；
- 主文本模型；
- protocol order；
- timeout；
- Codex Runtime path/version/source；
- Phase 7.31 Multi-Agent / rollout-budget CLI governance；
- Phase 7.32 Memory/CPU/PID 资源上限；
- FDEX app version。

任一相关输入变化，旧 smoke 即不能继续解锁 rollout。

默认 freshness：**168 小时（7 天）**。过期记录必须重新执行真实 smoke。

## 3. 兼容等级

### `none`

未验证，或在官方 Host 建立前失败。

### `wire`

必须真实完成：

```text
codex app-server initialize
→ thread/start
→ turn/start
→ official notifications
→ turn/completed
```

并返回 smoke marker。只收到普通 HTTP 文本响应不能得到 `wire`。

### `tools`

在 `wire` 基础上，模型必须在 FDEX 创建的隔离 scratch workspace 中真实执行 command/file change：

- 官方 Item 至少出现 `commandExecution` 或 `fileChange`；
- scratch 文件必须真实存在；
- 文件内容必须精确匹配随机 marker。

模型只在文字中声称“已创建文件”不会通过。

### `full`

生产 rollout 要求 `full`。除 `wire` 与 `tools` 外还必须证明：

- 至少观察到官方 `reasoning` Item；
- FDEX 一次性 loopback MCP capability 被真实调用；
- 官方 Item 出现 `mcpToolCall`；
- MCP 服务端记录的参数精确匹配随机 marker；
- 官方 Multi-Agent V2 实际出现 `collabAgentToolCall`；
- collaboration tool 必须包含 `spawnAgent`。

因此 full-compatible 不能由模型自己宣称，也不能靠静态配置推断。

## 4. Smoke workspace 与安全边界

真实 smoke 不使用任何用户仓库。每次测试创建独立：

```text
server/data/codex-provider-smoke/<random>/
  workspace/
  codex-home/
```

测试使用与生产一致的：

- official `codex app-server`；
- sanitized Provider environment；
- Phase 7.31 operator-owned governance overrides；
- Phase 7.32 transient systemd/cgroup process-tree isolation；
- `workspace-write` sandbox；
- web search disabled；
- shell environment does not inherit Provider/API/GitHub secrets。

测试不会 commit、push、创建 Pull Request，也不会复用用户 durable Codex Thread。结束后 scratch workspace 与 CODEX_HOME 被清理。

管理员入口在启动 smoke 前还要求 Phase 7.32 process-tree isolation `enforced=true`，否则拒绝把当前机器上的测试作为 rollout 证据。

## 5. 内置 MCP smoke capability

内部地址：

```text
/internal/codex-provider-smoke-mcp/<capability>
```

该路由：

- 只接受真实 TCP loopback peer `127.0.0.1` / `::1`；
- capability 使用高熵随机值；
- SQLite 中只保存 token SHA-256；
- 短时过期；
- body 有严格大小上限；
- 只暴露一个无副作用工具 `fdex_smoke_echo`；
- tool argument 必须精确匹配当前 smoke marker；
- 服务端独立记录 call count 与最后参数。

所以 `mcpToolCall` 的判定同时要求 Codex 官方 Item 和 MCP 服务端副作用，不依赖单侧事件。

## 6. Provider 选择与 Failover

生产 Codex selector 按现有供应商 priority 顺序检查：

1. Provider 必须完整配置 Responses / API Key / Base URL / text model；
2. 当前 fingerprint 必须与记录一致；
3. 记录必须 fresh；
4. compatibility 必须为 `full`；
5. 最近一次 smoke 不能带 terminal error。

只有满足上述条件的 Provider 才可被选择。

### 允许的 failover

在**用户 Codex Host 尚未启动**时，FDEX 可以跳过 stale、unverified 或不兼容的高优先级 Provider，选择下一个 fresh full-compatible Provider。这不会污染 worktree。

### 禁止的 failover

一旦 `codex app-server` / Turn 已开始，Provider 失败就让当前任务失败。FDEX 不会在同一个已经可能发生文件修改的 worktree 中切换另一个 Provider 继续生成。

这条规则避免：

- 两个模型对同一半完成修改产生不同假设；
- 第二 Provider 误把第一 Provider 的残留状态当成自己的上下文；
- 重试造成重复 tool/MCP side effects；
- Provider failover 变成不可审计的“续写”。

### Retry 是新的安全边界

用户 Retry 创建新的 FDEX task。失败任务如果没有受信 commit，不会把脏 worktree 当作 continuation 基线。因此新的任务可以重新从 rollout selector 选择 Provider。

`FDEX_AGENT_ENGINE=auto` 同样只允许在 Codex **尚未 ready / Host 尚未开始**时回退 legacy；已开始的 Codex 失败不会被捕获后转到 legacy 在同一 worktree 继续。

## 7. 管理入口

管理员页面：

```text
/admin/agent/codex-providers
```

展示：

- 当前 Runtime；
- Phase 7.32 process isolation；
- Provider priority/model/Responses 配置；
- compatibility level；
- fingerprint/freshness 失效原因；
- wire/tools/MCP/subagent/reasoning 实证；
- last checked / latency / Runtime version；
- full smoke 操作。

页面只显示 masked API Key，不输出明文 secret。

full smoke 会真实消耗模型调用，UI 必须明确提示可能产生上游费用。

## 8. CI 与真实生产验证的区别

GitHub CI 可以验证：

- 兼容账本和 fingerprint 逻辑；
- secret 不落盘；
- freshness/config-change invalidation；
- loopback capability MCP 行为；
- full evidence classifier；
- safe pre-start Provider selection；
- no mid-task fallback 语义；
- Admin/UI/main wiring；
- FastAPI 与 Android regression。

GitHub CI **不能证明用户部署的真实 Provider full-compatible**，因为 CI 不持有生产 Provider 凭据，也不应获得这些密钥。

因此：

- 合并 Phase 7.33 代码 ≠ 某生产 Provider 已通过；
- 实际 Center 必须由管理员运行 full smoke；
- 没有 fresh full record 时 Codex selector 应保持 not ready；
- `FDEX_AGENT_ENGINE=legacy` 不因合并本阶段而自动改变。

## 9. Plugin 安全边界不变

Phase 7.33 不改变 Phase 7.32 的 Plugin 结论。Executable Plugin install/uninstall 仍 fail-closed，直到建立独立 filesystem/execution sandbox 并验证本地 stdio Plugin process 的宿主文件访问边界。

---

## English

Phase 7.33 closes the final production-rollout gap: **a generally healthy Provider is not automatically a Codex-compatible Provider.**

FDEX now keeps Codex compatibility separate from generic Provider health. A Provider is eligible for production Codex selection only after the deployed FDEX Center executes a real full smoke using the current official Runtime, Provider configuration, API key, model, Multi-Agent governance, and cgroup resource policy.

`FDEX_AGENT_ENGINE=legacy` remains the production default. This phase creates a verifiable rollout gate; it does not automatically switch the default engine.

### Compatibility ledger

Records live in `server/data/codex-provider-compatibility.db`. Plain Provider API keys are never copied into this database. The key contributes only a SHA-256 component to the overall fingerprint, so key rotation invalidates stale evidence without persisting the secret.

The fingerprint binds Provider endpoint/model/protocol settings, key identity, Runtime path/version/source, Phase 7.31 governance, Phase 7.32 resource limits, and FDEX version. Default validity is 168 hours.

### Levels

- `wire`: real native app-server initialize/thread/turn completion with the expected marker.
- `tools`: official command/file-change evidence plus an actual scratch-file side effect.
- `full`: additionally requires official reasoning, a real loopback MCP call with server-side marker evidence, official `mcpToolCall`, and official Multi-Agent `collabAgentToolCall(spawnAgent)` evidence.

Production selection requires a fresh `full` record.

### Safe smoke environment

Smoke runs in a random scratch workspace and scratch `CODEX_HOME`, never a user repository. It uses the same official app-server, sanitized Provider environment, Phase 7.31 governance, and Phase 7.32 process-tree isolation as production. It does not commit, push, create a PR, or reuse a user's durable Thread, and scratch state is removed afterward.

The admin route refuses to start a production-evidence smoke unless Phase 7.32 process isolation is enforced.

### MCP evidence

The built-in MCP smoke endpoint is loopback-only and capability-protected. Raw capability tokens are not stored; only their hashes are durable. The single read-only echo tool requires an exact random marker and records the real call server-side. Full classification requires both official Codex `mcpToolCall` evidence and the server-side call record.

### Failover semantics

FDEX may skip an unverified/stale higher-priority Provider **before a user Host starts** and select a lower-priority fresh full-compatible Provider. Once a Codex Host/Turn has started, Provider failure terminalizes the task; FDEX never switches Provider inside a potentially modified worktree.

Retry creates a new FDEX task/worktree boundary and may perform Provider selection again. `auto` mode can fall back to legacy only before Codex starts, never after a started Codex task fails.

### Operator surface

`/admin/agent/codex-providers` shows compatibility level, freshness, evidence, Runtime, and masked Provider metadata, and lets an administrator run a real full smoke with an explicit cost warning.

### CI is not production Provider proof

CI verifies the rollout machinery and security semantics but does not have production Provider credentials. Merging Phase 7.33 must never be interpreted as proof that a deployed Provider is full-compatible. The operator must run the real full smoke on the deployed Center. Without a fresh full record the Codex selector remains not ready, and the production default remains legacy.

The Phase 7.32 executable Plugin filesystem-sandbox requirement remains unchanged.
