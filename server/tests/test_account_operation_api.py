from app.auth_routes import ClearMemoryRequest, DeleteAccountRequest


def test_destructive_request_models_require_explicit_confirmation_fields() -> None:
    clear = ClearMemoryRequest(password="password-123", confirmation="CLEAR MY FDEX MEMORY")
    delete = DeleteAccountRequest(password="password-123", confirmation="DELETE MY FDEX")
    assert clear.confirmation == "CLEAR MY FDEX MEMORY"
    assert delete.confirmation == "DELETE MY FDEX"
