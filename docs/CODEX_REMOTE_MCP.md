# FDEX Remote MCP Registry, Secure Gateway & Credential Vault / Remote MCP 注册表、安全网关与凭据库

Phase 7.25 introduced the owner-scoped Remote MCP control plane. Phase 7.26 activated anonymous HTTPS Streamable HTTP MCP through a FDEX-owned destination-enforcing localhost gateway. Phase 7.27 adds **static Bearer authentication held and injected only by FDEX**; Codex still receives neither the user-supplied remote URL nor the remote Bearer token.

Phase 7.25 建立按账号隔离的 Remote MCP 控制平面；Phase 7.26 通过 FDEX 自己控制的目的地址强制网关启用匿名 HTTPS Streamable HTTP MCP；Phase 7.27 新增**仅由 FDEX 保存和注入的静态 Bearer 认证**。Codex 仍拿不到用户填写的远端 URL，也拿不到远端 Bearer token。

## Threat model / 威胁模型

FDEX is a multi-tenant Center. Official Codex can support remote MCP, HTTP headers, bearer/OAuth authentication and local stdio MCP, but exposing those surfaces directly would create SSRF, secret-exfiltration and arbitrary-process risks.

The FDEX boundary therefore assumes:

- Remote MCP URL is user controlled and may attempt loopback/private/link-local/cloud-metadata access, DNS rebinding or redirect escape;
- MCP tool payloads may attempt to call tools outside the owner's configured allowlist;
- Codex/model/user-controlled local requests may try to supply their own `Authorization`, Cookie or forwarding headers;
- a live MCP registry row or credential may be edited, disabled, rotated or deleted while a task still holds an old capability;
- database-only compromise must not provide plaintext bearer tokens or a cheap offline verifier for low-entropy tokens;
- a missing/wrong vault key must never cause FDEX to silently replace the key and continue as if credentials were valid.

FDEX 是多租户中心服务，因此 Remote MCP 的 URL、工具调用、本地请求 Header、运行中配置变更和长期凭据都视为独立攻击面。数据库泄露不能直接得到 token，也不应通过普通 SHA 指纹获得低熵 token 的离线猜测验证器；密钥丢失或错配时必须失败关闭。

## Phase 7.25 registry / 7.25 注册表

Each registry entry belongs to exactly one Center `user_id` and stores only the public control-plane configuration:

- opaque registry id and name;
- HTTPS URL;
- enabled/disabled policy;
- explicit tool allowlist;
- save-time public DNS snapshot;
- startup/tool timeout;
- timestamps.

The registry itself still contains **no bearer/OAuth token, arbitrary remote HTTP header, command, args, env or cwd**. Phase 7.27 does not weaken this design; secrets live in a separate credential table/vault.

每条注册项严格绑定一个 Center `user_id`。公开注册表继续保持“无秘密”设计，Bearer 密文位于独立凭据表中，不污染 `remote_mcp_servers`。

### URL admission / URL 准入

Registry URLs must be HTTPS on port 443, have no URL userinfo/query/fragment, and resolve only to globally routable addresses. Mixed public/private DNS answers fail closed. Disabling remains DNS-independent so a poisoned/broken endpoint can always be shut off immediately.

保存与重新启用时执行公网 DNS 准入；停用不依赖 DNS 当前健康状态。

## Phase 7.26 destination-enforcing runtime / 7.26 目的地址强制运行时

```text
Codex Runtime
    │
    │ URL = http://127.0.0.1:<fdex_port>/internal/codex-mcp/<lease_id>
    │ X-FDEX-MCP-Capability = task-scoped random capability
    ▼
FDEX localhost MCP gateway
    ├─ direct loopback + reverse-proxy-marker rejection
    ├─ owner/task/server/revision lease validation
    ├─ live enabled/tool policy validation
    ├─ tools/call allowlist enforcement
    ├─ request-time public DNS validation
    ├─ connector pinned to admitted IPs
    ├─ original hostname retained for TLS SNI/certificate verification
    ├─ trust_env=False
    ├─ redirects blocked
    └─ upstream auth challenges suppressed
    ▼
HTTPS Remote MCP server
```

