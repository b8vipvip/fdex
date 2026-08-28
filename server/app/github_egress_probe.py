from __future__ import annotations

import time
from typing import Any

import httpx

from app.github_app import GitHubAppClient


def probe_github_egress_network() -> dict[str, Any]:
    """Probe functional GitHub access through the current FDEX-only egress.

    A response merely proving that TCP/TLS reached GitHub is not enough. The website probe must
    return a normal 2xx/3xx response and the API probe must return HTTP 200. In particular 403
    rate-limit responses and 406 content-negotiation failures are reported as unhealthy instead
    of the previous false-positive "passed" state.
    """
    github = GitHubAppClient()
    targets = (
        (
            "github.com",
            "https://github.com/",
            {"Accept": "text/html,application/xhtml+xml", "User-Agent": "fdex-github-network-test"},
            lambda status: 200 <= status < 400,
        ),
        (
            "api.github.com",
            "https://api.github.com/meta",
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "fdex-github-network-test",
            },
            lambda status: status == 200,
        ),
    )
    result: dict[str, Any] = {
        "proxy_configured": bool(github.settings.fdex_github_http_proxy.strip()),
        "connect_timeout_seconds": github.settings.fdex_github_connect_timeout_seconds,
        "read_timeout_seconds": github.settings.fdex_github_read_timeout_seconds,
        "targets": [],
    }
    for label, url, headers, healthy in targets:
        request_headers = dict(headers)
        if label == "api.github.com" and github.settings.github_token.strip():
            request_headers["Authorization"] = f"Bearer {github.settings.github_token.strip()}"
        started = time.perf_counter()
        try:
            with github._client(follow_redirects=True) as client:
                response = client.get(url, headers=request_headers)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            ok = bool(healthy(response.status_code))
            error = ""
            if not ok:
                if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
                    reset = response.headers.get("x-ratelimit-reset", "")
                    error = "GitHub API rate limit exhausted" + (f"; reset={reset}" if reset else "")
                else:
                    error = f"unexpected HTTP {response.status_code}"
            result["targets"].append(
                {
                    "name": label,
                    "ok": ok,
                    "reachable": True,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "error": error,
                }
            )
        except httpx.HTTPError as exc:
            result["targets"].append(
                {
                    "name": label,
                    "ok": False,
                    "reachable": False,
                    "status_code": 0,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "error": type(exc).__name__,
                }
            )
    return result
