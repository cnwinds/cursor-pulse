"""智谱 GLM Coding Plan quota — logic ported from cc-switch parse_zhipu_token_tiers."""

from __future__ import annotations

from datetime import UTC, datetime

from pulse.http_clients import outbound_client
from pulse.integrations.coding_plan.types import CodingPlanQuotaResult, QuotaTier

TIER_FIVE_HOUR = "five_hour"
TIER_WEEKLY_LIMIT = "weekly_limit"

_ZHIPU_CN = "https://open.bigmodel.cn"
_ZHIPU_EN = "https://api.z.ai"


def zhipu_quota_base(region: str) -> str:
    if region == "bigmodel":
        return _ZHIPU_CN
    return _ZHIPU_EN


def _millis_to_iso8601(ms: int) -> str | None:
    if ms <= 0:
        return None
    secs = ms / 1000.0
    return datetime.fromtimestamp(secs, tz=UTC).isoformat()


def _classify_zhipu_window(item: dict) -> str | None:
    unit = item.get("unit")
    if unit == 3:
        return TIER_FIVE_HOUR
    if unit == 6:
        return TIER_WEEKLY_LIMIT
    return None


def parse_zhipu_token_tiers(data: dict) -> list[QuotaTier]:
    type Entry = tuple[int | None, float, str | None]
    five_hour: Entry | None = None
    weekly: Entry | None = None
    unclassified: list[Entry] = []

    for limit_item in data.get("limits") or []:
        if not isinstance(limit_item, dict):
            continue
        limit_type = str(limit_item.get("type") or "")
        if not (limit_type.lower() == "tokens_limit".lower() or limit_type.lower() == "credit_limit".lower()):
            continue
        percentage = float(limit_item.get("percentage") or 0)
        reset_ms = limit_item.get("nextResetTime")
        reset_ms_int = int(reset_ms) if reset_ms is not None else None
        reset_iso = _millis_to_iso8601(reset_ms_int) if reset_ms_int is not None else None
        entry: Entry = (reset_ms_int, percentage, reset_iso)
        window = _classify_zhipu_window(limit_item)
        if window == TIER_FIVE_HOUR and five_hour is None:
            five_hour = entry
        elif window == TIER_WEEKLY_LIMIT and weekly is None:
            weekly = entry
        else:
            unclassified.append(entry)

    unclassified.sort(key=lambda e: (e[0] is not None, e[0] if e[0] is not None else 0))
    for entry in unclassified:
        if five_hour is None:
            five_hour = entry
        elif weekly is None:
            weekly = entry

    tiers: list[QuotaTier] = []
    for name, slot in ((TIER_FIVE_HOUR, five_hour), (TIER_WEEKLY_LIMIT, weekly)):
        if slot is None:
            continue
        _, percentage, resets_at = slot
        tiers.append(QuotaTier(name=name, utilization_pct=percentage, resets_at=resets_at))
    return tiers


def parse_zhipu_mcp_extras(data: dict) -> list[dict]:
    extras: list[dict] = []
    for limit_item in data.get("limits") or []:
        if not isinstance(limit_item, dict):
            continue
        if str(limit_item.get("type") or "").upper() != "TIME_LIMIT":
            continue
        reset_ms = limit_item.get("nextResetTime")
        reset_iso = _millis_to_iso8601(int(reset_ms)) if reset_ms is not None else None
        extras.append(
            {
                "name": "mcp_time_limit",
                "utilization_pct": float(limit_item.get("percentage") or 0),
                "resets_at": reset_iso,
            }
        )
    return extras


def parse_zhipu_response(body: dict) -> CodingPlanQuotaResult:
    if body.get("success") is False:
        msg = body.get("msg") or "Unknown error"
        raise ValueError(f"GLM API error: {msg}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise ValueError("GLM response missing data")
    level = data.get("level")
    plan_level = str(level) if level is not None else None
    return CodingPlanQuotaResult(
        plan_level=plan_level,
        tiers=parse_zhipu_token_tiers(data),
        extras=parse_zhipu_mcp_extras(data),
    )


_ZHIPU_QUOTA_PATH = "/api/monitor/usage/quota/limit"
_ZHIPU_TEAM_QUOTA_URL = f"{_ZHIPU_CN}{_ZHIPU_QUOTA_PATH}?type=2"


def _zhipu_common_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": api_key.strip(),
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en",
    }


def _parse_zhipu_http_response(resp) -> CodingPlanQuotaResult:
    if resp.status_code in (401, 403):
        raise ValueError(f"GLM authentication failed (HTTP {resp.status_code})")
    if not resp.is_success:
        raise ValueError(f"GLM API error (HTTP {resp.status_code}): {resp.text[:500]}")
    return parse_zhipu_response(resp.json())


def fetch_zhipu_quota(api_key: str, *, region: str) -> CodingPlanQuotaResult:
    base = zhipu_quota_base(region)
    url = f"{base}{_ZHIPU_QUOTA_PATH}"
    with outbound_client() as client:
        resp = client.get(url, headers=_zhipu_common_headers(api_key), timeout=15.0)
    return _parse_zhipu_http_response(resp)


def fetch_zhipu_team_quota(
    api_key: str,
    *,
    organization_id: str,
    project_id: str,
) -> CodingPlanQuotaResult:
    """智谱团队版：固定国内站 + ?type=2 + org/project 头（cc-switch query_zhipu_team）。"""
    org = organization_id.strip()
    proj = project_id.strip()
    if not org or not proj:
        raise ValueError("智谱团队版须同时填写组织 ID 与项目 ID")
    headers = {
        **_zhipu_common_headers(api_key),
        "bigmodel-organization": org,
        "bigmodel-project": proj,
    }
    with outbound_client() as client:
        resp = client.get(_ZHIPU_TEAM_QUOTA_URL, headers=headers, timeout=15.0)
    return _parse_zhipu_http_response(resp)


def fetch_glm_quota(
    api_key: str,
    *,
    region: str,
    organization_id: str | None = None,
    project_id: str | None = None,
) -> CodingPlanQuotaResult:
    org = (organization_id or "").strip()
    proj = (project_id or "").strip()
    if org or proj:
        if region != "bigmodel":
            raise ValueError("智谱团队版仅支持国内站（bigmodel）")
        return fetch_zhipu_team_quota(api_key, organization_id=org, project_id=proj)
    return fetch_zhipu_quota(api_key, region=region)


def is_glm_team_account(*, organization_id: str | None, project_id: str | None) -> bool:
    return bool((organization_id or "").strip() and (project_id or "").strip())
