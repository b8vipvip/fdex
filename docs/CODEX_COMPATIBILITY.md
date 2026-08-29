# FDEX × OpenAI Codex Compatibility / FDEX × OpenAI Codex 兼容规范

> Phase 7.20。本文回答“`openai/codex` 仓库所有开源项目是否都应该应用到 FDEX，以及怎样达到最大兼容”。

## 结论

**不能把“整个仓库所有 crate 都复制进 FDEX”定义为完美兼容。**

`openai/codex` 是一个大型 Rust workspace，其中同时包含：

- Codex Agent 的本地执行核心；
- App Server Protocol；
- CLI / TUI 等客户端；
- sandbox / exec / policy；
- Skills / Hooks / MCP / Plugins；
- Thread / State / Worktree；
- 远程环境和 cloud/backend client；
- 与 ChatGPT/OpenAI 后端绑定的服务客户端；
- 测试、构建、发布、平台专用工具。

因此 FDEX 的正确兼容目标是：

> **对官方 Codex Runtime 的公开 app-server protocol 做原生宿主兼容，完整利用可移植的本地开源能力；对需要用户交互的能力增加 FDEX owner-scoped 权限/UI 桥；对依赖 OpenAI/ChatGPT 私有云服务的能力明确标记为 backend-bound，并使用官方服务或 FDEX 自有替代实现，而不是宣称任意 Provider 下“完美兼容”。**

## 为什么以 App Server Protocol 为兼容边界

官方 Codex 已经把 Thread / Turn / Item、事件、审批、Skills、Apps、MCP、配置等富客户端能力通过 `codex app-server` 暴露出来。

FDEX 若逐 crate 复制：

- 会形成长期 Rust fork；
- 上游每次安全修复和协议变化都要手工同步；
- 容易把本应由 Codex 内部保持一致的 core / sandbox / exec / protocol 版本拆散；
- 反而降低与官方 Codex 的兼容性。

FDEX 采用：

```text
FDEX control plane
        ↕
official app-server JSON-RPC
        ↕
official Codex Runtime
```

意味着一个官方 Runtime Release 自己携带彼此匹配的 core、exec、sandbox、skills、MCP 等实现。

## A. 应直接采用：本地 / 可移植开源能力

下面这些能力原则上应由官方 Codex Runtime 提供，FDEX 不重新实现 Agent 算法本体。

| Codex 领域 | FDEX 采用方式 | 目标状态 |
|---|---|---|
| `core`, `core-api`, `protocol` | 通过官方 Runtime + App Server | 全面采用 |
| `app-server`, `app-server-protocol`, transports | FDEX 原生 JSON-RPC host | Phase 7.20 已开始 |
| `exec`, shell command, apply-patch | 由 Codex Runtime 执行 | 采用 |
| `sandboxing`, `linux-sandbox`, `bwrap` | 官方 Runtime 内部执行；外层再叠 FDEX 资源 envelope | 采用 |
| `execpolicy`, permissions | Runtime 执行；FDEX 负责用户/项目策略桥 | 采用 |
| `file-system`, file search/watcher | Runtime 原生工具与事件 | 采用 |
| `worktree`, git utilities | Codex 可用于本地分析；FDEX 保留最终 GitHub authority | 条件采用 |
| `thread-store`, `state`, history | owner-scoped CODEX_HOME | 采用 |
| Skills / `ext/skills` | Runtime discovery/read/execute | 计划开放 |
| Hooks | Runtime discovery/notifications | 计划开放 |
| local MCP / `ext/mcp` / MCP server | Runtime MCP stack | 计划开放 |
| local plugin / marketplace infrastructure | Runtime 本地插件机制 | 计划开放 |
| collaboration / agent roles / sub-agent | Runtime 原生 Item/Thread 能力 | 计划开放 |
| diagnostics | 可用于 FDEX Runtime 健康页 | 计划采用 |
| local model provider infrastructure | FDEX Responses Provider → Codex model provider | 已采用 |
| local Ollama / LM Studio support | 若与 FDEX Provider 策略兼容，可作为可选后端 | 后续评估 |
| code-mode local components | 通过公开 Runtime API、权限化开放 | 后续评估 |

“直接采用”不代表把 crate source vendoring 到 FDEX；正常情况是**运行官方组合后的 Codex binary**。

## B. 可以兼容，但必须经过 FDEX 权限 / UI 桥

这些能力代码和协议本身是可用的，但 FDEX 是多用户服务端，不能照本地单用户 CLI 的交互假设直接开放。

### 1. Command / file approval

App Server 可向 client 主动请求：

