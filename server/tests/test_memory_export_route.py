import inspect

from app.auth_routes import data_export


def test_data_export_requires_center_user() -> None:
    source = inspect.getsource(data_export)
    assert "require_user(request)" in source
    assert 'account_operation(user_id, "data_export")' in source
