# Codex Capability Control / Codex 能力控制

## 中文

Phase 7.30 为官方 OpenAI Codex Skills、Hooks、Plugins API 建立账号隔离控制面。Phase 7.32 完成整个 Codex 进程树的 cgroup v2 资源和生命周期隔离后，进一步审计 bundled `openai-codex-cli-bin==0.147.0`，确认这仍不足以安全开放可执行 Plugin 写路径：本地 stdio MCP/Plugin command 可作为 app-server 的本地子进程启动，cgroup 只能限制 CPU、内存、PID 与生命周期，并不提供文件系统保密边界。

因此 Phase 7.32 的最终策略是：**Plugin inventory/read 保持可用，Plugin install/uninstall 及所有 Marketplace/Share 写操作继续 fail-closed。**

### 官方协议面

FDEX 继续复用原生 `codex app-server` JSON-RPC Host。当前开放控制面使用：

- `skills/list`
- `skills/config/write`
- `hooks/list`
- `plugin/list`
- `plugin/installed`
- `plugin/read`

官方协议虽然存在 `plugin/install`、`plugin/uninstall`、Marketplace 与 Share mutation，但 FDEX Center 当前不调用这些写方法。

### 账号与项目边界

每次能力控制请求都使用当前用户独立 `CODEX_HOME` 与净化环境启动短生命周期官方 app-server。GitHub 凭据、Center 密钥及无关服务环境不会交给 Codex。

选择项目不会调用 `prepare_repository()`。项目仓库已存在于当前账号沙箱时可作为 cwd；尚未落地时回退到账号 `CODEX_HOME`，不会因为打开能力页而 clone/fetch GitHub。

### Skill 写入绑定官方 inventory

Skill 启用/禁用不会直接信任浏览器提交的绝对路径。FDEX 先执行 `skills/list(forceReload=true)`，要求当前账号/项目官方清单中恰好存在同一路径，再调用 `skills/config/write`。

### Hooks 保持只读

FDEX 展示 `hooks/list`，但不提供绕过官方安全模型创建任意 command Hook 的私有入口。

### Plugin discovery 始终 local-only

能力页调用：

```json
{
  "marketplaceKinds": ["local"],
  "forceRefetch": false
}
```

因此打开页面不会成为远程 Plugin catalog 的隐式网络出口。`plugin/read` 也必须先重新确认 marketplace path + plugin name 出现在当前本地官方 inventory 中。

### 为什么 Phase 7.32 仍然不开放 Plugin 写操作

Phase 7.32 已确保真实 FDEX Provider Host 运行在 transient systemd service/cgroup v2 边界中，覆盖 app-server、sub-agent、shell/command、stdio MCP 和 Plugin 后代的资源限制与整树终止。

但是 bundled Codex 0.147 的本地 stdio MCP 路径使用本地子进程启动模型。该边界并不会自动阻止 Plugin command 读取 FDEX service user 本来就能读取的宿主文件。因此“cgroup enforced=true”不能作为 Plugin 可执行写路径的充分安全证明。

最终策略：

- `plugin/install`：fail-closed；
- `plugin/uninstall`：fail-closed；
- `marketplace/add/remove/upgrade`：fail-closed；
- 远程 catalog Plugin 安装：fail-closed；
- `plugin/share/*`：fail-closed。

兼容 POST 路由仍保留，避免旧浏览器页面直接 404，但这些路由只记录安全审计并返回拒绝，**不会创建 mutation Host，也不会调用官方 Plugin mutation RPC**。

真正开放 Plugin 可执行写路径必须新增独立文件系统/执行沙箱边界，并对 bundled/managed Runtime 的实际启动链做回归验证后再评估。

### 用户入口

`/account/agent/capabilities`

页面包含账号/项目扫描范围、Skill 安全开关、Hook 状态、本地 Plugin inventory、官方 `plugin/read` 与明确的 Plugin 写安全门状态，不展示安装/卸载按钮。

详见 `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`。

---

## English

Phase 7.30 introduced an owner-scoped control plane for the official OpenAI Codex Skills, Hooks, and Plugins APIs. Phase 7.32 completes whole-process-tree cgroup v2 resource/lifecycle isolation, but a final review of bundled `openai-codex-cli-bin==0.147.0` showed that this is still insufficient to enable executable Plugin mutation safely: local stdio MCP/Plugin commands may be launched as local children of app-server. Cgroups bound CPU, memory, PIDs, and lifecycle; they do not provide a filesystem-confidentiality boundary.

The final Phase 7.32 policy is therefore: **Plugin inventory/read stays available, while Plugin install/uninstall and all Marketplace/Share mutation remain fail-closed.**

### Official protocol surface

FDEX continues to use the native `codex app-server` JSON-RPC Host. The enabled control-plane methods are:

- `skills/list`
- `skills/config/write`
- `hooks/list`
- `plugin/list`
- `plugin/installed`
- `plugin/read`

Although the official protocol exposes Plugin mutation methods, FDEX Center currently does not invoke them.

### Owner and project boundary

Each capability request uses a short-lived official app-server with the current owner's isolated `CODEX_HOME` and sanitized environment. GitHub credentials, Center secrets, and unrelated service variables are not passed to Codex.

Selecting a project never calls `prepare_repository()`. An already-materialized owner-scoped checkout may be used as cwd; otherwise discovery falls back to the owner's `CODEX_HOME` without clone/fetch side effects.

### Skill writes are inventory-bound

FDEX first executes `skills/list(forceReload=true)`, requires exactly one matching official Skill path in the current owner/project scope, and only then invokes `skills/config/write`.

### Hooks remain read-only

FDEX surfaces `hooks/list` status but provides no private bypass for arbitrary command Hook creation.

### Plugin discovery remains local-only

The capability page uses local marketplaces with `forceRefetch=false`. Opening the page therefore cannot become implicit remote Plugin-catalog egress. `plugin/read` is permitted only after marketplace path + Plugin name are revalidated against current local official inventory.

### Why Plugin mutation remains blocked after Phase 7.32

Phase 7.32 constrains real FDEX-provider Hosts and their descendants with transient systemd services and cgroup v2. This covers resource limits and deterministic whole-tree termination for app-server, sub-agents, shell/command descendants, stdio MCP helpers, and Plugin descendants.

However, bundled Codex 0.147 local stdio MCP execution still uses a local child-process path. That path is not equivalent to a filesystem sandbox and does not prevent executable Plugin code from reading host files already readable by the FDEX service user.

Therefore all executable Plugin mutation remains fail-closed:

- `plugin/install`
- `plugin/uninstall`
- `marketplace/add/remove/upgrade`
- remote-catalog Plugin installs
- `plugin/share/*`

Compatibility POST endpoints remain so stale browser pages do not turn into 404s, but they only record the blocked action and return an error. They do not create a mutation Host and do not invoke official Plugin mutation RPCs.

A separate filesystem/execution sandbox boundary plus runtime-level regression verification is required before executable Plugin mutation can be reconsidered.

### User entry

`/account/agent/capabilities`

The page exposes owner/project discovery scope, safe Skill toggles, Hook status, local Plugin inventory, official `plugin/read`, and an explicit Plugin-write safety gate. Install/uninstall controls are not rendered.

See `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`.
