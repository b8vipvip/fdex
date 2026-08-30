# Codex Capability Control / Codex 能力控制

## 中文

Phase 7.30 为官方 OpenAI Codex Skills、Hooks、Plugins API 建立账号隔离控制面；Phase 7.32 在整个 Codex 进程树外层隔离真实生效后，只开放经过严格再验证的本地 Plugin 安装/卸载。FDEX 不重新实现第二套 Plugin Runtime，也不把 Marketplace/Share 写权限泛化给租户。

### 官方协议面

FDEX 继续复用原生 `codex app-server` JSON-RPC Host。当前控制面使用：

- `skills/list`
- `skills/config/write`
- `hooks/list`
- `plugin/list`
- `plugin/installed`
- `plugin/read`
- `plugin/install`（Phase 7.32 条件开放）
- `plugin/uninstall`（Phase 7.32 条件开放）

当前 bundled fallback `openai-codex-cli-bin==0.147.0` 已包含这些 v2 Plugin/Skill/Hook 协议结构，因此实现不是只依赖未来 Codex main。

### 账号与项目边界

每次能力控制请求都使用当前用户独立 `CODEX_HOME` 与净化环境启动短生命周期官方 app-server。GitHub 凭据、Center 密钥及无关服务环境不会交给 Codex。

选择项目不会调用 `prepare_repository()`。项目仓库已存在于当前账号沙箱时可作为 cwd；尚未落地时回退到账号 `CODEX_HOME`，不会因为打开能力页而 clone/fetch GitHub。

### Skill 写入绑定官方 inventory

Skill 启用/禁用不会直接信任浏览器提交的绝对路径。FDEX 会先强制执行 `skills/list(forceReload=true)`，要求当前账号/项目官方清单中恰好存在同一路径，再调用 `skills/config/write`。

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

### Phase 7.32 本地 Plugin 安装安全门

只有 `codex_process_isolation_status().enforced == true` 时，安装路径才可进入。隔离不可用时会在创建 mutation Host 之前 fail-closed。

安装流程：

1. 重新执行官方 local-only `plugin/list`；
2. 要求 `marketplacePath + pluginName` 唯一精确匹配；
3. 要求 `availability == AVAILABLE`；
4. `installPolicy` 只接受官方已知可安装值 `AVAILABLE` 或 `INSTALLED_BY_DEFAULT`，未知/未来值默认拒绝；
5. 通过官方 `plugin/read` 再确认同一目标；
6. 调用 `plugin/install`，且 `remoteMarketplaceName=null`；
7. 再执行 `plugin/installed`；
8. 只有同一 marketplace path 下同名 Plugin 唯一出现且 `installed=true` 才视为成功。

这样同名 Plugin 不能从另一个 marketplace 冒充安装确认，浏览器也不能提交 inventory 外的任意 Plugin 名称。

### Phase 7.32 Plugin 卸载安全门

卸载只接受当前账号官方 `plugin/installed` 中唯一精确匹配的 `pluginId`。调用 `plugin/uninstall` 后必须再次查询 `plugin/installed` 并确认该 ID 已消失；否则返回失败，而不是把 RPC 返回当作完成证据。

### 仍然禁止的写操作

Phase 7.32 **没有**开放以下能力：

- `marketplace/add`
- `marketplace/remove`
- `marketplace/upgrade`
- 远程 catalog Plugin 安装
- `plugin/share/*`

这些入口继续 fail-closed。进程树隔离解决“可执行后代如何受控”，并不自动赋予租户扩张软件来源、共享范围或远程 Marketplace 的权限。

### 用户入口

`/account/agent/capabilities`

页面包含账号/项目扫描范围、Skill 安全开关、Hook 状态、本地 Plugin inventory、官方 `plugin/read`、受 cgroup 安全门约束的安装/卸载，以及 Marketplace/Share 禁用状态。

### 与 Phase 7.32 进程隔离的关系

所有真实 FDEX Provider Codex Host 由 Phase 7.32 transient systemd service/cgroup v2 外层约束。Plugin mutation gate 查询的正是这一外层隔离状态，不使用单独的“配置已启用”布尔值冒充运行时事实。

详见 `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`。

