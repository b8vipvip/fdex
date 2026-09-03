# Coding Agent Native Turn Routing / Coding Agent 原生 Turn 路由

## 目标

FDEX 不再在 Coding Agent 之前用自然语言关键词或“能力分类器”决定一条消息是否值得进入 Agent，也不再在 Agent Runtime 内选择 Legacy/Auto 执行核心。

只要用户正在和启用了 `coding_agent` 的智体对话，这条消息就直接进入官方 Codex 执行路径：

```text
User message
    |
    v
FDEX identity / employee / project boundary
    |
    v
Coding Agent task
    |
    +-- new project context --> Codex thread/start
    |
    +-- same chat + same project + durable prior Codex binding --> Codex thread/resume
    |
    v
Codex turn/start
    |
    +-- direct answer, no tools required
    |
    +-- native Codex tool / command / file / test activity
    |
    v
FDEX validates worktree / commit / push / PR authority
```

FDEX 只做宿主必须做的确定性工作：owner 身份、项目/worktree 解析、GitHub App 权限、Provider rollout gate、sandbox、审计、持久化和发布权限。是否要读文件、运行命令、修改代码、跑测试，或者直接回答而不调用工具，由官方 Codex Thread/Turn 自己决定。

## 与旧路由和旧引擎的区别

旧实现经历过三层迁移形态：

1. 只把显式 GitHub/仓库执行请求送入 Coding Agent；
2. capability-first 分类器判断文件/测试/Git 等意图；
3. Coding Agent 已经 Agent-first，但运行时仍保留 `legacy|auto|codex` selector。

Phase 7.36 删除第三层遗留选择。现在：

- `coding_agent=true`：直接进入 Coding Agent task，并且该 task 的唯一执行核心是官方 Codex Host；
- `coding_agent=false`：才允许走普通智体的通用 `client_ai` 对话路径；
- Codex Runtime / Provider 没有通过安全就绪条件：任务 fail-closed；
- 不存在“Codex 不可用所以转旧 Agent”或“转普通 AI”的回退。

因此 `读取 server/app/main.py`、`运行测试并修复失败项`、`当前 GitHub 有几个仓库？`、`什么是 Python 的 GIL？`、`你好，先说说你能做什么` 对 Coding Agent 都使用同一 Codex 入口。是否需要工具由 Codex 自己决定。

## Project / cwd 边界

原生 Codex 总是在具体 `cwd` 中运行。FDEX 不能为了让纯概念问题通过而把用户放进全局 FDEX 源码 workspace，因为那会破坏 owner/project 隔离。

每个 Coding Agent Turn 仍必须解析出一个启用项目。解析顺序只负责选择工作区，不负责判断意图：

1. 当前消息明确的 `owner/repo`；
2. 当前员工聊天最近一次 durable `coding_agent.task` 的 `project_id`；
3. 最近聊天文本中唯一明确的项目；
4. 当前账号只有一个启用项目时使用它；
5. 否则 fail closed，要求用户明确项目。

显式当前消息项目始终可以覆盖旧 Thread 的项目，从而安全开始新的项目 Thread。

## Thread continuity

员工聊天不再为每条消息无条件创建互不相关的 Codex Thread。

当历史 assistant message 中存在真实 `coding_agent.task` tool evidence，并且 task 属于当前 owner、`project_id` 与本 Turn 相同、task 已 terminal、`codex_host_store` 中存在真实 Thread binding 时，FDEX 使用 `create_codex_continuation(..., fork=False)` 创建新的 FDEX child task/worktree，并通过官方 `thread/resume` 在同一 Codex Thread 上开始下一 Turn。

只有没有可续接的真实 binding 时才 `thread/start`。新 Thread 会一次性带入附近的 Web 聊天 bootstrap context；后续 resume Turn 不重复把历史文本塞进 prompt，因为官方 Codex Thread 已经保存上下文。

## Trusted host facts

GitHub App inventory、FDEX task status 等必须由 FDEX 控制平面获取的事实仍可在 Turn 前确定性收集，但它们不再用于决定“要不要进入 Agent”。它们作为 `FDEX_TRUSTED_TOOL_DATA` 注入 Codex Turn。

模型不能把 provider-side plugin/connector envelope 当成这些事实，也不能声称一个未被 FDEX/Codex Runtime 执行的操作已经完成。

## Attachments

Web Coding Agent chat 可把当前官方 Codex Runtime 已支持的媒体直接绑定为 task-scoped `UserInput[]`：

- PNG / JPEG / WebP -> `localImage`，最多 20 MiB；
- MP3 / WAV / M4A -> `localAudio`，最多 50 MiB。

资产继续由 `codex_task_input_store` 做 owner/task 隔离、MIME + magic validation 和受控持久化。当前 Host 尚未支持的 PDF/DOCX 等类型 fail closed，不会回退给通用 AI。

## Provider rollout 是启动门槛，不是引擎选择

Phase 7.33 fresh-full Provider rollout gate 继续保留，但语义已经收口：它只回答“官方 Codex Host 当前是否可以安全启动”，不再参与“选 Codex 还是 Legacy”的决策。

如果没有官方 Runtime、没有 fresh full-compatible Provider、进程树隔离不满足要求或其他 Codex 启动条件失败，Coding Agent task 直接失败并给出原因。Retry 会创建新的 task/worktree 边界并重新执行 Provider selector；已经开始的 Codex Turn 仍然禁止中途切换 Provider。

`FDEX_AGENT_ENGINE` 已从正式配置模型、管理员 UI 和示例环境中移除。旧部署 `.env` 中残留的该变量因为 Settings `extra="ignore"` 而没有任何运行时效果，可以在部署维护时删除。

## English summary

Coding-Agent-enabled employee chats are now Codex-only. FDEX no longer classifies user text before entering the Agent and no longer selects between legacy/auto/codex engines. Every Coding Agent task enters the official Codex Host; FDEX only resolves identity, project/worktree, host-owned facts, security policy, persistence, and publish authority. The Phase 7.33 fresh-full Provider gate remains a fail-closed Codex readiness gate, not an engine selector. Ordinary `client_ai` remains available only to employees without Coding Agent permission.
