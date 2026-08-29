# FDEX × Codex MCP Elicitation / MCP 引导交互

> Phase 7.24。本文只描述 FDEX 对官方 Codex App Server `mcpServer/elicitation/request` 的宿主兼容边界，不代表 FDEX 已开放任意 MCP Server 安装或 ChatGPT Connector 云服务。

## 1. 目标

官方 Codex Runtime 可以在 MCP 工具执行过程中向 App Server client 发起 `mcpServer/elicitation/request`。FDEX 是多用户 Center，因此不能沿用“本机单用户 TUI 可以直接打开链接、启动任意本地 MCP command、共享本机 OAuth 凭据”的安全假设。

Phase 7.24 的原则是：

```text
Codex Runtime
    ↓ server request
mcpServer/elicitation/request
    ↓
FDEX owner/task/Host-session durable broker
    ↓
FDEX Web interaction projection
    ↓
current authenticated owner
    ↓
validated official response
{ action, content, _meta }
    ↓
only the matching Codex stdio Host may claim it
```

FDEX 继续拥有账号、凭据、网络、进程和远程服务权限边界。

## 2. 支持的官方 request identity

FDEX 保留：

- JSON-RPC request id，且数字 id 与字符串 id 不混淆；
- `threadId`；
- nullable `turnId`；
- `serverName`；
- elicitation mode；
- `elicitationId`（URL 模式存在时）；
- 发起请求的 Host session id。

浏览器看到的 form projection 不是协议真相。响应 API 会重新从 owner-scoped SQLite 读取原始 MCP interaction，再生成正式响应。

## 3. `mode=form`

Phase 7.24 支持公开 MCP typed object schema。

支持的字段：

- `string`；
- `number`；
- `integer`；
- `boolean`；
- string single-select enum；
- enum-backed array multi-select。

支持并验证的约束包括：

- `required`；
- `default`；
- `minLength` / `maxLength`；
- `minimum` / `maximum`；
- `minItems` / `maxItems`；
- enum membership；
- string format：`email`、`uri`、`date`、`date-time`。

未知字段、未知 field type、非法 enum 值、重复 multi-select、未知 form field id、无法安全映射的 schema 都 fail closed。

### 浏览器投影

为复用已经经过 Phase 7.23 安全测试的 Web 表单和 SSE，MCP form 在浏览器侧被投影成一个内部 `requestUserInput` 风格卡片：

- 第一题是 MCP action：`accept` / `decline` / `cancel`；
- 后续题目来自 `requestedSchema.properties`；
- property name 被编码成不可与控制字段碰撞的内部 field id；
- 服务端不会根据浏览器声称的 method 决定协议响应。

SQLite 中原始 interaction 的 method 始终保持 `mcpServer/elicitation/request`。

## 4. 响应

正式响应严格为：

```json
{
  "action": "accept | decline | cancel",
  "content": {},
  "_meta": null
}
```

语义：

- `accept` + form：`content` 为验证后的 typed object；
- `accept` + URL：`content = null`；
- `decline` / `cancel`：`content = null`；
- FDEX 当前不生成额外 `_meta`，因此为 `null`。

## 5. 表单值与隐私

MCP form 值可能本身就是敏感信息，因此它们使用 Phase 7.23 的 transient encrypted response channel：

1. Web worker 验证字段；
2. 完整 response JSON 使用 Fernet 加密后进入 `response_cipher`；
3. 只有 owner + interaction + Host-session 全部匹配的 stdio Host 可以 claim；
4. claim 成功时在同一数据库事务中清空 ciphertext；
5. timeout、cancel、Host exit、orphan cleanup 和 account deletion 同样清空 ciphertext。

历史只保存：

- action；
- mode；
- serverName；
- accepted field names/count；
- URL 模式的 destination host。

**不保存表单值到 response summary 或普通 interaction event history。**

原始 MCP request 本身仍属于 owner-scoped interaction state，并随账号清理一起删除。

