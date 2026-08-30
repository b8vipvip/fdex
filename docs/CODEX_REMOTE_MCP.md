# FDEX Remote MCP Registry & Secure Gateway / Remote MCP 注册表与安全网关

Phase 7.25 introduced the owner-scoped Remote MCP control plane. Phase 7.26 activates **anonymous HTTPS Streamable HTTP MCP** through a FDEX-owned localhost gateway without handing the user-supplied remote URL directly to Codex.

Phase 7.25 建立按账号隔离的 Remote MCP 控制平面；Phase 7.26 在此基础上正式启用 **匿名 HTTPS Streamable HTTP MCP**，但 Codex 不直接连接用户填写的远端 URL，而是只能连接 FDEX 自己控制的 localhost 安全网关。

## Threat model / 威胁模型

Official Codex supports remote Streamable HTTP MCP, static HTTP headers, bearer-token environment variables, OAuth and local stdio MCP processes. FDEX is a multi-tenant Center, so those capabilities cannot be exposed directly.

The two primary threats are:

1. a user-controlled remote URL becoming an SSRF path to loopback, private networks, link-local/cloud metadata or other internal infrastructure;
2. a user-controlled stdio definition becoming arbitrary server-side process execution.

Additional Phase 7.26 threats include DNS rebinding after save-time validation, redirect escape, reverse-proxy exposure of a localhost endpoint, capability leakage through URL/access logs, and live-task authority drift when an MCP registry row is edited while a task is running.

官方 Codex 支持 Remote MCP、HTTP Header、bearer token、OAuth 和本地 stdio MCP。FDEX 是多租户中心服务，因此不能直接透传这些能力。除了 SSRF 与任意进程启动风险，7.26 还必须处理保存后的 DNS rebinding、重定向逃逸、反向代理暴露 localhost 端点、capability 进入 URL/访问日志，以及任务运行期间修改注册项导致的权限漂移。

## Phase 7.25 registry / 7.25 注册表

Each registry entry is owned by exactly one Center `user_id` and stores only:

- opaque registry id;
- name;
- HTTPS URL;
- enabled/disabled policy;
- explicit tool allowlist;
- last save-time public DNS admission snapshot;
- startup timeout;
- tool timeout;
- timestamps.

The registry contains **no bearer token, OAuth token, arbitrary remote HTTP header, command, args, environment or cwd**.

每条记录严格绑定一个 Center `user_id`。注册表不保存 bearer token、OAuth token、任意远端 HTTP Header，也不提供 `command/args/env/cwd`。

### URL admission

Registry URLs must be:

- `https://` only;
- port 443 only;
- hostname present;
- no URL username/password;
- no query string or fragment;
- direct IP literals globally routable;
- DNS names resolvable to globally routable addresses only;
- mixed public/private DNS answers rejected.

保存与重新启用时都会执行以上准入检查。停用是安全逃生动作，不依赖 DNS 当前是否健康，因此即使域名已被污染或失效，账号仍可立即停用。

## Phase 7.26 runtime architecture / 7.26 运行架构

```text
Codex Runtime
    │
    │ Streamable HTTP
    │ URL = http://127.0.0.1:<fdex_port>/internal/codex-mcp/<lease_id>
    │ Header = X-FDEX-MCP-Capability: <ephemeral 256-bit capability>
    ▼
FDEX localhost MCP gateway
    │
    ├─ direct loopback + capability validation
    ├─ owner/task/server/revision lease validation
    ├─ live registry enabled/tool policy validation
    ├─ tools/call allowlist enforcement
    ├─ request-time DNS resolution
    ├─ reject every non-public answer
    ├─ pin connector to admitted IP addresses
    ├─ keep original hostname for TLS SNI/certificate verification
    ├─ no redirects
    ├─ no remote auth challenge forwarding
    └─ no system proxy inheritance
    ▼
Anonymous HTTPS Remote MCP server
```

Codex never receives the original remote MCP URL in its task config. It receives only a loopback URL and a short-lived local capability. The gateway resolves the remote hostname immediately before each outbound request, rejects any non-public answer, and supplies only those just-admitted IP addresses to a custom `aiohttp` resolver. The HTTP URL still contains the original hostname, so TLS SNI and certificate verification remain bound to that hostname.

Codex 不会在任务 MCP 配置中拿到远端 URL。它只拿到 `127.0.0.1` URL 与短期本地 capability。FDEX 在**每一次**远端请求前重新解析 DNS，拒绝任何非公网地址，并把本次连接固定到刚验证过的 IP；与此同时 URL 仍保留原域名，因此 TLS SNI 与证书验证仍针对原域名完成。

## Capability lifecycle / Capability 生命周期

Every enabled registry entry gets a fresh task-scoped lease when a new Codex task, resume or fork starts.

The raw capability:

- is generated from cryptographically random bytes;
- is never stored in SQLite;
- is represented in SQLite only by SHA-256;
- is not placed in the localhost URL;
- is sent in `X-FDEX-MCP-Capability` only on the local Codex→FDEX request;
- is never forwarded to the remote MCP server;
- is not added to the Codex shell/process environment;
- is revoked when the task scope exits;
- has a six-hour absolute expiry as crash fallback.

每个新任务 / resume / fork 都重新签发 task-scoped lease。capability 原文不落库、不进入 URL、不进入远端请求、不进入 Codex shell 环境；任务 scope 退出即撤销，同时保留 6 小时硬过期作为 worker 崩溃后的兜底。

### Registry revision binding / 注册项版本绑定

