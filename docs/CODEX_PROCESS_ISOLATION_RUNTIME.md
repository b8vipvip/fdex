# Codex Process Isolation & Runtime Lifecycle / Codex 进程树隔离与 Runtime 生命周期

## 中文

Phase 7.32 把 Codex 的外层安全边界从“管理一个 `codex app-server` PID”提升为“由 FDEX 拥有整个 Codex 进程树”，并加入官方 Codex Runtime 的受管安装、完整性验证、原子切换、回滚和跨 worker 启动/切换 fence。

### 1. 整个进程树由 FDEX 持有

真实 FDEX Provider Host 使用 transient systemd service：

```text
fdex.service
  └─ fdex-codex-<sha256-prefix>.service
       ├─ codex app-server
       ├─ official sub-agents
       ├─ shell / command descendants
       ├─ stdio MCP helpers
       └─ Plugin descendants
```

unit 名由 owner scope + Host isolation key 的 SHA-256 派生，不暴露用户 ID、任务名或项目路径。低层公共 app-server 握手测试若没有 FDEX Provider credential，则继续直接启动 binary；这类测试只验证协议 transport，不冒充生产 Host。

### 2. cgroup v2 资源边界

生产真实 Provider Host 要求 Linux、systemd 和 cgroup v2 的 `cpu`、`memory`、`pids` controller。缺少要求的外层边界时 fail-closed。

每个 transient service 使用：

- `MemoryMax=<Agent memory limit>`
- `CPUQuota=<Agent CPU percent>`
- `TasksMax=<Agent PID limit>`
- `KillMode=control-group`
- `SendSIGKILL=yes`
- `TimeoutStopSec=<grace>`
- `BindsTo=<FDEX service>`
- `After=<FDEX service>`

限制作用于 app-server 与全部后代总树，而不是单一父 PID。

### 3. Provider secret 不进入 argv

FDEX 先生成净化后的 Codex 环境。transient service 通过 `systemd-run --setenv=NAME` 传递环境变量名称，实际 secret value 从净化 launcher 环境继承，不写入 `systemd-run` argv/process listing。

systemd 可能解释 ExecStart 中的 `$` 和 `%`，因此 FDEX 在交给 PID 1 前做 `$$` / `%%` 转义，保证官方 Codex 接收原始配置内容。

### 4. 整树终止

关闭 Host 时：

1. 关闭 JSON-RPC stdin；
2. `systemctl stop <fdex-codex-unit>`；
3. 等待 grace period；
4. 必要时 `systemctl kill --kill-who=all --signal=SIGKILL`；
5. 再次确认 unit 已不 active；
6. 只有确认整树退出才把清理视为成功。

Runtime 切换前同样枚举并停止全部精确匹配 `fdex-codex-[0-9a-f]{32}.service` 的旧树。`BindsTo=fdex.service` 还保证 Center stop/restart 时 Codex trees 一并退出。

### 5. Runtime 官方来源与 supply-chain 校验

Runtime Manager 只接受 OpenAI Codex 官方 GitHub Releases：

- metadata：`api.github.com/repos/openai/codex/releases/...`
- asset URL 必须属于 `github.com/openai/codex/releases/download/<tag>/...`
- tag 必须是 `rust-v<semver>`
- draft/prerelease 拒绝
- Linux x86_64 / arm64 只接受对应官方 musl 资产
- 必须存在官方 `sha256:` digest

下载过程使用 staging：校验 asset id/size、流式 SHA-256、实际字节数、tar 路径安全；拒绝 symlink、hardlink、device、path traversal；只提取唯一预期 Codex executable；不使用 `tar.extractall()`。

### 6. 激活前 Runtime 能力验证

候选 binary 必须通过：

- executable check
- `codex --version` 与目标 release 一致
- Phase 7.31 Multi-Agent/Rollout CLI governance overrides
- `codex ... app-server --help` 成功解析
- managed Runtime manifest SHA-256 校验

因此目录名或 release metadata 本身都不是充分信任依据。

### 7. Runtime switch / launch fence

Phase 7.32 最终安全审查发现了一个跨 worker 竞态：如果切换流程执行“清理旧 trees → 写新 pin”，另一个 worker 可能恰好在二者之间启动旧 Runtime Host。

最终实现增加 Linux `flock` fence：

- Runtime Manager 在“整树清理 + 激活 pin”期间持有 **exclusive switch lock**；
- trusted `codex_env_wrapper.py` 在 transient service 内、`execve()` 官方 Runtime 前持有 **shared launch lock**；
- 第一次受管 Runtime switch 后记录实际有效 Runtime 路径；
- 如果旧 Host 在切换后才进入 exec boundary，会发现路径已 stale 并 fail-closed；
- 如果 Host 先取得 shared lock，switch 随后取得 exclusive lock 时会看到该 transient unit，并在改 pin 前将其清掉。

下载和大文件完整性校验仍发生在 exclusive lock 外，避免升级下载期间冻结正常 Host 启动。

### 8. 激活与回滚

受管版本位于：

```text
server/data/codex-runtimes/releases/<version>/
  codex
  manifest.json
```

激活关键顺序：

1. 下载/验证候选 Runtime；
2. 获取 exclusive Runtime switch fence；
3. 要求 process isolation 可用；
4. 停止并确认所有旧 Codex trees；
5. 更新 `FDEX_AGENT_CODEX_BIN`；
6. 写 Runtime state / previous pin；
7. 记录 fence 的实际有效 binary；
8. 管理路由调度 FDEX service restart。

