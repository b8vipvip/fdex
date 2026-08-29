# FDEX Current Status

Last updated: 2026-08-29

## Current baseline

- Default branch: `main`
- Accepted `main` baseline before this branch: **Phase 7.24 — Owner-scoped Codex MCP Elicitation**
- Accepted Phase 7.24 main commit: `22d8a15ecdf99e526614f93acb0788b6d4100542`
- Baseline proposed by this branch: **Phase 7.25 — Owner-scoped Remote MCP Registry**
- Current development branch: `agent/phase7-25-remote-mcp-registry`
- Current Android stable release: **v1.1.36**
- v1.1.36 contains the Phase 7.13 Android migration to the universal `智体` product model.
- Phase 7.14 through Phase 7.25 are server/Web/infrastructure changes and do not require an Android release by themselves.
- `FDEX_AGENT_ENGINE=legacy` remains the rollout default until a production Responses/tool smoke task succeeds.

A concrete commit becomes an accepted `main` baseline only after FastAPI Tests, Android unit tests and Android Debug APK are all green on the PR and again after merge.

## Current product model

FDEX user-facing identities use the universal **智体** model, not the former company / industry / department / position / AI-employee hierarchy. Historical `employee` fields and routes may remain internally for compatibility.

## Center account and isolation

FDEX Center `user_id` is the canonical owner scope for GitHub, Coding Agent, Web workspace, remote memory, tasks, Codex Thread/Turn/Item state, interactive Codex requests, Remote MCP registry entries and sandboxes.

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

Implemented:

- direct stdio host for official `codex app-server`;
- initialize/initialized, request/response, notifications and fail-closed server requests;
- native Thread/Turn execution;
- schema-light forward-compatible notification transport;
- Runtime selection and health reporting;
- owner-scoped `CODEX_HOME`;
- sanitized process environment;
- shared FDEX Responses Provider mapping;
- project `allow_network` mapping;
- GitHub credentials remain outside Codex;
- protected-path validation and FDEX-controlled commit/push/PR.

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

PR #85 passed FastAPI Tests, Android unit tests and Android Debug APK. The post-merge `main` Build and Test run passed all of the same jobs again.

Implemented official `mcpServer/elicitation/request` handling on top of the Phase 7.23 encrypted interaction bridge:

- preserves original MCP request/Host/Thread/Turn/server identity;
- standard public `mode=form` gets typed Web rendering and validation;
- supports string/number/integer/boolean/single-select and enum-backed multi-select fields;
- validates required/default/min/max/length/item-count/enum and supported string formats;
- returns exact `{action, content, _meta}` response shape;
- submitted values remain encrypted only while waiting for the matching Host and are not written to durable response summaries;
- generic `mode=url` accepts only credential-free HTTPS URLs;
- `serverName=codex_apps` acceptance stays blocked;
- proprietary `openai/form` / `openaiForm` acceptance stays fail-closed;
- unsupported/malformed schemas stay decline/cancel-only;
- no user-configurable local stdio MCP process launch.

## Phase 7.25 — Owner-scoped Remote MCP Registry (current branch)

Phase 7.25 builds the MCP **control plane only**. It deliberately does not activate registered endpoints inside Codex Runtime yet.

Implemented/proposed on this branch:

- new owner-scoped SQLite Remote MCP registry;
- maximum 20 registry entries per account;
- credential-free schema: no bearer token, OAuth token, arbitrary HTTP Header, `command`, `args`, `env` or `cwd` columns;
- strict URL admission:
  - HTTPS only;
  - port 443 only;
  - hostname required;
  - no URL username/password;
  - no query or fragment;
  - direct IP literals must be globally routable;
  - DNS is resolved at save/re-enable time;
  - every resolved address must be globally routable;
  - mixed public/private DNS answers fail closed;
- explicit bounded per-server tool allowlist; an enabled entry must contain at least one tool and an empty list never means “all tools”;
- startup and tool-call timeout policy fields;
- Web CRUD in `/account/agent/runtime#remote-mcp` with CSRF and current-user owner scope;
- dedicated atomic **disable** path that does not depend on DNS health, so DNS poisoning/unavailability cannot trap an enabled policy in the on state;
- re-enable always performs the full public-DNS admission check again;
- account portable export includes only the user-owned non-secret registry fields;
- permanent account deletion erases all Remote MCP registry rows for that owner;
- regression coverage proves another owner cannot read/overwrite/delete an entry;
- regression coverage proves current `_codex_thread_config()` contains neither `mcp_servers` nor `mcpServers`.

### Why Runtime activation is intentionally blocked

A save-time DNS check is not sufficient runtime SSRF protection. DNS can change after registration, an attacker can use DNS rebinding, and an HTTP client can follow redirects to a different address. Therefore an entry marked `enabled` in Phase 7.25 represents account policy intent only; it does not create an MCP connection.

The next activation layer must be a FDEX-controlled destination-enforcing egress bridge that revalidates DNS at connection time, blocks private/loopback/link-local/metadata destinations, constrains redirects/TLS destination, enforces owner/server/tool policy and later injects only narrowly scoped FDEX-held credentials.

See `docs/CODEX_REMOTE_MCP.md`.

Phase 7.25 is **not accepted into `main` until PR CI and post-merge main CI are green**.

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
- **Phase 7.15** — dedicated FDEX GitHub egress.
- **Phase 7.16** — managed VLESS/Xray GitHub-only egress.
- **Phase 7.17** — multi-node VLESS pool and strict health semantics.
- **Phase 7.18** — explicit Xray dependency/activation failures.
- **Phase 7.19** — official Codex Runtime foundation.
- **Phase 7.20** — native official Codex App Server host and compatibility strategy.
- **Phase 7.21** — durable Thread/Turn Host and continuation/control lifecycle.
- **Phase 7.22** — durable full Item/event stream and reconnect-safe Web SSE.
- **Phase 7.23** — durable owner-scoped approvals and requestUserInput bridge.
- **Phase 7.24** — owner-scoped official MCP elicitation bridge.

## What remains after Phase 7.25

The next compatibility layers include:

- FDEX-controlled Remote MCP destination-enforcing egress gateway and actual Runtime activation;
- owner/server-scoped MCP credential vault and OAuth callback/token broker without exposing refresh/bearer credentials to arbitrary Codex shell processes;
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
7. Keep secrets out of Codex Runtime/shell beyond the one selected Provider key needed by Runtime.
8. Keep project network/worktree policy authoritative; a human approval must not bypass it.
9. Treat the official App Server Protocol as the Codex compatibility ABI; do not fork core crates without a compelling reason.
10. Fail closed on unsupported, unverifiable or over-broad server-initiated permission/MCP requests.
11. Never expose user-configurable stdio MCP process launch in the multi-tenant Center without an outer process-tree sandbox/allowlist boundary.
12. Do not activate a user Remote MCP URL from Codex until a FDEX-controlled request-time destination enforcement layer exists.
13. Do not describe proprietary OpenAI cloud services as open-source merely because their client code is present in the repository.
14. Require full FastAPI + Android CI before merge.

## Historical progress file

`DEVELOPMENT_PROGRESS.md` contains older detailed history and may describe an earlier baseline. Use this file plus the latest merged PRs for the current baseline.
