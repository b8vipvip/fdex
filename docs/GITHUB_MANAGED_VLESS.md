# FDEX managed GitHub VLESS egress

Phase 7.16 added the administrator control page at `/admin/github-egress`; Phase 7.17 extends it into a persistent multi-node VLESS proxy pool.

The goal remains narrow: let the FDEX Center use an operator-owned VLESS node for GitHub while every unrelated service on the same Linux host keeps its original network path.

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

That inbound has an automatically generated username/password. FDEX never renders those credentials in the admin UI or audit log.

The Xray routing table has no `freedom` fallback. It sends only these GitHub-owned domain families through the VLESS outbound:

- `github.com`
- `*.githubusercontent.com`
- `*.githubassets.com`

Every other request arriving at the private inbound goes to Xray `blackhole`.

This is application-level isolation. A process with Linux `root` authority is outside this threat boundary because root can inspect any local process or root-owned configuration.

## VLESS proxy pool

Phase 7.17 stores multiple VLESS nodes in:

```text
server/data/github-egress/vless-nodes.json
```

The file is atomically written with mode `0600`. The admin page renders only a node name and non-secret transport summary such as `host:443 · WS/TLS`; it never renders the stored full `vless://` URI or UUID.

The pool supports:

- add
- edit name
- replace the saved VLESS link without echoing the old link
- enable
- disable
- delete

FDEX uses **single-active-node semantics**. Multiple nodes may be saved, but only one node is enabled at a time. Enabling another node switches the FDEX GitHub path to that node and marks the previously active node disabled. It does not start a system-wide proxy and does not run several competing GitHub routes.

Disabling the active node stops the managed Xray and returns FDEX GitHub traffic to direct mode. The saved node remains in the list until explicitly deleted.

Existing Phase 7.16 deployments are migrated on first use: the old single `FDEX_GITHUB_VLESS_URI` is imported into the node pool without exposing the secret in HTML or audit output.

## Managed lifecycle

Supported `vless://` combinations are:

- security: `none`, `tls`, `reality`
- transport: TCP/raw, WebSocket, gRPC, HTTPUpgrade, XHTTP/SplitHTTP

Xray-core must already be installed. FDEX deliberately does not download or replace a networking binary from the web through the admin page.

The managed Xray process is launched as the transient systemd unit:

```text
fdex-github-egress.service
```

It is recreated by FDEX startup when managed VLESS mode is configured. The transient unit gets basic process hardening and a memory limit. Switching to direct or custom HTTP proxy mode stops the managed unit.

When an operator enables a VLESS node, FDEX validates the selected Xray binary and VLESS syntax before switching. The live environment update is treated transactionally: if the new Xray path cannot start, FDEX restores the previous persisted egress values instead of silently falling back through an unexpected route.

## Secret handling

The admin UI never echoes:

- the VLESS UUID/share link
- generated local proxy username/password
- the derived authenticated HTTP proxy URL

Admin audit records contain node IDs and non-secret operation/status fields, never the VLESS URI or generated proxy secret.

The active derived proxy credentials remain in `server/.env`, which is mode `0600`. Saved VLESS nodes live in the mode-`0600` pool file described above.

## GitHub traffic covered

The derived authenticated loopback proxy becomes `FDEX_GITHUB_HTTP_PROXY`, so the Phase 7.15 transport layer covers:

- GitHub App OAuth/API
- short-lived installation-token operations
- compatibility Device OAuth/PAT API calls
- Coding Agent `git clone`, `git fetch`, `git push`
- FDEX version/maintenance GitHub API reads

Git subprocess configuration is process-local and GitHub-scoped; it does not create a global Git proxy.

## Admin tests

`/admin/github-egress` provides a live functional test that:

1. requests `github.com` with normal website request headers and requires a 2xx/3xx response
2. requests `api.github.com/meta` and requires HTTP `200`
3. in managed mode, attempts to use the local Xray HTTP proxy without the private FDEX credentials and expects HTTP `407`

Phase 7.17 intentionally distinguishes **reachable** from **healthy**. A response such as HTTP `403 rate limit exceeded` proves that the route reached GitHub, but the GitHub API is not functionally usable and the overall test is therefore red. HTTP `406` is also not treated as success.

If `GITHUB_TOKEN` is configured, the API health probe and version-maintenance reads use it so they are not unnecessarily limited to GitHub's anonymous API quota.

## Typical flow

```text
saved VLESS node pool
          |
          | one active node
          v
FDEX GitHub HTTP/API + Coding Agent Git HTTPS
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
