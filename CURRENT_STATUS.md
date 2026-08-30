# FDEX Current Status / FDEX 当前状态

Last updated / 最后更新：2026-08-30

## Current baseline / 当前基线

- Default branch / 默认分支：`main`
- Accepted Codex Host baseline：**Phase 7.33 — Codex Provider compatibility + rollout seal**
- Accepted Phase 7.33 code commit：`080c4ba962ce72cd43f0ee0802aef8050b290748`
- Phase 7.33 PR：**#95**
- Phase 7.33 final PR head：`94da1865f29caf043f756348a265a1a9378036a8`
- Final PR CI：FastAPI **437 passed / 2 pre-existing skipped** + Android unit + Android Debug APK — success
- Post-merge `main` run：`33307653585` — FastAPI + Android unit + Android Debug APK — success
- Previous accepted Phase 7.32 commit：`6c59b2b6c96132770589040c763c3ebd97793307`
- Current Android stable release：**v1.1.36**
- Production default：`FDEX_AGENT_ENGINE=legacy`

Phase 7.33 code is accepted, but **a code merge is not a production Provider compatibility proof**. GitHub CI has no deployed Center Provider credentials. A deployed Provider becomes Codex-eligible only after the actual Center produces a fresh `full` smoke record from `/admin/agent/codex-providers`.

A phase is accepted only from its final reviewed head and the merged `main` check. Do not reuse older green CI after a branch changes.

## Product and authority model / 产品与权限模型

FDEX Center `user_id` is the canonical owner scope. User-facing identities use the universal **智体** model. GitHub App Installation remains repository authority. Codex never receives GitHub Installation tokens and never owns push/PR authority; FDEX validates the worktree and owns commit/push/PR after a successful Codex turn.

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

Official `mcpServer/elicitation/request` support is complete with constrained standard modes and fail-closed unsupported modes.

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
- bundled `openai-codex-cli-bin==0.147.0` parses real governance config in CI;
- Phase 7.22 event persistence automatically covers sub-agent activity.

See `docs/CODEX_SUBAGENT_GOVERNANCE.md`.

### Phase 7.32 — Whole Codex process-tree isolation + Runtime lifecycle ✅

Accepted in PR #94 at `main@6c59b2b6c96132770589040c763c3ebd97793307`.

Completed:

- every real FDEX-provider Host runs in a transient systemd service;
- cgroup v2 Memory/CPU/PID limits cover app-server and all descendants;
- `KillMode=control-group`, graceful stop and verified all-process SIGKILL fallback;
- `BindsTo=<FDEX service>` lifecycle ownership;
- Provider secret values stay out of systemd-run argv;
- systemd `$` / `%` ExecStart escaping is enforced for transient service launch arguments;
- fail-closed Linux/systemd/cgroup-v2 production preflight;
- official OpenAI Codex Release source/tag/asset/digest/size validation;
- staged SHA-256 download and safe tar extraction;
- candidate Runtime version + Phase 7.31 governance/app-server parsing validation;
- immutable managed Runtime installs and `/admin/agent/runtime` upgrade/rollback surface;
- stale Codex trees stop before Runtime pin changes;
- cross-worker Runtime launch/switch `flock` fence closes the old-runtime launch race;
- rollback fallback validation uses the same runtime resolution precedence as launch: configured pin when present, otherwise system Codex before bundled fallback.

#### Plugin security conclusion

Executable Plugin mutation remains fail-closed. Bundled Codex 0.147 local stdio Plugin/MCP commands may run as local app-server children; cgroup v2 constrains resource/lifecycle but is not a filesystem confidentiality sandbox. `plugin/install`, `plugin/uninstall`, Marketplace mutation, remote-catalog install and `plugin/share/*` therefore remain blocked. A separate filesystem/execution sandbox is required before reconsideration.

See `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md` and `docs/CODEX_CAPABILITY_CONTROL.md`.

### Phase 7.33 — Codex Provider compatibility + rollout seal ✅

Accepted in PR #95 at `main@080c4ba962ce72cd43f0ee0802aef8050b290748`.

Completed:

