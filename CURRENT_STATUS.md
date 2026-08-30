# FDEX Current Status / FDEX 当前状态

Last updated / 最后更新：2026-08-30

## Current baseline / 当前基线

- Default branch / 默认分支：`main`
- Accepted Codex Host baseline / 已接受 Codex Host 主线：**Phase 7.31 — Official Codex Multi-Agent V2 governance**
- Current accepted `main` commit：`56d7f4ab938e5597f1cfb880ee3302f04c1a3b15`
- Phase 7.29 accepted commit：`aa7acfb4f651b5145c2b2b0902cf68318a2f3294`
- Phase 7.30 accepted commit：`c51afb04fad9b0265792c96688a1b0343c30a950` · PR **#92**
- Phase 7.31 accepted commit：`56d7f4ab938e5597f1cfb880ee3302f04c1a3b15` · PR **#93**
- Active development / 当前开发：**Phase 7.32 — Whole Codex process-tree isolation + Runtime lifecycle**, PR **#94**, branch `agent/phase7-32-codex-process-isolation`
- Phase 7.32 is not accepted until the latest PR head passes FastAPI Tests + Android unit tests + Android Debug APK and the merged main commit is verified.
- Current Android stable release / 当前 Android 正式版：**v1.1.36**
- `FDEX_AGENT_ENGINE=legacy` remains the production rollout default until Phase 7.33 Provider production compatibility smoke is complete.

A phase is accepted only after its final code/security review and required CI seal. Do not infer completion from an older PR run after the branch head has changed.

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
- CI starts a real bundled Codex binary and performs native app-server handshake/API validation.

### Phase 7.21 — Durable Thread / Turn Host ✅

- persistent Thread/Turn/task bindings;
- resume / fork / steer / compact;
- cross-worker control queue;
- Thread execution lease and stale-worker reconciliation;
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
- FDEX network/worktree policy remains authoritative over human approval.

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
- gateway independently enforces tool allowlists;
- leases bind owner/task/server/registry revision and are revoked on terminal task exit.

### Phase 7.27 — FDEX-held static Bearer credential vault ✅

- encrypted owner/server Bearer custody outside the public MCP registry;
- Codex config/shell never receives the remote Bearer;
- FDEX gateway injects Authorization only after policy checks;
- credential revision participates in the capability lease contract;
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

### Phase 7.29 — Official multimodal / Skill / Mention Turn inputs ✅

- official `UserInput[]` rather than FDEX prompt emulation;
- `text`, `localImage`, `localAudio`, `skill`, `mention`;
- owner/task media store with generated filenames;
- image/audio MIME/signature/size validation;
- repository-relative Mention resolved only after isolated worktree creation;
- owner `CODEX_HOME/skills/<name>/SKILL.md` resolution only;
- absolute path, `..` and symlink escape rejection;
- queued-only input mutation and immutable inputs after execution begins;
- Retry inheritance without duplicating Thread Resume/Fork history;
- account-deletion cleanup;
- user-facing Agent input center;
- dedicated attack/regression coverage;
- bilingual `docs/CODEX_MULTIMODAL_INPUTS.md`.

### Phase 7.30 — Skills / Hooks / Plugins capability control ✅

Accepted in PR #92 / `c51afb04fad9b0265792c96688a1b0343c30a950`.

- native `skills/list`, `skills/config/write`, `hooks/list`;
- owner/project-scoped capability inventory;
- Skill write path is freshly revalidated against official inventory;
- local-only `plugin/list` with `forceRefetch=false`;
- verified `plugin/read`;
- no implicit repository clone/fetch from opening capability UI;
- Dynamic Tool compatibility gate remains fail-closed on the bundled compatibility line;
- Phase 7.30 originally kept executable Plugin mutation closed pending Phase 7.32 outer isolation.

Phase 7.32 extends this control plane with narrowly verified local Plugin install/uninstall while Marketplace/share mutations remain closed. See `docs/CODEX_CAPABILITY_CONTROL.md`.

### Phase 7.31 — Official Multi-Agent V2 governance ✅

Accepted in PR #93 / `56d7f4ab938e5597f1cfb880ee3302f04c1a3b15`.

- official Codex Multi-Agent V2, not a parallel FDEX sub-agent scheduler;
- operator-owned CLI governance overrides;
- root + child concurrency ceiling;
- shared weighted rollout-token budget;
- bounded `wait_agent` intervals;
- child Provider/model override exposure disabled;
- tenant `CODEX_HOME/config.toml` cannot loosen Center limits;
- bundled `openai-codex-cli-bin==0.147.0` is invoked in CI to parse the real Multi-Agent/Rollout configuration;
- Item/Event persistence continues through the existing schema-light stream.

