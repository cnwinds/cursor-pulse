"""Reject insecure placeholder service tokens before serving traffic."""

from __future__ import annotations

_PLACEHOLDER_PREFIXES = ("change-me", "changeme", "replace-me", "todo-")


def is_insecure_token(value: str | None) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return any(text.startswith(p) or text == p.rstrip("-") for p in _PLACEHOLDER_PREFIXES)


def assert_secure_service_tokens(*, assistant_token: str, pulse_internal_token: str) -> None:
    """Raise ValueError if configured tokens look like documentation placeholders."""
    bad: list[str] = []
    if is_insecure_token(assistant_token):
        bad.append("ASSISTANT_SERVICE_TOKEN")
    if is_insecure_token(pulse_internal_token):
        bad.append("PULSE_INTERNAL_SERVICE_TOKEN")
    if bad:
        raise ValueError(
            "Insecure placeholder token(s): "
            + ", ".join(bad)
            + ". Run docker/scripts/setup.sh or set high-entropy values "
            "(never use change-me-*)."
        )
