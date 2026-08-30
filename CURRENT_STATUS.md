# FDEX Current Status

Last updated: 2026-08-30

## Current baseline

- Default branch: `main`
- Accepted `main` feature baseline: **Phase 7.27 — FDEX-held Remote MCP Credential Vault + Static Bearer Injection**
- Accepted Phase 7.27 feature commit: `4bee0e128354ad5306433b793ae00f4725fc5500`
- Phase 7.27 pull request: **#88**
- Phase 7.27 final PR head: `33b024b19155e15c54c885d9792929c23edaa5a6`
- Phase 7.27 PR Build and Test run: `33290719832` — FastAPI Tests + Android unit tests + Android Debug APK all passed.
- Phase 7.27 post-merge `main` Build and Test run: `33290817719` — FastAPI Tests + Android unit tests + Android Debug APK all passed.
- Phase 7.27 Android Auto Release run: `33290917088` — skipped as expected for a server/Web-only phase.
- Active feature-development phase after this seal: none; Phase 7.27 is complete and accepted.
- Current Android stable release: **v1.1.36**
- v1.1.36 contains the Phase 7.13 Android migration to the universal `智体` product model.
- Phase 7.14 through Phase 7.27 are server/Web/infrastructure changes and do not require an Android release by themselves.
- `FDEX_AGENT_ENGINE=legacy` remains the rollout default until a production Responses/tool smoke task succeeds.

A concrete commit becomes an accepted `main` feature baseline only after FastAPI Tests, Android unit tests and Android Debug APK are all green on the final PR head and again after merge.

## Current product model

FDEX user-facing identities use the universal **智体** model, not the former company / industry / department / position / AI-employee hierarchy. Historical `employee` fields and routes may remain internally for compatibility.

## Center account and isolation

FDEX Center `user_id` is the canonical owner scope for GitHub, Coding Agent, Web workspace, remote memory, tasks, Codex Thread/Turn/Item state, interactive Codex requests, Remote MCP registry/credential/lease state and sandboxes.

Completed account lifecycle includes registration/login, rotating refresh sessions, password change/reset, device/session management, login rate limiting, security audit, data export, remote-memory erasure and permanent account deletion.

## GitHub authority and dedicated egress

The preferred GitHub architecture is **GitHub App Installation**.

Completed capabilities include:

- user-owned GitHub App Installation binding and ownership verification;
- repository synchronization from the actual Installation scope;
- short-lived downscoped installation tokens;
- owner-scoped Agent projects/tasks/sandboxes and per-task worktrees;
- durable tasks/events, cancellation/retry and cross-worker execution locking;
- push/PR only when allowed by effective project/GitHub App permission;
- no direct Coding Agent write to `main`;
- dedicated operator-controlled GitHub HTTP(S) egress;
- managed multi-node VLESS/Xray GitHub-only proxy pool;
- loopback/authenticated local proxy and non-GitHub blackhole routing;
- no host-global HTTP_PROXY/HTTPS_PROXY/ALL_PROXY, DNS, firewall or global Git side effects;
- bounded retry and actionable GitHub transport diagnostics.

Earlier PAT / Device OAuth paths remain compatibility layers.

## Official OpenAI Codex architecture

### Phase 7.19 — Runtime foundation

FDEX integrated the official Codex Runtime while retaining FDEX account/GitHub/worktree/security authority.

### Phase 7.20 — Native App Server Host

The long-term compatibility boundary is the public **`codex app-server` JSON-RPC protocol** rather than a high-level SDK wrapper.

Implemented direct stdio app-server hosting, native Thread/Turn execution, schema-light notification transport, owner-scoped `CODEX_HOME`, sanitized process environment, shared FDEX Responses Provider mapping, project network policy and FDEX-controlled GitHub publish authority.

### Phase 7.21 — Durable Thread / Turn Host

Implemented persistent owner/task/Thread/Turn state, resume/fork/steer/compact operations, cross-worker control, kernel `flock` Host ownership, stale-state reconciliation and account-erasure semantics.

### Phase 7.22 — Durable Item / Event Stream

Implemented bounded persistence of the complete app-server notification stream, durable Item projections, reconnect-safe deltas/SSE, orphan reconciliation, schema-light future Item handling and safe DOM rendering.

### Phase 7.23 — Durable Codex Interactions

Accepted main commit: `bbec3145dfba5d22bf68afc3341cc49c8b1b468d`.

Supported owner-scoped interactive requests:

- `item/commandExecution/requestApproval`;
- `item/fileChange/requestApproval`;
- `item/permissions/requestApproval`;
- `item/tool/requestUserInput`.

The bridge preserves typed JSON-RPC ids and approval ids, binds every request to owner/task/Host/Thread/Turn/Item, supports cross-worker response submission, encrypts waiting answers with Fernet, destroys ciphertext after Host claim, stores redacted history only, fails closed above 1 MiB, and keeps FDEX worktree/network policy authoritative over every approval click.

### Phase 7.24 — Owner-scoped MCP Elicitation

Accepted main commit: `22d8a15ecdf99e526614f93acb0788b6d4100542`.

