# FDEX Codex Engine / FDEX Codex 执行核心

> Phase 7.20 Native Host。中文为默认说明，英文摘要附后。

## 目标

FDEX 的 Coding Agent 不再把自研“模型输出一个 JSON tool → FDEX 执行 → observation 回填模型”的循环作为长期 Agent Core，也不把 OpenAI Codex 的 Rust 源码复制进 FDEX 维护 fork。

Phase 7.20 的长期架构是：

- **官方 OpenAI Codex Runtime** 负责 Agent Core、Thread / Turn / Item、命令执行、文件修改、sandbox、exec policy、skills、hooks、MCP、collaboration/sub-agent 等可移植能力；
- **FDEX** 作为 Codex 的宿主与控制平面，负责用户身份、项目授权、GitHub App、Provider、资源预算、审计、UI、任务生命周期和最终发布；
- 两者之间以官方 **`codex app-server` JSON-RPC protocol** 作为长期兼容边界。

FDEX 已有资产继续保留：

- FDEX 中心账号与 `user_id` owner scope；
- GitHub App Installation 与每用户仓库授权；
- project / task / worktree 隔离；
- SQLite 任务历史、跨 Worker 执行锁、取消与重试；
- GitHub 专用 VLESS 出站；
- FDEX 审计、权限、Provider 与发布策略。

## 为什么 Phase 7.20 不再把 Python SDK 当协议边界

Phase 7.19 通过官方 `openai-codex` Python SDK 启动 Codex Runtime。这是安全的第一步，但 SDK 暴露的方法集合和它绑定的 bundled Runtime 版本可能晚于官方 Codex Release。

Phase 7.20 改为直接实现公开的 app-server JSON-RPC：

```text
FDEX Web / Android
        │
        ▼
FDEX identity / project / task control plane
        │
        ├── legacy → FdexAgentLoop（迁移期回退）
        │
        └── codex
              │
              ▼
      FDEX Codex Native Host
      codex_app_server.py
              │
       JSON-RPC over stdio
              │
              ▼
      official `codex app-server`
              │
   ┌──────────┼──────────┐
   ▼          ▼          ▼
 Core/Exec  Sandbox    Skills/MCP/...
   │
   ▼
FDEX task-isolated worktree
   │
   ▼
FDEX validates resulting Git state
   │
   ├── local commit
   ├── optional push through FDEX GitHub authority
   └── optional Pull Request
```

因此 FDEX 不必等待某个 Python SDK 新增包装函数，才能使用已经由官方 Runtime 暴露的新 app-server 方法或通知。

## Runtime 选择

Phase 7.20 的官方 `codex` 二进制解析顺序：

1. `FDEX_AGENT_CODEX_BIN`：运维明确固定的官方 Codex 二进制；
2. `PATH` 中的系统 `codex`；
3. `openai-codex-cli-bin` 安装的官方 bundled Runtime，作为安全回退。

这允许生产服务器在经过验证后采用较新的官方 Release，同时保留仓库依赖提供的已知可用 fallback。

FDEX 不自动信任任意第三方“Codex compatible”二进制。生产应只使用 OpenAI 官方发布物或由运维从官方源码可复现构建并验证的二进制。

## Native App Server Host

`server/app/codex_app_server.py` 实现一个 schema-light、版本容忍的宿主：

- 启动官方 `codex app-server --listen stdio://`；
- 完成 `initialize` / `initialized` 握手；
- 管理 request id 与异步 response future；
- 接收所有 server notification；
- 接收 server-initiated request；
- 捕获有限 stderr tail 用于诊断；
- 传输中断时 fail pending requests；
- 不把未知 notification 当作致命协议错误。

“schema-light”不是放弃验证。原则是：

- **传输层**允许官方协议向前扩展；
- **FDEX 真正使用某个能力时**，在功能边界验证该 method 的字段和权限；
- 未支持的 server request 一律 fail closed，而不是猜测批准结果。