Codex receives only a localhost URL and local capability. The original remote URL never enters the task MCP configuration or Codex shell environment. Request-time DNS is revalidated and the actual TCP connector is pinned to the just-admitted addresses while TLS verification remains bound to the original hostname.

Codex 只连接 localhost capability。每次远端请求前重新检查公网 DNS 并固定实际连接 IP，同时继续按原域名完成 TLS SNI/证书验证。

## Phase 7.27 credential vault / 7.27 凭据库

Static Bearer authentication is stored separately from the public registry in `remote_mcp_credentials`, scoped by **owner + server**.

The vault properties are:

- bearer plaintext is encrypted with Fernet before durable storage;
- the SQLite table stores ciphertext plus metadata, never plaintext;
- the display fingerprint is a truncated **HMAC-SHA256 keyed by the vault key**, not a raw token hash;
- UI receives only auth type, keyed fingerprint and timestamps;
- account export does not export Bearer plaintext/ciphertext;
- Codex task config does not contain the remote Bearer;
- Codex shell/process environment does not contain the remote Bearer;
- only the FDEX gateway decrypts the token immediately before an authorized outbound request;
- a Codex/user-supplied `Authorization` header is never forwarded and cannot override the vault value;
- Cookie, arbitrary remote headers and local capability headers are not forwarded.

静态 Bearer 按 `owner + server` 单独保存。SQLite 只保存 Fernet 密文与元数据；页面只显示带密钥短指纹和更新时间。远端 Bearer 不进入 Codex 配置、shell 环境、账号导出或普通页面输出，只有 FDEX 网关在实际出站前解密并构造远端 `Authorization: Bearer ...`。

### Vault key lifecycle / Vault 根密钥生命周期

Default key path:

`server/data/remote-mcp-secrets/credential-vault.key`

The parent directory is hardened to mode `0700` and the key file to `0600` where the platform supports POSIX modes. Creation is race-safe for multi-worker startup.

**The vault key is part of server backup/restore state.** If encrypted credential rows already exist and the key file is missing, FDEX fails closed instead of generating a replacement. If the key file is syntactically valid but cannot decrypt stored ciphertext, startup also fails closed for the credential store.

默认 key 位于 `server/data/remote-mcp-secrets/credential-vault.key`。它必须和服务端数据一起备份。已有密文时若 key 丢失，FDEX 不会自动生成新 key；key 与现有密文不匹配时同样失败关闭，避免把不可恢复的密文误当成正常状态。

## Capability + credential revision binding / Capability 与凭据版本绑定

Every new task/resume/fork receives fresh task-scoped leases. The durable lease stores only the capability SHA-256, never the raw capability.

Phase 7.27 binds each lease to two independent revisions:

1. registry `updated_at`;
2. credential `updated_at` (empty means the lease was issued while the server was anonymous).

Any registry edit/disable/re-enable or Bearer add/rotation/delete invalidates old task leases. Credential writes/deletes also proactively mark existing server leases revoked in the same SQLite transaction when the lease table exists.

每个 lease 同时绑定注册项版本与凭据版本。新增、轮换、删除 Bearer 都不会让运行中的旧 capability 静默获得新权限；旧 lease 被主动 revoke，新凭据必须由后续新任务 / resume / fork 重新签发。

A request already admitted before a concurrent revocation may finish as an in-flight request; revocation controls subsequent authorization/admission and does not attempt to cancel bytes already sent to a remote server.

并发撤销不能追回已经发出的远端字节；这里的“立即失效”指撤销提交后新的授权准入不再接受旧 lease。

## Credential TOCTOU handling / 凭据 TOCTOU

After lease validation, the gateway requests the Bearer using the exact credential revision captured by that lease.

- if a credential expected by the lease disappeared, fail closed;
- if it rotated, fail closed;
- if an anonymous lease sees a newly added credential, fail closed rather than silently inheriting new authority;
- only an exact revision match is decrypted and injected.

