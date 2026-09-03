# FDEX Current Status / FDEX 当前状态

Last updated / 最后更新：2026-09-03

## Current baseline / 当前基线

- Default branch / 默认分支：`main`
- Current accepted Coding Agent architecture：**Phase 7.40 — Atomic Task Kind / Retry Lineage**
- Accepted `main`：`6b5bd8fec5e4f0f3b6882f0c3e821f03b82b835b`
- Phase 7.37 PR：**#104 — Add Codex Agent health monitor and live admin status**
- Phase 7.37 final head：`cd99f99891393c7f5ec3662712f2c2373640545c`
- Phase 7.37 merge：`1cbf145c84bb345615f37434bea79b79fe65e2d6`
- Phase 7.37 PR CI：`33713572859` (`Build and Test #1504`) — success
- Phase 7.37 post-merge CI：`33713741855` (`Build and Test #1505`) — success
- Phase 7.38 PR：**#105 — Add structured bounded retry for Codex tasks**
- Phase 7.38 final head：`0506b195343570c87ddb140efc6bd442508b3b6e`
- Phase 7.38 merge：`6eeb880aa5a3cc5836be128a720081020243c0dd`
- Phase 7.38 final PR CI：`33716497231` (`Build and Test #1507`) — FastAPI + Android unit + Android Debug APK — success
- Phase 7.38 post-merge CI：`33733769670` (`Build and Test #1508`) — FastAPI + Android unit + Android Debug APK — success
- Phase 7.39 PR：**#106 — Project bounded Codex retries as one logical task**
- Phase 7.39 final head：`4b8c63052f5fe03ae3c1f5eb489515ab29692e0e`
- Phase 7.39 merge：`54e6f56af95485e4d61aee241caad689eba69aa0`
- Phase 7.39 final PR CI：`33735664635` (`Build and Test #1510`) — FastAPI + Android unit + Android Debug APK — success
- Phase 7.39 post-merge CI：`33735906178` (`Build and Test #1511`) — FastAPI + Android unit + Android Debug APK — success
- Phase 7.40 PR：**#107 — Make Agent task kind and retry lineage atomic**
- Phase 7.40 final head：`cce70b803246904976a574ba22aab0608ace26e0`
- Phase 7.40 merge：`6b5bd8fec5e4f0f3b6882f0c3e821f03b82b835b`
- Phase 7.40 final PR CI：`33748131003` (`Build and Test #1513`) — FastAPI + Android unit + Android Debug APK — success
- Phase 7.40 post-merge CI：`33748381161` (`Build and Test #1514`) — FastAPI + Android unit + Android Debug APK — success
- Current development candidate：**Phase 7.41 — Durable Retry Chain Reconciler** on `feature/codex-retry-chain-reconciler-20260903`; it is **not accepted until its final PR head and merged-main CI both pass**.
- Current Android stable release：**v1.1.36**

A phase is accepted only from its final reviewed head and the merged `main` check. Do not reuse an older green run after a branch changes.

## Product and authority model / 产品与权限模型

FDEX Center `user_id` is the canonical owner scope. User-facing identities use the universal **智体** model. GitHub App Installation remains repository authority. Codex never receives GitHub Installation tokens and never owns push/PR authority; FDEX validates the worktree and owns commit/push/PR after a successful Codex turn.

Execution boundary:

```text
employee.coding_agent == true
    -> FDEX owner / project / isolated worktree
    -> Phase 7.33 fresh-full Provider readiness gate
    -> official codex app-server Thread / Turn
    -> Phase 7.37 structured health evidence
    -> on structured transient failure only:
         Phase 7.38 NEW retry AgentTask + NEW worktree + NEW Host boundary
         at most 2 automatic retries
    -> Phase 7.39 logical root + physical attempt projection
    -> Phase 7.40 atomic task-kind / logical-root lineage
    -> Phase 7.41 candidate: reclaim only durable queued auto-retry orphans after root lease loss
    -> FDEX validates and publishes resulting Git state

employee.coding_agent == false
    -> ordinary FDEX client_ai conversation path
```

There is no Legacy/Auto Agent execution mode and no fallback from a Coding-Agent-enabled 智体 to ordinary `client_ai`.

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

- every real FDEX-provider Host runs in a transient systemd service;
- cgroup v2 Memory/CPU/PID limits cover app-server and all descendants;
- `KillMode=control-group`, graceful stop and verified all-process SIGKILL fallback;
- Provider secret values stay out of systemd-run argv;
- fail-closed Linux/systemd/cgroup-v2 production preflight;
- official OpenAI Codex Release validation, immutable managed Runtime installs and safe rollback;
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
- stale/unverified Providers may be skipped only **before a user Host starts**;
- FDEX **never switches Providers inside** a started Codex Host/Turn/task/worktree;
- Retry may re-run Provider selection only at a fresh task/worktree/Host boundary.

