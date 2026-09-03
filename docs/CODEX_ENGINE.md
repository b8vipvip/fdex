# FDEX Codex Engine / FDEX Codex 执行核心

> Phase 7.20 Native Host → Phase 7.36 Codex-only Agent Core。

## 目标

FDEX 的 Coding Agent 不维护另一套模型 JSON-tool Agent Core，也不 fork OpenAI Codex Rust 源码。官方 **OpenAI Codex Runtime** 是 Coding Agent 的唯一执行核心；FDEX 是它的宿主与控制平面。

长期边界：

- **官方 Codex Runtime**：Agent Core、Thread / Turn / Item、命令执行、文件修改、sandbox、skills、hooks、MCP、collaboration/sub-agent 等原生能力；
- **FDEX**：用户身份、项目授权、GitHub App、Provider、资源预算、审计、UI、任务生命周期、worktree 与最终发布权限；
- **协议边界**：官方 `codex app-server` JSON-RPC protocol。

```text
FDEX Web / Android
        |
        v
FDEX identity / project / task control plane
        |
        v
FDEX Codex Native Host
        |
   JSON-RPC over stdio
        |
        v
official `codex app-server`
        |
   Core / Exec / Sandbox / Skills / MCP / Multi-Agent
        |
        v
FDEX task-isolated worktree
        |
        v
FDEX validates Git state and owns commit / push / PR
```

Phase 7.36 删除 `legacy|auto|codex` Agent engine selector。只要智体启用了 Coding Agent 权限，它的任务就必须使用官方 Codex Host。Codex 未就绪时 fail-closed，不会转旧 FDEX Agent loop，也不会转普通 AI。

## Native App Server Host

`server/app/codex_app_server.py` 实现 schema-light、版本容忍的宿主：启动官方 `codex app-server --listen stdio://`，完成 `initialize/initialized`，管理 request/response future，接收 notification 与 server-initiated request，并在 transport 中断时 fail pending requests。

传输层允许官方协议向前扩展；FDEX 真正启用某项能力时在功能边界验证字段和权限。未知或未支持的 server request fail closed。

## Thread / Turn / Item

核心调用包括：

```text
thread/start
thread/resume
thread/fork
thread/compact/start
turn/start
turn/steer
turn/interrupt
```

FDEX 持久化官方 Thread/Turn/task binding、完整受控事件流与 Item projection。Web Coding Agent 在同一员工聊天、同一项目、存在真实 durable Codex binding 时自动 resume 同一 Thread，再启动新的 Turn。

## Owner-scoped CODEX_HOME

每个 FDEX owner 使用独立：

```text
server/data/codex/<fdex-user-id>/
```

同一账号可以延续 Codex thread/history/skills/hooks/plugin/MCP 状态，不同账号文件系统根边界完全隔离。Task repository worktree 仍按任务隔离。

## Provider 与 rollout

Codex 自定义 Model Provider 使用 Responses wire API。生产 selector 不只检查配置完整性，而要求 Phase 7.33 compatibility ledger 中存在与当前 Provider/Runtime/governance/resource fingerprint 匹配的 fresh `full` proof。

Provider rollout 现在只是 **Codex readiness gate**：

- 有 fresh full-compatible Provider + 官方 Runtime：允许启动 Codex Host；
- 没有：任务 fail-closed；
- Host 启动前可以跳过 stale/unverified Provider；
- Host/Turn 一旦开始，禁止中途切换 Provider；
- Retry 创建新的 task/worktree boundary 后重新选择 Provider。

不存在回退到其他 Agent Core 的语义。

## 密钥隔离

`codex_env_wrapper.py` 在 exec 官方 Runtime 前清洗环境。Codex Runtime 只获得当前 Model Provider 所需凭据和必要构建环境；GitHub、SMTP、Admin、其他 Provider 密钥不会进入 Runtime。Codex shell 使用 `shell_environment_policy.inherit = none`，模型 Provider Key 也不会进入仓库命令、测试脚本或用户代码环境。

## 网络与进程树权限

FDEX project 的 `allow_network` 映射到 Codex workspace sandbox network policy，Web Search 默认独立禁用。整个 Codex app-server 与全部 descendants 受 Phase 7.32 transient systemd/cgroup v2 Memory/CPU/PID/lifecycle 边界约束。

## Interactive approvals / permissions

官方 server request 经 FDEX owner/project policy、交互存储与 Web/Android 决策桥处理。Human approval 不能越过 FDEX filesystem/network/GitHub policy。未支持或无法验证的 permission/plugin/tool 状态继续 fail closed。

## GitHub 权限边界

Codex 不获得 GitHub App Installation Token、用户 PAT/OAuth token、FDEX `GITHUB_TOKEN` 或服务器个人 SSH Key，也不负责 commit/push/PR。

Turn 完成后 FDEX：

1. 验证 worktree 与保护路径；
2. 阻止 `.env`（`.env.example` 除外）、`server/data`、`.git` 内部路径；
3. 对合法变更创建本地 commit；
4. 根据 `allow_push` 使用 FDEX GitHub authority 推送 `fdex-agent/*`；
5. 根据 `allow_pr` 创建 Pull Request。

## Multimodal / Skills / MCP / Multi-Agent

现有 Native Host 已承载：

- official `UserInput[]` text/localImage/localAudio/Skill/Mention；
- command/file/permission approvals + requestUserInput；
- MCP elicitation 与 owner-scoped Remote MCP security stack；
- Skills/Hooks/local Plugin capability control；
- official Multi-Agent V2 governance；
- Runtime upgrade/rollback 与 process-tree isolation；
- Provider full-smoke rollout seal。

这些能力都围绕同一个官方 Codex Thread/Turn Core，而不是 FDEX 自建平行 Agent scheduler。

## Coding Agent 与普通智体

执行边界现在非常明确：

```text
employee.coding_agent == true
    -> Coding Agent task
    -> mandatory official Codex Host

employee.coding_agent == false
    -> ordinary FDEX client_ai conversation path
```

FDEX 不再分析“这句话是不是编程意图”来决定 Coding Agent 是否应该进入 Codex。对于启用了 Coding Agent 的智体，概念回答、代码读取、命令、测试、修改、Git 操作等全部由 Codex Turn 自己决定是否需要工具。

## 旧配置迁移

`FDEX_AGENT_ENGINE` 已从正式 Settings、管理员 UI 与 `.env.example` 移除。旧部署 `.env` 中残留的值会被 Settings `extra="ignore"` 忽略，没有运行时效果，可以在部署维护时删除。

---

## English summary

FDEX now uses the official OpenAI Codex Runtime as the **only** Coding Agent execution core. The long-term ABI remains the native `codex app-server` JSON-RPC protocol. FDEX owns identity, project/worktree isolation, Provider proof, secrets, resource governance, auditing and GitHub publish authority; Codex owns the Agent Thread/Turn/Item execution stack. The former legacy/auto/codex selector is removed. A Coding Agent task either starts a verified official Codex Host or fails closed; it never falls back to a legacy FDEX Agent loop or ordinary AI.
