from __future__ import annotations

from app.account_data_export import build_account_export


def test_account_export_source_declares_secret_exclusions() -> None:
    # Keep the privacy contract explicit even if export sections evolve. This lightweight
    # regression catches accidental removal of the redaction list from the portable format.
    import inspect

    source = inspect.getsource(build_account_export)
    for secret in (
        "password_hash",
        "access_token",
        "refresh_token",
        "github_token_cipher",
        "provider_api_keys",
        "embeddings",
    ):
        assert secret in source