- command execution approval；
- file change approval；
- permissions escalation。

FDEX 必须验证：

```text
request thread/task
→ FDEX owner_id
→ project policy
→ requested filesystem/network scope
→ Web/Android 当前用户确认或受控 policy engine
→ typed approval response
```

不能让模型自己批准自己的权限升级。

### 2. `tool/requestUserInput`

Codex 可以在任务中主动问用户问题。

FDEX 需要把该 request 变为：

- durable task pending state；
- Android/Web 实时问题卡；
- 只允许 task owner 回答；
- Worker 重启后仍能恢复。

### 3. MCP elicitation / OAuth

MCP 服务器可能需要表单、OAuth 或用户确认。FDEX 必须：

- 按 `user_id` 隔离 MCP auth；
- OAuth state 与 owner/session 绑定；
- 不把其他账号的 MCP token 写进共享 CODEX_HOME；
- 根据 FDEX 管理策略限制可连接服务器。

### 4. Multimodal inputs

公开 `UserInput` 已包含：

- text；
- remote/local image；
- remote/local audio；
- skill；
- mention。

FDEX 可以完整映射 Android/Web 附件，但必须先把附件 materialize 到该 task/owner 可读的安全路径，并限制路径逃逸和生命周期。

### 5. Skills / Hooks / Plugins

本地能力可以由 Runtime 发现，但服务端多租户需要额外策略：

- owner 独立安装根；
- project allowlist；
- 插件脚本执行资源限制；
- MCP server 权限；
- 禁止插件读取 FDEX 服务端密钥；
- UI 明确区分“本地插件”与“远程 OpenAI 插件服务”。

### 6. Sub-agent / collaboration

Codex 可产生 collaboration/sub-agent Item。FDEX 应把它纳入：

- 一个父 task 的资源预算；
- 最大子 Agent 数；
- CPU/RAM/PID 总额；
- owner/project/worktree 权限；
- 实时状态 UI。

不能让一个用户通过无限 spawn 绕过全局并发配额。

## C. 源码可见，但依赖 OpenAI / ChatGPT 后端

这是“不能承诺任意 FDEX Provider 下完美兼容”的核心原因。

部分 Codex 代码是**OpenAI 云服务的客户端实现**。客户端源码开源，不等于对应服务器、数据、账户授权、调度或目录服务也开源并可由 FDEX任意替换。

典型类别包括：

- ChatGPT 登录、账号/workspace 服务；
- Codex Web / cloud task 相关 backend；
- remote plugin catalog / sharing / install backend；
- hosted Apps / Connectors 的部分服务能力；
- remote control enrollment/relay；
- 某些 attestation / workload identity / enterprise compliance 服务；
- OpenAI feedback/analytics 云接收端；
- 依赖 OpenAI 权威 model catalog / entitlement 的行为。

对于这些能力，FDEX 有三种合法选择：

1. **官方模式**：用户真的使用对应 OpenAI/ChatGPT 服务时，按官方协议接入；
2. **FDEX-native adapter**：在公开协议允许的地方，用 FDEX 自己的目录、任务、认证或服务替代；
3. **明确不可用**：没有安全、合法、语义等价替代时返回 unavailable，不伪造成功。

不得把“代码仓库里有一个 client”描述成“FDEX 已经拥有 OpenAI 云端服务”。

## D. 不应作为 FDEX Server Agent Core 移植的项目

部分 workspace 项目虽然开源，但它们是其他产品层或工程辅助，不是 FDEX 服务端 Agent 能力缺口。

### CLI / TUI

`codex` CLI / TUI 是终端客户端。FDEX 已有 Android/Web UI，因此：

- 可以借鉴其事件展示和交互模式；
- 可以用 CLI 做运维/诊断；
- 不需要把 TUI 嵌进 FDEX 服务端。

### 平台专用 sandbox

FDEX Linux Center 应采用 Linux sandbox 能力。Windows sandbox 只在未来 FDEX 真正运行 Windows Worker 时才相关。

### test support / build / release tooling

测试 fixture、release binary builder、lint 工具等应按开发需要使用，而不是包装成用户 Agent feature。

## GitHub 能力为什么仍由 FDEX 控制

Codex 本地 Agent 能够执行 `git`，但 FDEX 是多用户 SaaS/Center。把 GitHub Installation Token直接交给 Agent 会破坏已有安全边界。

因此长期设计保持：

```text
Codex
  └─ local repository/worktree analysis + edits/tests

FDEX
  ├─ validates changed paths
  ├─ local commit authority
  ├─ user GitHub App Installation
  ├─ allow_push / allow_pr
  ├─ short-lived downscoped token
  └─ push / PR / audit
```