See `docs/CODEX_SUBAGENT_GOVERNANCE.md`.

### Phase 7.32 — Whole Codex process-tree isolation + Runtime lifecycle 🚧 PR #94

Implementation is present on `agent/phase7-32-codex-process-isolation`; **do not mark accepted until the final PR head and merged main are sealed**.

Implemented in the branch:

- real FDEX Provider `codex app-server` Hosts run in independent transient systemd services;
- cgroup v2 `MemoryMax`, `CPUQuota`, `TasksMax` apply to the entire descendant tree;
- `KillMode=control-group`, graceful stop and verified all-process SIGKILL fallback;
- `BindsTo=<FDEX service>` removes old Codex trees on Center shutdown/restart;
- deterministic hashed unit names do not expose owner/task/project identifiers;
- Provider secret values do not appear in systemd-run argv; environment is forwarded by variable name from the sanitized Host environment;
- systemd `$` and `%` ExecStart expansion is escaped;
- Linux/systemd/cgroup-v2 preflight is fail-closed for real FDEX Provider Hosts;
- all old FDEX Codex transient units are terminated and rechecked before Runtime pin changes;
- official OpenAI Codex release metadata/URL/digest/size validation;
- staged download, SHA-256 validation and safe tar extraction without `extractall()`;
- symlink/hardlink/device/path-traversal archives are rejected;
- candidate Runtime must pass `--version` and current Phase 7.31 governance + `app-server --help` parsing;
- immutable managed Runtime versions + manifest hashes;
- activation and reversible rollback through existing `FDEX_AGENT_CODEX_BIN` pin;
- admin Runtime page at `/admin/agent/runtime`;
- local Plugin install only when cgroup isolation is actually enforced, only for exact local inventory entries with `availability=AVAILABLE` and a known installable policy;
- install is confirmed by same-marketplace `plugin/installed` inventory;
- uninstall requires an exact currently installed `pluginId` and post-operation disappearance confirmation;
- Marketplace add/remove/upgrade, remote-catalog install and `plugin/share/*` remain fail-closed;
- dedicated process-isolation, Runtime supply-chain and Plugin-mutation regression tests.

Architecture/operations: `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`.

## Remaining Codex Host work / 尚未完成

### Phase 7.33 — Provider production compatibility + rollout seal

This is the remaining formal Codex Host rollout phase after Phase 7.32 is accepted.

Planned requirements:

- real Responses streaming/tool-call compatibility smoke per Provider;
- classify wire-compatible vs Codex-tool-compatible vs full-feature-compatible Providers;
- safe task-level Provider failure semantics without continuing on a partially modified worktree;
- production smoke gate before changing `FDEX_AGENT_ENGINE` default from legacy/auto policy;
- consolidated security regression and operator deployment documentation;
- final production rollout decision.

## Android-native parity

Server/Web Codex Host capability remains the priority. Android-native rendering/actions for new Codex Item/approval/input/management surfaces are separate unless explicitly required by a phase. Do not publish a new Android stable release merely because a server/Web-only phase merged.

## Development rules / 后续规则

1. Use Center `user_id` for every user-owned resource.
2. Use GitHub App Installation as repository authority; never give its token to Codex.
3. Never allow Codex to write directly to `main`.
4. App Server Protocol is the Codex compatibility ABI; do not fork the full Rust workspace without a compelling reason.
5. Human approval never overrides FDEX filesystem/network/GitHub policy.
6. Remote MCP destinations and long-lived credentials stay behind FDEX enforcement.
7. Executable Plugin/stdio descendants must remain inside the Phase 7.32 process-tree boundary.
8. Browser-provided file paths are never trusted as server paths.
9. Unsupported or unverifiable permission/tool/Plugin states fail closed.
10. Runtime switching must terminate old Codex trees before changing the active binary pin.
11. Require FastAPI + Android unit + Android Debug APK CI on the final PR head before merge; verify the resulting main baseline before declaring the phase accepted.

## Reference docs / 参考文档

- `docs/CODEX_ENGINE.md`
- `docs/CODEX_COMPATIBILITY.md`
- `docs/CODEX_INTERACTIONS.md`
- `docs/CODEX_MCP_ELICITATION.md`
- `docs/CODEX_REMOTE_MCP.md`
- `docs/CODEX_MULTIMODAL_INPUTS.md`
- `docs/CODEX_CAPABILITY_CONTROL.md`
- `docs/CODEX_SUBAGENT_GOVERNANCE.md`
- `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`

`DEVELOPMENT_PROGRESS.md` is historical. Use this file plus the latest merged PRs as the current development baseline.
