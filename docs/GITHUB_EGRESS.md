# GitHub dedicated egress for FDEX

Phase 7.15 makes `FDEX_GITHUB_HTTP_PROXY` the single operator-controlled GitHub egress setting for both:

- GitHub HTTP/API traffic (`github.com` and `api.github.com`)
- Coding Agent Git HTTPS traffic (`clone`, `fetch`, `push`)

It does **not** set process-wide `HTTP_PROXY` / `HTTPS_PROXY`, so AI providers, SMTP and unrelated outbound traffic are not sent through the GitHub proxy.

## Recommended China-mainland deployment

For a China-mainland FDEX Center with an operator-owned overseas Xray/VLESS node:

```text
FDEX Center
   |
   | HTTP CONNECT to 127.0.0.1:10808
   v
local Xray client
   |
   | VLESS / TLS
   v
operator-owned overseas VPS
   |
   v
GitHub
```

The local Xray client should expose an HTTP or mixed inbound on loopback only, for example `127.0.0.1:10808`.

Do not expose that local proxy port publicly.

## FDEX configuration

Example `server/.env`:

```dotenv
FDEX_GITHUB_HTTP_PROXY=http://127.0.0.1:10808
FDEX_GITHUB_CONNECT_TIMEOUT_SECONDS=15
FDEX_GITHUB_READ_TIMEOUT_SECONDS=90
FDEX_GITHUB_RETRY_ATTEMPTS=3
```

Only `http://` and `https://` proxy URLs are accepted by FDEX. If an Xray client exposes SOCKS only, add an HTTP/mixed inbound and point FDEX at that HTTP endpoint.

The proxy URI may include credentials, but FDEX does not render the configured secret back into the admin UI or audit log.

## What Phase 7.15 changes

### GitHub API

The GitHub App path already used the dedicated proxy. Phase 7.15 also routes the legacy Device OAuth / PAT compatibility path through the same transport, timeout and retry policy.

Safe reads may be retried. Single-use OAuth exchanges and non-idempotent API writes are not blindly replayed.

### Git clone / fetch / push

Git receives process-local configuration equivalent to:

```text
http.https://github.com.proxy=<FDEX_GITHUB_HTTP_PROXY>
```

This is injected only into the Git subprocess. It is not written to the user's repository config and not written to global Git config.

Slow GitHub transfers use the configured GitHub read timeout as a low-speed threshold.

Transient network failures on `clone`, `fetch` and `push` are retried up to `FDEX_GITHUB_RETRY_ATTEMPTS`. A failed clone removes only its controlled partial repository directory before retrying.

Authentication remains unchanged: GitHub App installation tokens stay short-lived and user/repository scoped. The network proxy is only an egress path and never becomes an authorization boundary.

## Validation

First verify the local proxy itself:

```bash
curl -x http://127.0.0.1:10808 -I https://api.github.com
curl -x http://127.0.0.1:10808 https://api.github.com/meta
```

Then use the FDEX admin GitHub network test. It probes both `github.com` and `api.github.com` without user credentials and reports latency/status.

For Coding Agent validation, run a repository task that requires:

1. clone or fetch
2. tests
3. commit
4. push to an `fdex-agent/*` branch
5. pull request creation

A GitHub outage or proxy failure should now report a GitHub-specific transport error instead of an opaque generic timeout.

## Security rules

- Use only an operator-controlled or explicitly trusted proxy.
- Do not send GitHub App installation tokens or OAuth credentials through public GitHub mirrors.
- Keep the local Xray HTTP/mixed inbound bound to `127.0.0.1`.
- Do not replace FDEX per-user GitHub App authorization with a shared server SSH key.
- Keep the GitHub proxy separate from general FDEX outbound traffic unless there is an explicit infrastructure reason to route everything through the same gateway.
