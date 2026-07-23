import pytest

from pulse.security_tokens import assert_secure_service_tokens, is_insecure_token


def test_is_insecure_token_detects_placeholders():
    assert is_insecure_token("change-me-assistant-token")
    assert is_insecure_token("CHANGE-ME")
    assert not is_insecure_token("")
    assert not is_insecure_token("a" * 48)


def test_assert_secure_service_tokens_rejects_placeholders():
    with pytest.raises(ValueError, match="ASSISTANT_SERVICE_TOKEN"):
        assert_secure_service_tokens(
            assistant_token="change-me-assistant-token",
            pulse_internal_token="ok-token-value-with-entropy",
        )
    with pytest.raises(ValueError, match="PULSE_INTERNAL_SERVICE_TOKEN"):
        assert_secure_service_tokens(
            assistant_token="ok-token-value-with-entropy",
            pulse_internal_token="change-me-pulse-internal-token",
        )
    assert_secure_service_tokens(
        assistant_token="ok-token-value-with-entropy",
        pulse_internal_token="another-ok-token-value-with-entropy",
    )