## Thread / Turn / Item

Phase 7.20 直接调用：

```text
thread/start
turn/start
turn/interrupt
```

并消费官方通知，例如：

```text
thread/started
turn/started
turn/completed
turn/diff/updated
turn/plan/updated
item/started
item/completed
item/agentMessage/delta
item/commandExecution/outputDelta
item/fileChange/patchUpdated
item/mcpToolCall/progress
hook/started
hook/completed
warning/error/...
```

FDEX 的 durable task/event 历史只保存经过筛选的用户可见事件，不直接把原始 JSON payload 全量写入数据库，避免日志把命令输出中的敏感内容扩大持久化范围。

## Owner-scoped CODEX_HOME

Phase 7.19 每个 task 使用独立 `CODEX_HOME`，这会阻止 Codex 自己的 thread/history/skills/plugin/MCP 状态在同一 FDEX 用户任务间延续。

Phase 7.20 改为：

```text
server/data/codex/<fdex-user-id>/
```

一个 FDEX owner 一个 CODEX_HOME。

因此：

- 同一 FDEX 用户未来可以 resume/fork Codex thread；
- 用户级 skills/hooks/plugin/MCP 本地状态可以延续；
- 不同 FDEX `user_id` 从文件系统根目录开始完全隔离；
- Codex 不使用服务器 root 的 `~/.codex`。

Task 的 repository worktree 仍然独立，不因为共享用户级 CODEX_HOME 而共享项目写目录。

## Provider 兼容

Codex 自定义 Model Provider 使用 OpenAI Responses wire API。FDEX 从“供应商管理”选择：

1. 已启用；
2. `protocol_order` 包含 `responses`；
3. 已配置 API Key；
4. 已配置文本模型；
5. 按 FDEX 原有 priority 排序。

Codex Runtime 收到一个名为 `fdex` 的 model provider。API Key 不写入命令行 TOML，而通过专用环境变量注入。

注意：**标记支持 Responses 并不等于已经证明支持 Codex 所需的完整 Responses/tool streaming 语义。** 生产仍必须运行真实 Coding Agent smoke task 后才能把 `codex` 设为默认。

跨 Provider failover 也不能照普通聊天直接实现。Codex Turn 可能已经修改 worktree；中途静默换 Provider 再继续可能造成不可审计的混合执行。后续 failover 必须基于 turn/thread checkpoint 或明确重试语义。

## 密钥隔离

FDEX 服务进程同时可能持有 GitHub App、SMTP、Admin、Provider 等敏感配置。`codex_env_wrapper.py` 在 exec 官方 Runtime 前清洗环境：

- 只保留 PATH、语言/证书/构建变量；
- 保留 owner 专属 HOME/CODEX_HOME；
- 仅当前 Model Provider Key 进入 Codex Runtime；
- GitHub、SMTP、Admin、其他 Provider 密钥不进入 Runtime。

Codex shell 同时配置：

```text
shell_environment_policy.inherit = none
```

只显式恢复必要构建变量。因此模型 Provider Key 不进入仓库命令、测试脚本或用户代码的环境。

## 网络权限

FDEX project 的 `allow_network` 继续是权威设置：

```text
FDEX project.allow_network
        ↓
Codex sandbox_workspace_write.network_access
```

Phase 7.20 仍默认：

```text
web_search = disabled
```

因为 Web Search 是独立的模型/Agent 能力，不能用它绕过项目 shell 网络权限。未来若开放 Web Search，应增加单独的 FDEX 项目权限。

## Interactive approvals / permissions

Native App Server Protocol 会由 server 主动向 client 发起请求，包括：

- command execution approval；
- file change approval；
- permission escalation；
- tool request user input；
- MCP elicitation；
- dynamic tool calls；
- 以及部分认证/attestation 请求。

Phase 7.20 的 transport 已经能接收这些请求，但 FDEX 尚未完成 Android/Web owner-scoped 交互决策 UI，因此当前全部 **fail closed**。

