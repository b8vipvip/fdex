# FDEX managed GitHub VLESS egress

Phase 7.16 adds an administrator control page at `/admin/github-egress`.

The goal is narrow: let the FDEX Center use an operator-owned VLESS node for GitHub while every
unrelated service on the same Linux host keeps its original network path.

## Isolation model

Managed mode does **not**:

- export `HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY`
- change the host default route
- change DNS
- add iptables/nftables policy-routing rules
- write a global Git proxy
- inject proxy variables into AI Provider, SMTP, Letta, Qdrant or other services

Instead FDEX generates a private Xray config under:

```text
server/data/github-egress/xray.json
```

The directory is mode `0700` and the generated config is mode `0600`.

Xray exposes one HTTP proxy inbound:

```text
127.0.0.1:<FDEX managed port>
```

That inbound has an automatically generated username/password. FDEX never renders those
credentials in the admin UI or audit log.

The Xray routing table has no `freedom` fallback. It sends only these GitHub-owned domain families
through the VLESS outbound:

- `github.com`
- `*.githubusercontent.com`
- `*.githubassets.com`

Every other request arriving at the private inbound goes to Xray `blackhole`.

This is application-level isolation. A process with Linux `root` authority is outside this threat
boundary because root can inspect any local process or root-owned configuration.

## Managed lifecycle

The admin page accepts a `vless://` share link and generates Xray JSON. Supported combinations are:

- security: `none`, `tls`, `reality`
- transport: TCP/raw, WebSocket, gRPC, HTTPUpgrade, XHTTP/SplitHTTP

Xray-core must already be installed. FDEX deliberately does not download or replace a networking
binary from the web through the admin page.

The managed Xray process is launched as the transient systemd unit:

```text
fdex-github-egress.service
```

It is recreated by FDEX startup when managed VLESS mode is configured. The transient unit gets
basic process hardening and a memory limit. Switching to direct or custom HTTP proxy mode stops the
managed unit.

If the managed unit cannot start, FDEX does not silently fall back to direct GitHub access. The
saved dedicated proxy remains the selected path so GitHub operations fail closed until the operator
repairs Xray/VLESS or explicitly selects direct mode.

## Secret handling

The admin form never echoes:

- the VLESS UUID/share link
- generated local proxy username/password
- the derived authenticated HTTP proxy URL

Admin audit records contain only mode, booleans, local port, timeout/retry settings and sanitized
errors.

The VLESS link and generated local proxy credentials live in `server/.env`, which FDEX already
protects as a mode-`0600` secret file and backs up using the same mode-`0600` mechanism used for
other server secrets.

## GitHub traffic covered

The derived authenticated loopback proxy becomes `FDEX_GITHUB_HTTP_PROXY`, so the existing Phase
7.15 transport layer automatically covers:

- GitHub App OAuth/API
- short-lived installation-token operations
- compatibility Device OAuth/PAT API calls
- Coding Agent `git clone`, `git fetch`, `git push`
- FDEX version/maintenance GitHub API reads

Git subprocess configuration is still process-local. Phase 7.16 additionally scopes the proxy to
GitHub-owned HTTPS host families used by Git redirects/assets; it does not create a global Git
proxy.

## Admin tests

`/admin/github-egress` provides a live test that:

1. requests `github.com`
2. requests `api.github.com`
3. in managed mode, attempts to use the local Xray HTTP proxy without the private FDEX credentials
   and expects HTTP `407`

The third check verifies that another ordinary local process cannot use the proxy merely because it
knows the loopback port.

A `403 rate limit exceeded` result on the version-maintenance page is a GitHub API quota condition,
not a VLESS transport failure. The maintenance client now uses the same dedicated GitHub proxy and
uses `GITHUB_TOKEN` when configured to obtain the authenticated GitHub API quota.

## Typical flow

```text
FDEX GitHub HTTP/API
          |
Coding Agent Git HTTPS
          |
          v
authenticated 127.0.0.1 HTTP proxy
          |
          v
FDEX-managed Xray
          |
          | VLESS/TLS or VLESS/Reality
          v
operator-owned overseas VPS
          |
          v
GitHub

AI / SMTP / Letta / Qdrant / other host services
          |
          +------------------------------> original host network
```
