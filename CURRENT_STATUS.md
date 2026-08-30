# FDEX Current Status

Last updated: 2026-08-30

## Current baseline

- Default branch: `main`
- Accepted `main` baseline before this branch: **Phase 7.25 — Owner-scoped Remote MCP Registry**
- Accepted Phase 7.25 main commit: `c6263f136563bde26d5df9f630883509fa1253c0`
- Baseline proposed by this branch: **Phase 7.26 — Remote MCP Destination-Enforcing Gateway**
- Current development branch: `agent/phase7-26-remote-mcp-egress`
- Current Android stable release: **v1.1.36**
- v1.1.36 contains the Phase 7.13 Android migration to the universal `智体` product model.
- Phase 7.14 through Phase 7.26 are server/Web/infrastructure changes and do not require an Android release by themselves.
- `FDEX_AGENT_ENGINE=legacy` remains the rollout default until a production Responses/tool smoke task succeeds.

A concrete commit becomes an accepted `main` baseline only after FastAPI Tests, Android unit tests and Android Debug APK are all green on the PR and again after merge.

## Current product model

FDEX user-facing identities use the universal **智体** model, not the former company / industry / department / position / AI-employee hierarchy. Historical `employee` fields and routes may remain internally for compatibility.

## Center account and isolation

FDEX Center `user_id` is the canonical owner scope for GitHub, Coding Agent, Web workspace, remote memory, tasks, Codex Thread/Turn/Item state, interactive Codex requests, Remote MCP registry/lease state and sandboxes.

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

PR #86 and the post-merge `main` Build and Test run both passed FastAPI Tests, Android unit tests and Android Debug APK.

Implemented:

- owner-scoped SQLite Remote MCP registry;
- maximum 20 entries per account;
- credential-free schema with no bearer/OAuth token, arbitrary HTTP Header, `command`, `args`, `env` or `cwd`;
- HTTPS-only/443-only URL admission;
- no URL userinfo/query/fragment;
- globally routable IP requirement;
- save/re-enable DNS admission where every resolved address must be public;
- mixed public/private DNS answers fail closed;
- explicit bounded per-server tool allowlist;
- startup/tool timeouts;
- Web CRUD and CSRF/current-owner scope;
- DNS-independent emergency disable;
- account export and permanent-delete lifecycle;
- deliberate Runtime non-activation until a destination-enforcing egress layer exists.

## Phase 7.26 — Remote MCP Destination-Enforcing Gateway (current branch)

Phase 7.26 activates **anonymous HTTPS Streamable HTTP Remote MCP** while keeping FDEX as the network and authorization boundary.

Implemented/proposed on this branch:

- Codex receives only task-scoped `http://127.0.0.1:<fdex_port>/internal/codex-mcp/<lease_id>` MCP URLs;
- the original user-supplied Remote MCP URL is never inserted into Codex Thread config;
- each task receives a cryptographically random local `X-FDEX-MCP-Capability`;
- raw capability values are never persisted; SQLite stores SHA-256 only;
- capability is kept out of URL/access-log paths and is never forwarded remotely;
- capability/remote URL are not injected into Codex shell environment;
- leases are scoped to owner + task + server and have six-hour crash-fallback expiry;
- task scope revokes leases on normal completion, failure or cancellation;
- resume/fork/new task receives fresh leases;
- every lease is bound to the exact registry `updated_at` revision at issuance;
- any registry edit, disable or re-enable invalidates old leases immediately so a live task cannot be silently retargeted to a new URL or expanded tool policy;
- gateway requires a direct loopback peer, rejects standard reverse-proxy forwarding markers and requires the local capability;
- request-time DNS is resolved again for every outbound request;
- every resolved address must be globally routable;
- a custom `aiohttp` resolver pins the connection to exactly the just-admitted IP addresses;
- the original hostname remains in the HTTPS URL so TLS SNI/certificate verification remains authoritative;
- `aiohttp` uses `trust_env=False`, so host/global proxy environment does not redirect Remote MCP traffic;
- redirects are disabled and 3xx becomes a gateway failure rather than escaping destination policy;
- 401/403/407 authentication challenges are not exposed to Codex, preventing accidental Codex MCP OAuth flow in this anonymous phase;
- request headers use an allowlist and never forward Authorization, Cookie, capability or arbitrary user headers;
- configured `enabled_tools` is passed to Codex and independently enforced again by the gateway on JSON `tools/call` before network I/O;
- opaque/non-JSON MCP POST bodies fail closed because tool authorization could not be inspected;
- POST bodies are bounded to 8 MiB;
- GET/SSE remains stream-capable while upstream response bodies are relayed incrementally;
- account deletion removes Remote MCP lease rows before deleting registry rows;
- Web UI now reflects actual 7.26 gateway activation and immediate old-lease invalidation semantics.

### Phase 7.26 security boundary

Phase 7.26 remains **anonymous Remote MCP only**. It does not store or inject bearer tokens, OAuth credentials, arbitrary remote HTTP headers or ChatGPT Connector credentials. It also continues to reject user-configurable local stdio MCP process launch.

The localhost capability is the primary authentication boundary. Direct-loopback and reverse-proxy-marker checks are defense in depth; deployment should still avoid exposing `/internal/codex-mcp/` through a public reverse proxy.

See `docs/CODEX_REMOTE_MCP.md`.

Phase 7.26 becomes accepted `main` only after its PR and post-merge main CI are green.

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

## What remains after Phase 7.26

The next compatibility layers include:

- owner/server-scoped Remote MCP credential vault and OAuth callback/token broker without exposing long-lived credentials to arbitrary Codex shell processes;
- authenticated Remote MCP gateway credential injection with strict secret lifetime/rotation/revocation semantics;
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
13. Every Remote MCP capability must be owner/task/server/revision scoped and revocable.
14. Do not describe proprietary OpenAI cloud services as open-source merely because their client code is present in the repository.
15. Require full FastAPI + Android CI before merge.

## Historical progress file

`DEVELOPMENT_PROGRESS.md` contains older detailed history and may describe an earlier baseline. Use this file plus the latest merged PRs for the current baseline.
