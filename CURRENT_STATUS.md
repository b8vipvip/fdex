# FDEX Current Status

Last verified: 2026-08-28

## Current baseline

- Default branch: `main`
- Current code baseline: **Phase 7.14**
- Verified main commit: `ded5a49fdbd714719446db1e572089fcb52e2a5e`
- Current Android stable release: **v1.1.36**
- v1.1.36 contains the Phase 7.13 Android migration to the universal `智体` product model.
- Phase 7.14 is a server/Web synchronization change, so the Android Auto Release workflow skipped it by design.

## Repository health

At the time of this audit:

- Open pull requests: **0**
- Open issues: **0**
- Phase 7.14 PR Build and Test: **success**
- Phase 7.14 main push Build and Test: **success**
- FastAPI Tests: **success**
- Android unit tests: **success**
- Android Debug APK: **success**
- Explicit `TODO` search: no result
- Explicit `FIXME` search: no result
- Explicit `Phase 7.15` unfinished marker: no result

## Current product model

FDEX user-facing terminology is now the universal **智体** model rather than the former company / industry / department / position / AI-employee model.

- A 智体 can represent any user-defined identity or role.
- Identity prompt may be empty at creation time.
- Name, identity prompt, knowledge permissions, chat permissions and Coding Agent capability can be configured independently.
- Historical `employee`, `employee_id`, legacy URLs and database fields may remain internally for compatibility, but they are not the current product model.

## Center account and isolation

FDEX Center `user_id` is the canonical owner scope.

Completed account features include registration, login, rotating refresh sessions, Android automatic refresh, password change, password reset by email code, device/session management, login rate limiting, security audit, data export, remote-memory erasure and permanent account deletion.

GitHub, Coding Agent, Web workspace, remote memory, tasks and sandboxes are isolated by the current FDEX `user_id`.

## GitHub App and Coding Agent

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
- systemd transient-unit resource isolation
- Per-task worktree/branch isolation
- Push/PR support when allowed by effective GitHub App permission
- No direct Agent write to `main`
- Real GitHub tool retrieval for Coding-Agent-enabled Web 智体 chat

Earlier PAT / Device OAuth / per-project permission paths are compatibility layers rather than the recommended flow.

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

## What is not proven by repository CI

This audit verifies repository state, merged code and GitHub Actions. It does **not** prove that a production server has already deployed the latest `main`, or that production GitHub App, SMTP, Provider, proxy and other external configuration is correct. Production deployment/configuration requires runtime inspection of the actual FDEX server.

## Development rules going forward

1. Use `智体` for user-facing identities; do not reintroduce company/department/position semantics.
2. Use Center `user_id` as the owner scope for every user resource.
3. Treat GitHub App Installation as the repository-permission authority.
4. Keep GitHub installation tokens short-lived and downscoped.
5. Never allow Coding Agent to write directly to `main`.
6. Route AI inference through the shared FDEX Provider pool.
7. Keep destructive account/data cleanup fail-closed.
8. Require FastAPI Tests, Android unit tests and Android Debug APK CI before merge.

## Historical progress file

`DEVELOPMENT_PROGRESS.md` contains older detailed development history and may describe an earlier baseline. For the current baseline, use this file and the latest merged PRs / Releases.
