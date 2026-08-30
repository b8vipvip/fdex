# FDEX Current Status / FDEX 当前状态

Last updated / 最后更新：2026-08-30

## Current baseline / 当前基线

- Default branch / 默认分支：`main`
- Accepted Codex Host baseline：**Phase 7.32 — Whole Codex process-tree isolation + Runtime lifecycle**
- Accepted `main` commit：`6c59b2b6c96132770589040c763c3ebd97793307`
- Phase 7.32 PR：**#94**
- Phase 7.32 final PR head：`7d06b17af7392a2bce8a3474fbba7070a19177cb`
- Phase 7.32 final PR CI：FastAPI + Android unit + Android Debug APK — success
- Phase 7.32 post-merge `main` run：`33306295953` — FastAPI + Android unit + Android Debug APK — success
- Active development：**Phase 7.33 — Codex Provider compatibility + rollout seal**
- Phase 7.33 branch：`agent/phase7-33-codex-provider-rollout-seal`
- Phase 7.33 PR：**#95**
- Current Android stable release：**v1.1.36**
- `FDEX_AGENT_ENGINE=legacy` remains the production default. Merging Phase 7.33 does not by itself prove that a deployed Provider is full-compatible.

A phase is accepted only from its final reviewed head. Do not reuse an older green CI run after the branch changes.

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
- Dynamic Tool remains fail-closed on bundled compatibility line.

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
- systemd `$` / `%` ExecStart escaping;
- fail-closed Linux/systemd/cgroup-v2 production preflight;
- official OpenAI Codex Release source/tag/asset/digest/size validation;
- staged SHA-256 download and safe tar extraction;
- candidate Runtime version + Phase 7.31 governance/app-server parsing validation;
- immutable managed Runtime installs and `/admin/agent/runtime` upgrade/rollback surface;
- stale Codex trees are stopped before Runtime pin changes;
- cross-worker Runtime launch/switch `flock` fence closes the old-runtime launch race;
- rollback fallback uses the same system-before-bundled resolution precedence as normal launch.

#### Plugin security conclusion

Executable Plugin mutation remains fail-closed. Bundled Codex 0.147 local stdio Plugin/MCP commands may run as local app-server children; cgroup v2 constrains resource/lifecycle but is not a filesystem confidentiality sandbox. Therefore `plugin/install`, `plugin/uninstall`, Marketplace mutation, remote-catalog install and `plugin/share/*` remain blocked. A separate filesystem/execution sandbox is required before reconsideration.

See `docs/CODEX_PROCESS_ISOLATION_RUNTIME.md` and `docs/CODEX_CAPABILITY_CONTROL.md`.

### Phase 7.33 — Codex Provider compatibility + rollout seal 🚧 PR #95

Current implementation branch: `agent/phase7-33-codex-provider-rollout-seal`.

Implemented so far:

- a separate `codex-provider-compatibility.db`; generic Provider health is no longer treated as Codex compatibility;
- fingerprint binding to Provider endpoint, API-key identity hash, model, protocol config, Runtime, Multi-Agent governance, cgroup resource settings and app version;
- default compatibility freshness of 168 hours;
- compatibility levels `none -> wire -> tools -> full`, with production selector requiring fresh `full`;
- real official `codex app-server` smoke in an isolated scratch workspace, never a user repository;
- wire evidence from real initialize/thread/turn completion;
- tool evidence from official `commandExecution/fileChange` plus verified scratch-file contents;
- loopback-only one-time MCP capability with server-side exact-marker call evidence plus official `mcpToolCall`;
- full evidence additionally requires official `reasoning` and `collabAgentToolCall(spawnAgent)`;
- Admin UI at `/admin/agent/codex-providers` with explicit upstream-cost warning and masked Provider metadata;
- production Codex selector skips stale/unverified Providers only before Host start and chooses the next fresh full-compatible Provider;
- no mid-task Provider switch after Codex Host/Turn starts;
- Retry remains the fresh task/worktree boundary where Provider selection may occur again;
- `auto` fallback to legacy remains pre-start only.

Important production truth:

GitHub CI does not hold the deployed Center's real Provider credentials and therefore cannot prove a production Provider is `full`. CI only verifies the smoke harness, evidence classifier and rollout safety semantics. After deployment, an administrator must run the real full smoke from `/admin/agent/codex-providers`. Without a matching fresh full record, Codex remains not ready for the rollout selector.

See `docs/CODEX_PROVIDER_ROLLOUT.md`.

## Remaining work / 尚未完成

### Phase 7.33 finalization

Before merge:

- finish FastAPI security/unit regressions;
- inspect first PR CI and fix real failures;
- final code/security review;
- newest PR head must pass FastAPI + Android unit + Android Debug APK;
- squash merge and re-check merged `main` CI.

After deployment:

- operator runs real full smoke against actual configured Provider(s);
- only actual fresh full records make Codex ready;
- changing default away from `legacy` remains an explicit operator rollout decision, not an automatic code-merge side effect.

### Executable Plugin filesystem sandbox — deferred security requirement

This remains a separate future requirement. Phase 7.33 does not weaken the Phase 7.32 Plugin write gate.

## Android-native parity

Phase 7.30–7.33 are server/Web/Codex Host changes and do not by themselves require a new Android stable release. Keep v1.1.36 unless Android source or a release-worthy Android behavior changes.

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
13. Require FastAPI + Android unit + Android Debug APK on the final PR head before merge.
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
