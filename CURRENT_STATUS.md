# FDEX Current Status

Last updated: 2026-08-29

## Current baseline

- Default branch: `main`
- Baseline proposed by this branch: **Phase 7.20 — Codex Native Host**
- Current Android stable release: **v1.1.36**
- v1.1.36 contains the Phase 7.13 Android migration to the universal `智体` product model.
- Phase 7.14 through Phase 7.20 are server/Web/infrastructure changes and do not require an Android release by themselves.
- `FDEX_AGENT_ENGINE=legacy` remains the rollout default until a production Responses/tool smoke task succeeds.

A concrete commit becomes an accepted `main` baseline only after FastAPI Tests, Android unit tests and Android Debug APK are all green.

## Current product model

FDEX user-facing identities use the universal **智体** model, not the former company / industry / department / position / AI-employee hierarchy. Historical `employee` fields and routes may remain internally for compatibility.

## Center account and isolation

FDEX Center `user_id` is the canonical owner scope for GitHub, Coding Agent, Web workspace, remote memory, tasks and sandboxes.

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

### Phase 7.19 foundation

Phase 7.19 first integrated the official `openai-codex==0.147.0` Python SDK and matching bundled Codex Runtime while retaining FDEX account/GitHub/worktree/security authority.

### Phase 7.20 native host

Phase 7.20 changes the long-term compatibility boundary from the high-level Python SDK to the public **`codex app-server` JSON-RPC protocol**.

Implemented on this branch:

- `server/app/codex_app_server.py` directly hosts official `codex app-server` over stdio;
- native initialize/initialized, request/response, notification and server-request routing;
- direct `thread/start`, `turn/start` and `turn/interrupt` execution;
- unknown/new notifications are transport-compatible rather than fatal;
- unsupported interactive approval/permission/user-input/MCP requests fail closed until FDEX has an owner-scoped UI/policy bridge;
- Runtime selection: `FDEX_AGENT_CODEX_BIN` → system `codex` → official bundled Runtime;
- admin runtime status reports version/source/protocol/provider without secrets;
- FDEX can therefore use a newer verified official Runtime without waiting for the Python SDK to wrap every new method;
- one owner-scoped `CODEX_HOME` per FDEX `user_id`, allowing native thread/history/skills/hooks/plugin/MCP state to persist inside the user boundary;
- task repository worktrees remain independent;
- shared FDEX Provider pool remains the model source and requires a configured Responses-capable provider;
- Codex process environment is sanitized before the official binary starts;
- Provider API Key does not enter Codex shell command environments (`inherit=none`);
- FDEX project `allow_network` maps to Codex workspace-write network access;
- Web Search remains disabled until FDEX defines a separate explicit permission;
- Codex never receives GitHub App/OAuth/PAT/maintenance credentials;
- FDEX validates `.env`, `server/data` and `.git` protected paths before commit/push;
- FDEX remains the authority for local commit, optional branch push and optional PR.

### Real Runtime CI

Phase 7.20 adds a FastAPI integration test that starts the actual official bundled Codex binary, completes the real app-server handshake and calls a native non-model API. This detects wire-protocol/runtime drift without requiring an API key or model quota.

Production CI still cannot prove a configured external Provider implements all Codex Responses/tool-streaming semantics. A real production Coding Agent smoke task is required before changing the default engine.

## Open-source Codex compatibility policy

FDEX does **not** vendor every Rust crate from `openai/codex`.

The goal is to use the complete portable local capability set through the official Runtime while keeping FDEX as a multi-user control plane. See:

- `docs/CODEX_ENGINE.md`
- `docs/CODEX_COMPATIBILITY.md`

The compatibility policy distinguishes:

1. local/portable runtime capabilities that FDEX should directly adopt (Core, Exec, Sandbox, ExecPolicy, Thread/State, Skills, Hooks, local MCP/plugins, collaboration, etc.);
2. capabilities requiring FDEX owner-scoped permission/UI bridges (approvals, user input, MCP elicitation/OAuth, multimodal, plugins, sub-agents);
3. open client code that still depends on proprietary OpenAI/ChatGPT cloud services and therefore cannot be claimed as arbitrary-provider standalone compatibility;
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
- **Phase 7.19** — official Codex SDK/Runtime foundation.
- **Phase 7.20** — native official Codex App Server host and full-repository compatibility strategy.

## What remains after Phase 7.20

The native host is the foundation for broad Codex compatibility, not a claim of ChatGPT Codex cloud-product parity. Follow-up work includes:

- persist Codex thread/turn ids in FDEX task records;
- resume/fork/steer/compact and continuation UI;
- rich Item/Turn real-time Web/Android rendering;
- image/audio/local attachment/skill/mention input;
- command/file/permission approval bridge;
- `tool/requestUserInput` and MCP elicitation/OAuth bridge;
- Skills/Hooks/MCP/local plugin management;
- collaboration/sub-agent resource governance;
- whole Codex process-tree systemd CPU/Memory/PID/concurrency envelope;
- verified official Runtime updater/rollback;
- provider compatibility smoke tests and safe failover semantics.

## Development rules going forward

1. Use `智体` for user-facing identities.
2. Use Center `user_id` as the owner scope for every user resource.
3. Treat GitHub App Installation as repository-permission authority.
4. Keep GitHub tokens short-lived and downscoped; never give them to Codex.
5. Never allow Coding Agent/Codex to write directly to `main`.
6. Keep GitHub egress application-scoped and avoid host-global networking side effects.
7. Keep secrets out of Codex Runtime/shell beyond the one selected Provider key needed by Runtime.
8. Keep project network policy authoritative; do not use Web Search as a bypass.
9. Treat the official App Server Protocol as the Codex compatibility ABI; do not fork core crates without a compelling reason.
10. Fail closed on unsupported server-initiated approval/permission requests.
11. Do not describe proprietary OpenAI cloud services as open-source merely because their client code is present in the repository.
12. Require full FastAPI + Android CI before merge.

## Historical progress file

`DEVELOPMENT_PROGRESS.md` contains older detailed history and may describe an earlier baseline. Use this file plus the latest merged PRs for the current baseline.
