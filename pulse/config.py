from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class DingTalkConfig(BaseModel):
    app_key: str = ""
    app_secret: str = ""
    robot_code: str = ""
    group_open_conversation_id: str = ""
    chat_id: str = ""  # 钉钉群号（展示用；API 需 openConversationId）


class CollectionConfig(BaseModel):
    period_format: str = "%Y-%m"
    start_day: int = 1
    start_time: str = "09:00"
    deadline_day: int = 3
    deadline_time: str = "18:00"
    daily_check_time: str = "10:00"
    report_day: int = 4
    report_time: str = "11:00"
    timezone: str = "Asia/Shanghai"


class StorageConfig(BaseModel):
    database_url: str = "sqlite:///data/pulse.db"
    raw_files_dir: str = "data/raw"


class ObjectStorageConfig(BaseModel):
    enabled: bool = False
    endpoint_url: str = ""
    bucket: str = ""
    access_key: str = ""
    secret_key: str = ""
    prefix: str = "pulse/raw"
    region: str = ""


class BotPlatformConfig(BaseModel):
    """群平台：dingtalk（默认）| feishu | wecom（后两者为扩展桩）。"""
    name: str = "dingtalk"


class CursorTeamsConfig(BaseModel):
    """Cursor Teams/Enterprise Admin API（与 CSV 收集并存，可选）。"""
    enabled: bool = False
    api_base_url: str = "https://api.cursor.com"
    admin_api_key: str = ""


class AdminConfig(BaseModel):
    dingtalk_user_ids: list[str] = Field(default_factory=list)


class LLMConfig(BaseModel):
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    timeout_seconds: float = 60.0
    vision_enabled: bool = False
    vision_model: str = "gpt-4o"
    confidence_threshold: float = 0.75
    review_low_confidence: bool = True


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    admin_token: str = ""
    jwt_secret: str = ""
    dingtalk_oauth_redirect_uri: str = "http://localhost:5173/login/callback"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


class TenantConfig(BaseModel):
    slug: str = "default"
    name: str = "Default Team"


class AlertsConfig(BaseModel):
    enabled: bool = True
    member_events_spike_pct: float = 100.0
    team_events_spike_pct: float = 50.0
    member_cost_spike_usd: float = 10.0


class IntegrationsConfig(BaseModel):
    webhook_url: str = ""
    webhook_secret: str = ""
    push_on_report: bool = True


class PersonaConfig(BaseModel):
    name: str = "小脉"
    role: str = "团队 Cursor 用量协调员"
    tone: str = "亲切、干练，像真人同事，不用机器人腔"
    work_hours: str = "工作日 9:00-18:00"
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    work_start: str = "09:00"
    work_end: str = "18:00"


class CredentialConfig(BaseModel):
    encryption_key: str = ""


class MemoryConfig(BaseModel):
    evolution_enabled: bool = True
    evolution_day_of_week: int = 6
    evolution_time: str = "02:00"
    retrieval_top_k: int = 8
    evolution_min_confidence: float = 0.7
    evolution_auto_execute: bool = True
    embedding_enabled: bool = True
    embedding_model: str = "text-embedding-3-small"
    conversation_turn_limit: int = 10
    conversation_keep: int = 20


class AppConfig(BaseModel):
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    object_storage: ObjectStorageConfig = Field(default_factory=ObjectStorageConfig)
    bot: BotPlatformConfig = Field(default_factory=BotPlatformConfig)
    cursor_teams: CursorTeamsConfig = Field(default_factory=CursorTeamsConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    tenant: TenantConfig = Field(default_factory=TenantConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    credentials: CredentialConfig = Field(default_factory=CredentialConfig)


class EnvSettings(BaseSettings):
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_robot_code: str = ""
    dingtalk_group_id: str = ""
    dingtalk_chat_id: str = ""
    dingtalk_admin_user_ids: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_enabled: str = ""
    vision_enabled: str = ""
    vision_model: str = ""
    admin_web_token: str = ""
    jwt_secret: str = ""
    web_cors_origins: str = ""
    dingtalk_oauth_redirect_uri: str = ""
    pulse_team_slug: str = ""
    bi_webhook_url: str = ""
    bi_webhook_secret: str = ""
    database_url: str = ""
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_enabled: str = ""
    cursor_teams_api_key: str = ""
    bot_platform: str = ""
    pulse_credential_encryption_key: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    data = _expand_env(data)
    cfg = AppConfig.model_validate(data)

    env = EnvSettings()
    if env.dingtalk_app_key:
        cfg.dingtalk.app_key = env.dingtalk_app_key
    if env.dingtalk_app_secret:
        cfg.dingtalk.app_secret = env.dingtalk_app_secret
    if env.dingtalk_robot_code:
        cfg.dingtalk.robot_code = env.dingtalk_robot_code
    if env.dingtalk_group_id:
        cfg.dingtalk.group_open_conversation_id = env.dingtalk_group_id
    if env.dingtalk_chat_id:
        cfg.dingtalk.chat_id = env.dingtalk_chat_id
    if env.dingtalk_admin_user_ids:
        cfg.admin.dingtalk_user_ids = [
            uid.strip() for uid in env.dingtalk_admin_user_ids.split(",") if uid.strip()
        ]
    if env.llm_api_key:
        cfg.llm.api_key = env.llm_api_key
    if env.llm_base_url:
        cfg.llm.base_url = env.llm_base_url
    if env.llm_model:
        cfg.llm.model = env.llm_model
    if env.llm_enabled.lower() in ("1", "true", "yes", "on"):
        cfg.llm.enabled = True
    if env.vision_enabled.lower() in ("1", "true", "yes", "on"):
        cfg.llm.vision_enabled = True
    if env.vision_model:
        cfg.llm.vision_model = env.vision_model
    if env.admin_web_token:
        cfg.web.admin_token = env.admin_web_token
    if env.jwt_secret:
        cfg.web.jwt_secret = env.jwt_secret
    if env.dingtalk_oauth_redirect_uri:
        cfg.web.dingtalk_oauth_redirect_uri = env.dingtalk_oauth_redirect_uri
    if env.web_cors_origins:
        cfg.web.cors_origins = [
            o.strip() for o in env.web_cors_origins.split(",") if o.strip()
        ]
    if env.pulse_team_slug:
        cfg.tenant.slug = env.pulse_team_slug
    if env.bi_webhook_url:
        cfg.integrations.webhook_url = env.bi_webhook_url
    if env.bi_webhook_secret:
        cfg.integrations.webhook_secret = env.bi_webhook_secret
    if env.database_url:
        cfg.storage.database_url = env.database_url
    if env.s3_enabled.lower() in ("1", "true", "yes", "on"):
        cfg.object_storage.enabled = True
    if env.s3_endpoint:
        cfg.object_storage.endpoint_url = env.s3_endpoint
    if env.s3_bucket:
        cfg.object_storage.bucket = env.s3_bucket
    if env.s3_access_key:
        cfg.object_storage.access_key = env.s3_access_key
    if env.s3_secret_key:
        cfg.object_storage.secret_key = env.s3_secret_key
    if env.cursor_teams_api_key:
        cfg.cursor_teams.admin_api_key = env.cursor_teams_api_key
        cfg.cursor_teams.enabled = True
    if env.bot_platform:
        cfg.bot.name = env.bot_platform

    cfg.credentials.encryption_key = env.pulse_credential_encryption_key

    if not cfg.dingtalk.group_open_conversation_id:
        from pulse.bot.dingtalk.group_store import load_persisted_group_id

        persisted = load_persisted_group_id()
        if persisted:
            cfg.dingtalk.group_open_conversation_id = persisted

    return cfg