不能为了“功能多”而自动 accept，因为这会绕过 FDEX 项目权限。后续实现必须满足：

```text
Codex server request
        ↓
FDEX validates task owner + project policy
        ↓
Web/Android user decision or strict policy engine
        ↓
FDEX returns protocol-specific typed response
```

## GitHub 权限边界

Codex 不获得：

- GitHub App Installation Token；
- 用户 OAuth/PAT；
- FDEX 维护用 `GITHUB_TOKEN`；
- 服务器个人 SSH Key。

Developer instructions 禁止 Codex自行 commit/push/PR。Turn 完成后由 FDEX：

1. 检查工作区与 commit 差异；
2. 阻止 `.env`（`.env.example` 除外）、`server/data`、`.git` 内部路径；
3. 对合法变更创建本地 commit；
4. 根据 `allow_push` 使用 FDEX GitHub authority 推送 `fdex-agent/*`；
5. 根据 `allow_pr` 创建 Pull Request。

这使 Codex Core 可以持续升级，而 GitHub 仓库权限仍由 FDEX 的 `user_id`、Installation 和 project policy 决定。

## CI 协议验证

Phase 7.20 不只 mock JSON-RPC。

FastAPI CI 会启动 `server/requirements.txt` 实际安装的 **OpenAI 官方 bundled Codex Runtime**，完成真实：

```text
process start
→ initialize
→ initialized
→ native app-server method
→ response
→ shutdown
```

该 smoke test 不调用模型，因此不需要 CI API Key。它用于尽早发现：

- wire protocol 握手漂移；
- bundled Runtime 不可执行；
- app-server method/transport 兼容问题。

生产 Provider/tool 兼容仍需要另一个有真实 Responses 上游的 smoke task。

## 与 openai/codex 全仓库的关系

FDEX 的目标不是“把 Cargo workspace 每个 crate 复制到自己的仓库”，而是最大化使用官方 Runtime 已经组合好的开源能力。

详细兼容分类见：

`docs/CODEX_COMPATIBILITY.md`

其中明确区分：

- 可由官方 Runtime 本地提供、FDEX 应直接采用的能力；
- 需要 FDEX UI/权限桥才能开放的能力；
- 源码开源但依赖 OpenAI/ChatGPT 云后端的能力；
- TUI/测试/发布工具等并非 FDEX Server Runtime 能力的项目。

## 当前仍需继续

Phase 7.20 Native Host 建立长期协议边界，但并不声称 ChatGPT Codex 云产品完全等价。后续重点是：

- 持久化 `codex_thread_id` / `codex_turn_id` 到 FDEX task schema；
- thread resume / fork / steer / compact；
- 完整 Item/Turn 实时 UI；
- 图片、本地附件、audio、skill、mention 输入；
- approval / permission / requestUserInput / MCP elicitation UI；
- Skills / Hooks / MCP / local plugin 管理界面；
- collaboration/sub-agent 状态 UI 与资源配额；
- 将整个 Codex process tree 纳入 FDEX systemd CPU/Memory/PID/并发 envelope；
- 生产 Responses/tool smoke test；
- 安全的官方 Codex Runtime 更新、版本固定与回滚机制；
- 真实验证后再把默认引擎从 `legacy` 迁移为 `auto`/`codex`。

---

## English summary

Phase 7.20 makes the public Codex app-server JSON-RPC protocol the long-term ABI between FDEX and the official OpenAI Codex runtime. FDEX no longer depends on the high-level Python SDK exposing every new runtime feature. The official runtime remains responsible for the agent execution stack, while FDEX remains authoritative for user identity, project/worktree isolation, provider selection, secrets, GitHub App permissions, auditing and publishing. CODEX_HOME is isolated per FDEX owner so native Codex thread and capability state can persist without crossing account boundaries. Unsupported interactive permission requests fail closed until FDEX has an owner-scoped UI/policy bridge.
