from __future__ import annotations

import secrets


def agent_token_valid(configured: str, provided: str) -> bool:
    expected = (configured or "").strip()
    actual = (provided or "").strip()
    return bool(expected and actual and secrets.compare_digest(expected, actual))
