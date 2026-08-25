import inspect

from app.auth_routes import RegisterMemoryScopeRequest


def test_scope_registration_request_has_no_owner_id() -> None:
    assert set(RegisterMemoryScopeRequest.model_fields) == {"scope_token"}