## 6. `mode=url`

Generic URL elicitation 仅在以下条件下允许用户最终选择 `accept`：

- scheme 必须是 HTTPS；
- 必须存在 hostname；
- URL 不允许 username/password userinfo；
- 长度和控制字符受限；
- `serverName` 不能是 `codex_apps`。

URL 模式代表“用户在浏览器完成外部动作后回来确认”，不是 FDEX 自动替用户执行 OAuth/token exchange。

response summary 只记录 hostname，不记录完整 URL/query。

### `codex_apps`

官方 Codex TUI 对 `codex_apps` 有 ChatGPT/Connector 专用处理。FDEX 在 Phase 7.24 **不复制或伪造这层 ChatGPT 账户权限**：

- 可以安全 decline/cancel；
- 不允许在 FDEX generic MCP bridge 中把 `codex_apps` auth URL 标记为成功；
- 不导入 ChatGPT Connector token；
- 不让一个 FDEX user 共享另一个用户的 Connector/OAuth 状态。

## 7. `openai/form` / `openaiForm`

这两个 mode 是 OpenAI-specific schema surface，不等同于公开 typed MCP form。

Phase 7.24：

- `accept` fail closed；
- `decline` / `cancel` 可正常返回；
- 不用 generic JSON editor 猜测其语义；
- 不宣称任意 FDEX Provider 可以等价实现 OpenAI proprietary form backend。

## 8. 为什么不开放用户自定义 stdio MCP command

官方 Codex MCP config 支持本地 stdio transport，包含类似：

```text
command
args
env
cwd
```

这对单用户本地 CLI 很合理，但在 FDEX Center 上意味着“让远程账号请求 Center 主机启动任意程序”。在缺少外层 whole-process-tree namespace/seccomp/resource/allowlist 边界时，这相当于新增一个服务端任意进程执行入口。

因此 Phase 7.24 明确不做：

- 用户填写任意 MCP `command`；
- 用户上传任意 MCP executable 后由 Center 直接启动；
- 把 Center 服务账号环境变量整体继承给 MCP process；
- 用 Codex `CODEX_HOME` 作为跨用户共享 MCP credential store。

未来如果开放 local MCP，必须先有独立的 owner/project MCP execution envelope。

## 9. OAuth / bearer token 边界

Phase 7.24 不新增 MCP token store。

未来正确结构应是：

```text
FDEX owner-scoped MCP registry
    ├─ server allowlist
    ├─ transport config
    ├─ encrypted OAuth/bearer credential vault
    ├─ per-tool policy
    └─ audit/revocation
             ↓
minimum runtime capability / controlled proxy
             ↓
Codex Runtime
```

而不是：

```text
refresh token / bearer token
        ↓
Codex shell environment
        ↓
arbitrary child process
```

## 10. 多 worker / Host 生命周期

MCP elicitation沿用 Phase 7.23：

- 任意 authenticated Web worker 可以提交用户响应；
- 只有发起 request 的 Host session 可以 claim；
- FDEX worker 间不共享内存 broker；SQLite 是 durable rendezvous；
- 每个 Web worker都安装同样的 MCP public projection；
- Host 退出时 pending/answered interaction terminalize；
- orphan reconciliation 防止旧交互永久阻塞账号删除。

## 11. Phase 7.24 不声称完成的能力

本阶段不等于完整 MCP 平台。仍待后续：

- owner-scoped remote MCP registry；
- FDEX-held MCP OAuth/token vault；
- OAuth callback/state/PKCE broker；
- server/tool allowlist；
- official `item/tool/call` dynamic tool policy；
- local stdio MCP 外层沙箱；
- MCP install/enable lifecycle；
- Android-native MCP form/link UI。

这些能力必须继续遵守 Center `user_id` 隔离，并且不得把“官方 Codex 客户端代码可见”误写成“FDEX 已拥有对应 OpenAI/ChatGPT 云端服务”。