- Codex compatibility is stored separately from generic Provider health in `codex-provider-compatibility.db`;
- levels are `none -> wire -> tools -> full`; production selection requires a fresh `full` record, default freshness 168 hours;
- fingerprint v2 binds Provider ID/Base URL, API-key identity hash, **complete effective text-model candidate order including backup-only configurations**, protocol order, timeout, Runtime path/version/source, Phase 7.31 governance, Phase 7.32 resource limits and FDEX app version;
- API Key plaintext never enters the compatibility ledger;
- any bound configuration drift invalidates the previous proof and requires a new smoke;
- real smoke uses official `codex app-server` in an isolated scratch workspace, never a user repository;
- `wire` requires a real initialize/thread/turn completion path;
- `tools` requires official `commandExecution`/`fileChange` plus a verified scratch-file side effect;
- MCP proof requires both official `mcpToolCall` and a real call reaching the short-lived FDEX loopback capability with the exact random marker;
- smoke MCP reuses the hardened direct-loopback check and rejects proxy-marker headers, closing same-host reverse-proxy loopback confusion;
- `full` requires official `reasoning`, completed `collabAgentToolCall(spawnAgent)`, completed `collabAgentToolCall(wait)`, and official `subAgentActivity` evidence;
- Phase 7.32 process isolation must be enforced before a smoke can create rollout evidence;
- `/admin/agent/codex-providers` exposes compatibility state and explicit-cost real smoke operation;
- admin engine switching, Runtime status, capability-control Hosts and user task Hosts all use the same fresh-full rollout gate;
- stale/unverified Providers may be skipped only **before** a Codex Host starts;
- there is no Provider switch inside a started task/worktree;
- Retry creates the fresh task/worktree boundary where Provider selection may run again;
- `auto` may fall back to legacy only before Codex starts, never after a started Codex task fails;
- final security regressions execute under the repository's actual pytest setup rather than being silently skipped.

See `docs/CODEX_PROVIDER_ROLLOUT.md`.

## Operational rollout still required / 仍需生产运维验证

The code milestone is complete. The remaining action is deployment-specific rather than unfinished repository implementation:

1. deploy accepted `main` to the real FDEX Center;
2. keep `FDEX_AGENT_ENGINE=legacy` initially;
3. open `/admin/agent/codex-providers`;
4. run real full smoke against each Provider intended for Codex production use;
5. verify the desired Provider shows a fresh `full` result bound to the current fingerprint;
6. only then make an explicit operator decision whether to change the engine rollout mode;
7. re-run full smoke after Provider key/model/endpoint, Runtime, governance, resource-limit or app-version changes.

Do not treat GitHub CI as a substitute for this deployed-provider test.

### Executable Plugin filesystem sandbox — deferred security requirement

This remains a separate future requirement. Phase 7.33 does not weaken the Phase 7.32 Plugin write gate.

## Android-native parity

Phase 7.30–7.33 are server/Web/Codex Host changes and do not by themselves require a new Android stable release. Keep **v1.1.36** unless Android source or a release-worthy Android behavior changes.

## Development rules / 后续规则

1. Use Center `user_id` for every user-owned resource.
2. Keep GitHub App Installation authority outside Codex.
3. Never allow Codex to write directly to `main`.
4. Use native App Server Protocol as the compatibility ABI.
5. Human approval never overrides FDEX filesystem/network/GitHub policy.
6. Remote MCP destinations and long-lived credentials remain behind FDEX enforcement.
7. Cgroup containment is not a substitute for filesystem sandboxing.
8. Generic Provider health is not a Codex compatibility proof.
9. A production Codex Provider requires fresh full evidence bound to its current fingerprint.
10. Never switch Provider inside a started Codex task/worktree.
11. Unsupported or unverifiable tool/permission/Plugin states fail closed.
12. Runtime switching must kill old Codex trees before changing the active binary pin and must use the launch/switch fence.
13. Require FastAPI + Android unit + Android Debug APK on the final PR head and re-check merged `main`.
14. Do not automatically change the production Agent default merely because Phase 7.33 code is merged.

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
- `docs/CODEX_PROVIDER_ROLLOUT.md`
