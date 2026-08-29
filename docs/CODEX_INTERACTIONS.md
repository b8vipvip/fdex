# Codex Interactive Request Bridge / Codex 交互请求桥

> Phase 7.23。本文定义 FDEX 如何在多用户、多 Uvicorn worker 的 Center Server 中承接官方 `codex app-server` 主动发给 client 的审批与提问请求。

## 目标

FDEX 不是单用户终端。官方 Codex 可以在一个 Turn 中主动发出 JSON-RPC server request，要求 client：

- 批准 command execution；
- 批准 file change；
- 批准额外 permissions；
- 回答 `item/tool/requestUserInput`。

Phase 7.23 的目标不是“把所有请求都变成一个允许按钮”，而是建立下面这条可恢复且可审计的边界：

```text
official Codex app-server
        │ JSON-RPC server request
        ▼
FDEX stdio Host worker
        │ persist request identity
        ▼
owner-scoped SQLite interaction broker
        │
        ├── Web snapshot / SSE ──► authenticated task owner
        │                              │
        │                              └── CSRF-protected response
        ▼
matching Host session atomically claims encrypted response
        │
        ▼
official JSON-RPC response
```

浏览器请求不需要命中持有 Codex 子进程的同一个 Uvicorn worker。

## Supported requests / 当前支持

Phase 7.23 支持：

- `item/commandExecution/requestApproval`
- `item/fileChange/requestApproval`
- `item/permissions/requestApproval`
- `item/tool/requestUserInput`

其他 server request 继续 fail closed。MCP elicitation/OAuth 进入后续阶段，不能因为“同样是 server request”就自动套用当前表单。

## Request identity / 请求身份

一个交互记录同时保存：

- FDEX `owner_id`
- Agent `task_id`
- FDEX Host session id
- JSON-RPC request id
- method
- Codex `threadId`
- Codex `turnId`
- Codex `itemId`
- command approval `approvalId`（如果存在）

JSON-RPC request id 官方允许 string 或 number。FDEX 在 SQLite 中以带类型前缀的 key 保存，因此数字 `1` 与字符串 `"1"` 不会碰撞。

`approvalId` 不能被 `itemId` 替代。官方 command approval 在普通 command 上可以使用 Item id，但 zsh-exec-bridge 子命令或 `writeStdin` callback 可以拥有独立 approval id。

## Cross-worker delivery / 跨 worker 交付

server request 到来后：

1. 持有 stdio 的 worker 创建 durable interaction；
2. request 进入 `pending`；
3. 浏览器通过 task-owner-scoped snapshot/SSE 获取它；
4. 用户提交后，任意 Uvicorn worker 都可以把 response 写成 `answered`；
5. response 只允许绑定该 `host_session_id` 的 stdio worker 原子 claim；
6. claim 成功后立即转为 `responded`；
7. 同一 response 无法被第二次 claim。

这避免把“HTTP 请求恰好落到哪个 worker”当作正确性条件。

## Secret answers / Secret 回答

`requestUserInput` 的官方 question 带 `isSecret`。

FDEX 的规则：

- secret 输入框使用 `type=password`；
- 不把 answer body 写入 Item event；
- 不把 answer body 写入 response summary；
- 不把 answer body写进 task event/audit message；
- waiting response 使用独立 Fernet key 加密；
- key 文件权限为 `0600`，目录保持 owner-only；
- 多 worker 首次创建 key 使用完整临时文件 + 原子 hard-link 发布，其他 worker 不会读到半写/零字节 key；
- matching Host claim 后立即清空 ciphertext；
- Host 退出、取消、超时、orphan reconciliation 与账号注销都会清空 ciphertext。

这不是“长期秘密保险库”。加密字段只用于跨 worker 的短生命周期交付。

## Payload bounds / 协议载荷边界

Phase 7.22 的 raw notification 历史允许使用显式 truncated envelope，因为它是观察记录。

Interactive request/response 不允许这样做。截断一个 JSON-RPC response 会改变协议语义，因此 Phase 7.23 对超过 1 MiB 的 request/response **直接 fail closed**，不会把它改造成另一个 JSON 对象再发送给 Codex。

## FDEX policy remains authoritative / FDEX 策略高于审批点击

用户点击“允许”并不等于获得 FDEX Server 的任意权限。

### Command approval

Phase 7.23 允许的正向 command decision 被严格限制：

