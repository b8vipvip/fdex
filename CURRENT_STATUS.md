# FDEX Current Status / FDEX 当前状态

Last updated / 最后更新：2026-08-30

## Current baseline / 当前基线

- Default branch / 默认分支：`main`
- Accepted main before Phase 7.29 / 7.29 前已接受主线：**Phase 7.28 — FDEX-held Remote MCP OAuth Broker**
- Phase 7.28 accepted main commit：`7e7cac7f298366d9ef39880ee929a92338b1d018`
- Phase 7.28 PR：**#90**
- Phase 7.28 final PR head：`981ea213168d26227d4512683cc8a369077c80ea`
- Phase 7.28 PR Build and Test：`33294402334` — success
- Phase 7.28 post-merge main checks：FastAPI Tests + Android unit tests + Android Debug APK — success
- Android Auto Release：skipped as expected for server/Web-only changes
- Current Phase 7.29 PR：**#91 — official Codex multimodal task inputs**
- Phase 7.29 validated head before this documentation refresh：`a15100490c221ae3dc35ccc29a4848a6c07441f3`
- Phase 7.29 Build and Test run：`33295186025` — FastAPI Tests + Android unit tests + Android Debug APK all passed
- Current Android stable release / 当前 Android 正式版：**v1.1.36**
- `FDEX_AGENT_ENGINE=legacy` remains the production rollout default until the production Provider compatibility smoke gate is completed.

A phase is accepted only after the final PR head and the post-merge main commit both pass FastAPI Tests, Android unit tests and Android Debug APK.

## Product and ownership model / 产品与归属模型

FDEX uses Center `user_id` as the canonical owner scope. User-facing identities use the universal **智体** model. GitHub App Installation remains repository authority; Codex never receives GitHub installation tokens and never owns push/PR authorization.

## Official OpenAI Codex Host progress / 官方 Codex Host 进度

### Phase 7.19–7.20 — Official Runtime + Native App Server Host ✅

- official Codex Runtime integrated without vendoring the Rust workspace;
- native `codex app-server` JSON-RPC is the long-term ABI;
- owner-scoped `CODEX_HOME`;
- FDEX Responses Provider mapping;
- sanitized process/shell environment;
- task worktree and GitHub authority remain FDEX controlled;
- CI starts a real bundled Codex binary and performs a native app-server handshake/API smoke test.

### Phase 7.21 — Durable Thread / Turn Host ✅

- persistent Thread/Turn/task bindings;
- resume / fork / steer / compact;
- cross-worker control queue;
- Thread execution lease and stale worker reconciliation;
- account erasure lifecycle.

### Phase 7.22 — Complete Item/Event stream + realtime UI ✅

- durable raw app-server notification stream;
- current Item projections and reconnect-safe deltas;
- SSE realtime browser UI;
- orphan Item reconciliation;
- schema-light handling for future Item variants.

### Phase 7.23 — Approvals + requestUserInput ✅

- command, file-change and permission approvals;
- `item/tool/requestUserInput`;
- typed JSON-RPC id preservation;
- encrypted cross-worker answer bridge;
- secret answers destroyed after Host claim;
- FDEX network/worktree policy remains authoritative over a human approval click.

### Phase 7.24 — MCP elicitation ✅

- official `mcpServer/elicitation/request` bridge;
- standard form and constrained HTTPS URL modes;
- proprietary/unsupported modes fail closed.

### Phase 7.25 — Owner-scoped Remote MCP Registry ✅

- HTTPS-only owner/server registry;
- explicit tool allowlists;
- public-DNS admission and emergency disable;
- account export/delete lifecycle;
- no arbitrary user-configurable local stdio process launch.

### Phase 7.26 — Destination-enforcing Remote MCP Gateway ✅

- Codex receives only task-scoped localhost MCP capability URLs;
- request-time DNS revalidation and pinned public-IP connection;
- original hostname remains TLS SNI/certificate authority;
- no redirects, host proxy inheritance or auth challenge passthrough;
- gateway independently enforces the tool allowlist;
- leases bind owner/task/server/registry revision and are revoked on terminal task exit.

### Phase 7.27 — FDEX-held static Bearer credential vault ✅

- encrypted owner/server Bearer custody outside the public MCP registry;
- Codex config/shell never receives the remote Bearer;
- FDEX gateway injects Authorization only after all policy checks;
- credential revision is part of the capability lease contract;
- rotation/delete/TOCTOU fail closed;
- vault key lifecycle and permanent account cleanup covered by tests.

### Phase 7.28 — FDEX-held OAuth broker ✅

