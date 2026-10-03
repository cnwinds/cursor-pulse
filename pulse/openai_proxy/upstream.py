"""OpenAI-compatible upstream base URLs for Coding Plan vendors."""

from __future__ import annotations

from typing import Literal

CodingPlanVendor = Literal["glm", "minimax", "kimi"]

CP_VENDORS: frozenset[str] = frozenset({"glm", "minimax", "kimi"})


def coding_plan_gateway_public_base(*, proxy_public_url: str) -> str:
    """OpenAI SDK base_url on Go data plane (layer A), not Pulse Web."""
    return f"{(proxy_public_url or 'http://127.0.0.1:8317').rstrip('/')}/openai/v1"


def cp_gateway_endpoints(*, session, config) -> list[dict[str, str]]:
    """OpenAI base_url per configured Go proxy (team settings → config fallback)."""
    from pulse.tool_center.key_loan_notify import resolve_proxy_addresses

    out: list[dict[str, str]] = []
    for addr in resolve_proxy_addresses(session, config):
        proxy_url = addr.url.rstrip("/")
        display = (addr.display_name or proxy_url).strip() or proxy_url
        out.append(
            {
                "display_name": display,
                "proxy_url": proxy_url,
                "openai_base_url": coding_plan_gateway_public_base(proxy_public_url=proxy_url),
            }
        )
    return out


def openai_base_url(*, vendor_slug: str, api_region: str | None) -> str:
    """Return base URL ending with / (OpenAI SDK style)."""
    slug = (vendor_slug or "").strip().lower()
    region = (api_region or "").strip().lower()

    if slug == "glm":
        if region == "bigmodel":
            # 国内团队/个人 Coding Plan 常用 OpenAI 兼容面（与额度 monitor 同域）
            return "https://open.bigmodel.cn/api/coding/paas/v4/"
        # z.ai 国际 Coding Plan（见 Z.AI devpack Tool Integration）
        return "https://api.z.ai/api/coding/paas/v4/"

    if slug == "minimax":
        domain = "api.minimax.io" if region == "global" else "api.minimaxi.com"
        return f"https://{domain}/v1/"

    if slug == "kimi":
        return "https://api.kimi.com/coding/v1/"

    raise ValueError(f"unsupported coding plan vendor: {vendor_slug}")


def auth_header(*, vendor_slug: str, api_key: str) -> dict[str, str]:
    slug = vendor_slug.strip().lower()
    key = api_key.strip()
    headers = {"Content-Type": "application/json"}
    if slug == "glm":
        # 与额度 monitor 一致：GLM 部分面接受 Bearer；Coding OpenAI 面通常 Bearer
        headers["Authorization"] = f"Bearer {key}"
        headers["Accept-Language"] = "en-US,en"
        return headers
    headers["Authorization"] = f"Bearer {key}"
    return headers
