from app.account_operations import AccountOperationStatus


def test_account_operation_status_contains_only_non_secret_metadata() -> None:
    payload = AccountOperationStatus(True, "memory_clear", "2026-08-25T00:00:00+00:00", "a" * 64).to_dict()
    assert set(payload) == {"busy", "operation", "started_at", "account_hash"}
