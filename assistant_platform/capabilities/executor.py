from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from assistant_platform.capabilities.models import ToolInvocationRow
from assistant_platform.capabilities.pulse_client import PulseCapabilityClient
from assistant_platform.capabilities.resolve import resolve_capabilities
from assistant_platform.config import AssistantConfig
from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from assistant_platform.secrets.store import get_secret, put_secret

_BIND_CAPABILITY = "cursor.key.bind"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(arguments)
    redacted.pop("api_key", None)
    return redacted


def _redact_result_value(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"api_key", "apiKey"} and isinstance(item, str) and item:
                hint = item[:8] + "…" + item[-4:] if len(item) > 12 else "****"
                out[key] = f"[REDACTED:{hint}]"
            else:
                out[key] = _redact_result_value(item)
        return out
    if isinstance(value, list):
        return [_redact_result_value(item) for item in value]
    return value


def _redact_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return _redact_result_value(dict(result))

class CapabilityExecutor:
    def __init__(
        self,
        *,
        session: Session,
        config: AssistantConfig,
        pulse_client: PulseCapabilityClient,
    ) -> None:
        self._session = session
        self._config = config
        self._pulse = pulse_client

    def invoke(
        self,
        *,
        actor_member_id: str,
        team_id: str,
        role: str | None,
        capability_key: str,
        arguments: dict[str, Any],
        confirmed: bool,
        capability_version: str = "1",
    ) -> CapabilityInvokeResult:
        invocation_id = str(uuid.uuid4())
        request_redacted = _redact_arguments(arguments)

        resolved_by_key = {
            cap.key: cap
            for cap in resolve_capabilities(
                self._session,
                team_id=team_id,
                role=role,
                member_id=actor_member_id,
            )
        }

        if capability_key == "bot.help":
            if "bot.help" not in resolved_by_key:
                return self._fail(
                    invocation_id=invocation_id,
                    team_id=team_id,
                    actor_member_id=actor_member_id,
                    capability_key=capability_key,
                    capability_version=capability_version,
                    request_redacted=request_redacted,
                    error_code="forbidden",
                    user_message="无权执行该能力",
                )
            from assistant_platform.conversation.help import build_help_message

            topic = arguments.get("topic")
            help_text = build_help_message(
                resolved_by_key.values(),
                topic=str(topic) if topic else None,
                member_id=actor_member_id,
                role=role,
            )
            row = ToolInvocationRow(
                invocation_id=invocation_id,
                assistant_id=self._config.assistant_id,
                team_id=team_id,
                capability_key=capability_key,
                capability_version=capability_version,
                actor_member_id=actor_member_id,
                status="succeeded",
                request_redacted_json=request_redacted,
            )
            self._session.add(row)
            self._session.flush()
            return CapabilityInvokeResult(
                status="succeeded",
                user_message=help_text,
                result={"capability_key": capability_key},
            )

        resolved = resolved_by_key.get(capability_key)
        if resolved is None or resolved.version != capability_version:
            return self._fail(
                invocation_id=invocation_id,
                team_id=team_id,
                actor_member_id=actor_member_id,
                capability_key=capability_key,
                capability_version=capability_version,
                request_redacted=request_redacted,
                error_code="forbidden",
                user_message="无权执行该能力",
            )

        if resolved.confirmation_required and not confirmed:
            return self._fail(
                invocation_id=invocation_id,
                team_id=team_id,
                actor_member_id=actor_member_id,
                capability_key=capability_key,
                capability_version=capability_version,
                request_redacted=request_redacted,
                error_code="confirmation_required",
                user_message="该操作需要确认后执行",
            )

        stored_args, provider_args = self._prepare_arguments(
            capability_key=capability_key,
            arguments=arguments,
        )

        row = ToolInvocationRow(
            invocation_id=invocation_id,
            assistant_id=self._config.assistant_id,
            team_id=team_id,
            capability_key=capability_key,
            capability_version=capability_version,
            actor_member_id=actor_member_id,
            status="planned",
            request_redacted_json=stored_args,
        )
        self._session.add(row)
        # Commit the "planned" record BEFORE the provider HTTP call so we do not
        # hold a DB write lock across network I/O (the call can take 20s+).
        self._session.commit()

        request = CapabilityInvokeRequest(
            invocation_id=invocation_id,
            idempotency_key=invocation_id,
            team_id=team_id,
            actor_member_id=actor_member_id,
            capability_key=capability_key,
            capability_version=capability_version,
            arguments=provider_args,
            confirmed_by=actor_member_id if confirmed else None,
            requested_at=_utcnow().isoformat(),
        )

        try:
            result = self._pulse.invoke(request)
        except Exception:
            row.status = "failed"
            row.error_code = "provider_error"
            row.updated_at = _utcnow()
            self._session.commit()
            raise

        row.status = result.status
        row.error_code = result.error_code
        row.result_redacted_json = _redact_result(
            dict(result.result) if result.result else None
        )
        row.updated_at = _utcnow()
        self._session.commit()
        return result

    def _prepare_arguments(
        self,
        *,
        capability_key: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        stored_args = _redact_arguments(arguments)
        provider_args = dict(arguments)

        if capability_key != _BIND_CAPABILITY:
            return stored_args, provider_args

        if "api_key" in provider_args:
            ref_id = put_secret(
                self._session,
                kind="cursor_api_key",
                plaintext=str(provider_args.pop("api_key")),
                secret_key=self._config.secret_key,
                service_token=self._config.service_token,
            )
            stored_args["secret_ref"] = ref_id
            provider_args["secret_ref"] = ref_id

        secret_ref = provider_args.get("secret_ref")
        if secret_ref and "api_key" not in provider_args:
            plaintext = get_secret(
                self._session,
                str(secret_ref),
                secret_key=self._config.secret_key,
                service_token=self._config.service_token,
            )
            if plaintext is not None:
                provider_args["api_key"] = plaintext

        return stored_args, provider_args

    def _fail(
        self,
        *,
        invocation_id: str,
        team_id: str,
        actor_member_id: str,
        capability_key: str,
        capability_version: str,
        request_redacted: dict[str, Any],
        error_code: str,
        user_message: str,
    ) -> CapabilityInvokeResult:
        row = ToolInvocationRow(
            invocation_id=invocation_id,
            assistant_id=self._config.assistant_id,
            team_id=team_id,
            capability_key=capability_key,
            capability_version=capability_version,
            actor_member_id=actor_member_id,
            status="failed",
            request_redacted_json=request_redacted,
            error_code=error_code,
        )
        self._session.add(row)
        self._session.flush()
        return CapabilityInvokeResult(
            status="failed",
            error_code=error_code,
            user_message=user_message,
        )
