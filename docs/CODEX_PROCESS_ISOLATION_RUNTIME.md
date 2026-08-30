# Codex Process Isolation & Runtime Lifecycle / Codex 进程树隔离与 Runtime 生命周期

## 中文

Phase 7.32 把 Codex 的安全边界从“直接管理一个 `codex app-server` 子进程”提升为“由 FDEX 拥有整个 Codex 进程树”。它同时加入官方 Codex Runtime 的受管安装、完整性验证、原子激活与回滚，并把本地 Plugin install/uninstall 的开放条件绑定到真实进程树隔离状态。

## 1. 进程树归属

真实 FDEX Provider Host 使用 transient systemd service：

```text
fdex.service
  └─ fdex-codex-<sha256-prefix>.service
       ├─ codex app-server
       ├─ official sub-agents
       ├─ shell / command descendants
       ├─ stdio MCP helpers
       └─ executable Plugin descendants
```

unit 名由 owner scope + Host isolation key 的 SHA-256 派生，只暴露固定前缀和哈希，不把用户 ID、任务名、项目路径写入 systemd unit 名称。

低层协议握手测试如果没有 FDEX Provider credential，则继续直接启动官方 binary；它们用于验证公共 app-server transport，不冒充生产 Host。

## 2. cgroup v2 资源边界

Phase 7.32 要求 Linux、systemd 和 cgroup v2 的 `cpu`、`memory`、`pids` controller。生产真实 Provider Host 在缺少这些条件且隔离被要求时 fail-closed。

每个 transient service 使用现有 Agent sandbox 资源配置映射：

- `MemoryMax=<FDEX Agent memory MB>`
- `CPUQuota=<FDEX Agent CPU percent>`
- `TasksMax=<FDEX Agent PID limit>`
- `KillMode=control-group`
- `SendSIGKILL=yes`
- `TimeoutStopSec=<configured grace>`
- `BindsTo=<FDEX service>`
- `After=<FDEX service>`

因此限制作用于 app-server 与其后代总树，而不是只限制父 PID。

## 3. Provider secret 不进入 argv

FDEX 仍先构造净化后的 Codex 环境。启动 transient service 时，环境变量以 `--setenv=NAME` 的“变量名”形式交给 `systemd-run`，实际 secret value 从调用进程的净化环境继承，不出现在 systemd-run argv/process listing 中。

`systemd-run`/PID 1 可能解释 ExecStart 中的 `$` 和 `%`，因此 FDEX 在交给 systemd 前将它们分别转义为 `$$` 和 `%%`，保证官方 Codex 收到原始配置值。

## 4. 整树终止与残留清理

正常关闭：

1. 关闭 JSON-RPC stdin；
2. `systemctl stop <fdex-codex-unit>`；
3. 等待 configured grace period；
4. 若 unit 仍 active，执行 `systemctl kill --kill-who=all --signal=SIGKILL`；
5. 再次确认 unit 已不 active；
6. 只有确认整树清理后才把关闭视为完成。

Runtime 切换前，FDEX 会通过 `fdex-codex-*.service` 枚举候选 unit，但只操作满足精确 `fdex-codex-[0-9a-f]{32}.service` 规则的服务，避免通配符误伤其他 systemd unit。所有匹配 Codex trees 必须停止后才允许修改 Runtime pin。

`BindsTo=fdex.service` 还保证 Center 服务停止/重启时 transient Codex service 随父服务退出。

## 5. 官方 Runtime 来源约束

Runtime Manager 只接受官方 OpenAI Codex GitHub Releases：

- metadata API：`api.github.com/repos/openai/codex/releases/...`
- asset URL 必须精确位于 `github.com/openai/codex/releases/download/<tag>/...`
- tag 必须符合 `rust-v<semver>`
- draft/prerelease 被拒绝
- Linux x86_64 使用 `codex-x86_64-unknown-linux-musl.tar.gz`
- Linux arm64 使用 `codex-aarch64-unknown-linux-musl.tar.gz`

FDEX 不执行下载到的安装脚本，也不通过 npm/pip 的可变解析链升级生产 Runtime。

## 6. 下载与归档完整性

安装使用 staging directory：