这不是降低 Codex 能力，而是把“代码智能执行”和“远程仓库授权”分层。

## Model Provider 兼容边界

FDEX 可以把 Responses-compatible Provider交给 Codex，但兼容分三级：

### Level 1 — Wire compatible

能接受 `/responses` 基本请求。

不足以证明能运行 Coding Agent。

### Level 2 — Codex tool compatible

能正确支持 Codex 所需的：

- streaming；
- tool calls / tool outputs；
- reasoning/tool interleaving；
- error/retry semantics；
- 模型特定参数。

这是 FDEX 切换 `codex` 默认前必须 smoke 验证的最低级别。

### Level 3 — Feature compatible

进一步支持特定模型才具有的能力，例如复杂 reasoning、sub-agent 策略或特定 Responses 扩展。

FDEX 不应该因为 Provider 自称 “OpenAI compatible” 就把它自动标记为 Level 3。

## “完美兼容”的可验证定义

FDEX 不使用模糊的“100% compatible”宣传。针对一个具体官方 Codex Runtime 版本，兼容状态应按以下维度报告：

```text
Runtime process                     PASS/FAIL
initialize/initialized              PASS/FAIL
Thread lifecycle                    PASS/FAIL
Turn lifecycle                      PASS/FAIL
Item/event decoding                 PASS/FAIL
command/file execution              PASS/FAIL
sandbox/network policy              PASS/FAIL
approval bridge                     PASS/FAIL/NOT_ENABLED
multimodal                          PASS/FAIL/NOT_ENABLED
skills                              PASS/FAIL/NOT_ENABLED
hooks                               PASS/FAIL/NOT_ENABLED
MCP                                 PASS/FAIL/NOT_ENABLED
plugins local                       PASS/FAIL/NOT_ENABLED
plugins remote/OpenAI backend       OFFICIAL_ONLY/UNAVAILABLE
sub-agent/collaboration             PASS/FAIL/NOT_ENABLED
thread resume/fork/steer            PASS/FAIL/NOT_ENABLED
FDEX GitHub publishing boundary     PASS/FAIL
FDEX owner isolation                PASS/FAIL
Provider Codex tool compatibility   PASS/FAIL/UNTESTED
```

只有这些自动化/真实测试通过，才报告相应能力兼容。

## Runtime 版本策略

FDEX 应维护两条版本通道：

- **bundled fallback**：由仓库依赖锁定、CI 每次真实启动验证；
- **operator/current official**：运维可以通过 `FDEX_AGENT_CODEX_BIN` 指向经过验证的更新官方 Release。

未来应增加：

- 官方 Release metadata 查询；
- 平台/架构匹配；
- SHA-256 / Sigstore 验证；
- staging smoke；
- 一键切换/回滚；
- 版本兼容矩阵记录。

不能在生产任务运行中自动替换 Codex binary。

## Apache-2.0

`openai/codex` 仓库声明 Apache-2.0。FDEX 可以在许可证条件下使用、修改和分发相关开源代码。

当前架构优先运行官方 binary/protocol，而不是复制 Rust core，因此长期合并冲突和源码归属问题更少。如果未来确实复制或修改某段 OpenAI 源码，必须保留适用的许可证、版权和 NOTICE 要求，并在 FDEX 第三方声明中记录来源与版本。

## Roadmap

Phase 7.20 之后按以下顺序推进，而不是逐 crate 盲目搬运：

1. Native App Server transport + real Runtime CI；
2. persistent thread/task mapping；
3. rich Item/Turn streaming UI；
4. approval / permission / user-input bridge；
5. multimodal + skills + mentions；
6. MCP + hooks；
7. local plugin management；
8. sub-agent resource governance；
9. remote exec environments where needed；
10. Codex process-tree cgroup；
11. official Runtime updater/rollback；
12. backend-bound feature adapters only when FDEX has a real equivalent service.

最终目标不是“FDEX 仿 Codex”，而是：

> **FDEX 成为官方开源 Codex Runtime 的多用户、安全、可替换 Provider、GitHub App 隔离的完整宿主平台。**

---

## English summary

FDEX should not vendor every crate from the OpenAI Codex workspace. Its compatibility boundary is the official public app-server protocol. Portable local capabilities should run inside the official Codex runtime; interactive permissions and tools need FDEX owner-scoped policy/UI bridges; features whose open-source code is a client for proprietary OpenAI/ChatGPT backend services are official-only or require a genuine FDEX-native replacement. Compatibility is reported per capability and per runtime version, never as an unqualified claim that every repository component works with every arbitrary model provider.
