from __future__ import annotations

import logging
from datetime import datetime, timezone
from pulse.util.datetime_fmt import serialize_datetime

import httpx

from pulse.config import CursorTeamsConfig
from pulse.http_clients import outbound_client

logger = logging.getLogger(__name__)


class CursorTeamsClient:
    """Cursor Teams/Enterprise Admin API 客户端（桩 + 基础 HTTP 框架）。"""

    def __init__(self, config: CursorTeamsConfig):
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.admin_api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.admin_api_key}",
            "Accept": "application/json",
        }

    def fetch_usage_summary(self, period: str) -> dict:
        """拉取团队级用量摘要（API 路径随 Cursor 官方文档更新）。"""
        if not self.enabled:
            raise RuntimeError("Cursor Teams API 未启用，请设置 CURSOR_TEAMS_API_KEY")

        url = f"{self.config.api_base_url.rstrip('/')}/teams/v1/usage"
        params = {"period": period}
        with outbound_client(timeout=30) as client:
            response = client.get(url, headers=self._headers(), params=params)
            if response.status_code == 404:
                logger.warning("Cursor Teams API 端点未就绪，返回占位结构")
                return {
                    "period": period,
                    "source": "cursor_teams_api",
                    "status": "not_available",
                    "fetched_at": serialize_datetime(datetime.now(timezone.utc)),
                    "message": "Admin API 端点待 Cursor 官方发布；当前请使用 API Key 自动同步。",
                }
            response.raise_for_status()
            return response.json()