1. 读取官方 release asset metadata；
2. 要求 GitHub asset 提供 `sha256:` digest；
3. 校验 asset id 与大小边界；
4. 流式下载并同步计算 SHA-256；
5. 实际字节数必须等于 release metadata size；
6. 实际 SHA-256 必须等于官方 asset digest；
7. tar member 路径必须是安全相对路径；
8. symlink、hardlink、device member 全部拒绝；
9. 只提取唯一预期 Codex executable；
10. binary 大小必须与 tar member 声明一致；
11. staging 验证全部通过后才移动到 immutable version directory。

没有使用 `tar.extractall()`，归档中的其他普通文件不会被安装到 Runtime 目录。

## 7. 激活前 Runtime 能力验证

候选 binary 必须通过：

- executable check；
- `codex --version`，版本必须与目标 release 匹配；
- 注入当前 Phase 7.31 Multi-Agent/Rollout governance CLI overrides；
- `codex ... app-server --help` 必须成功，由官方 CLI 实际解析这些参数；
- 安装后记录 binary SHA-256 与 manifest。

已经安装的 managed Runtime 在后续激活/回滚前会重新检查 manifest SHA-256 和官方 CLI 能力，不只相信目录名。

## 8. 原子激活与回滚

受管版本存放于：

```text
server/data/codex-runtimes/releases/<version>/
  codex
  manifest.json
```

激活顺序：

1. 要求 Phase 7.32 process isolation `enforced=true`；
2. 终止并确认所有旧 `fdex-codex-*.service` process trees；
3. 更新现有 `FDEX_AGENT_CODEX_BIN` pin；
4. 清理 Settings cache；
5. 写入 Runtime Manager state，包括 previous pin；
6. 管理路由调度 FDEX service restart，让后续 Host 全部使用新 pin。

回滚会先重新验证 previous pin；如果是 managed Runtime，则重新校验 manifest/binary；如果 previous pin 为空，则验证 bundled official fallback。切换仍执行同样的整树清理。rollback 后旧 current pin 被保存为新的 previous pin，因此可以反向恢复。

## 9. 管理入口

管理员入口：

```text
/admin/agent/runtime
```

页面展示：

- 当前 Runtime pin / version / path；
- active validation 状态；
- process isolation 状态；
- managed releases；
- SHA-256；
- previous pin / rollback availability；
- upgrade / rollback 操作。

页面不展示 Provider API key 或其他 secret。

## 10. Plugin 安全门耦合

Phase 7.32 不使用“管理员打开某个开关”作为 Plugin 执行安全证明。`plugin/install` / `plugin/uninstall` 每次进入写路径前都调用真实 `codex_process_isolation_status()`；只有 `enforced=true` 才继续。

开放范围仍是最小白名单：verified local install + exact installed-ID uninstall。Marketplace add/remove/upgrade、remote catalog install、`plugin/share/*` 继续 fail-closed。详见 `docs/CODEX_CAPABILITY_CONTROL.md`。

## 11. CI 与生产预检

CI 覆盖：

- systemd launch argument/resource-property construction；
- secret 不进入 argv；
- `$` / `%` escaping；
- fail-closed platform/cgroup preflight；
- whole-unit termination wiring；
- official release metadata validation；
- malicious/path-traversal/link tar rejection；
- Runtime version/governance validation；
- staged immutable install；
- activation order：tree kill 必须先于 Runtime pin write；
- Plugin mutation isolation gate 与 inventory revalidation。

GitHub hosted runner 的单测不会把 FDEX 本身部署成真实生产 systemd service，因此生产部署仍必须依赖运行时 preflight；preflight 不满足时真实 FDEX Provider Codex Host 和 Runtime activation 会 fail-closed，而不是静默降级成无 cgroup 的生产执行。

---

## English

Phase 7.32 upgrades the Codex boundary from managing one direct `codex app-server` child process to FDEX ownership of the complete Codex process tree. It also introduces managed official Runtime install, integrity verification, activation and rollback, and ties local Plugin mutation to actual process-tree isolation.

## 1. Process-tree ownership

Real FDEX-provider Hosts run as transient systemd services:

```text
fdex.service
  └─ fdex-codex-<sha256-prefix>.service
       ├─ codex app-server
       ├─ official sub-agents
       ├─ shell / command descendants
       ├─ stdio MCP helpers
       └─ executable Plugin descendants
```

The unit name is derived from owner scope + Host isolation key through SHA-256. User IDs, task names, and project paths are not exposed in the unit name.

Low-level public protocol handshake tests without an FDEX Provider credential remain direct; they validate app-server transport and are not treated as production Hosts.

