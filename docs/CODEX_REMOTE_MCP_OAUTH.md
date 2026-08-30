# FDEX Remote MCP OAuth Broker / Remote MCP OAuth 认证代理

Phase 7.28 extends the accepted Phase 7.27 Remote MCP credential vault with an owner/server-scoped OAuth 2.0 Authorization Code broker. The security goal is unchanged: **Codex never receives the remote OAuth client secret, authorization code, access token or refresh token.**

Phase 7.28 在 Phase 7.27 已验收的 Remote MCP 凭据库上新增按 `owner + server` 隔离的 OAuth 2.0 Authorization Code 代理。安全目标不变：**Codex 永远拿不到远端 OAuth client secret、authorization code、access token 或 refresh token。**

## Flow / 流程

```text
FDEX user
  -> CSRF-protected "Start OAuth"
  -> FDEX generates random state + PKCE verifier
  -> durable store keeps state SHA-256 + encrypted verifier
  -> browser redirects to configured HTTPS authorization endpoint
  -> callback is fixed from Settings.public_base_url
  -> logged-in FDEX owner claims state exactly once
  -> FDEX exchanges code through pinned HTTPS token client
  -> encrypted OAuth grant enters Remote MCP credential vault
  -> task receives only the existing localhost MCP capability
  -> gateway resolves/refreshes access token inside FDEX and injects Authorization remotely
```

## Endpoint admission / Endpoint 准入

Authorization, token and optional revocation endpoints are limited to HTTPS on port 443. URL userinfo and fragments are rejected. Token/revocation endpoints cannot contain preset query parameters. Every token/refresh/revoke request performs fresh public-DNS validation and uses a resolver pinned to those just-admitted addresses while TLS verification remains bound to the original hostname. Redirects are disabled and host proxy environment variables are ignored (`trust_env=False`).

Authorization、token 与可选 revocation endpoint 仅允许 HTTPS 443。禁止 URL userinfo/fragment；token/revocation endpoint 不允许预置 query。每次 token/refresh/revoke 请求都会重新执行公网 DNS 准入并固定实际连接 IP，同时继续按原始 hostname 验证 TLS。禁止跟随重定向，也不继承宿主机代理环境。

## State + PKCE / State 与 PKCE

- random OAuth `state` is returned only to the browser; SQLite stores only SHA-256;
- PKCE verifier is encrypted with the existing Remote MCP credential-vault key;
- S256 challenge is mandatory;
- flow rows are owner/server scoped, expire after a bounded window and are claim-once under `BEGIN IMMEDIATE`;
- callback requires the FDEX user to still be logged in and can claim only that user's state;
- callback URI is derived from operator-controlled `public_base_url`, never from Host/X-Forwarded-* request headers.

随机 `state` 只返回浏览器，数据库只存 SHA-256；PKCE verifier 使用现有 Remote MCP vault key 加密；强制 S256；flow 按 owner/server 隔离、限时、只能领取一次。回调地址由服务端 `public_base_url` 决定，不信任外部 Host/X-Forwarded-*。

## Client authentication / Client 认证

Supported client authentication methods:

- `none` for public clients;
- `client_secret_post`;
- `client_secret_basic`.

Client secrets are encrypted with the same FDEX-held vault and are password-input-only in Web UI. The public OAuth configuration view and account export never include the client secret.

支持 public client、`client_secret_post` 与 `client_secret_basic`。client secret 只在 FDEX vault 中保存密文，页面不回显，账号导出不包含秘密。

## Grant revision vs token refresh / Grant 版本与短期 token 刷新

Phase 7.27 used credential `updated_at` as the lease revision. Phase 7.28 migrates the credential store to an explicit `grant_revision` while preserving the existing lease column name for backward database compatibility.

- static Bearer add/rotation/delete changes the grant/lease revision;
- OAuth authorization or reauthorization creates a new grant revision and revokes old leases;
- OAuth revocation deletes the grant and revokes old leases;
- ordinary OAuth access-token refresh updates ciphertext/fingerprint/`updated_at` **without changing the grant revision**.

This separation prevents a normal short-lived access-token refresh from breaking an otherwise authorized running task, while still ensuring that a real authorization-boundary change invalidates existing task capabilities.

Phase 7.28 将凭据的授权边界升级为显式 `grant_revision`：Bearer 轮换、OAuth 重新授权或撤销都会改变授权边界并让旧 lease 失效；仅 access token 自动刷新不会改变 grant revision，因此不会无意义打断仍然被授权的运行任务。

## Refresh locking / 刷新锁

Access tokens near expiry are refreshed only inside FDEX. A durable per-owner/server refresh lock ensures one Uvicorn worker performs the refresh. Other workers wait for a bounded durable update and fail closed if refresh does not complete. A missing refresh token, changed grant revision or deleted OAuth config fails closed.

临近过期的 access token 只由 FDEX 刷新。按 owner/server 的持久 refresh lock 保证多 worker 只有一个执行刷新；其他 worker 有界等待，超时失败关闭。缺失 refresh token、grant 变化或 OAuth 配置被删除都会失败关闭。

## Revocation / 撤销

If a revocation endpoint is configured, FDEX attempts remote revocation before deleting the local encrypted grant. Remote revocation failure deliberately keeps the local grant so the UI cannot falsely claim the remote authorization was revoked. When no revocation endpoint is configured, local grant deletion still invalidates all FDEX task leases but does not claim to revoke authority at the third-party authorization server.

配置 revocation endpoint 时，FDEX 先尝试远端撤销，成功后才删除本地 grant；远端撤销失败则保留本地密文，避免误报“已撤销”。未配置 revocation endpoint 时，本地删除只能保证 FDEX 不再使用该 grant，并不宣称第三方授权服务器已撤权。

## Export and account erasure / 导出与注销

Account export may include non-secret OAuth client configuration (endpoint URLs, client id, scopes, auth method and `has_client_secret`) but explicitly excludes client secrets, state/PKCE material, access tokens and refresh tokens. Permanent account deletion removes leases before encrypted credentials; credential cleanup also removes pending OAuth flows/configuration before the public MCP registry is removed.

账号导出可以包含非秘密 OAuth client 配置，但不导出 client secret、state/PKCE、access token、refresh token。永久注销继续遵循 lease → credential/OAuth flow/config → public registry 的清理边界。

## Explicit boundaries / 明确边界

Phase 7.28 does not enable arbitrary user-supplied HTTP headers, ChatGPT Connector credential impersonation, or local stdio MCP command/args/env/cwd. These remain separate capability surfaces. The normal Phase 7.26 destination enforcement, Phase 7.27 encrypted vault and tool allowlist remain authoritative.

Phase 7.28 不开放任意 HTTP Header、不冒充 ChatGPT Connector 凭据，也不开放用户自定义 stdio MCP。7.26 的目的地址强制、7.27 的加密凭据库和工具 allowlist 继续作为不可绕过的外层边界。
