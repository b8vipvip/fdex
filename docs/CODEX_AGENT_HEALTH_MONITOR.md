# Codex Agent Health Monitor / Codex Agent 链路监控

## 中文

Phase 7.37 在 Codex-only 架构上增加服务端持续健康监控。它不会重新引入 Agent 路由器，也不会改变 Phase 7.33 fresh-full rollout gate；它的职责是把真正影响 Coding Agent 可用性的链路状态持续采样、持久化并回显到 `/admin/agent`。

监控链路：

```text
FDEX Agent enabled
    -> official Codex Runtime resolve/version
    -> Phase 7.32 systemd/cgroup-v2 process isolation
    -> official codex app-server initialize/initialized handshake
    -> Provider DNS/TLS/HTTP/auth/rate-limit/upstream reachability
    -> Phase 7.33 fresh full compatibility proof
    -> rollout selector selected Provider
    -> READY / DEGRADED / BLOCKED
```

### 1. 两层检测，不把页面刷新变成模型调用

后台每 60 秒执行轻量检查：Runtime、process isolation、Provider 实时 HTTP 链路、compatibility/fingerprint/freshness 和 selector。管理页每 5 秒仅 GET 已持久化快照。

官方 `codex app-server` Host 握手默认每 300 秒执行一次，只做 `initialize -> initialized`，不启动真实用户 Turn，不修改用户仓库，也不是 full-smoke。管理员点“立即检测链路”时会强制刷新 Host 握手。

### 2. Provider 实时链路与 Full Smoke 明确分离

实时链路使用已配置 Provider 的认证信息请求模型元数据入口，只用于识别 DNS/TLS/HTTP、401/403、429、5xx 和网络超时。404/405 等响应仍可证明网络/HTTP 端点可达，但不证明 Codex 语义兼容。

真实 Codex compatibility 仍然只认 Phase 7.33 ledger：`wire -> tools -> full`，生产要求 fresh `full`。因此实时健康检查永远不能把一个未验证 Provider 自动升级为可用于 Coding Agent 的 Provider，也不能越过 fresh-full gate 把已经验证的 Provider 单凭 `/models` 元数据行为判定为 rollout 不可用。

部分 OpenAI-compatible 网关可能允许 Responses/Codex 流量但限制 `/models`。因此 `/models` 返回 401/403 时监控标记 `DEGRADED / PROVIDER_AUTH_PROBE_FAILED`，提示管理员检查元数据鉴权，但不会覆盖真实 full-smoke 的执行权威。真正的 Provider 身份/协议不兼容仍由 fresh-full fingerprint/evidence gate fail-closed。

### 3. 结构化状态

总状态：

- `READY`：Runtime、Host、process isolation、实时 Provider 与 fresh-full selector 均可用；
- `DEGRADED`：轻量 `/models` 鉴权信号异常、429、连续瞬时网络/5xx、Host handshake 异常、full-smoke 即将过期；
- `BLOCKED`：Runtime 不可用、强制 process isolation 不可用、没有 fresh-full Provider，或 fresh-full gate 因 fingerprint/smoke/config 明确拒绝；
- `DISABLED`：Coding Agent 被管理员关闭，但监控仍继续采样链路；
- `UNKNOWN`：监控尚未完成或自身迭代失败。

结构化 code 包括：`RUNTIME_UNAVAILABLE`、`PROCESS_ISOLATION_UNAVAILABLE`、`PROVIDER_CONFIG_INVALID`、`PROVIDER_AUTH_PROBE_FAILED`、`PROVIDER_RATE_LIMITED`、`PROVIDER_UNREACHABLE`、`SMOKE_MISSING`、`SMOKE_EXPIRED`、`SMOKE_EXPIRING`、`FINGERPRINT_MISMATCH`、`COMPATIBILITY_INSUFFICIENT`、`SMOKE_FAILED`、`HOST_UNAVAILABLE` 等。

这些 code 是后续 bounded retry controller 的输入基础；重试策略不需要解析中文错误字符串。

### 4. 多 Worker 单探针

