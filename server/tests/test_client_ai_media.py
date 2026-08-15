from app.client_ai import IMAGE_GENERATION_MIN_TIMEOUT_SECONDS, _image_providers


def test_image_generation_uses_longer_timeout_without_mutating_provider() -> None:
    original = [{"id": 1, "name": "chat2api", "timeout_seconds": 60}]
    adjusted = _image_providers(original)

    assert original[0]["timeout_seconds"] == 60
    assert adjusted[0]["timeout_seconds"] == IMAGE_GENERATION_MIN_TIMEOUT_SECONDS
    assert adjusted[0] is not original[0]


def test_image_generation_keeps_larger_configured_timeout() -> None:
    original = [{"id": 1, "name": "slow-image", "timeout_seconds": 480}]
    adjusted = _image_providers(original)
    assert adjusted[0]["timeout_seconds"] == 480
