# Coding Agent Native Turn Routing / Coding Agent 原生 Turn 路由

## 目标

FDEX 不再在 Coding Agent 之前用自然语言关键词或“能力分类器”决定一条消息是否值得进入 Agent。

只要用户正在和启用了 `coding_agent` 的智体对话，这条消息就已经属于 Agent 会话：

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

FDEX 只做宿主必须做的确定性工作：owner 身份、项目/worktree 解析、GitHub App 权限、Provider rollout gate、sandbox、审计、持久化和发布权限。是否要读文件、运行命令、修改代码、跑测试，或者直接回答而不调用工具，由 Agent Turn 自己决定。

## 与旧路由的区别

旧实现经历过两层临时修复：

1. 先只把显式 GitHub/仓库执行请求送入 Coding Agent；
2. 再改成 capability-first 分类器，把文件/测试/Git 等请求送入 Agent。

两者仍然在 Codex 之前重建了一套不完整的意图判断。新的规则删除这层边界：Coding Agent 智体的所有消息都进入 Agent Runtime；普通智体才使用通用 `client_ai` 对话路径。

因此下面这些消息在 Coding Agent 中都属于同一个 Agent 入口：

- `读取 server/app/main.py`；
- `运行测试并修复失败项`；
- `当前 GitHub 有几个仓库？`；
- `什么是 Python 的 GIL？`；
- `你好，先说说你能做什么`。

前三者可能需要真实工具或 FDEX host facts；后两者通常可以由 Codex 直接回答而不使用工具。FDEX 不在 Turn 之前替 Codex 做这个决定。

## Project / cwd 边界

原生 Codex 总是在具体 `cwd` 中运行。FDEX 不能为了让“纯概念问题”通过而把用户放进全局 FDEX 源码 workspace，因为那会破坏 owner/project 隔离。

因此每个 Coding Agent Turn 仍必须解析出一个启用项目。解析顺序只负责**选择工作区**，不负责判断意图：

1. 当前消息明确的 `owner/repo`；
2. 当前员工聊天最近一次 durable `coding_agent.task` 的 `project_id`；
3. 最近聊天文本中唯一明确的项目；
4. 当前账号只有一个启用项目时使用它；
5. 否则 fail closed，要求用户明确项目。

显式当前消息项目始终可以覆盖旧 Thread 的项目，从而安全开始新的项目 Thread。

## Thread continuity

员工聊天不再为每条消息无条件创建互不相关的 Codex Thread。

当历史 assistant message 中存在真实 `coding_agent.task` tool evidence，并且：

- task 属于当前 owner；
- `project_id` 与本 Turn 相同；
- task 已 terminal；
- `codex_host_store` 中存在真实 Thread binding；

FDEX 使用现有 `create_codex_continuation(..., fork=False)`，创建新的 FDEX child task/worktree，并通过官方 `thread/resume` 在同一 Codex Thread 上开始下一 Turn。

只有没有可续接的真实 binding 时才 `thread/start`。新 Thread 会一次性带入附近的 Web 聊天 bootstrap context；后续 resume Turn 不重复把历史文本塞进 prompt，因为官方 Codex Thread 已经保存上下文。

## Trusted host facts

GitHub App inventory、FDEX task status 等必须由 FDEX 控制平面获取的事实仍可在 Turn 前确定性收集，但它们不再用于决定“要不要进入 Agent”。它们作为 `FDEX_TRUSTED_TOOL_DATA` 注入 Agent Turn，等价于宿主提供给 Agent 的已执行事实。

模型不能把 provider-side plugin/connector envelope 当成这些事实，也不能声称一个未被 FDEX/Codex Runtime 执行的操作已经完成。

## Attachments

Web Coding Agent chat 现在可把当前官方 Codex Runtime 已支持的媒体直接绑定为 task-scoped `UserInput[]`：

- PNG / JPEG / WebP -> `localImage`，最多 20 MiB；
- MP3 / WAV / M4A -> `localAudio`，最多 50 MiB。

资产继续由 `codex_task_input_store` 做 owner/task 隔离、MIME + magic validation 和受控持久化。

当前 Codex Host 的 task input contract 尚未支持任意 PDF/DOCX 文档，因此这类附件 fail closed，不会回退给通用 AI。

## Engine rollout 仍然独立

“所有 Coding Agent 消息都进入 Agent Runtime”与“生产是否已经允许 official Codex Provider”是两个不同问题。

`FDEX_AGENT_ENGINE=legacy|auto|codex` 以及 Phase 7.33 的 fresh-full Provider rollout gate 继续保持权威。此次路由改造不会为了架构纯度绕过生产 Provider 验证，也不会在已经开始的 Codex Turn 内切换 Provider。

当 engine 为 `codex`（或满足 gate 的 `auto`）时，上述 Agent Turn 由官方 `codex app-server` Thread/Turn loop 执行；legacy 仅保留为明确的迁移/回退配置，而不再作为 Web Coding Agent 与通用 AI 之间的自然语言路由器。

## English summary

Coding-Agent-enabled employee chats are now Agent-first. FDEX no longer classifies user text to decide whether a turn should enter the Agent. Every message goes through the Agent runtime, while FDEX only resolves the authorized project/worktree and host-owned facts. The official Codex engine can then decide inside the Thread/Turn loop whether to answer directly or use tools. Same-chat/same-project turns resume a durable Codex Thread when a real binding exists, and supported Web image/audio attachments are forwarded as official task-scoped Codex UserInput media. Production Codex rollout gates remain unchanged.
