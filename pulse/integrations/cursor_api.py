from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api2.cursor.sh"
DEFAULT_TOKEN_SKEW_SECONDS = 300


@dataclass
class UsageEventDTO:
    event_at: datetime
    event_date: datetime.date
    model: str
    kind: str
    tokens_input_cache_write: int
    tokens_input_no_cache: int
    tokens_cache_read: int
    tokens_output: int
    tokens_total: int
    cost_usd: float
    cost_raw: str
    external_id: str
    source_row_hash: str


@dataclass
class _CachedAccessToken:
    access_token: str
    expires_at: float


def _api_key_cache_key(api_key: str) -> str:
    return hashlib.sha256(api_key.strip().encode()).hexdigest()


def _decode_jwt_payload(token: str) -> dict:
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(segment))
    except Exception:
        return {}


def _jwt_expires_at(access_token: str) -> float:
    try:
        data = _decode_jwt_payload(access_token)
        exp = data.get("exp")
        if exp is None:
            raise ValueError("JWT missing exp claim")
        return float(exp)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("invalid accessToken JWT") from exc


def _normalize_account_email(value: str) -> str:
    return value.strip().lower()


def email_from_jwt_payload(payload: dict) -> str | None:
    if not payload:
        return None
    for key in ("email", "preferred_username", "https://cursor.com/email"):
        value = payload.get(key)
        if isinstance(value, str) and "@" in value:
            return _normalize_account_email(value)
    return None


def resolve_account_email_from_exchange(data: dict) -> str | None:
    direct = data.get("email")
    if isinstance(direct, str) and "@" in direct:
        return _normalize_account_email(direct)
    for field in ("accessToken", "refreshToken", "idToken", "id_token"):
        token = data.get(field)
        if isinstance(token, str) and token:
            email = email_from_jwt_payload(_decode_jwt_payload(token))
            if email:
                return email
    return None


def map_usage_event(raw: dict) -> UsageEventDTO:
    ts_ms = int(raw["timestamp"])
    event_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    token = raw.get("tokenUsage") or {}
    input_t = int(token.get("inputTokens") or 0)
    output_t = int(token.get("outputTokens") or 0)
    cache_read = int(token.get("cacheReadTokens") or 0)
    cache_write = int(token.get("cacheWriteTokens") or 0)
    cents = float(raw.get("chargedCents") or token.get("totalCents") or 0)
    conv = raw.get("conversationId") or ""
    external_id = hashlib.sha256(
        f"{raw['timestamp']}:{raw.get('model')}:{conv}".encode()
    ).hexdigest()[:32]
    kind = raw.get("kind") or "unknown"
    cost_raw = "included" if "INCLUDED" in kind else "usage_based"
    return UsageEventDTO(
        event_at=event_at,
        event_date=event_at.date(),
        model=raw.get("model") or "unknown",
        kind=kind,
        tokens_input_cache_write=cache_write,
        tokens_input_no_cache=input_t,
        tokens_cache_read=cache_read,
        tokens_output=output_t,
        tokens_total=input_t + output_t + cache_read + cache_write,
        cost_usd=round(cents / 100.0, 6),
        cost_raw=cost_raw,
        external_id=external_id,
        source_row_hash=external_id,
    )


