# FDEX Current Status

Last updated: 2026-08-29

## Current baseline

- Default branch: `main`
- Accepted `main` baseline before this branch: **Phase 7.22 — Durable/Realtme Codex Item Stream**
- Accepted Phase 7.22 main commit: `e4169f906589dcca33c82472519ff9aed42be78b`
- Baseline proposed by this branch: **Phase 7.23 — Durable Codex Interactions**
- Current development branch: `agent/phase7-23-codex-interactions`
- Current Android stable release: **v1.1.36**
- v1.1.36 contains the Phase 7.13 Android migration to the universal `智体` product model.
- Phase 7.14 through Phase 7.23 are server/Web/infrastructure changes and do not require an Android release by themselves.
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

Accepted on `main` before Phase 7.22.

Implemented:

- persistent owner/task/Thread/Turn identities in SQLite;
- `thread/resume`, `thread/fork`, `turn/steer`, `thread/compact/start`;
- continuation tasks inherit the verified parent commit while retaining isolated worktrees;
- cross-Uvicorn-worker control queue;
- kernel `flock` prevents two workers from owning one Thread Host simultaneously;
- stale worker/Turn/control reconciliation;
- account deletion refuses active Host state and erases durable Thread/Turn/control records.

### Phase 7.22 — Durable Item / Event Stream

Accepted `main` commit: `e4169f906589dcca33c82472519ff9aed42be78b`.

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

Phase 7.22 PR CI passed FastAPI Tests (`297 passed, 2 skipped`) plus Android unit/debug APK, and the post-merge `main` CI passed again.

### Phase 7.23 — Durable Codex Interactions (current branch)

This branch changes supported interactive requests from unconditional denial to an owner-scoped, fail-closed broker for:

- `item/commandExecution/requestApproval`;
- `item/fileChange/requestApproval`;
- `item/permissions/requestApproval`;
- `item/tool/requestUserInput`.

Implemented/proposed on this branch:

- the real JSON-RPC request id is preserved, including the distinction between numeric and string ids;
- command `approvalId` is stored separately from `itemId`, matching official subcommand/writeStdin routing semantics;
- requests are bound to FDEX `owner_id`, `task_id`, Host session, Thread, Turn and Item;
- responses can be submitted through any Uvicorn worker and are atomically claimed only by the worker that owns the matching stdio Host;
- `requestUserInput` secret answers are encrypted with a dedicated Fernet key while waiting for the Host;
- first-use interaction-key publication is atomic across workers;
- answer ciphertext is destroyed immediately after successful Host claim;
- response history stores only a redacted summary, never secret answer bodies;
- request/response protocol payloads fail closed above 1 MiB instead of being shape-truncated;
- task cancellation from another worker terminates a pending interaction;
- Host shutdown, coroutine cancellation and stale/orphan Host state terminalize pending interactions and clear ciphertext;
- Web task detail renders command/file/permissions approvals and requestUserInput questions;
- secret questions use password inputs with autocomplete disabled;
- normal HTML forms use CSRF + 303 redirect, while JSON callers can request JSON results;
- interaction snapshot and updates share the Phase 7.22 owner-scoped SSE bus;
- Codex task pages no longer hard-refresh every four seconds while the user may be entering an answer; terminal Turn events trigger a delayed final refresh instead;
- positive approvals remain subordinate to FDEX project/worktree policy:
  - `allow_network=false` cannot be overridden by a user approval click;
  - filesystem permission escalation must stay inside the task worktree;
  - glob/special filesystem escalation fails closed;
  - file-change approvals must have a recoverable Item and verified in-worktree paths;
  - session-wide file approval additionally requires an explicit in-worktree `grantRoot`;
  - ordinary unsandboxed command escalation remains blocked; only scoped network approval (when project network is enabled) and one-time `writeStdin` can be positively approved;
- account deletion reconciles orphan interactions, refuses genuinely active ones, and erases interaction rows before Item/Thread/task state.

Phase 7.23 is **not accepted into `main` until its PR and post-merge main CI are green**.

## Real Runtime CI

The FastAPI suite includes integration coverage that starts the actual bundled Codex binary, completes the real app-server handshake and calls a native non-model API. This detects wire/runtime drift without requiring a model API key.

Production CI still cannot prove an external Provider implements every Codex Responses/tool-streaming semantic. A real production Coding Agent smoke task remains required before changing the default engine.

## Open-source Codex compatibility policy

FDEX does **not** vendor every Rust crate from `openai/codex`.

The goal is to use the portable local capability set through the official Runtime while FDEX remains the multi-user control plane. See:

- `docs/CODEX_ENGINE.md`
- `docs/CODEX_COMPATIBILITY.md`
- `docs/CODEX_INTERACTIONS.md`

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

## What remains after Phase 7.23

The next compatibility layers still include:

- MCP elicitation/OAuth and owner-scoped MCP authorization;
- image/audio/local attachment/skill/mention Turn input;
- Skills/Hooks/MCP/local plugin management and policy UI;
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
10. Fail closed on unsupported, unverifiable or over-broad server-initiated permission requests.
11. Do not describe proprietary OpenAI cloud services as open-source merely because their client code is present in the repository.
12. Require full FastAPI + Android CI before merge.

## Historical progress file

`DEVELOPMENT_PROGRESS.md` contains older detailed history and may describe an earlier baseline. Use this file plus the latest merged PRs for the current baseline.