---

## English

Phase 7.30 introduced an owner-scoped control plane for the official OpenAI Codex Skills, Hooks, and Plugins APIs. Phase 7.32 conditionally permits narrowly verified local Plugin install/uninstall only when whole-Codex-process-tree isolation is actually enforced. FDEX does not implement a second Plugin Runtime and does not grant tenants broad Marketplace or sharing mutation authority.

### Official protocol surface

FDEX continues to use the native `codex app-server` JSON-RPC Host. The control plane uses:

- `skills/list`
- `skills/config/write`
- `hooks/list`
- `plugin/list`
- `plugin/installed`
- `plugin/read`
- `plugin/install` (conditionally enabled in Phase 7.32)
- `plugin/uninstall` (conditionally enabled in Phase 7.32)

The bundled fallback `openai-codex-cli-bin==0.147.0` already contains the corresponding v2 Skill/Hook/Plugin protocol structures, so this is not an implementation that only works against a future Codex main branch.

### Owner and project boundary

Each capability request uses a short-lived official app-server with the current owner's isolated `CODEX_HOME` and sanitized environment. GitHub credentials, Center secrets, and unrelated service variables are not passed to Codex.

Selecting a project never calls `prepare_repository()`. An already-materialized owner-scoped checkout may be used as cwd; otherwise discovery falls back to the owner's `CODEX_HOME` without cloning or fetching GitHub merely because the page was opened.

### Skill writes are inventory-bound

A browser-supplied absolute Skill path is never written directly. FDEX first executes `skills/list(forceReload=true)`, requires exactly one matching official Skill in the current owner/project scope, then invokes `skills/config/write`.

### Hooks remain read-only

FDEX surfaces `hooks/list` status but provides no private bypass for creating arbitrary command Hooks.

### Plugin discovery remains local-only

The capability page calls:

```json
{
  "marketplaceKinds": ["local"],
  "forceRefetch": false
}
```

Opening the page therefore cannot become an implicit remote Plugin-catalog egress path. `plugin/read` is also allowed only after marketplace path + Plugin name are revalidated against the current local official inventory.

### Phase 7.32 local Plugin install gate

Installation is reachable only when `codex_process_isolation_status().enforced == true`; otherwise it fails closed before creating a mutation Host.

The install sequence is:

1. refresh official local-only `plugin/list`;
2. require an exact unique `marketplacePath + pluginName` match;
3. require `availability == AVAILABLE`;
4. accept only known installable policies `AVAILABLE` or `INSTALLED_BY_DEFAULT`; unknown/future values fail closed;
5. re-read the exact target through official `plugin/read`;
6. invoke `plugin/install` with `remoteMarketplaceName=null`;
7. query `plugin/installed` again;
8. accept success only when the same marketplace path contains exactly one same-name Plugin with `installed=true`.

A same-name Plugin from another marketplace cannot satisfy confirmation, and callers cannot submit arbitrary Plugin names outside inventory.

### Phase 7.32 uninstall gate

Uninstall accepts only a unique exact `pluginId` from the current owner's official `plugin/installed` inventory. After `plugin/uninstall`, FDEX queries `plugin/installed` again and requires the ID to disappear. A successful RPC response alone is not treated as completion evidence.

### Mutations that remain prohibited

Phase 7.32 does **not** enable:

- `marketplace/add`
- `marketplace/remove`
- `marketplace/upgrade`
- remote-catalog Plugin installs
- `plugin/share/*`

These remain fail-closed. Process-tree isolation controls executable descendants; it does not imply tenant authority to expand software provenance, sharing scope, or remote Marketplace sources.

### User entry

`/account/agent/capabilities`

The page exposes owner/project discovery scope, safe Skill toggles, Hook status, local Plugin inventory, official `plugin/read`, cgroup-gated install/uninstall, and the explicit Marketplace/Share mutation lock state.

### Relationship to Phase 7.32 process isolation

Every real FDEX-provider Codex Host is constrained by the Phase 7.32 transient-systemd-service/cgroup-v2 boundary. The Plugin mutation gate queries that runtime isolation status directly; it does not trust a separate configuration boolean as proof of enforcement.

See `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`.
