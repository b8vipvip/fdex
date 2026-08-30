# Codex Capability Control / Codex 能力控制

Phase 7.30 adds an owner-scoped control plane for the official OpenAI Codex Skills, Hooks and Plugins APIs without creating a second FDEX plugin runtime.

Phase 7.30 为官方 OpenAI Codex 的 Skills、Hooks、Plugins API 增加账号隔离控制面，同时明确不在 FDEX 中重新实现第二套 Plugin Runtime。

## Official protocol surface / 官方协议面

FDEX talks to the native `codex app-server` JSON-RPC boundary already used by the Coding Agent Host.

FDEX 继续复用现有原生 `codex app-server` JSON-RPC Host，而不是添加私有 SDK 兼容层。

Phase 7.30 uses:

- `skills/list`
- `skills/config/write`
- `hooks/list`
- `plugin/list`
- `plugin/installed`
- `plugin/read`

The bundled fallback line currently used by FDEX (`rust-v0.147.0`) already contains the corresponding v2 protocol structures, including `SkillsListParams`, `SkillsExtraRootsSetParams`, `HooksListParams`, `PluginListParams`, `PluginInstalledParams` and `PluginReadParams`. Therefore the control plane is not implemented only against a future Codex main branch.

FDEX 当前 bundled fallback 对应的 `rust-v0.147.0` 已包含上述 v2 协议结构，因此 7.30 不是只对最新 Codex main 生效的前瞻实现。

## Security boundary / 安全边界

### Owner CODEX_HOME / 账号级 CODEX_HOME

Every control request starts a short-lived official app-server using the same owner-scoped `CODEX_HOME` and sanitized process environment as normal Codex tasks. GitHub credentials, Center secrets and unrelated service environment variables are not passed to Codex.

每次能力控制请求都使用当前用户独立 `CODEX_HOME` 与现有净化环境启动短生命周期官方 app-server；GitHub 凭据、Center 密钥及无关服务环境不会交给 Codex。

### No implicit repository network I/O / 页面扫描不隐式访问 GitHub

Selecting a project never calls `prepare_repository()`. If its repository is already present inside the owner sandbox, that local checkout can be used as the discovery cwd. Otherwise the control plane scans the owner `CODEX_HOME` and does not clone/fetch as a side effect of opening the page.

选择项目时不会调用 `prepare_repository()`。项目仓库已经存在于当前账号沙箱时可以作为 cwd；尚未落地时回退到账号 `CODEX_HOME`，不会因为打开能力页而 clone/fetch GitHub。

### Skill writes are inventory-bound / Skill 写入绑定官方清单

A Skill enable/disable request cannot write an arbitrary caller-supplied path directly. FDEX first executes a fresh `skills/list` with `forceReload=true`, requires exactly one matching official Skill path in the current owner/project scope, and only then calls `skills/config/write`.

Skill 启用/禁用不会直接信任浏览器提交的绝对路径。FDEX 会先强制执行 `skills/list(forceReload=true)`，要求当前账号/项目官方清单中恰好存在同一路径，再调用 `skills/config/write`。

### Hooks are read-only in Phase 7.30 / 7.30 Hook 只读

`hooks/list` is exposed for status/inventory. FDEX does not add a separate route that lets a tenant create arbitrary command Hooks.

7.30 仅展示 `hooks/list` 状态，不提供绕过官方安全模型创建任意 command Hook 的 FDEX 私有入口。

### Plugin discovery is local-only by default / Plugin 默认只扫描本地

`plugin/list` is called with:

```json
{
  "marketplaceKinds": ["local"],
  "forceRefetch": false
}
```

This makes page rendering inventory-only and prevents the control page from becoming an unexpected remote catalog/network egress trigger. `plugin/read` is allowed only after the requested marketplace path + plugin name pair is revalidated against that same current local inventory.

能力页默认只查询本地 marketplace，且禁止 `forceRefetch`。`plugin/read` 也必须先重新确认 marketplace path + plugin name 确实出现在当前官方本地清单中。

## Why Plugin mutation is still locked / 为什么 Plugin 安装仍然锁定

The official protocol currently exposes mutation methods such as marketplace add/remove/upgrade, plugin install/uninstall and plugin sharing. FDEX intentionally does **not** route those methods in Phase 7.30.

官方协议已经提供 marketplace 增删升级、plugin install/uninstall、plugin share 等写操作，但 FDEX 在 Phase 7.30 **明确不开放**这些调用。

A plugin can introduce local command/stdio capabilities. On a multi-tenant FDEX Center, enabling those mutations before the outer process tree is resource- and lifecycle-isolated would create a server-side execution path outside the intended task boundary.

Plugin 可能带入本地 command/stdio 能力。在多租户 FDEX Center 上，如果外层 Codex 进程树尚未完成资源和生命周期隔离就允许安装，会形成新的服务端执行入口。

Therefore mutation remains fail-closed until Phase 7.32 provides the outer boundary for the entire Codex process tree:

- cgroup v2 CPU/RAM/PID limits;
- process-tree ownership;
- deterministic tree kill on cancellation/timeout/account deletion;
- Runtime install/verify/upgrade/rollback governance.

所以 Plugin 写操作必须等待 Phase 7.32 完成：

- cgroup v2 CPU/RAM/PID 限额；
- 整个 Codex 进程树归属；
- 取消、超时、账号注销时可靠 tree kill；
- Runtime 安装、验证、升级、回滚治理。

The user page includes a visible “Plugin install safety gate” probe. It never calls the install API; it verifies the pre-7.32 policy remains fail-closed.

用户界面提供“Plugin 安装安全门”验证按钮，但该按钮不会执行安装，只用于确认 7.32 前安全门仍然 fail-closed。

## User entry / 用户入口

`/account/agent/capabilities`

The page contains:

- owner/project discovery scope;
- official Skill inventory and safe enable/disable;
- Hook inventory/status;
- local Plugin marketplace inventory;
- verified `plugin/read` details;
- explicit Plugin mutation lock state.

页面包含：账号/项目扫描范围、官方 Skill 清单及安全开关、Hook 状态、本地 Plugin marketplace 清单、经过重新验证的 `plugin/read` 详情，以及明确的 Plugin 写操作锁定状态。

## Next phases / 后续阶段

### Phase 7.31

Use the official Codex multi-agent/sub-agent core and add FDEX owner hierarchy, count/concurrency and shared-resource governance instead of building a parallel `FdexSubAgentLoop`.

优先使用官方 Codex multi-agent/sub-agent 核心，FDEX 只增加账号层级、数量、并发和共享资源治理，不重新实现一套子 Agent 循环。

### Phase 7.32

Complete the outer Codex process-tree isolation and Runtime lifecycle governance. Only after this gate may Plugin mutation be evaluated for activation.

完成整个 Codex 进程树外层隔离和 Runtime 生命周期治理；只有该安全门完成后，才评估开放 Plugin 写操作。