class CursorApiClient:
    def __init__(
        self,
        api_base: str = API_BASE,
        timeout: float = 30.0,
        *,
        token_skew_seconds: int = DEFAULT_TOKEN_SKEW_SECONDS,
    ):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.token_skew_seconds = token_skew_seconds
        self._token_cache: dict[str, _CachedAccessToken] = {}
        self._token_lock = threading.Lock()

    def get_access_token(self, api_key: str, *, force: bool = False) -> str:
        api_key = api_key.strip()
        cache_key = _api_key_cache_key(api_key)
        now = time.time()
        if not force:
            with self._token_lock:
                cached = self._token_cache.get(cache_key)
                if cached and cached.expires_at - self.token_skew_seconds > now:
                    return cached.access_token

        token = self._exchange_api_key(api_key)
        expires_at = _jwt_expires_at(token)
        with self._token_lock:
            self._token_cache[cache_key] = _CachedAccessToken(token, expires_at)
        return token

    def invalidate_token(self, api_key: str) -> None:
        cache_key = _api_key_cache_key(api_key.strip())
        with self._token_lock:
            self._token_cache.pop(cache_key, None)

    def exchange_api_key(self, api_key: str) -> str:
        """Force a fresh exchange; prefer get_access_token() for normal calls."""
        return self.get_access_token(api_key, force=True)

    def exchange_user_api_key_response(self, api_key: str) -> dict:
        api_key = api_key.strip()
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.api_base}/auth/exchange_user_api_key",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={},
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("accessToken")
            if not token:
                raise ValueError("exchange returned no accessToken")
            expires_at = _jwt_expires_at(token)
            cache_key = _api_key_cache_key(api_key)
            with self._token_lock:
                self._token_cache[cache_key] = _CachedAccessToken(token, expires_at)
            return data

    def resolve_api_key_account_email(
        self, api_key: str, *, exchange: dict | None = None
    ) -> str | None:
        data = exchange or self.exchange_user_api_key_response(api_key)
        email = resolve_account_email_from_exchange(data)
        if email:
            return email
        token = data.get("accessToken")
        if not isinstance(token, str) or not token:
            return None
        try:
            me = self.get_me(token, api_key=api_key)
        except Exception:
            return None
        me_email = me.get("email")
        if isinstance(me_email, str) and "@" in me_email:
            return _normalize_account_email(me_email)
        return None

    def _exchange_api_key(self, api_key: str) -> str:
        return self.exchange_user_api_key_response(api_key)["accessToken"]

    def _post_dashboard(
        self,
        token: str,
        method: str,
        body: dict,
        *,
        api_key: str | None = None,
    ) -> dict:
        try:
            return self._do_post_dashboard(token, method, body)
        except httpx.HTTPStatusError as exc:
            if api_key and exc.response.status_code == 401:
                logger.debug("cursor dashboard 401, refreshing access token")
                self.invalidate_token(api_key)
                token = self.get_access_token(api_key, force=True)
                return self._do_post_dashboard(token, method, body)
            raise

    def _do_post_dashboard(self, token: str, method: str, body: dict) -> dict:
        delays = (1.0, 2.0, 4.0)
        last_exc: Exception | None = None
        for attempt, delay in enumerate((0.0, *delays)):
            if delay:
                time.sleep(delay)
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.api_base}/aiserver.v1.DashboardService/{method}",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "Connect-Protocol-Version": "1",
                        },
                        json=body,
                    )
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                if status not in (429,) and status < 500:
                    raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        raise RuntimeError("cursor dashboard request failed")

    def get_current_period_usage(
        self, token: str, *, api_key: str | None = None
    ) -> dict:
        return self._post_dashboard(
            token, "GetCurrentPeriodUsage", {}, api_key=api_key
        )

    def get_me(self, token: str, *, api_key: str | None = None) -> dict:
        return self._post_dashboard(token, "GetMe", {}, api_key=api_key)

    def iter_filtered_usage_events(
        self,
        token: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        page_size: int = 100,
        api_key: str | None = None,
    ):
        page = 1
        body: dict = {"page": page, "pageSize": page_size}
        if start_ms is not None:
            body["startDate"] = str(start_ms)
        if end_ms is not None:
            body["endDate"] = str(end_ms)
        while True:
            body["page"] = page
            data = self._post_dashboard(
                token,
                "GetFilteredUsageEvents",
                body,
                api_key=api_key,
            )
            events = data.get("usageEventsDisplay") or []
            for raw in events:
                yield map_usage_event(raw)
            if not events:
                break
            page += 1

    def create_user_api_key(
        self, token: str, name: str, *, api_key: str | None = None
    ) -> dict:
        return self._post_dashboard(
            token, "CreateUserApiKey", {"name": name}, api_key=api_key
        )

    def list_user_api_keys(
        self, token: str, *, api_key: str | None = None
    ) -> list[dict]:
        data = self._post_dashboard(token, "ListUserApiKeys", {}, api_key=api_key)
        return data.get("apiKeys") or []

    def revoke_user_api_key(
        self, token: str, key_id: int, *, api_key: str | None = None
    ) -> None:
        self._post_dashboard(token, "RevokeUserApiKey", {"id": key_id}, api_key=api_key)