Implemented official `mcpServer/elicitation/request` handling on the encrypted interaction bridge, including typed public `mode=form`, constrained credential-free `mode=url`, exact response shapes and fail-closed handling of unsupported/proprietary modes.

### Phase 7.25 — Owner-scoped Remote MCP Registry

Accepted main commit: `c6263f136563bde26d5df9f630883509fa1253c0`.

Implemented owner-scoped credential-free Remote MCP registry, HTTPS/443/public-DNS admission, explicit bounded tool allowlists, Web CRUD/emergency disable, export/delete lifecycle and deliberate Runtime non-activation until FDEX owned the destination-enforcing egress layer.

### Phase 7.26 — Remote MCP Destination-Enforcing Gateway

Accepted main commit: `0f977d3986af25597b4e8f80639da78504be1015`.

PR #87 and the post-merge `main` Build and Test run both passed FastAPI Tests, Android unit tests and Android Debug APK. Android Auto Release was skipped as expected for a server/Web-only phase.

Implemented:

- task-scoped localhost Remote MCP URLs instead of the original remote URL in Codex config;
- cryptographically random `X-FDEX-MCP-Capability`, with SHA-256-only durable lease storage;
- owner + task + server + registry-revision lease binding;
- task completion/failure/cancel revocation and fresh leases for resume/fork/new task;
- direct-loopback/reverse-proxy-marker defense in depth;
- request-time public DNS revalidation and connection pinning;
- original-host TLS SNI/certificate verification;
- `aiohttp trust_env=False`, no redirect following and generic auth-challenge suppression;
- request-header allowlist and no Authorization/Cookie/capability forwarding;
- gateway-side `tools/call` allowlist enforcement before network I/O;
- bounded inspectable POST bodies and streaming upstream responses;
- owner-scoped `CODEX_HOME` permanent-account cleanup.

### Phase 7.27 — FDEX-held Remote MCP Credential Vault + Static Bearer Injection

Accepted main feature commit: `4bee0e128354ad5306433b793ae00f4725fc5500`.

PR #88 final head `33b024b19155e15c54c885d9792929c23edaa5a6` passed Build and Test run `33290719832`. The post-merge `main` Build and Test run `33290817719` also passed FastAPI Tests, Android unit tests and Android Debug APK. Android Auto Release run `33290917088` was skipped as expected.

Phase 7.27 extends the accepted 7.26 gateway to authenticated **static Bearer** Remote MCP without handing the remote secret to Codex.

Implemented:

- separate `remote_mcp_credentials` table; the public `remote_mcp_servers` registry remains credential-free;
- strict owner + server credential scope;
- Fernet encryption for durable Bearer storage;
- dedicated vault key under `server/data/remote-mcp-secrets/credential-vault.key`;
- POSIX hardening to `0700` parent / `0600` key where supported;
- race-safe multi-worker first-key creation;
- startup fail-closed when encrypted credentials exist but the key is missing;
- startup fail-closed when a syntactically valid key cannot decrypt stored ciphertext;
- keyed HMAC-SHA256 short fingerprint instead of raw token SHA, preventing a database-only offline guess verifier;
- UI exposes only auth status, keyed fingerprint and timestamps; token is password-input-only and never echoed;
- account export keeps Remote MCP Bearer/OAuth secrets explicitly excluded;
- Codex task MCP config continues to contain only localhost capability and tool policy, never the remote Bearer;
- Codex shell/process environment does not receive Remote MCP credentials;
- FDEX gateway is the only component that decrypts and injects `Authorization: Bearer ...` remotely;
- Codex/user-supplied `Authorization`, Cookie and arbitrary headers cannot override the vault value;
- every lease is bound to both registry `updated_at` and credential `updated_at`;
- adding, rotating or deleting a Bearer proactively revokes active leases for that server when the lease table exists;
- credential TOCTOU fails closed if a credential disappears, rotates, or appears after an anonymous lease was validated;
- credential writes, credential deletion and whole-server deletion are serialized with short `BEGIN IMMEDIATE` transactions;
- Remote MCP deletion atomically revokes leases, deletes encrypted credential and deletes the owner-scoped registry row in one SQLite transaction;
- permanent account deletion orders Remote MCP cleanup as lease → credential → registry;
- multi-worker lease-schema migration serializes the `credential_updated_at` ALTER with `BEGIN IMMEDIATE`;
- dedicated attack/regression coverage for vault encryption, key lifecycle, owner isolation, lease revocation, auth-header override attempts, TOCTOU, atomic deletion and multi-worker migration;
- Phase 7.26 registry-revision regression coverage remains active and now explicitly coexists with the credential-revision check.

#### Phase 7.27 security boundary

Phase 7.27 supports only **anonymous or static Bearer HTTPS Streamable HTTP Remote MCP**. It does not provide OAuth authorization-code callbacks/token refresh, arbitrary remote headers, ChatGPT Connector credential proxying or user-configurable local stdio MCP.

A request already admitted before a concurrent revocation may finish as an in-flight request; credential/lease revocation prevents subsequent authorization admission and does not claim to recall bytes already sent remotely.

The vault key is part of server backup/restore state. Existing ciphertext never causes automatic key replacement.

