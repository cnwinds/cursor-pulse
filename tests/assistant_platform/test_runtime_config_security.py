from __future__ import annotations

import pytest

from assistant_platform.config import AssistantConfig, validate_runtime_config

_SECURE_TOKEN = "a" * 48


def test_validate_runtime_config_rejects_insecure_service_token():
    with pytest.raises(SystemExit, match="ASSISTANT_SERVICE_TOKEN"):
        validate_runtime_config(
            AssistantConfig(service_token="change-me-assistant-token"),
        )


def test_validate_runtime_config_rejects_insecure_service_token_in_strict_mode():
    with pytest.raises(SystemExit, match="ASSISTANT_SERVICE_TOKEN"):
        validate_runtime_config(
            AssistantConfig(
                service_token="change-me-assistant-token",
                secret_key="another-secure-secret-key-with-entropy",
            ),
            strict=True,
        )


def test_validate_runtime_config_accepts_secure_service_token_without_secret_key():
    validate_runtime_config(AssistantConfig(service_token=_SECURE_TOKEN))


def test_validate_runtime_config_rejects_missing_secret_key_in_strict_mode():
    with pytest.raises(SystemExit, match="ASSISTANT_SECRET_KEY"):
        validate_runtime_config(
            AssistantConfig(service_token=_SECURE_TOKEN, secret_key=""),
            strict=True,
        )


def test_validate_runtime_config_rejects_insecure_secret_key_in_strict_mode():
    with pytest.raises(SystemExit, match="ASSISTANT_SECRET_KEY"):
        validate_runtime_config(
            AssistantConfig(
                service_token=_SECURE_TOKEN,
                secret_key="change-me-secret-key",
            ),
            strict=True,
        )


def test_validate_runtime_config_accepts_secure_tokens_in_strict_mode():
    validate_runtime_config(
        AssistantConfig(
            service_token=_SECURE_TOKEN,
            secret_key="another-secure-secret-key-with-entropy",
        ),
        strict=True,
    )