即使凭据恰好在 lease 校验和解密之间发生变化，也不会退化成匿名请求或静默获得新 Bearer。

## Atomic server deletion / 原子删除

The registry, credential table and lease table share the same SQLite database. Deleting a Remote MCP from the Web control plane uses one transaction to:

1. revoke active leases for that owner/server;
2. delete its encrypted credential, if present;
3. delete the owner-scoped registry row.

Either the transaction commits as a unit or rolls back as a unit. It does not intentionally leave an enabled server behind in an accidental anonymous state.

删除整个 Remote MCP 时，lease 撤销、凭据删除和注册项删除在同一事务中完成。

## Destination enforcement / 目的地址强制

Every outbound request repeats public-DNS validation and pins the connector to admitted addresses. Loopback/private/link-local/reserved destinations fail closed. DNS cache is disabled for that connector, `trust_env=False` prevents host proxy-environment redirection, redirects are disabled, and TLS verification remains against the original hostname.

保存时 DNS 检查不是最终边界；真正出站时仍重新准入并固定 IP。

## Tool least privilege / 工具最小权限

An enabled server must have an explicit non-empty tool allowlist. It is enforced both in Codex registration (`enabled_tools`) and again by FDEX before network I/O for JSON `tools/call`. Opaque/non-JSON POST bodies fail closed because FDEX cannot prove tool authorization.

工具白名单在 Runtime 注册和 FDEX 网关两层执行，空列表绝不表示全部工具。

## Header and authentication policy / Header 与认证策略

Headers copied from Codex are limited to MCP transport metadata. The following are never accepted as remote authority from Codex/user input:

- `Authorization`;
- `Cookie`;
- `X-FDEX-MCP-Capability`;
- reverse-proxy forwarding headers;
- arbitrary HTTP headers.

For a configured static Bearer, FDEX constructs a fresh upstream `Authorization: Bearer <vault secret>` after all local authorization and destination checks. Upstream 401/403/407 are converted to a generic gateway failure and are not passed through as an OAuth/auth challenge to Codex.

来自 Codex 的 Authorization 永远不能覆盖 FDEX vault；远端 Authorization 只能由 FDEX 自己生成。

## Streaming and limits / 流式与限制

- POST bodies are capped at 8 MiB and must be inspectable JSON;
- POST/DELETE use the configured tool timeout;
- GET/SSE may remain open for transport lifecycle;
- upstream response bodies are streamed instead of accumulated in memory;
- 3xx redirects are blocked;
- 401/403/407 are suppressed as generic authentication failure.

## Account lifecycle and export / 账号生命周期与导出

Permanent account deletion removes, in order, Remote MCP leases, encrypted credentials and then public registry rows. Account export continues to include only the credential-free registry control plane and explicitly excludes Remote MCP Bearer/OAuth secrets.

永久注销按 lease → credential → registry 顺序清理。数据导出仍只有非秘密 Remote MCP 注册信息，不导出 Bearer 明文或密文。

## Explicit non-goals after Phase 7.27 / 7.27 后仍明确不做

Phase 7.27 deliberately does **not** provide:

- OAuth authorization-code callback/token refresh broker;
- arbitrary remote HTTP-header storage;
- ChatGPT Connector credential proxying;
- user-configurable local stdio MCP `command/args/env/cwd`;
- a general secret API visible to Codex/model-generated code.

OAuth must be implemented as a later FDEX-owned owner/server-scoped broker with CSRF/state/PKCE, callback binding, encrypted access/refresh tokens, expiry/refresh locking, revocation and the same credential-revision lease semantics. Local stdio MCP remains out of scope until FDEX has a stronger whole-process-tree sandbox boundary.

7.27 只完成静态 Bearer。OAuth、任意 Header、ChatGPT Connector 凭据代理和用户自定义 stdio MCP 仍不开放；后续 OAuth 必须继续由 FDEX 作为 owner/server 范围的认证代理，而不能把长期 token 交给 Codex。