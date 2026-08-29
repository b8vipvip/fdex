# FDEX Current Status

Last updated: 2026-08-29

## Current baseline

- Default branch: `main`
- Accepted `main` baseline before this branch: **Phase 7.23 — Durable Codex Interactions**
- Accepted Phase 7.23 main commit: `bbec3145dfba5d22bf68afc3341cc49c8b1b468d`
- Baseline proposed by this branch: **Phase 7.24 — Owner-scoped Codex MCP Elicitation**
- Current development branch: `agent/phase7-24-codex-mcp-elicitation`
- Current Android stable release: **v1.1.36**
- v1.1.36 contains the Phase 7.13 Android migration to the universal `智体` product model.
- Phase 7.14 through Phase 7.24 are server/Web/infrastructure changes and do not require an Android release by themselves.
- `FDEX_AGENT_ENGINE=legacy` remains the rollout default until a production Responses/tool smoke task succeeds.

A concrete commit becomes an accepted `main` baseline only after FastAPI Tests, Android unit tests and Android Debug APK are all green.

## Current product model

FDEX user-facing identities use the universal **智体** model, not the former company / industry / department / position / AI-employee hierarchy. Historical `employee` fields and routes may remain internally for compatibility.

## Center account and isolation

FDEX Center `user_id` is the canonical owner scope for GitHub, Coding Agent, Web workspace, remote memory, tasks, Codex Thread/Turn/Item state, interactive Codex requests and sandboxes.

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

Implemented:

- persistent owner/task/Thread/Turn identities in SQLite;
- `thread/resume`, `thread/fork`, `turn/steer`, `thread/compact/start`;
- continuation tasks inherit the verified parent commit while retaining isolated worktrees;
- cross-Uvicorn-worker control queue;
- kernel `flock` prevents two workers from owning one Thread Host simultaneously;
- stale worker/Turn/control reconciliation;
- account deletion refuses active Host state and erases durable Thread/Turn/control records.

### Phase 7.22 — Durable Item / Event Stream

Implemented:

- bounded persistence of the complete official app-server notification stream;
- durable `item/started` / `item/completed` projection;
- reconnect-safe persisted live deltas;
- orphan Item marking when a terminal Turn is observed without `item/completed`;
- unknown future notification/Item variants remain visible through schema-light raw JSON;
- owner-scoped snapshot endpoint and SSE with `Last-Event-ID` resume;
- Web rendering for command/file/MCP/dynamic tool/collaboration/sub-agent/Web/image/reasoning/plan/context-compaction and fallback Item types;
- DOM rendering uses `textContent` / `replaceChildren`, never executes Item HTML;
- account deletion erases Item/event/delta history.

### Phase 7.23 — Durable Codex Interactions

Accepted main commit: `bbec3145dfba5d22bf68afc3341cc49c8b1b468d`.

Supported owner-scoped interactive requests:

- `item/commandExecution/requestApproval`;
- `item/fileChange/requestApproval`;
- `item/permissions/requestApproval`;
- `item/tool/requestUserInput`.

Implemented:

- typed JSON-RPC request identity, including numeric vs string ids;
- command `approvalId` preserved separately from `itemId`;
- durable owner/task/Host-session/Thread/Turn/Item correlation;
- response submission through any worker and atomic claim only by the matching stdio Host;
- Fernet-encrypted waiting answers with atomic cross-worker key creation;
- answer ciphertext destroyed immediately after Host claim or terminal cleanup;
- redacted response history; secret answer bodies are never written into interaction/event audit history;
- request/response payloads fail closed above 1 MiB instead of changing protocol shape through truncation;
- cross-worker task cancellation, Host shutdown and orphan reconciliation;
- realtime Web approval/question UI over the Phase 7.22 SSE bus;
- no hard page refresh while a user may be typing an interaction;
- FDEX project/worktree/network authority remains above every human approval click;
- account deletion reconciles orphan interactions, refuses genuinely active ones and erases interaction state.

PR #84 and the post-merge `main` Build and Test run both passed. Android auto-release was skipped because Phase 7.23 was server/Web-only.

### Phase 7.24 — Owner-scoped MCP Elicitation (current branch)

This branch adds the next official app-server server-request type:

- `mcpServer/elicitation/request`.

Phase 7.24 scope is deliberately narrower than “arbitrary MCP configuration”. It reuses the Phase 7.23 durable encrypted interaction channel and keeps FDEX as the multi-user authority.

Implemented/proposed on this branch:

- preserve the original MCP JSON-RPC request, `threadId`, nullable `turnId`, `serverName`, mode and Host-session identity in the durable interaction store;
- standard public MCP `mode=form` is projected into the existing safe Web question renderer;
- typed form validation supports string, number, integer, boolean, single-select enum and enum-backed multi-select arrays;
- required fields, defaults, min/max, length, enum membership and supported string formats are validated before an accepted response is delivered;
- exact official response shape is returned as `{action, content, _meta}` with `action` limited to `accept`, `decline` or `cancel`;
- user form values travel through the Phase 7.23 Fernet-encrypted transient answer column and ciphertext is erased after Host claim;
- durable response summaries contain field names/count and action only, never submitted values;
- generic `mode=url` is accepted only for credential-free HTTPS URLs; response history records only the destination host, not the full URL/query;
- `serverName=codex_apps` URL acceptance remains blocked because FDEX does not impersonate or proxy ChatGPT Connector authentication;
- proprietary `openai/form` and `openaiForm` acceptance remains fail-closed; decline/cancel are still protocol-valid;
- every Web/Uvicorn worker installs the same MCP projection so snapshot, pending SSE and answered SSE have identical semantics;
- unsupported or malformed MCP elicitation schemas remain reject/decline-only rather than falling back to an unsafe generic JSON editor;
- existing owner/task scope, CSRF, Host claim, timeout/orphan cleanup and account-erasure rules apply unchanged.

#### Explicitly out of scope in Phase 7.24

FDEX **does not expose user-configurable local stdio MCP commands**. Official Codex MCP configuration can launch a local command with arguments/environment; exposing that directly in a multi-tenant Center before a whole-process-tree execution envelope would create an arbitrary server-process execution surface.

FDEX also does not hand ChatGPT/OpenAI Connector tokens, OAuth refresh tokens or arbitrary MCP bearer tokens to Codex in this phase. A later owner-scoped MCP registry/OAuth broker must keep those credentials under FDEX authority and expose only the minimum runtime capability.

Phase 7.24 becomes accepted `main` only after its PR and post-merge main CI are green.

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

## What remains after Phase 7.24

The next compatibility layers include:

- owner-scoped remote MCP registry with FDEX-held credentials and explicit server/tool allowlists;
- MCP OAuth callback/token broker without exposing refresh/bearer credentials to arbitrary Codex shell processes;
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
12. Do not describe proprietary OpenAI cloud services as open-source merely because their client code is present in the repository.
13. Require full FastAPI + Android CI before merge.

## Historical progress file

`DEVELOPMENT_PROGRESS.md` contains older detailed history and may describe an earlier baseline. Use this file plus the latest merged PRs for the current baseline.