- OAuth 2.0 Authorization Code + PKCE broker owned by FDEX;
- encrypted access/refresh/client-secret custody;
- owner/server/state binding and callback validation;
- destination-enforced token/refresh/revoke transport;
- durable cross-worker refresh serialization;
- OAuth grant revision participates in lease authorization;
- tokens remain absent from Codex config, shell, normal UI plaintext and account export.

### Phase 7.29 — Official multimodal / Skill / Mention Turn inputs 🚧 PR #91

Implementation completed on the feature branch and the pre-documentation head passed full PR CI.

Implemented:

- official `UserInput[]` instead of FDEX prompt emulation;
- `text`, `localImage`, `localAudio`, `skill`, `mention`;
- owner/task media store with generated filenames;
- image MIME/signature checks and 20 MiB limit;
- audio MIME/signature checks and 50 MiB limit;
- repository-relative Mention resolved only after the isolated worktree exists;
- owner `CODEX_HOME/skills/<name>/SKILL.md` resolution only;
- absolute path, `..` and symlink escape rejection;
- queued-only mutation and immutable input after execution begins;
- Retry inheritance without duplicating Thread Resume/Fork history;
- permanent account deletion of input metadata and media assets;
- user-facing “Agent 输入” center and task input page;
- dedicated attack/regression tests;
- bilingual architecture doc: `docs/CODEX_MULTIMODAL_INPUTS.md`.

## Remaining Codex Host work / 尚未完成

These are **not yet complete**, therefore the complete Codex Host program is **not ready for a new formal release declaration**:

### Phase 7.30 — Skills / Hooks / Plugins control plane

- native `skills/list`, `skills/config/write`, `hooks/list` integration;
- owner-scoped Skill installation/update/remove policy;
- official plugin/list/read/install/uninstall/marketplace bridge where portable;
- explicit prohibition or outer sandbox for plugin-provided local stdio execution;
- safe policy bridge for dynamic `item/tool/call` requests;
- Web management UI and account lifecycle.

### Phase 7.31 — Sub-Agent / Collaboration resource governance

- expose official child/descendant Thread relationships and collaboration Items;
- per-owner/per-root-task sub-agent count and nesting limits;
- aggregate CPU/RAM/PID/concurrency budgets across the whole agent tree;
- prevent child agents from expanding GitHub/network/worktree/MCP authority;
- parent cancel/account deletion must terminate descendant work.

### Phase 7.32 — Whole Codex process-tree isolation + Runtime lifecycle

- entire `codex app-server` + spawned command process tree inside a dedicated cgroup v2/systemd scope;
- CPU, MemoryMax, TasksMax/PID and concurrency enforcement at the process-tree boundary;
- reliable tree termination (`cgroup.kill`/systemd scope stop) on cancel/crash;
- staged official Runtime install;
- version/protocol/schema smoke verification;
- atomic activation and previous-version rollback;
- admin status/diagnostics without leaking secrets.

### Phase 7.33 — Provider production compatibility + rollout seal

- real Responses streaming/tool-call compatibility smoke per Provider;
- classify wire-compatible vs Codex-tool-compatible vs full-feature-compatible Providers;
- safe task-level provider failure semantics without continuing on a partially modified worktree;
- production smoke gate before changing `FDEX_AGENT_ENGINE` default from legacy/auto policy;
- final consolidated security regression and operator deployment documentation.

### Android-native parity

Server/Web Codex Host capability is the priority. Android-native rendering/actions for new Codex Item/approval/input/management surfaces remain separate unless required by a phase; no Android release should be emitted merely for server/Web-only changes.

## Development rules / 后续规则

1. Use Center `user_id` for every user-owned resource.
2. Use GitHub App Installation as repository authority; never give its token to Codex.
3. Never allow Codex to write directly to `main`.
4. App Server Protocol is the Codex compatibility ABI; do not fork the full Rust workspace without a compelling reason.
5. Human approval never overrides FDEX filesystem/network/GitHub policy.
6. Remote MCP destinations and long-lived credentials stay behind FDEX enforcement.
7. Do not expose arbitrary local stdio MCP/plugin process launch in a multi-tenant Center without an outer process-tree sandbox.
8. Browser-provided file paths are never trusted as server paths.
9. Unsupported or unverifiable permission/tool requests fail closed.
10. Require FastAPI + Android unit + Android Debug APK CI before every merge and again on main.

## Reference docs / 参考文档

- `docs/CODEX_ENGINE.md`
- `docs/CODEX_COMPATIBILITY.md`
- `docs/CODEX_INTERACTIONS.md`
- `docs/CODEX_MCP_ELICITATION.md`
- `docs/CODEX_REMOTE_MCP.md`
- `docs/CODEX_MULTIMODAL_INPUTS.md`

`DEVELOPMENT_PROGRESS.md` is historical. Use this file plus the latest merged PRs as the current baseline.