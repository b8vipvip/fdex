# FDEX Remote MCP Registry / Remote MCP 注册表

Phase 7.25 adds the **owner-scoped control plane** for remote MCP servers. It intentionally does **not** activate those servers inside Codex Runtime yet.

Phase 7.25 新增 **按 FDEX 账号隔离的 Remote MCP 控制平面**。本阶段故意**不把这些端点激活到 Codex Runtime**。

## Why registry first / 为什么先做注册表

Official Codex supports streamable HTTP MCP servers and can also support bearer-token environment variables, HTTP headers and OAuth. It also supports local stdio MCP processes with `command`, `args`, `env` and `cwd`.

FDEX is a multi-tenant Center. Passing those capabilities through directly would create two different security problems:

1. a user-controlled HTTP MCP URL can become an SSRF path into loopback, private networks, metadata endpoints or another tenant's infrastructure;
2. a user-controlled stdio MCP definition is effectively a server-side arbitrary process launch surface.

A save-time DNS check is not enough to solve runtime SSRF. DNS can change after registration, a hostname can be rebound, and an HTTP client may follow redirects to a different destination. Therefore Phase 7.25 stores policy only. Runtime activation waits for a FDEX-controlled destination-enforcing egress gateway.

官方 Codex 支持 Streamable HTTP MCP，也支持 bearer token 环境变量、HTTP Header、OAuth；同时还支持通过 `command/args/env/cwd` 启动本地 stdio MCP。

FDEX 是多租户中心服务，不能直接把这些能力原样开放给账号：

1. 用户控制的 HTTP MCP URL 可能演化为 SSRF，访问 loopback、内网、云 metadata 或其它租户基础设施；
2. 用户控制的 stdio MCP 定义本质上等同于服务端任意进程启动入口。

仅在保存时检查 DNS 仍不足以解决运行时 SSRF，因为 DNS 可以变化、发生 rebinding，HTTP 客户端也可能跟随重定向。因此 Phase 7.25 只保存策略，等 FDEX 自己的目的地址强制出站网关完成后再激活 Runtime。

## Stored fields / 保存字段

Each entry is owned by exactly one Center `user_id` and contains only:

- opaque registry id;
- name;
- HTTPS URL;
- desired enabled/disabled policy state;
- explicit tool allowlist;
- last public DNS admission snapshot;
- startup timeout;
- tool timeout;
- timestamps.

每条记录严格绑定一个 Center `user_id`，只保存：注册表 ID、名称、HTTPS URL、启停策略、明确工具 allowlist、最近一次公网 DNS 校验结果、启动/工具超时以及时间戳。

The Phase 7.25 database contains **no** bearer token, OAuth token, arbitrary HTTP header, command, argv, environment or cwd column.

Phase 7.25 数据库**不包含** bearer token、OAuth token、任意 HTTP Header、command、argv、env 或 cwd 字段。

## URL admission policy / URL 准入规则

A registry URL must satisfy all of these rules:

- `https://` only;
- port 443 only;
- hostname required;
- no URL username/password;
- no query string;
- no fragment;
- direct IP literals must be globally routable;
- DNS names are resolved when saved/enabled;
- every resolved address must be globally routable;
- mixed public/private DNS answers fail closed.

注册 URL 必须同时满足：仅 HTTPS、仅 443、必须有主机名、禁止 URL 用户名/密码、禁止 query、禁止 fragment；IP 字面量必须是公网地址；域名在保存/启用时进行 DNS 解析，而且**全部**返回地址都必须是公网地址。只要混入 loopback、内网、链路本地、保留或其它非公网地址就拒绝。

This is an admission check, not a runtime destination guarantee. The registry therefore remains disconnected from Codex in Phase 7.25.

这只是准入检查，不是运行时目的地址保证，因此 7.25 仍不把注册表连接到 Codex。

## Tool allowlist / 工具白名单

An entry cannot be marked enabled unless at least one MCP tool is explicitly allowed. Tool names are bounded and validated. The future Runtime bridge must expose only those explicitly listed tools; an empty list must never mean "all tools".

注册项只有配置至少一个明确工具 allowlist 后才能标记为启用。未来 Runtime 桥接只能暴露明确列出的工具；空列表永远不能解释为“全部工具”。

## Fail-safe disable / 故障安全停用

Re-enabling an entry always performs the full public-DNS admission check again.

Disabling is different: it is an emergency safety action and must **not depend on DNS health**. If a previously valid hostname is poisoned, hijacked or temporarily unresolvable, the owner can still atomically mark that entry disabled.

重新启用必须重新进行完整公网 DNS 校验；停用则是安全逃生动作，**不能依赖 DNS 当前是否正常**。即使域名被污染、劫持或已经无法解析，账号仍然可以原子停用该注册项。

## Owner isolation / 账号隔离

Every CRUD statement includes `owner_id`. An id from another owner cannot be read, overwritten, disabled or deleted. The account export includes only the owner's non-secret registry fields. Permanent account deletion erases all registry rows for that owner.

所有 CRUD 都带 `owner_id` 条件。其它账号的 ID 无法读取、覆盖、停用或删除。账号导出只包含本账号非敏感注册字段；永久注销会清除该账号全部 Remote MCP 记录。

## Runtime boundary / Runtime 边界

Phase 7.25 adds an explicit regression test asserting that FDEX's current `_codex_thread_config()` contains neither `mcp_servers` nor `mcpServers`.

This means a registry entry—even one marked enabled—does not currently create an outbound MCP connection.

Phase 7.25 用回归测试明确锁死：当前 FDEX `_codex_thread_config()` 不包含 `mcp_servers` / `mcpServers`。因此注册项即使标记“启用”，目前也不会产生 MCP 出站连接。

## Next activation layer / 下一层激活架构

Before Remote MCP becomes active in Codex, FDEX needs an outer runtime boundary that can enforce destination identity at request time. The intended direction is:

```text
Codex Runtime
    ↓
FDEX local MCP egress bridge
    ↓  owner + server policy lookup
DNS resolve + public-address verification
    ↓
TLS destination / redirect enforcement
    ↓
optional FDEX-held credential injection
    ↓
Remote MCP server
```

The bridge must preserve these properties:

- Codex never receives long-lived OAuth refresh tokens or a general credential vault;
- arbitrary shell commands are still not accepted as MCP configuration;
- redirects cannot escape the approved destination policy;
- DNS is revalidated at connection time;
- private/loopback/link-local/metadata destinations fail closed;
- credentials are scoped to one owner and one MCP server;
- only the configured tool allowlist is exposed;
- disabling a registry entry cuts off future bridge requests immediately.

在 Remote MCP 真正进入 Codex 前，需要先完成 FDEX 自己控制的本地 MCP 出站桥：在每次连接时重新解析并校验公网地址、限制 TLS 目的地和重定向、按 owner/server 注入最小凭据、执行工具 allowlist，并让停用状态立即阻断后续请求。Codex 不能获得长期 OAuth refresh token，也不能获得通用凭据仓库。

## Explicit non-goals / 明确不做

Phase 7.25 does not provide:

- local stdio MCP `command/args/env/cwd`;
- bearer-token storage;
- arbitrary HTTP-header storage;
- OAuth login/callback/token storage;
- ChatGPT Connector authentication proxying;
- direct Runtime MCP activation.

这些能力不会因为官方 Codex 支持就自动成为 FDEX 多租户服务端能力；必须分别经过 owner isolation、secret lifecycle、network enforcement and process isolation design.
