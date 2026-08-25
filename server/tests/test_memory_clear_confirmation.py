from app.auth_routes import ClearMemoryRequest


def test_clear_memory_confirmation_phrase() -> None:
    request = ClearMemoryRequest(password="abcdefgh", confirmation="CLEAR MY FDEX MEMORY")
    assert request.confirmation == "CLEAR MY FDEX MEMORY"
