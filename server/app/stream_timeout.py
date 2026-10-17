from __future__ import annotations

import httpx


def upstream_stream_timeout(
    provider_timeout_seconds: float,
    *,
    read_timeout_seconds: float,
) -> httpx.Timeout:
    """Keep connection/write failures bounded without timing out long AI reasoning.

    The provider's timeout controls establishing the connection, sending the
    request, and pool acquisition. For SSE, only *inactivity between bytes* is
    governed by the separate, much longer streaming read timeout; it is never
    a fixed total request duration. Set read_timeout_seconds=0 to allow a
    silent upstream indefinitely until client cancellation or transport close.
    """
    provider_timeout = max(5.0, float(provider_timeout_seconds))
    read_timeout = float(read_timeout_seconds)
    if read_timeout < 0:
        raise ValueError("Streaming read timeout must be nonnegative")
    return httpx.Timeout(
        connect=min(15.0, provider_timeout),
        write=provider_timeout,
        pool=provider_timeout,
        read=None if read_timeout == 0 else read_timeout,
    )
