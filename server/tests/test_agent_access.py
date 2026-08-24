from app.agent_access import agent_token_valid


def test_agent_token_requires_both_values() -> None:
    assert agent_token_valid("", "") is False
    assert agent_token_valid("secret", "") is False
    assert agent_token_valid("", "secret") is False


def test_agent_token_uses_exact_trimmed_match() -> None:
    assert agent_token_valid("  secret-value  ", "secret-value") is True
    assert agent_token_valid("secret-value", "secret-value ") is True
    assert agent_token_valid("secret-value", "SECRET-value") is False
    assert agent_token_valid("secret-value", "secret-value-2") is False
