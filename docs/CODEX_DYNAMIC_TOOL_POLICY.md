# Codex Dynamic Tool Policy / Codex Dynamic Tool 安全策略

Phase 7.30 explicitly keeps official Codex Dynamic Tools disabled instead of silently treating them as an available feature.

Phase 7.30 明确把官方 Codex Dynamic Tools 保持为禁用状态，而不是把协议中已经出现的类型误当成生产可用能力。

## Protocol reality / 协议现实

Current Codex main exposes experimental `thread/start.dynamicTools` and Dynamic Tool call/response protocol types. A Dynamic Tool is supplied by the hosting client and Codex can call back into that client through an app-server server request.

当前 Codex main 已出现实验性 `thread/start.dynamicTools`，同时包含 Dynamic Tool call/response 协议。Dynamic Tool 的定义由宿主客户端交给 Codex，执行时 Codex 会通过 app-server server request 回调宿主。

FDEX production currently bundles `openai-codex-cli-bin==0.147.0`. That fallback line already contains `DynamicToolSpec`, but its generated `ThreadStartParams` does **not** contain a `dynamicTools` field.

FDEX 当前生产 fallback 为 `openai-codex-cli-bin==0.147.0`。这一版本已经存在 `DynamicToolSpec`，但它生成的 `ThreadStartParams` 中**还没有** `dynamicTools` 字段。

Therefore FDEX must not inject a field that the fallback contract does not declare.

因此 FDEX 不能为了追赶最新 main，向 fallback 的 `thread/start` 偷塞它尚未声明的字段。

## Phase 7.30 enforcement / 7.30 强制策略

- `thread/start` and resume/fork parameter builders continue to omit `dynamicTools`.
- There is no user/API route that registers an arbitrary Dynamic Tool executor.
- The existing Codex Host default remains fail-closed for unsupported app-server server requests.
- The user capability page shows Dynamic Tools as locked and explains the compatibility/security reason.
- Regression tests pin the absence of `dynamicTools` from FDEX thread creation parameters.

具体约束：

- `thread/start` 以及 resume/fork 参数继续省略 `dynamicTools`；
- 不增加允许用户注册任意 Dynamic Tool executor 的 API/UI；
- 现有 Codex Host 对未知 app-server server request 继续默认拒绝；
- 用户能力页明确显示 Dynamic Tools“已锁定”及原因；
- 回归测试固定检查 FDEX Thread 创建参数中不存在 `dynamicTools`。

## Activation requirements / 将来开放条件

Dynamic Tools may only be reconsidered after all of the following exist:

1. runtime capability detection instead of version guessing;
2. owner-scoped Dynamic Tool registry;
3. explicit per-tool authority and input/output limits;
4. a bounded FDEX-controlled client executor;
5. audit/event persistence for every Dynamic Tool call;
6. Phase 7.32 process-tree isolation and deterministic cleanup.

未来只有同时满足以下条件才重新评估开放：

1. 基于 Runtime 实际能力探测，而不是猜版本；
2. owner-scoped Dynamic Tool 注册表；
3. 每个工具独立权限与输入/输出上限；
4. FDEX 控制的有界客户端执行器；
5. 每次 Dynamic Tool 调用都进入审计/事件持久化；
6. Phase 7.32 完成进程树隔离与可靠清理。

This preserves the compatibility rule used throughout the Codex Host work: new upstream capability is not considered enabled merely because a newer schema exists.

这延续了 Codex Host 的兼容原则：上游新 schema 出现，不等于 FDEX 可以在生产中自动把对应能力视为已启用。