A lease stores the registry row's `updated_at` value at issuance. Every request compares that revision with the live registry row.

Any edit, disable or re-enable changes `updated_at` and immediately invalidates the old lease. This prevents an already-running task from silently being retargeted to a new URL or expanded tool policy after its capability was issued.

lease 在签发时绑定注册项 `updated_at`。任何修改、停用、重新启用都会改变版本并让旧 lease 立即失效，防止运行中的任务被静默切换到新 URL 或扩大后的工具权限。新配置只能由后续新任务 / resume / fork 重新签发。

## Destination enforcement / 目的地址强制

Save-time DNS validation is only the first gate. Phase 7.26 repeats DNS validation at request time and then pins the actual TCP connector to the admitted addresses.

Properties:

- every resolved IPv4/IPv6 address must be globally routable;
- loopback/private/link-local/reserved/metadata-style destinations fail closed;
- DNS cache is disabled for the pinned request connector;
- the custom resolver refuses any hostname other than the admitted original hostname;
- `aiohttp` is configured with `trust_env=False`, so host `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` do not redirect MCP traffic;
- the upstream request uses normal TLS verification against the original hostname;
- redirects are disabled and 3xx responses become FDEX 502 instead of being exposed to Codex.

保存时 DNS 检查只是第一道门。7.26 在请求时重新检查并把 TCP 连接固定到刚验证过的公网 IP，同时显式禁用系统代理继承和 HTTP 重定向。

## Local endpoint isolation / 本地端点隔离

The gateway route is `/internal/codex-mcp/{lease_id}`. It is not an account API.

A request must have:

1. a direct loopback peer;
2. no standard reverse-proxy forwarding marker such as `Forwarded`, `Via`, `X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Host` or `X-Forwarded-Proto`;
3. a valid task capability header.

The capability remains the primary authentication boundary. Reverse-proxy marker rejection is defense in depth so a normal public Nginx/Caddy route cannot turn an external caller into an apparent loopback client at the FastAPI layer. Operators should still avoid publishing `/internal/codex-mcp/` through an external reverse proxy.

网关要求直接 loopback peer、无标准反代转发标记并持有正确 capability。capability 才是真正认证边界；反代头拒绝是额外防御。部署层仍应避免把 `/internal/codex-mcp/` 对公网反代。

## Tool least privilege / 工具最小权限

An enabled server must have at least one explicit tool allowlist entry. An empty list never means "all tools".

The allowlist is enforced twice:

1. Codex receives `enabled_tools`, so unlisted tools are not registered for the model;
2. the FDEX gateway parses every JSON `tools/call` request and rejects any tool name outside the live owner allowlist before network I/O.

启用项必须配置明确工具白名单。Codex 配置先过滤一次，FDEX 网关在真正发出 `tools/call` 前再次检查，因此即使 Runtime 侧未来出现行为变化，网关仍保留最终授权边界。

## Header policy / Header 策略

Only MCP transport headers are forwarded from Codex to the remote server, such as content type, protocol version, MCP session id and SSE resume id.

FDEX never forwards:

- `X-FDEX-MCP-Capability`;
- `Authorization`;
- `Cookie`;
- arbitrary user headers;
- reverse-proxy forwarding headers.

The gateway also suppresses upstream 401/403/407 authentication challenges and returns a generic 502. Phase 7.26 therefore cannot accidentally trigger Codex's own MCP OAuth flow against a user-controlled endpoint.

网关不会向远端转发本地 capability、Authorization、Cookie 或任意用户 Header；401/403/407 也不会把认证挑战透传给 Codex。因此 7.26 仍然是**匿名 Remote MCP**，不会意外进入 Codex 自己的 OAuth 流程。

## Streaming and limits / 流式与限制

- MCP POST bodies are limited to 8 MiB and must be inspectable JSON so `tools/call` authorization cannot be bypassed with opaque bodies.
- POST/DELETE use the configured tool timeout.
- GET/SSE is allowed to remain open for the streaming transport lifecycle.
- upstream response bodies are streamed rather than accumulated in FDEX memory.

POST 最大 8 MiB 且必须是可解析 JSON；POST/DELETE 使用工具超时，GET/SSE 可保持长连接；远端响应采用流式转发而不是整体读入内存。

## Account lifecycle / 账号生命周期

Permanent account deletion removes Remote MCP leases before deleting the owner registry rows. Registry export still contains only non-secret user-owned control-plane fields; lease tokens are never exportable because raw tokens are never persisted.

永久注销先清理 lease，再删除账号注册表。账号数据导出仍只包含非敏感注册字段；原始 capability 因为从未持久化，所以不存在可导出的秘密记录。

## Explicit non-goals after Phase 7.26 / 7.26 后仍明确不做

Phase 7.26 does **not** provide:

- bearer-token storage/injection;
- OAuth login/callback/token storage;
- arbitrary remote HTTP-header storage;
- ChatGPT Connector authentication proxying;
- user-configurable local stdio MCP `command/args/env/cwd`;
- a general secret vault exposed to Codex.

These require a later owner/server-scoped credential broker and secret lifecycle design. Local stdio MCP additionally requires a whole-process-tree execution boundary before it can be considered safe in a multi-tenant Center.

7.26 仍不提供 bearer token、OAuth、任意远端 Header、ChatGPT Connector 认证代理或用户自定义 stdio MCP。下一阶段若继续做认证 Remote MCP，必须由 FDEX 持有并按 owner/server 最小范围注入凭据，而不是把长期秘密交给 Codex shell。
