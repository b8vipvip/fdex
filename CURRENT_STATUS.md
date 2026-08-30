# FDEX Current Status / FDEX 当前状态

Last updated / 最后更新：2026-08-30

## Current baseline / 当前基线

- Default branch / 默认分支：`main`
- Accepted Codex Host baseline：**Phase 7.31 — Official Codex Multi-Agent V2 governance**
- Accepted `main` commit：`56d7f4ab938e5597f1cfb880ee3302f04c1a3b15`
- Phase 7.30：PR **#92** · accepted commit `c51afb04fad9b0265792c96688a1b0343c30a950`
- Phase 7.31：PR **#93** · accepted commit `56d7f4ab938e5597f1cfb880ee3302f04c1a3b15`
- Active development：**Phase 7.32 — Whole Codex process-tree isolation + Runtime lifecycle**
- PR：**#94**
- Branch：`agent/phase7-32-codex-process-isolation`
- Phase 7.32 is not accepted until the final PR head passes FastAPI Tests + Android unit tests + Android Debug APK and the merged `main` baseline is checked.
- Current Android stable release：**v1.1.36**
- `FDEX_AGENT_ENGINE=legacy` remains the production default until Phase 7.33 real Provider compatibility/rollout smoke is complete.

A phase is accepted only from its final reviewed head. Do not reuse an older green CI run after the branch changes.

## Product and authority model / 产品与权限模型

FDEX Center `user_id` is the canonical owner scope. User-facing identities use the universal **智体** model. GitHub App Installation remains repository authority. Codex never receives GitHub Installation tokens and never owns push/PR authority; FDEX validates the worktree and owns commit/push/PR after the Codex turn.

## Official Codex Host progress / 官方 Codex Host 进度

### Phase 7.19–7.20 — Official Runtime + Native App Server Host ✅

- official Codex Runtime integrated without vendoring the Rust workspace;
- native `codex app-server` JSON-RPC is the compatibility ABI;
- owner-scoped `CODEX_HOME`;
- FDEX Responses Provider mapping;
- sanitized process/shell environment;
- isolated task worktree and FDEX-owned GitHub authority;
- real bundled Runtime app-server handshake/API validation in CI.

### Phase 7.21 — Durable Thread / Turn Host ✅

- Thread/Turn/task persistence;
- resume / fork / steer / compact;
- cross-worker control queue and execution lease;
- stale-worker reconciliation and account-erasure lifecycle.

### Phase 7.22 — Complete Item/Event stream + realtime UI ✅

- schema-light durable app-server notification stream;
- current Item projections and reconnect-safe deltas;
- SSE browser UI;
- orphan Item reconciliation;
- future Item variants do not require a private FDEX event protocol.

### Phase 7.23 — Approvals + requestUserInput ✅

- command/file/permission approvals;
- official `item/tool/requestUserInput`;
- typed JSON-RPC request ID preservation;
- encrypted cross-worker answer bridge;
- secret answers destroyed after Host claim;
- FDEX filesystem/network/GitHub policy remains authoritative over approval clicks.

### Phase 7.24 — MCP elicitation ✅

- official `mcpServer/elicitation/request`;
- standard form and constrained HTTPS URL modes;
- unsupported/proprietary modes fail closed.

### Phase 7.25–7.28 — Remote MCP security stack ✅

- owner-scoped HTTPS Remote MCP registry and tool allowlists;
- request-time DNS revalidation, public-IP pinning and TLS hostname authority;
- task-scoped localhost capability gateway;
- encrypted static Bearer vault outside Codex;
- OAuth 2.0 Authorization Code + PKCE broker owned by FDEX;
- token/credential revisions bind capability leases;
- no long-lived MCP credential is exposed to Codex config, shell, normal UI or account export.

### Phase 7.29 — Official multimodal / Skill / Mention inputs ✅

- official Codex `UserInput[]` for text, localImage, localAudio, skill and mention;
- owner/task media store and signature/MIME/size validation;
- repository Mention and Skill path escape rejection;
- queued-only mutation and Retry inheritance;
- account deletion cleanup and Agent input center.

### Phase 7.30 — Skills / Hooks / Plugins capability control ✅

Accepted in PR #92.

- native `skills/list`, `skills/config/write`, `hooks/list`;
- local-only `plugin/list` with `forceRefetch=false`;
- `plugin/installed` and verified `plugin/read`;
- fresh exact Skill-path revalidation before writes;
- no implicit repository clone/fetch from capability inventory;
- Dynamic Tool remains fail-closed on bundled compatibility line.

Phase 7.30 intentionally kept executable Plugin mutation closed pending deeper isolation review. Phase 7.32 has now confirmed that cgroup isolation alone is still insufficient, so Plugin mutation remains closed. See `docs/CODEX_CAPABILITY_CONTROL.md`.

### Phase 7.31 — Official Multi-Agent V2 governance ✅

Accepted in PR #93.

