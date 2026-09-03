# FDEX Current Status / FDEX 当前状态

Last updated / 最后更新：2026-09-03

## Current baseline / 当前基线

- Default branch / 默认分支：`main`
- Accepted Coding Agent architecture：**Phase 7.36 — Codex-only Agent Core**
- Phase 7.36 core PR：**#102 — Make Coding Agent execution Codex-only**
- Phase 7.36 core merge commit：`6a63b64e383421fbb95c5beb0a633d30d9a7c48b`
- Phase 7.36 PR head：`a92976653021d0b778c4871d766f8d247079337c`
- PR CI：`33708726683` — FastAPI + Android unit + Android Debug APK — success
- Post-merge `main` run：`33708877552` (`Build and Test #1493`) — FastAPI + Android unit + Android Debug APK — success
- Previous Codex Host rollout baseline：**Phase 7.33**, PR #95, `main@080c4ba962ce72cd43f0ee0802aef8050b290748`
- Previous Web Agent-first routing：PR #101, `main@1f3036ab90f65fdee154fc114b9261c025749378`
- Current Android stable release：**v1.1.36**

Phase 7.36 changes the execution invariant: **if a 智体 has Coding Agent permission, its task uses the official OpenAI Codex native Host as the only Agent core.** There is no Legacy/Auto Agent execution mode and no fallback from Coding Agent to ordinary `client_ai`.

Phase 7.33 fresh-full Provider compatibility remains authoritative, but it is now only a **Codex readiness/safety gate**. If the official Runtime or a fresh full-compatible Provider is unavailable, the Coding Agent task fails closed instead of switching to another Agent core.

A phase is accepted only from its final reviewed head and the merged `main` check. Do not reuse older green CI after a branch changes.

## Product and authority model / 产品与权限模型

FDEX Center `user_id` is the canonical owner scope. User-facing identities use the universal **智体** model. GitHub App Installation remains repository authority. Codex never receives GitHub Installation tokens and never owns push/PR authority; FDEX validates the worktree and owns commit/push/PR after a successful Codex turn.

Execution boundary:

```text
employee.coding_agent == true
    -> FDEX owner / project / worktree boundary
    -> Phase 7.33 fresh-full Codex readiness gate
    -> official codex app-server Thread / Turn
    -> FDEX validates and publishes resulting Git state

employee.coding_agent == false
    -> ordinary FDEX client_ai conversation path
```

## Official Codex Host progress / 官方 Codex Host 进度

### Phase 7.19–7.20 — Official Runtime + Native App Server Host ✅

Official Runtime, native `codex app-server` JSON-RPC, owner-scoped `CODEX_HOME`, FDEX Responses Provider mapping, sanitized process/shell environment, isolated task worktrees and FDEX-owned GitHub authority are complete.

### Phase 7.21 — Durable Thread / Turn Host ✅

Thread/Turn/task persistence, resume/fork/steer/compact, cross-worker controls, execution lease and stale-worker reconciliation are complete.

### Phase 7.22 — Complete Item/Event stream + realtime UI ✅

Schema-light durable app-server notification stream, Item projections, SSE reconnect recovery and orphan reconciliation are complete.

### Phase 7.23 — Approvals + requestUserInput ✅

Command/file/permission approvals, official `item/tool/requestUserInput`, typed JSON-RPC correlation and encrypted cross-worker answer handling are complete. Human approval never overrides FDEX filesystem/network/GitHub policy.

### Phase 7.24 — MCP elicitation ✅

Official `mcpServer/elicitation/request` support is complete with constrained standard modes and fail-closed unsupported modes. Local stdio Plugin/MCP children remain under the Phase 7.32 process-tree limits, while arbitrary tenant-provided stdio command registration, credential injection and executable Plugin mutation remain out of scope and fail closed.

### Phase 7.25–7.28 — Remote MCP security stack ✅

Owner-scoped registry, DNS/IP/TLS rebinding defenses, loopback capability gateway, encrypted Bearer vault, OAuth 2.0 Authorization Code + PKCE broker and credential-revision-bound leases are complete.

### Phase 7.29 — Official multimodal / Skill / Mention inputs ✅

Official Codex `UserInput[]` support for text/local image/local audio/Skill/Mention with owner/task validation and retry/account cleanup is complete.

### Phase 7.30 — Skills / Hooks / Plugins capability control ✅

Accepted in PR #92 at `main@c51afb04fad9b0265792c96688a1b0343c30a950`.

- native `skills/list`, `skills/config/write`, `hooks/list`;
- local-only `plugin/list` with `forceRefetch=false`;
- `plugin/installed` and verified `plugin/read`;
- exact fresh Skill-path revalidation;
- no implicit repository clone/fetch;
- Dynamic Tool remains fail-closed on the bundled compatibility line.

### Phase 7.31 — Official Multi-Agent V2 governance ✅

Accepted in PR #93 at `main@56d7f4ab938e5597f1cfb880ee3302f04c1a3b15`.

- official Multi-Agent V2, no parallel FDEX sub-agent scheduler;
- operator-owned CLI governance above tenant config;
- total concurrent-thread ceiling and shared rollout-token budget;
- bounded wait intervals;
- `expose_spawn_agent_model_overrides=false`;
- bundled Runtime parses real governance config in CI;
- Phase 7.22 event persistence covers sub-agent activity.

### Phase 7.32 — Whole Codex process-tree isolation + Runtime lifecycle ✅

Accepted in PR #94 at `main@6c59b2b6c96132770589040c763c3ebd97793307`.

Completed:

- every real FDEX-provider Host runs in a transient systemd service;
- cgroup v2 Memory/CPU/PID limits cover app-server and all descendants;
- `KillMode=control-group`, graceful stop and verified all-process SIGKILL fallback;
- `BindsTo=<FDEX service>` lifecycle ownership;
- Provider secret values stay out of systemd-run argv;
- fail-closed Linux/systemd/cgroup-v2 production preflight;
- official OpenAI Codex Release validation, staged SHA-256 download and safe extraction;
- immutable managed Runtime installs and `/admin/agent/runtime` upgrade/rollback surface;
- stale Codex trees stop before Runtime pin changes;
- cross-worker Runtime launch/switch `flock` fence closes the old-runtime launch race.

Executable Plugin mutation remains fail-closed. Cgroup resource containment is not a filesystem confidentiality sandbox.

### Phase 7.33 — Codex Provider compatibility + rollout seal ✅

Accepted in PR #95 at `main@080c4ba962ce72cd43f0ee0802aef8050b290748`.

- compatibility ledger is separate from generic Provider health;
- levels are `none -> wire -> tools -> full`;
- production requires a fresh `full` proof, default freshness 168 hours;
- fingerprint binds Provider/API-key identity/model order/protocol/timeout/Runtime/governance/resource limits/app version;
- real smoke uses official `codex app-server` in an isolated scratch workspace;
- `full` requires real wire, command/file side effects, MCP, reasoning and Multi-Agent evidence;
- stale/unverified Providers may be skipped only before a Codex Host starts;
- there is no Provider switch inside a started task/worktree;
- Retry creates a fresh task/worktree boundary where Provider selection may run again.

### Phase 7.35 — Agent-first Web Coding Agent routing ✅

Accepted in PR #101 at `main@1f3036ab90f65fdee154fc114b9261c025749378`.

- removed the pre-Codex natural-language capability classifier;
- every `coding_agent=true` employee message enters Agent Runtime;
- project selection exists only to resolve the authorized Codex `cwd`;
- same-chat/same-project turns resume a durable official Codex Thread when a real binding exists;
- Web image/audio attachments map into official task-scoped Codex `UserInput[]`;
- unsupported attachment types fail closed instead of falling back to generic AI.

### Phase 7.36 — Codex-only Agent Core ✅

Accepted in PR #102 at `main@6a63b64e383421fbb95c5beb0a633d30d9a7c48b`.

Completed:

- removed the legacy FDEX model JSON-tool execution loop from the production Agent entry point;
- removed `legacy|auto|codex` runtime selection from Settings and admin UI;
- removed `FDEX_AGENT_ENGINE` from `.env.example`;
- `FdexAgentLoop` remains only as a stable compatibility facade for route/background call sites and always enters the official Codex Host;
- Codex readiness failure terminalizes the task instead of producing `engine.fallback`;
- Coding Agent never falls back to ordinary `client_ai`;
- Phase 7.33 fresh-full proof is retained strictly as a fail-closed Codex readiness gate;
- documentation now defines official Codex Runtime as the sole Coding Agent core;
- final dead `normalize_engine_mode()` compatibility residue is removed by the Phase 7.36 cleanup change, with a regression assertion that the helper no longer exists.

## Production rollout / 生产部署

After deploying this baseline to the real FDEX Center:

1. update the server to the accepted `main`;
2. old `.env` entries named `FDEX_AGENT_ENGINE` have no runtime effect and may be deleted;
3. open `/admin/agent/codex-providers`;
4. run/refresh real `full` smoke for each Provider intended for Coding Agent use;
5. verify at least one Provider has a fresh `full` proof bound to the current fingerprint;
6. enable Coding Agent;
7. if the readiness gate is not satisfied, treat the task failure as a configuration/compatibility problem — there is intentionally no legacy/generic fallback;
8. re-run full smoke after Provider key/model/endpoint, Runtime, governance, resource-limit or app-version changes.

GitHub CI is not a substitute for deployed Provider full-smoke because CI does not hold the real Center Provider credentials.

## Android-native parity

Phase 7.36 is server/Web/Codex Host architecture work and does not by itself require a new Android stable release. Keep **v1.1.36** unless Android source or release-worthy Android behavior changes.

## Development rules / 后续规则

1. Use Center `user_id` for every user-owned resource.
2. Keep GitHub App Installation authority outside Codex.
3. Never allow Codex to write directly to `main`.
4. Use native App Server Protocol as the compatibility ABI.
5. A Coding-Agent-enabled 智体 always uses the official Codex Core; do not recreate a parallel FDEX Agent core or intent router in front of it.
6. Human approval never overrides FDEX filesystem/network/GitHub policy.
7. Remote MCP destinations and long-lived credentials remain behind FDEX enforcement.
8. Cgroup containment is not a substitute for filesystem sandboxing.
9. Generic Provider health is not a Codex compatibility proof.
10. A production Codex Provider requires fresh full evidence bound to its current fingerprint.
11. Never switch Provider inside a started Codex task/worktree.
12. Unsupported or unverifiable tool/permission/Plugin states fail closed.
13. Runtime switching must kill old Codex trees before changing the active binary pin and must use the launch/switch fence.
14. Require FastAPI + Android unit + Android Debug APK on the final PR head and re-check merged `main`.

## Reference docs / 参考文档

- `docs/CODEX_ENGINE.md`
- `docs/CODEX_AGENT_TURN_ROUTING.md`
- `docs/CODEX_COMPATIBILITY.md`
- `docs/CODEX_INTERACTIONS.md`
- `docs/CODEX_MCP_ELICITATION.md`
- `docs/CODEX_REMOTE_MCP.md`
- `docs/CODEX_MULTIMODAL_INPUTS.md`
- `docs/CODEX_CAPABILITY_CONTROL.md`
- `docs/CODEX_SUBAGENT_GOVERNANCE.md`
- `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md`
- `docs/CODEX_PROVIDER_ROLLOUT.md`