rollback 同样在 exclusive fence 中完成。previous pin 为空时，校验优先级与 `codex_engine` 保持一致：system `codex` 优先于 bundled fallback，避免“验证 A、实际启动 B”。

### 9. 管理入口

管理员入口：`/admin/agent/runtime`

页面展示当前 Runtime pin/version/path、active validation、process-isolation 状态、managed releases、SHA-256、previous pin 与 upgrade/rollback 操作，不展示 Provider API key 或其他 secret。

### 10. Plugin 最终安全边界

Phase 7.32 **不开放** Plugin install/uninstall。

原因不是 cgroup 失败，而是 cgroup 的能力边界：它解决资源与生命周期，不等价于文件系统 sandbox。对 bundled Codex 0.147 的审计确认，本地 stdio MCP 使用本地 child-process launcher；Plugin MCP 配置可包含 host-side command。因此即使 cgroup enforced=true，Plugin command 仍可能读取 FDEX service user 原本可读的宿主文件。

所以当前策略是：

- `plugin/list` / `plugin/installed` / `plugin/read`：允许，只读；
- `plugin/install`：fail-closed；
- `plugin/uninstall`：fail-closed；
- Marketplace add/remove/upgrade：fail-closed；
- remote catalog install：fail-closed；
- `plugin/share/*`：fail-closed。

兼容写路由只返回明确拒绝并写安全审计，不创建 mutation Host，也不调用 mutation RPC。将来只有在增加独立文件系统/执行沙箱并验证实际 Runtime 启动链后，才可重新评估开放。

### 11. CI / production preflight

Phase 7.32 回归覆盖：

- systemd launch/resource properties
- secret 不进入 argv
- `$` / `%` escaping
- Linux/systemd/cgroup fail-closed preflight
- whole-tree TERM/KILL wiring
- Runtime official release metadata / digest / tar 安全
- immutable staged install / manifest
- kill-before-pin activation order
- Runtime launch/switch fence
- rollback resolver precedence
- Plugin mutation 在 cgroup 生效时仍保持 fail-closed

GitHub hosted runner 不会把 FDEX 本身部署成真实 production systemd unit，因此生产仍依赖运行时 preflight；不满足时真实 Provider Host 和 Runtime activation 必须 fail-closed。

---

## English

Phase 7.32 upgrades the outer Codex boundary from managing one app-server PID to FDEX ownership of the entire Codex process tree. It also adds managed official Runtime installation, integrity verification, atomic activation/rollback, and a cross-worker Runtime launch/switch fence.

### Whole-tree isolation

Real FDEX-provider Hosts run as transient systemd services with cgroup v2 `MemoryMax`, `CPUQuota`, `TasksMax`, `KillMode=control-group`, `SendSIGKILL=yes`, and `BindsTo=<FDEX service>`. The limits and lifecycle therefore cover app-server and all descendants.

Provider secret values are inherited through the sanitized environment and are not embedded in systemd-run argv. `$` and `%` are escaped before PID 1 receives ExecStart arguments.

Shutdown performs graceful stop, bounded wait, whole-unit SIGKILL fallback, and verifies that the unit is no longer active. Runtime switches perform the same cleanup for every exact FDEX Codex transient unit before the active binary pin changes.

### Official Runtime supply-chain boundary

The Runtime Manager accepts only stable OpenAI Codex GitHub Releases with validated tag, exact official asset URL, architecture-specific Linux musl asset, size, and official SHA-256 digest. Downloads are staged and streamed; unsafe tar paths, links, devices, and traversal are rejected; only the expected executable is extracted; `tar.extractall()` is not used.

Candidates must pass executable/version checks, parse the current Phase 7.31 governance overrides with `app-server --help`, and match managed manifest hashes before activation or rollback.

### Runtime launch/switch fence

A final Phase 7.32 review found a cross-worker race between old-tree cleanup and pin update. The final design adds a Linux `flock` fence:

- managed activation/rollback holds an exclusive switch lock across whole-tree cleanup and pin activation;
- the trusted wrapper holds a shared launch lock immediately before `execve()` of the official Runtime;
- after the first managed switch, the actually effective Runtime path is recorded;
- a stale Host that reaches the exec boundary after a switch is rejected;
- a Host that wins the shared launch lock first becomes a transient unit that the subsequent switch must stop before changing the pin.

Release download and heavy verification stay outside the exclusive lock.

Rollback uses the same resolution precedence as the main Runtime resolver when the previous configured pin is empty: system Codex before the bundled fallback.

### Plugin boundary

Phase 7.32 does **not** enable executable Plugin mutation. Cgroup isolation provides resource/lifecycle containment, not filesystem confidentiality. Bundled Codex 0.147 local stdio MCP execution uses a local child-process path, and host-side Plugin MCP configuration may include commands. Therefore an executable Plugin could still read host files available to the FDEX service user even when cgroup enforcement is active.

The final policy is read-only Plugin inventory/detail (`plugin/list`, `plugin/installed`, `plugin/read`) with `plugin/install`, `plugin/uninstall`, Marketplace mutation, remote-catalog install, and `plugin/share/*` all fail-closed. Compatibility write endpoints return an audited rejection without creating a mutation Host or invoking mutation RPCs.

A separate filesystem/execution sandbox plus runtime-level regression verification is required before executable Plugin mutation can be reconsidered.

Admin Runtime management is available at `/admin/agent/runtime`.