- `writeStdin`：只允许 one-time `accept`；
- 带明确 `networkApprovalContext` / network-policy request 的 command：只有项目 `allow_network=true` 才可批准；
- 普通无 network scope 的 command escalation：当前 fail closed。

原因是普通 on-request command escalation 可能表示 unsandboxed retry。在 FDEX 尚未为整个 Codex process tree 提供独立的外层 filesystem namespace 前，允许它可能暴露 Center Server 文件。

拒绝/取消始终可以提交，以便 Turn 能够继续处理失败路径。

### File change approval

正向 file approval 必须：

- 能从同一 owner/task/thread/turn/item 的 durable Item projection 恢复 `changes`；
- Item 未被截断；
- 每一个 `FileUpdateChange.path` 都解析在当前 task worktree 内；
- `grantRoot` 存在时也必须在 worktree 内；
- `acceptForSession` 额外要求明确的 in-worktree `grantRoot`。

无法证明路径安全时 fail closed。

### Permissions approval

正向 permission grant：

- `network` 非空时必须满足项目 `allow_network=true`；
- legacy `read` / `write` roots 必须位于 task worktree；
- `entries` 中只有可以解析成明确 path 的条目可授权；
- `glob_pattern` / `special` 当前 fail closed；
- 未知 permission 字段 fail closed；
- response 只回传 Codex 原始请求中经过验证的 permission profile，不扩大权限。

这样“人类审批”仍处在 FDEX tenant/project policy 之下。

## Browser behavior / 浏览器行为

Codex task 页面不再使用每四秒一次的硬刷新作为活动 Turn 的主要更新机制。

原因：如果 `requestUserInput` 正在输入，尤其是 secret 字段，meta refresh 会清掉尚未提交的回答。

Phase 7.23 使用：

- snapshot 恢复 durable Items + interactions；
- SSE 继续接收 Item / interaction 事件；
- DOM `textContent` / `replaceChildren` 安全渲染；
- 不使用 `innerHTML` 执行协议内容；
- `turn/completed` 后延迟刷新一次，用于加载最终 task/Commit/Push/PR 状态；
- 存在 pending interaction 时不触发该最终刷新。

HTML interaction form 使用 CSRF，提交后 303 回 task detail。API/测试调用者可以显式请求 JSON。

## Host failure and orphan recovery / Host 故障恢复

正常 Host scope 退出时，会把该 Host session 的 `pending` / `answered` interaction 置为 `interrupted` 并清空 ciphertext。

如果 worker 被硬杀，finally 无法执行：

1. Phase 7.21 Thread lease/orphan recovery 先把 stale Thread 从 `running/compacting` 修复为 terminal state；
2. Phase 7.23 snapshot、interaction listing 或账号清理执行 `interrupt_orphans()`；
3. 找不到对应 active Thread 的 pending interaction 被置为 `interrupted`；
4. ciphertext 被清空；
5. UI 不再展示一个永远无法送达的“允许”按钮。

## Account deletion / 账号注销

注销前：

- active Agent task 会阻止注销；
- active Codex Thread/control 会阻止注销；
- stale interaction 先做 orphan reconciliation；
- genuinely active interaction 会阻止注销。

真正清理时顺序为：

```text
Codex interactions (including encrypted pending answer)
→ Codex Item/Event/Delta history
→ Codex Thread/Turn/Control state
→ Agent task records
→ owner worktree/sandbox directories
```

因此交互中的敏感回答不会因为账号注销顺序错误而成为孤儿数据。

## Not yet included / 尚未包含

Phase 7.23 不宣称完成：

- MCP elicitation / MCP OAuth；
- execpolicy amendment UI；
- network-policy amendment UI；
- Android-native approval/question UI；
- arbitrary unsandboxed command escalation；
- filesystem glob/special escalation；
- whole-process-tree outer filesystem namespace/cgroup hardening。

这些必须继续经过 FDEX owner/project/security boundary，而不是为了追求“按钮覆盖率”直接放权。

## English summary

Phase 7.23 adds a durable, owner-scoped bridge for the four primary interactive Codex app-server requests. It preserves real JSON-RPC and Codex identities, routes responses across Uvicorn workers to the matching stdio Host, encrypts secret user-input answers only for the short pending-delivery window, destroys ciphertext after claim, reconciles orphaned requests, and erases interaction state with the account lifecycle. Human approval never overrides FDEX project network or task-worktree policy; unverifiable or broader escalations fail closed.