See `docs/CODEX_REMOTE_MCP.md`.

## Real Runtime CI

The FastAPI suite includes integration coverage that starts the actual bundled Codex binary, completes the real app-server handshake and calls a native non-model API. This detects wire/runtime drift without requiring a model API key.

Production CI still cannot prove an external Provider implements every Codex Responses/tool-streaming semantic. A real production Coding Agent smoke task remains required before changing the default engine.

## Open-source Codex compatibility policy

FDEX does **not** vendor every Rust crate from `openai/codex`.

The goal is to use the portable local capability set through the official Runtime while FDEX remains the multi-user control plane. See:

- `docs/CODEX_ENGINE.md`
- `docs/CODEX_COMPATIBILITY.md`
- `docs/CODEX_INTERACTIONS.md`
- `docs/CODEX_MCP_ELICITATION.md`
- `docs/CODEX_REMOTE_MCP.md`

The compatibility policy distinguishes:

1. local/portable runtime capabilities that FDEX should directly adopt;
2. capabilities requiring FDEX owner-scoped permission/UI bridges;
3. open client code that still depends on proprietary OpenAI/ChatGPT cloud services;
4. CLI/TUI/build/test/platform-specific projects that are not FDEX server Agent features.

## Recent completed phases

- **Phase 7.1–7.3** — Center account lifecycle, security, erasure/export and destructive-operation locking.
- **Phase 7.4** — persistent Agent tasks and sandbox lifecycle.
- **Phase 7.5–7.10** — user GitHub authorization evolution to GitHub App Installation authority.
- **Phase 7.11–7.12** — provider protocol routing and real owner-scoped GitHub tools in Coding-Agent-enabled Web chat.
- **Phase 7.13–7.14** — universal `智体` product model across Android/Web/server.
- **Phase 7.15–7.18** — dedicated GitHub egress, managed VLESS/Xray pool and explicit activation diagnostics.
- **Phase 7.19–7.20** — official Codex Runtime and native app-server Host.
- **Phase 7.21** — durable Thread/Turn Host and continuation/control lifecycle.
- **Phase 7.22** — durable full Item/event stream and reconnect-safe Web SSE.
- **Phase 7.23** — durable owner-scoped approvals and requestUserInput bridge.
- **Phase 7.24** — owner-scoped official MCP elicitation bridge.
- **Phase 7.25** — owner-scoped credential-free Remote MCP registry/control plane.
- **Phase 7.26** — destination-enforcing task-scoped localhost Remote MCP gateway.
- **Phase 7.27** — FDEX-held encrypted Remote MCP static Bearer vault and credential-revision-aware gateway injection.

## What remains after Phase 7.27

The next compatibility layers include:

- owner/server-scoped OAuth authorization-code callback/token broker with CSRF state + PKCE, encrypted access/refresh tokens, expiry/refresh locking and revocation;
- credential-aware OAuth refresh that preserves the Phase 7.27 credential-revision lease model;
- safe policy for official `item/tool/call` dynamic tool requests;
- image/audio/local attachment/skill/mention Turn input;
- Skills/Hooks/local plugin management and policy UI;
- collaboration/sub-agent resource governance;
- stronger whole Codex process-tree CPU/Memory/PID/concurrency and filesystem isolation;
- verified official Runtime updater/rollback;
- provider compatibility smoke tests and safe failover semantics;
- Android-native rendering/interaction parity where appropriate.

## Development rules going forward

1. Use `智体` for user-facing identities.
2. Use Center `user_id` as the owner scope for every user resource.
3. Treat GitHub App Installation as repository-permission authority.
4. Keep GitHub tokens short-lived and downscoped; never give them to Codex.
5. Never allow Coding Agent/Codex to write directly to `main`.
6. Keep GitHub egress application-scoped and avoid host-global networking side effects.
7. Keep long-lived secrets out of Codex Runtime/shell beyond the one selected Provider key required by Runtime.
8. Keep project network/worktree policy authoritative; a human approval must not bypass it.
9. Treat the official App Server Protocol as the Codex compatibility ABI; do not fork core crates without a compelling reason.
10. Fail closed on unsupported, unverifiable or over-broad server-initiated permission/MCP requests.
11. Never expose user-configurable stdio MCP process launch in the multi-tenant Center without an outer process-tree sandbox/allowlist boundary.
12. Remote MCP network traffic must remain behind FDEX request-time destination enforcement; Codex must not receive the user-supplied remote URL directly.
13. Every Remote MCP capability must be owner/task/server/registry-revision/credential-revision scoped and revocable.
14. Remote MCP long-lived credentials must remain FDEX-held, encrypted, owner/server-scoped and absent from Codex config/shell/export/UI plaintext.
15. Existing encrypted Remote MCP credentials must fail closed if their vault key is missing or mismatched; never auto-replace the key.
16. Do not describe proprietary OpenAI cloud services as open-source merely because their client code is present in the repository.
17. Require full FastAPI + Android CI before merge.

## Historical progress file

`DEVELOPMENT_PROGRESS.md` contains older detailed history and may describe an earlier baseline. Use this file plus the latest merged PRs for the current baseline.