FDEX 当前可以运行多个 Uvicorn worker。每个 worker 都会注册后台 monitor task，但 `server/data/codex-agent-health.db` 中的 SQLite lease 只允许一个 worker 成为当前探针 leader。leader 每轮续租；worker 崩溃后 lease 过期，其他 worker 自动接管，避免每个 worker 每分钟都对 Provider 重复探测。

### 5. 持久化与历史

数据库：

```text
server/data/codex-agent-health.db
```

保存：

- 最新完整、已脱敏健康快照；
- 最近 7 天总状态历史，最多 20,000 行；
- 每个 Provider 的连续实时失败计数；
- 多 worker monitor lease。

Provider API Key 永远不写入健康快照或历史。错误文本在持久化前会做已知 Provider secret 替换。手动检测路由也不会把任意第三方异常原文直接反射给浏览器或写入审计字段。

### 6. 管理控制台

`/admin/agent` 新增“Codex Agent 链路监控”：

- 总状态、结构化 code、原因、检测时间与耗时；
- Runtime version/source/解析延迟；
- app-server Host handshake 与最近握手时间；
- systemd/cgroup v2、Memory/CPU/PID；
- selector 当前实际 Provider；
- 每个 Provider 实时 HTTP 状态、延迟、连续失败；
- full compatibility level、code、smoke age、剩余有效期、原因；
- 最近健康历史；
- “立即检测链路”；
- 跳转到 Full Smoke 和 Runtime 管理。

JSON 管理接口：

```text
GET  /admin/agent/health.json
POST /admin/agent/health/check
```

两者都要求管理员 session；POST 还要求 CSRF。接口返回 `Cache-Control: no-store`。

### 7. 与 fail-closed / retry 的关系

Phase 7.37 只观测，不修改 Agent 执行 gate。`coding_agent=true` 仍然只能进入官方 Codex Core；Runtime 或 fresh-full gate 不满足时仍 fail-closed，不会回退 Legacy Agent 或普通 AI。

后续自动重试应使用这里的结构化状态，例如：

```text
PROVIDER_RATE_LIMITED / PROVIDER_UNREACHABLE / transient HOST_UNAVAILABLE
    -> bounded retry candidate

RUNTIME_UNAVAILABLE / FINGERPRINT_MISMATCH / SMOKE_EXPIRED /
COMPATIBILITY_INSUFFICIENT / CONFIG_INVALID
    -> do not blind-retry; require operator/config recovery
```

`PROVIDER_AUTH_PROBE_FAILED` 只是轻量 `/models` 探针信号，不能单独作为禁止 Codex 或自动重试整个任务的依据；重试控制器必须结合真实 Turn 错误和 fresh-full gate。

即使未来加入自动 retry，也必须继续遵守“Provider 只能在新 task/worktree 边界重新选择，不能在已开始的同一 Codex Turn 中切换 Provider”的 Phase 7.33 约束。

## English

Phase 7.37 adds a persistent server-side health monitor for the Codex-only Coding Agent path. It samples official Runtime resolution, Phase 7.32 process isolation, native app-server handshake health, lightweight Provider reachability, Phase 7.33 full-compatibility freshness/fingerprint state, and the actual rollout selector result.

The background monitor runs lightweight checks every 60 seconds and a native app-server initialize/initialized handshake every 300 seconds. The admin page polls persisted snapshots every five seconds and therefore does not turn UI refreshes into model calls. Full smoke remains a separate explicit compatibility proof and is never replaced by live reachability checks.

An authenticated `/models` metadata probe is advisory only. A 401/403 on that low-cost endpoint produces `DEGRADED / PROVIDER_AUTH_PROBE_FAILED`; it does not override an otherwise valid fresh-full proof because compatible gateways may restrict model-list metadata while still supporting the Responses/Codex path. The Phase 7.33 compatibility ledger remains the authoritative execution gate.

The monitor persists sanitized snapshots, seven days of history, Provider consecutive-failure counters, and a cross-worker SQLite leader lease in `server/data/codex-agent-health.db`. It exposes structured health codes intended to become the input contract for a later bounded automatic retry controller without parsing human-readable error strings.
