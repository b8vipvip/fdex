import inspect

from app.auth_routes import delete_account


def test_delete_endpoint_uses_cross_worker_account_lock() -> None:
    source = inspect.getsource(delete_account)
    assert 'account_operation(user_id, "account_delete")' in source