## 2. cgroup v2 resource boundary

Phase 7.32 requires Linux, systemd, and the cgroup v2 `cpu`, `memory`, and `pids` controllers. When isolation is required, a real Provider Host fails closed if these prerequisites are unavailable.

Each transient service maps the existing Agent sandbox resource settings to:

- `MemoryMax`
- `CPUQuota`
- `TasksMax`
- `KillMode=control-group`
- `SendSIGKILL=yes`
- `TimeoutStopSec`
- `BindsTo=<FDEX service>`
- `After=<FDEX service>`

The limits therefore apply to the entire app-server descendant tree, not only the parent PID.

## 3. Provider secrets stay out of argv

FDEX first constructs the existing sanitized Codex environment. Environment variables are then forwarded to `systemd-run` by variable **name** using `--setenv=NAME`; the secret value is inherited from the sanitized launcher environment and is not embedded in systemd-run argv/process listings.

Because systemd may expand `$` variables and `%` specifiers in ExecStart arguments, FDEX doubles them to `$$` and `%%` before handing the command to PID 1, preserving the original bytes delivered to Codex.

## 4. Whole-tree termination and stale cleanup

Normal shutdown closes JSON-RPC stdin, stops the transient unit, waits the configured grace interval, escalates to `systemctl kill --kill-who=all --signal=SIGKILL` if needed, and verifies the unit is no longer active.

Before a Runtime switch, FDEX enumerates `fdex-codex-*.service` but only acts on units matching the exact `fdex-codex-[0-9a-f]{32}.service` naming scheme. Every matching Codex tree must be stopped before the Runtime pin can change. `BindsTo=fdex.service` also tears down transient Codex services when the Center service stops or restarts.

## 5. Official Runtime source constraint

The Runtime Manager accepts only official OpenAI Codex GitHub Releases. Metadata, tag format, exact asset URL, architecture-specific Linux musl asset, stable-release status, asset digest, and size are all validated. FDEX does not execute downloaded install scripts and does not use a mutable npm/pip resolution chain to upgrade the production Runtime.

## 6. Download and archive integrity

Installation occurs in a staging directory. FDEX verifies the immutable GitHub SHA-256 digest and exact asset length, rejects unsafe tar paths, symlinks, hardlinks, and device members, extracts only the unique expected Codex executable, checks the binary length, validates it, writes a manifest, and only then moves staging into an immutable version directory. `tar.extractall()` is not used.

## 7. Runtime capability validation before activation

A candidate executable must pass `codex --version` with the expected release version and must successfully parse the current Phase 7.31 Multi-Agent/Rollout governance overrides while executing `app-server --help`. Managed installations record a binary SHA-256 manifest and are reverified before later activation or rollback.

## 8. Atomic activation and rollback

Managed versions live under `server/data/codex-runtimes/releases/<version>/`. Activation first requires enforced process isolation, then terminates all old FDEX Codex trees, updates the existing `FDEX_AGENT_CODEX_BIN` pin, records previous state, and schedules the FDEX service restart. Rollback revalidates the previous managed/configured/bundled target and follows the same whole-tree cleanup path. The former current pin becomes the next previous pin, keeping rollback reversible.

## 9. Admin entry

Administrators use `/admin/agent/runtime` to inspect active Runtime validation, process-isolation status, installed managed versions, SHA-256 values, previous pin, and upgrade/rollback actions. Provider keys and other secrets are not displayed.

## 10. Plugin-gate coupling

Plugin install/uninstall does not trust a configuration toggle as proof of isolation. Each mutation path queries `codex_process_isolation_status()` and continues only when `enforced=true`. The allowlist remains limited to verified local installs and exact installed-ID uninstalls; Marketplace mutations, remote-catalog installs, and `plugin/share/*` remain fail-closed.

## 11. CI and production preflight

CI covers launch construction, resource properties, argv secret hygiene, systemd escaping, platform/cgroup fail-closed behavior, whole-unit termination wiring, official release validation, malicious archive rejection, Runtime governance validation, staged immutable installation, kill-before-pin activation ordering, and Plugin mutation revalidation.

A GitHub hosted test runner does not deploy FDEX itself as the production systemd unit. Production therefore still relies on the runtime preflight: if the required systemd/cgroup boundary is not actually available, real FDEX-provider Codex Hosts and Runtime activation fail closed rather than silently running without the Phase 7.32 boundary.