### Phase 7.35 — Agent-first Web Coding Agent routing ✅

Accepted in PR #101 at `main@1f3036ab90f65fdee154fc114b9261c025749378`.

- removed the pre-Codex natural-language capability classifier;
- every `coding_agent=true` employee message enters Agent Runtime;
- project selection exists only to resolve the authorized Codex `cwd`;
- same-chat/same-project turns resume a durable official Codex Thread when a real binding exists;
- Web image/audio attachments map into official task-scoped Codex `UserInput[]`;
- unsupported attachment types fail closed instead of falling back to generic AI.

### Phase 7.36 — Codex-only Agent Core ✅

Accepted in PR #102 at `main@6a63b64e383421fbb95c5beb0a633d30d9a7c48b`, with final cleanup in PR #103 at `main@9ff2f19f618d1196aaa434ef96315e769d00d219`.

- removed the legacy FDEX model JSON-tool execution loop;
- removed `legacy|auto|codex` runtime selection and `FDEX_AGENT_ENGINE`;
- removed the dead `normalize_engine_mode()` and old model-loop tuning settings;
- `FdexAgentLoop` is a Codex-only stable call-site facade;
- Codex readiness failure terminalizes the task;
- Coding Agent never falls back to ordinary `client_ai`.

### Phase 7.37 — Codex Agent Health Monitor ✅

Accepted in PR #104 at `main@1cbf145c84bb345615f37434bea79b79fe65e2d6`.

Structured health chain:

- official Runtime resolve/version;
- Phase 7.32 process isolation availability;
- native app-server initialize/initialized handshake;
- lightweight Provider DNS/TLS/HTTP/rate-limit/5xx reachability;
- Phase 7.33 full compatibility/fingerprint/freshness;
- actual rollout-selected Provider;
- durable `READY / DEGRADED / BLOCKED / DISABLED / UNKNOWN` state and machine-readable codes.

The monitor does not unlock Providers and does not alter Agent routing. `/models` 401/403 remains advisory rather than overriding an otherwise fresh full proof. Structured health codes, not human error strings, are the recovery-policy input.

### Phase 7.38 — Codex Bounded Retry Controller ✅

Accepted in PR #105 at `main@6eeb880aa5a3cc5836be128a720081020243c0dd`.

- original attempt + at most 2 automatic retry children;
- default backoff 2s then 8s;
- each retry is a new AgentTask, worktree and Codex Host boundary;
- automatic retry only for structured `PROVIDER_RATE_LIMITED`, `PROVIDER_UNREACHABLE` and `HOST_UNAVAILABLE` evidence;
- Runtime/process-isolation/config/smoke/fingerprint/compatibility errors remain fail-closed;
- human `task.error` text, including a literal `429` or `timeout`, never decides retry eligibility;
- no replay after changed-files/commit/push/PR side-effect boundary;
- failed Provider can be task-locally excluded only when a different Provider is both live-healthy and fresh-full compatible;
- Provider identity is immutable inside a started Host/Turn;
- recovery context may fork only from a proven `last_completed_turn_id`; an incomplete failed Turn is not a checkpoint;
- root cancellation propagates to an active retry child and stops queued retry after backoff;
- root remains the user-facing logical task; child attempts remain durable audit tasks.

### Phase 7.39 — Retry Chain Observability / Logical Task Projection ✅

Accepted in PR #106 at `main@54e6f56af95485e4d61aee241caad689eba69aa0`.

- structured `codex_retry_attempts` ledger records physical attempt/provider/health/backoff/decision metadata;
- normal task history hides Phase 7.38 internal automatic retry attempts while keeping them durable for accounting/cleanup/audit;
- logical root detail displays the complete recovery chain;
- Codex Thread/Turn, Item/SSE, approval/requestUserInput, Steer and Compact follow the active/latest execution attempt;
- direct internal child Web detail redirects to logical root;
- authenticated Agent API exposes an owner-scoped explicit retry-chain projection without changing the old task-list response shape;
- retry child remains a real AgentTask/worktree/run-lock owner rather than a synthetic UI row;
- no execution, retry, Provider or GitHub authority boundary changes.

### Phase 7.40 — Atomic Task Kind / Retry Lineage ✅

Accepted in PR #107 at `main@6b5bd8fec5e4f0f3b6882f0c3e821f03b82b835b`.