- official Codex Multi-Agent V2, not an FDEX sub-agent reimplementation;
- operator-owned CLI governance above tenant config;
- root + child concurrency ceiling;
- shared weighted rollout-token budget;
- bounded `wait_agent` intervals;
- `expose_spawn_agent_model_overrides=false`;
- bundled `openai-codex-cli-bin==0.147.0` parses the real governance config in CI;
- existing Phase 7.22 Item/Event persistence automatically covers sub-agent activity.

See `docs/CODEX_SUBAGENT_GOVERNANCE.md`.

### Phase 7.32 — Whole Codex process-tree isolation + Runtime lifecycle 🚧 PR #94

Implementation is present on `agent/phase7-32-codex-process-isolation`. Final merge is pending the newest-head CI seal.

Implemented and reviewed:

- each real FDEX-provider `codex app-server` Host runs in its own transient systemd service;
- cgroup v2 `MemoryMax`, `CPUQuota`, `TasksMax` cover the complete descendant tree;
- `KillMode=control-group`, graceful stop, verified all-process SIGKILL fallback;
- `BindsTo=<FDEX service>` tears Codex trees down with Center stop/restart;
- deterministic hashed unit names do not expose owner/task/project identifiers;
- Provider secret values never appear in systemd-run argv;
- systemd `$` / `%` ExecStart escaping;
- Linux/systemd/cgroup-v2 production preflight fails closed;
- old Codex transient trees must stop before Runtime activation/rollback;
- official OpenAI Codex GitHub Release source/URL/tag/architecture/digest/size validation;
- staged SHA-256 download and safe tar extraction without `extractall()`;
- path traversal, symlink, hardlink and device archive entries rejected;
- candidate Runtime must pass `--version`, current Phase 7.31 governance and `app-server --help` parsing;
- immutable managed Runtime directories and manifests;
- `/admin/agent/runtime` status, upgrade and rollback surface;
- reversible `FDEX_AGENT_CODEX_BIN` pin lifecycle;
- cross-worker Runtime launch/switch `flock` fence closes the race between old-tree cleanup and pin activation;
- rollback fallback validation uses the same resolver precedence as runtime launch (system Codex before bundled fallback when pin is empty).

#### Phase 7.32 Plugin security conclusion

The final security review rejected the earlier idea of opening Plugin install/uninstall merely because cgroup isolation is enforced.

Bundled Codex 0.147 local stdio MCP/Plugin commands may run as local app-server child processes. cgroup v2 constrains resource use and lifecycle, but does not create a filesystem confidentiality boundary. Therefore executable Plugin code could still access host files readable by the FDEX service account.

Final Phase 7.32 policy:

- `plugin/list` / `plugin/installed` / `plugin/read` remain available;
- `plugin/install` remains fail-closed;
- `plugin/uninstall` remains fail-closed;
- Marketplace add/remove/upgrade remains fail-closed;
- remote catalog install remains fail-closed;
- `plugin/share/*` remains fail-closed;
- compatibility POST routes return audited rejection only and never create a mutation Host or invoke mutation RPC.

A separate filesystem/execution sandbox boundary is required before executable Plugin writes can be reconsidered.

Architecture/operations: `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`.

## Remaining Codex Host work / 尚未完成

### Phase 7.33 — Provider production compatibility + rollout seal

Start only after Phase 7.32 final-head CI and merge are complete.

Required work:

- real Responses streaming compatibility smoke per configured Provider;
- real reasoning → tool call → command/file change → MCP → Multi-Agent → completed task path;
- classify Providers as wire-compatible / Codex-tool-compatible / full-feature-compatible;
- define task-level failure semantics so Provider failover never continues on a partially modified worktree;
- controlled safe failover where a fresh task/worktree boundary exists;
- final production security regression and operator deployment checklist;
- only after real smoke succeeds, decide whether to move the production default away from `FDEX_AGENT_ENGINE=legacy`.

### Executable Plugin filesystem sandbox — deferred security requirement

This is now an explicit separate requirement rather than being hidden inside the 7.32 cgroup milestone. Before Plugin install/uninstall can be opened, FDEX must provide and verify a filesystem/execution sandbox that protects Center/service-host data from Plugin-local stdio processes.

## Android-native parity

Phase 7.30–7.33 server/Web/Codex Host work does not by itself require a new Android stable release. Keep v1.1.36 unless Android source or a release-worthy Android behavior changes.

## Development rules / 后续规则

1. Use Center `user_id` for every user-owned resource.
2. Keep GitHub App Installation authority outside Codex.
3. Never allow Codex to write directly to `main`.
4. Use native App Server Protocol as the compatibility ABI.
5. Human approval never overrides FDEX filesystem/network/GitHub policy.
6. Remote MCP destinations and long-lived credentials remain behind FDEX enforcement.
7. Cgroup containment is not a substitute for filesystem sandboxing.
8. Browser-provided file paths are never trusted as server paths.
9. Unsupported or unverifiable tool/permission/Plugin states fail closed.
10. Runtime switching must kill old Codex trees before changing the active binary pin and must use the launch/switch fence.
11. Require FastAPI + Android unit + Android Debug APK CI on the final PR head before merge.
12. Do not change production Agent default until Phase 7.33 real Provider smoke is complete.

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
