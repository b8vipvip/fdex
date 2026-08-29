# FDEX Current Status

Last updated: 2026-08-29

## Current baseline

- Default branch: `main`
- Current code baseline after this phase: **Phase 7.19 — Official Codex Core Foundation**
- Current Android stable release: **v1.1.36**
- v1.1.36 contains the Phase 7.13 Android migration to the universal `智体` product model.
- Phase 7.14 through Phase 7.19 are server/Web/infrastructure changes and do not require an Android release by themselves.
- Phase 7.19 deliberately keeps `FDEX_AGENT_ENGINE=legacy` as the initial default until the production Responses provider passes a real Codex smoke test.

The authoritative verification source for a concrete commit is the repository Build and Test workflow. Every merge must pass FastAPI Tests, Android unit tests and Android Debug APK before it becomes the accepted `main` baseline.

## Current product model

FDEX user-facing terminology is the universal **智体** model rather than the former company / industry / department / position / AI-employee model.

- A 智体 can represent any user-defined identity or role.
- Identity prompt may be empty at creation time.
- Name, identity prompt, knowledge permissions, chat permissions and Coding Agent capability can be configured independently.
- Historical `employee`, `employee_id`, legacy URLs and database fields may remain internally for compatibility, but they are not the current product model.

## Center account and isolation

FDEX Center `user_id` is the canonical owner scope.

Completed account features include registration, login, rotating refresh sessions, Android automatic refresh, password change, password reset by email code, device/session management, login rate limiting, security audit, data export, remote-memory erasure and permanent account deletion.

GitHub, Coding Agent, Web workspace, remote memory, tasks and sandboxes are isolated by the current FDEX `user_id`.

## GitHub App, Coding Agent and dedicated egress

The preferred GitHub architecture is **GitHub App Installation**.

Completed capabilities include:

- GitHub App Manifest bootstrap for administrators
- User-owned GitHub App Installation binding
- Installation ownership verification against FDEX `user_id`
- Automatic synchronization of repositories actually authorized by the Installation
- GitHub App Installation as the authority for repository scope and GitHub permissions
- Short-lived installation tokens per operation
- Repository/operation downscoping
- Owner-scoped Agent projects, tasks and sandboxes
- Durable tasks/events, cancellation and retry
- Sandbox disk budget and cleanup
- systemd transient-unit resource isolation for existing FDEX command/test tools
- Per-task worktree/branch isolation
- Push/PR support when allowed by effective GitHub App permission
- No direct Agent write to `main`
- Real GitHub tool retrieval for Coding-Agent-enabled Web 智体 chat
- Dedicated operator-controlled GitHub HTTP(S) egress via `FDEX_GITHUB_HTTP_PROXY`
- The same dedicated GitHub egress for GitHub REST/OAuth, version-maintenance reads and Coding Agent Git HTTPS `clone` / `fetch` / `push`
- Git proxy configuration injected only into the Git subprocess, without forcing AI providers, SMTP or unrelated FDEX traffic through that proxy
- Bounded retry for transient GitHub `clone` / `fetch` / `push` transport failures
- Safe cleanup of a controlled partial clone directory before clone retry
- Shared GitHub transport timeouts and actionable proxy/direct-path network errors
- Admin `/admin/github-egress` page for direct, custom HTTP(S) proxy and managed VLESS modes
- Multiple server-side VLESS proxy nodes with add/edit/enable/disable/delete controls
- Single-active-node switching: enabling one VLESS node automatically disables the other saved nodes
- Phase 7.16 single-node VLESS configuration migration into the proxy pool
- Managed `vless://` parsing for TLS/Reality and common Xray transports
- FDEX-managed Xray loopback HTTP inbound with generated authentication
- GitHub-domain allowlist plus blackhole fallback inside the managed Xray
- No system `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`, host routing, DNS, firewall or global Git changes
- Managed Xray startup restoration through the FDEX bootstrap path
- Live GitHub egress and local-proxy authentication isolation tests
- Functional egress health semantics: HTTP 403 rate limits and HTTP 406 are failures rather than false-positive success states
- Server `GITHUB_TOKEN` is used for the API health probe and maintenance reads when configured
- Version/maintenance GitHub API reads use the dedicated FDEX GitHub proxy and optional `GITHUB_TOKEN`

Earlier PAT / Device OAuth / per-project permission paths are compatibility layers rather than the recommended flow. Phase 7.15 routes those compatibility HTTP/API paths through the same dedicated GitHub transport policy so they no longer bypass operator egress settings.

For manual local HTTP/mixed Xray deployments see `docs/GITHUB_EGRESS.md`. For the managed VLESS proxy pool see `docs/GITHUB_MANAGED_VLESS.md`.

## Official OpenAI Codex engine foundation

Phase 7.19 begins replacing the custom `FdexAgentLoop` coding engine with the official OpenAI Codex execution core while retaining FDEX as the control plane.

Implemented foundation:

- Official `openai-codex==0.147.0` Python SDK and matching `openai-codex-cli-bin` runtime are pinned in server dependencies.
- FDEX can launch official `codex app-server` through the SDK rather than forking or copying the Codex Rust codebase.
- `FDEX_AGENT_ENGINE=legacy|codex|auto` provides a controlled rollout switch; the initial default remains `legacy`.
- Codex selects an enabled FDEX Provider whose protocol order includes `responses`, with API key and text model configured.
- Each Codex task uses the existing FDEX owner/project/task-isolated worktree and a task-specific `CODEX_HOME`.
- A trusted FDEX launcher wrapper strips unrelated server environment variables before exec'ing the official Codex runtime.
- GitHub App/OAuth/PAT, SMTP, Admin and unrelated Provider secrets are not inherited by the Codex runtime.
- The selected model Provider API key is available only to the Codex runtime; Codex shell commands use `shell_environment_policy.inherit=none` and do not inherit that key.
- Codex runs with workspace-write sandbox and `deny_all` approval policy during this foundation phase.
- FDEX project `allow_network` is explicitly mapped to Codex `sandbox_workspace_write.network_access`; default projects therefore keep shell network disabled.
- Codex Web Search is disabled in Phase 7.19 so model-side web access cannot bypass FDEX project-network policy.
- Codex never receives GitHub credentials and is instructed not to commit, push or create PRs itself.
- After a successful Codex turn, FDEX re-validates git changes and protected paths, then performs local commit / optional push / optional PR through the existing FDEX GitHub authority and project permissions.
- `.env` (except `.env.example`), `server/data` and `.git` internal paths are blocked before FDEX commit/push.
- Codex streaming lifecycle events and cancellation are bridged into the existing FDEX durable task/event system.
- Admin `/admin/agent` displays Codex SDK/runtime/provider/model readiness without exposing secrets and prevents strict `codex` selection when not ready.

Detailed architecture and rollout notes are in `docs/CODEX_ENGINE.md`.

Phase 7.19 is a foundation, not a claim of complete ChatGPT Codex parity. The official Codex process tree is not yet placed under the same FDEX systemd CPU/Memory/PID/concurrency envelope used by existing test/build tools, richer Item/Turn UI streaming and persistent thread continuation are not yet complete, and a production Responses-provider smoke test is still required before changing the default engine.

## Recent completed phases

- **Phase 7.1** — complete FDEX Center account lifecycle, session refresh, security and local migration.
- **Phase 7.2** — strict remote-memory erasure before account deletion.
- **Phase 7.3** — data export, memory-scope registry and serialized destructive account operations.
- **Phase 7.4** — persistent Agent tasks and sandbox lifecycle.
- **Phase 7.5** — account-scoped GitHub Device OAuth; later retained as compatibility after GitHub App migration.
- **Phase 7.6** — end-user Web GitHub authorization center.
- **Phase 7.7/7.8** — GitHub App Installation, Web registration/recovery, SMTP/IMAP.
- **Phase 7.9** — Web application parity and guided GitHub App onboarding.
- **GitHub App hotfixes** — Manifest audit/state fix and OAuth timeout/network diagnostics.
- **Phase 7.10** — GitHub App Installation becomes Coding Agent repository/permission authority.
- **Phase 7.11** — Web chat responsiveness and provider protocol routing.
- **Phase 7.12** — real owner-scoped GitHub tools in Coding-Agent-enabled chat.
- **Phase 7.13** — generalize old AI employees into universal 智体 across Android/Web/server while keeping data compatibility.
- **Phase 7.14** — synchronize server/Web routes and UI with the 智体 model and remove DOM-based legacy terminology rewriting.
- **Phase 7.15** — unify GitHub API/OAuth and Coding Agent Git HTTPS behind a dedicated operator-controlled GitHub egress, with scoped proxy injection, bounded network retry and deployment diagnostics/documentation for unreliable international routes.
- **Phase 7.16** — add a server-admin managed VLESS/Xray GitHub egress with loopback authentication, GitHub-only Xray routing, application-scoped lifecycle/test controls and maintenance-page proxy/token reuse.
- **Phase 7.17** — add a persistent multi-node VLESS proxy pool with single-active-node CRUD/switching, legacy single-node migration and strict functional GitHub health checks that reject 403/406 false positives.
- **Phase 7.18** — surface missing-Xray activation failures directly in the VLESS node area and prevent misleading enable actions when Xray is unavailable.
- **Phase 7.19** — integrate the official OpenAI Codex SDK/runtime as a selectable Coding Agent execution engine while preserving FDEX ownership, GitHub authority, worktree isolation, secret isolation and project network policy; keep legacy as the initial safe default pending production Responses smoke validation.

## What repository CI does not prove

Repository CI verifies source compatibility, server tests, dependency installation and Android builds. It does **not** prove that a production server has already deployed the latest `main`, that Xray/VLESS is currently reachable, that production GitHub App/SMTP/Provider configuration is correct, or that a production Responses-compatible Provider fully supports the Codex tool protocol. Phase 7.19 therefore requires a real production Codex smoke task before making Codex the default engine.

## Development rules going forward

1. Use `智体` for user-facing identities; do not reintroduce company/department/position semantics.
2. Use Center `user_id` as the owner scope for every user resource.
3. Treat GitHub App Installation as the repository-permission authority.
4. Keep GitHub installation tokens short-lived and downscoped.
5. Never allow Coding Agent or Codex to write directly to `main`.
6. Keep GitHub egress application-scoped. Do not modify system proxy variables, host default routes, DNS, firewall policy or global Git proxy as a side effect of FDEX GitHub configuration.
7. Managed Xray must bind loopback only, require generated authentication and blackhole non-GitHub destinations.
8. Stored VLESS nodes must never expose their full URI/UUID or generated local-proxy credentials in admin HTML or audit output.
9. Route AI inference through the shared FDEX Provider pool; Codex must use a configured Responses-capable Provider rather than a separate user API configuration.
10. Do not expose FDEX GitHub/SMTP/Admin secrets to the Codex process or model Provider key to Codex shell commands.
11. Keep FDEX project `allow_network` authoritative when constructing the Codex workspace sandbox; do not silently enable Web Search as a bypass.
12. Keep destructive account/data cleanup fail-closed.
13. Require FastAPI Tests, Android unit tests and Android Debug APK CI before merge.

## Historical progress file

`DEVELOPMENT_PROGRESS.md` contains older detailed development history and may describe an earlier baseline. For the current baseline, use this file and the latest merged PRs / Releases.
