# FDEX Codex Engine / FDEX Codex 执行核心

> Phase 7.19 foundation. 中文为默认说明，英文摘要附后。

## 目标

FDEX 的 Coding Agent 不再长期依赖自研的“模型输出单个 JSON 工具调用 → FDEX 执行 → 再把 observation 塞回模型”的简单循环。Phase 7.19 开始接入 OpenAI 官方开源 Codex 作为编码执行核心，同时保留 FDEX 已经建立的控制平面：

- FDEX 中心账号与 `user_id` owner scope；
- GitHub App Installation 与每用户仓库授权；
- 项目 / task / worktree 隔离；
- SQLite 任务历史、跨 Worker 执行锁、取消与重试；
- GitHub 专用 VLESS 出站；
- FDEX 审计、权限与发布策略。

FDEX 不 fork `openai/codex` Rust 源码。服务端使用 OpenAI 官方 `openai-codex` Python SDK；该 SDK 启动与自身版本匹配的官方 Codex CLI Runtime，并通过 `codex app-server` 协议驱动 Thread / Turn / Item。

## Phase 7.19 架构

```text
FDEX Web / Android
        │
        ▼
FDEX account / project / task control plane
        │
        ├── legacy → FdexAgentLoop（兼容回退）
        │
        └── codex  → official openai-codex SDK
                         │
                         ▼
                  official Codex app-server/core
                         │
                         ▼
                  task isolated worktree
                         │
                         ▼
                 local edits / commands / tests
                         │
                         ▼
             FDEX validates resulting Git state
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        FDEX local commit      no changes
              │
              ▼
      FDEX GitHub authority layer
              │
        push branch / create PR
```

## 为什么不直接 fork Codex

官方 Codex 已经把执行核心通过 app-server/SDK 暴露给富客户端。FDEX 若复制 Rust 核心，会承担长期同步上游安全修复、协议变化、沙箱变化、模型能力变化和工具实现的维护成本。通过官方 SDK，FDEX 可以升级 Codex Runtime，而自身只维护控制平面与适配层。

## Provider 兼容

Codex 自定义 Model Provider 使用 OpenAI Responses wire API。Phase 7.19 从 FDEX“供应商管理”里选择：

1. 已启用；
2. `protocol_order` 包含 `responses`；
3. 已配置 API Key；
4. 已配置文本模型；
5. 按 FDEX 原有 priority 顺序优先。

Phase 7.19 暂时选择一个 Responses Provider 交给一次 Codex Thread。FDEX 原有跨 Provider 自动故障转移仍由 legacy 引擎使用；Codex 原生 Provider failover 会在后续阶段单独设计，避免在一次可能已经修改文件的 Codex Turn 中途静默换 Provider。

## 引擎开关

`FDEX_AGENT_ENGINE`：

- `legacy`：现有 FDEX Agent Loop；Phase 7.19 初始默认。
- `codex`：必须使用官方 Codex；SDK/Runtime/Responses Provider 不就绪时任务 fail closed。
- `auto`：Codex 就绪时使用 Codex，否则在任务开始前回退 legacy。

`auto` 只在 Codex 尚未开始修改 worktree 时回退；不会在 Codex 已执行到一半后把半成品静默交给另一个 Agent 引擎继续。

## 密钥隔离

官方 SDK 默认启动子进程时会从父进程构造环境。FDEX 服务进程同时持有 GitHub App、SMTP、管理后台等敏感配置，因此 Phase 7.19 增加 `codex_env_wrapper.py`：

1. 可信 FDEX wrapper 首先接收 SDK 启动；
2. 在 `exec` 官方 Codex Runtime 前清空环境；
3. 只保留 PATH、语言/证书/构建工具变量、task 专属 HOME/CODEX_HOME 和当前 Model Provider Key；
4. GitHub、SMTP、Admin、其他 Provider 密钥不进入 Codex Runtime。

Codex 自身仍需要当前 Model Provider Key 请求 `/responses`。为了防止 Coding Agent 的 shell 命令读取该 Key，Thread 同时配置：

```text
shell_environment_policy.inherit = none
```

并只恢复 PATH/HOME/LANG/CI/JAVA_HOME/ANDROID SDK 等构建变量。模型 Provider Key 不进入 shell tool 环境。

## GitHub 权限边界

Codex 不获得：

- GitHub App Installation Token；
- 用户 OAuth/PAT；
- FDEX 服务器维护用 `GITHUB_TOKEN`；
- 服务器个人 SSH Key。

Developer instructions 明确禁止 Codex `git push` / 远程 PR。Codex 只在 task worktree 工作。Turn 完成后 FDEX：

1. 检查变更路径；
2. 若触碰 `.env`、`server/data`、`.git` 内部路径则阻止 commit/push；
3. 对合法未提交变更使用 `FDEX Agent <agent@fdex.local>` 身份创建本地 commit；
4. 仅当项目 `allow_push=true` 时使用 FDEX 现有 GitHub authority 推送 `fdex-agent/*`；
5. 仅当 `allow_pr=true` 且已经成功 push 时创建 PR。

## Thread 与持久化

Phase 7.19 的 Codex Thread 使用 `ephemeral=true`。FDEX 自己已经持久化 task、event、结果、branch、commit、PR，因此不依赖服务器 root 的 `~/.codex` 会话历史。

每个任务使用：

```text
server/data/codex/<owner>/<task-id>/
```

作为独立 `CODEX_HOME`，避免不同 FDEX 用户/任务共享 Codex 登录态或线程状态。

## 当前仍需继续的迁移

Phase 7.19 是 foundation，不宣称已经达到 ChatGPT Codex 的全部产品体验。后续重点：

- 将 Codex command execution 纳入 FDEX systemd cgroup 的统一 Memory/CPU/PID/并发上限；
- 更完整映射 Item/Turn streaming 到 Android/Web 实时 UI；
- 图片、本地附件、Skill/Mention 输入；
- Codex Thread resume/steer/interrupt 与 FDEX task continuation；
- Responses Provider 的真实 tool-call 兼容性探测；
- Provider failover 策略；
- MCP / Skills / sub-agent 能力的 FDEX 权限化开放；
- 真实生产 smoke test 通过后，将默认引擎从 `legacy` 迁移到 `auto`/`codex`。

---

## English summary

Phase 7.19 introduces the official OpenAI Codex Python SDK and matching Codex runtime as a new FDEX Coding Agent engine without forking Codex. FDEX remains the control plane for accounts, project/worktree isolation, GitHub App authority, task persistence, auditing, and outbound GitHub networking. Codex receives only a task worktree and one Responses-compatible model provider. It never receives GitHub credentials. FDEX sanitizes the Codex process environment and configures Codex shell tools with a no-inheritance environment policy so the model provider key cannot leak into repository commands. The legacy engine remains available during rollout.