- `agent_tasks` owns immutable `task_kind / logical_root_id / attempt_index` identity;
- legal task kinds are `user / auto_retry / manual_retry / resume / fork`;
- only `auto_retry` is hidden from ordinary history; manual Retry, Resume and Fork remain visible new logical tasks;
- auto-retry kind/root/attempt are present before the first durable `task.created` write;
- Phase 7.39 retry ledger remains Provider/health/backoff/decision audit rather than task-identity authority;
- Phase 7.39 databases migrate existing retry children from structured ledger evidence, never from human error/event text;
- a worker crash after child creation but before retry-ledger insertion cannot expose the child as a normal task;
- retry-chain projection can recover a main-table-only attempt as bounded `audit_pending` evidence;
- retry eligibility, Provider switching rules, side-effect boundary and Codex-only execution remain unchanged.

### Phase 7.41 — Durable Retry Chain Reconciler 🚧

Current development candidate on `feature/codex-retry-chain-reconciler-20260903`. Acceptance is pending final PR-head and merged-main CI.

Target behavior:

- background reconciler scans only durable `task_kind=auto_retry` tasks in `queued/running` state;
- real recovery requires acquiring the logical-root crash-safe `flock`, so multi-worker scans cannot duplicate execution;
- only a still-queued child with matching Phase 7.40 lineage + Phase 7.39 audit, no Provider/Host start evidence, no durable Turn and elapsed original backoff may be replayed;
- recovered child re-enters the existing Phase 7.38 `_drive_chain()` rather than creating a second retry engine;
- `running`, Provider-bound or Host-started attempts fail closed as `ATTEMPT_ALREADY_STARTED`;
- attempts with a durable Turn fail closed as `SIDE_EFFECT_UNKNOWN`;
- lineage-without-audit fails closed as `RECOVERY_METADATA_MISSING` instead of guessing Provider exclusion/backoff intent;
- terminal/canceled logical roots never execute leftover retry children;
- ordinary `user / manual_retry / resume / fork` tasks are never automatically replayed;
- `FdexAgentLoop.run()` rejects internal `auto_retry` tasks so an internal child cannot be detached from its logical-root lease;
- no change to Codex-only execution, retry budget, Provider switching boundary or GitHub authority.

## Production rollout / 生产部署

For the accepted Phase 7.40 baseline:

1. deploy `main@6b5bd8fec5e4f0f3b6882f0c3e821f03b82b835b` or a later accepted main;
2. remove obsolete `FDEX_AGENT_ENGINE`, `FDEX_AGENT_MAX_STEPS`, `FDEX_AGENT_MODEL_MAX_TOKENS` env entries if still present;
3. verify Phase 7.32 Runtime/process isolation health;
4. open `/admin/agent/codex-providers` and refresh real `full` smoke for intended Providers;
5. verify at least one Provider has a fresh `full` proof bound to the current fingerprint;
6. verify `/admin/agent` Phase 7.37 health chain is not hard-blocked;
7. enable Coding Agent;
8. treat readiness/fingerprint/smoke failures as configuration/compatibility failures — there is intentionally no legacy/generic fallback;
9. re-run full smoke after Provider key/model/endpoint, Runtime, governance, resource-limit or app-version changes.

GitHub CI is not a substitute for deployed Provider full-smoke because CI does not hold the real Center Provider credentials.

## Android-native parity

Phase 7.37–7.41 are server/Web/Codex Host architecture work and do not themselves require a new Android stable release. Keep **v1.1.36** unless Android source or release-worthy Android behavior changes. Every server architecture PR still must pass Android unit tests and Debug APK build to protect shared API/runtime compatibility.

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
11. Never switch Provider inside a started Codex Host/Turn/task/worktree.
12. Automatic recovery must use structured health evidence, a bounded budget and a new task/worktree/Host boundary.
13. Internal retry identity must come from durable AgentTask lineage, never error/event text.
14. Crash recovery must own the logical-root execution lease and must never replay a Provider/Host-started or side-effect-unknown attempt.
15. Unsupported or unverifiable tool/permission/Plugin states fail closed.
16. Runtime switching must kill old Codex trees before changing the active binary pin and must use the launch/switch fence.
17. Require FastAPI + Android unit + Android Debug APK on the final PR head and re-check merged `main`.

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
- `docs/CODEX_BOUNDED_RETRY.md`
- `docs/CODEX_RETRY_OBSERVABILITY.md`
- `docs/CODEX_TASK_LINEAGE.md`
- `docs/CODEX_RETRY_RECONCILIATION.md`