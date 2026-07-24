from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


def _bootstrap_dotenv() -> None:
    """Load .env into process env.

    Docker mounts ``docker/.env`` at ``/app/.env``. Compose ``env_file`` only
    injects at container *create* time, so a plain ``restart`` would keep stale
    values unless we re-read the mounted file with override=True.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    docker_env = Path("/app/.env")
    if docker_env.is_file():
        load_dotenv(docker_env, override=True)
        return
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        load_dotenv(cwd_env, override=False)


class DingTalkConfig(BaseModel):
    app_key: str = ""
    app_secret: str = ""
    robot_code: str = ""
    group_open_conversation_id: str = ""
    group_title: str = ""  # 群名称（展示用；绑定时自动写入）
    chat_id: str = ""  # 钉钉群号（展示用；API 需 openConversationId）
    sync_root_dept_id: int = 1


class CollectionConfig(BaseModel):
    period_format: str = "%Y-%m"
    start_day: int = 1
    start_time: str = "09:00"
    deadline_day: int = 3
    deadline_time: str = "18:00"
    daily_check_time: str = "10:00"
    report_day: int = 4
    report_time: str = "09:30"
    report_period_mode: str = "previous"  # previous | current
    report_on_first_business_day: bool = True
    readiness_deadline_time: str = "09:25"
    timezone: str = "Asia/Shanghai"
    reminders_enabled: bool = False
    publish_report_to_group: bool = False


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


class AssistantLlmSettings(BaseModel):
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    api_key: str = ""
    memory_enabled: bool = True


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    admin_token: str = ""
    admin_password: str = ""
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


class CursorSyncConfig(BaseModel):
    enabled: bool = True
    tick_interval_minutes: int = 2
    batch_size: int = 3
    pre_publish_batch_size: int = 5
    default_interval_minutes: int = 1440
    month_close_interval_minutes: int = 360
    default_interval_hours: int | None = None
    month_close_interval_hours: int | None = None
    max_retry_count: int = 8
    pre_publish_start_time: str = "08:00"
    readiness_sync_max_age_hours: int = 6
    # On-Demand Spending：同步时强制关闭 + 通知
    enforce_on_demand_disabled: bool = True
    # None = 未配置（回落管理员）；[] = 明确不通知名单中的人
    on_demand_notify_member_ids: list[str] | None = None
    on_demand_notify_primary: bool = True
    # GetHardLimit 失败（接口变更等）时单独通知 admin.dingtalk_user_ids
    on_demand_notify_admins_on_api_failure: bool = True

    @classmethod
    def model_validate(cls, obj, **kwargs):
        if isinstance(obj, dict):
            data = dict(obj)
            if "default_interval_minutes" not in data and data.get("default_interval_hours") is not None:
                data["default_interval_minutes"] = int(data["default_interval_hours"]) * 60
            if "month_close_interval_minutes" not in data and data.get("month_close_interval_hours") is not None:
                data["month_close_interval_minutes"] = int(data["month_close_interval_hours"]) * 60
            obj = data
        return super().model_validate(obj, **kwargs)


class LoanSelectionConfig(BaseModel):
    """Key 借用出借账号选择参数（打分权重与硬上限）。"""

    max_active_loans_per_account: int = Field(default=2, ge=1)
    min_coverage_hours: float = Field(default=1.0, ge=0)
    freshness_full_penalty_hours: float = Field(default=24.0, ge=0)
    weight_urgency: float = Field(default=0.50, ge=0)
    weight_surplus: float = Field(default=0.25, ge=0)
    weight_load: float = Field(default=0.15, ge=0)
    weight_freshness: float = Field(default=0.10, ge=0)


class ToolCenterConfig(BaseModel):
    loan_selection: LoanSelectionConfig = Field(default_factory=LoanSelectionConfig)


EVOLUTION_DAY_DAILY = -1
EVOLUTION_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def format_memory_evolution_cron(day_of_week: int, time: str) -> str:
    if day_of_week < 0:
        return f"每天 {time}"
    return f"{EVOLUTION_WEEKDAY_LABELS[day_of_week % 7]} {time}"


class MemoryConfig(BaseModel):
    evolution_enabled: bool = True
    evolution_day_of_week: int = Field(
        default=6,
        ge=EVOLUTION_DAY_DAILY,
        le=6,
        description="-1=每天，0-6=周一至周日",
    )
    evolution_time: str = "02:00"
    retrieval_top_k: int = 8
    evolution_min_confidence: float = 0.7
    evolution_auto_execute: bool = True
    embedding_enabled: bool = True
    embedding_model: str = "text-embedding-3-small"
    conversation_turn_limit: int = 10
    conversation_keep: int = 20


class AssistantMirrorConfig(BaseModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8090"
    service_token: str = ""
    timeout_seconds: float = 2.0
    fail_open: bool = True  # 镜像失败不阻塞文件/图本地处理


class CapabilityBridgeConfig(BaseModel):
    quota_self_read: bool = False
    cursor_key_bind: bool = False
    guide_image_update: bool = False


class InternalApiConfig(BaseModel):
    service_token: str = ""


class ProxyConfig(BaseModel):
    """客户端可达的代理公网/内网地址（写入一键复制命令）。"""

    public_url: str = "http://127.0.0.1:8317"


class WebSearchConfig(BaseModel):
    """Pulse-layer web search / fetch settings. API keys never leave this layer."""

    enabled: bool = False
    provider: str = "tavily"
    api_key: str = ""
    search_url: str = "https://api.tavily.com/search"
    timeout_seconds: float = 10.0
    max_results: int = 5
    fetch_max_bytes: int = 1_048_576
    fetch_max_redirects: int = 5
    rate_limit_per_minute: int = 30


class AppConfig(BaseModel):
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    object_storage: ObjectStorageConfig = Field(default_factory=ObjectStorageConfig)
    bot: BotPlatformConfig = Field(default_factory=BotPlatformConfig)
    cursor_teams: CursorTeamsConfig = Field(default_factory=CursorTeamsConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    assistant_llm: AssistantLlmSettings = Field(default_factory=AssistantLlmSettings)
    web: WebConfig = Field(default_factory=WebConfig)
    tenant: TenantConfig = Field(default_factory=TenantConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    credentials: CredentialConfig = Field(default_factory=CredentialConfig)
    cursor_sync: CursorSyncConfig = Field(default_factory=CursorSyncConfig)
    tool_center: ToolCenterConfig = Field(default_factory=ToolCenterConfig)
    assistant_mirror: AssistantMirrorConfig = Field(default_factory=AssistantMirrorConfig)
    capability_bridge: CapabilityBridgeConfig = Field(default_factory=CapabilityBridgeConfig)
    internal: InternalApiConfig = Field(default_factory=InternalApiConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)


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
    assistant_llm_enabled: str = ""
    assistant_llm_api_key: str = ""
    assistant_llm_base_url: str = ""
    assistant_llm_model: str = ""
    assistant_memory_enabled: str = ""
    admin_web_token: str = ""
    admin_password: str = ""
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
    usage_reminders_enabled: str = ""
    report_publish_to_group: str = ""
    pulse_credential_encryption_key: str = ""
    assistant_mirror_enabled: str = ""
    assistant_mirror_base_url: str = ""
    assistant_service_token: str = ""
    pulse_internal_service_token: str = ""
    proxy_public_url: str = ""
    capability_bridge_quota_self_read: str = ""
    capability_bridge_cursor_key_bind: str = ""
    capability_bridge_guide_image_update: str = ""
    tavily_api_key: str = ""
    tavily_search_url: str = ""
    web_search_enabled: str = ""
    web_search_timeout_seconds: str = ""
    web_search_max_results: str = ""

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
    _bootstrap_dotenv()
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
    if env.assistant_llm_api_key:
        cfg.assistant_llm.api_key = env.assistant_llm_api_key
    if env.assistant_llm_base_url:
        cfg.assistant_llm.base_url = env.assistant_llm_base_url
    if env.assistant_llm_model:
        cfg.assistant_llm.model = env.assistant_llm_model
    if env.assistant_llm_enabled.lower() in ("1", "true", "yes", "on"):
        cfg.assistant_llm.enabled = True
    if env.assistant_memory_enabled.lower() in ("1", "true", "yes", "on"):
        cfg.assistant_llm.memory_enabled = True
    elif env.assistant_memory_enabled.lower() in ("0", "false", "no", "off"):
        cfg.assistant_llm.memory_enabled = False
    if env.admin_web_token:
        cfg.web.admin_token = env.admin_web_token
    if env.admin_password:
        cfg.web.admin_password = env.admin_password
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
    if env.usage_reminders_enabled.lower() in ("1", "true", "yes", "on"):
        cfg.collection.reminders_enabled = True
    elif env.usage_reminders_enabled.lower() in ("0", "false", "no", "off"):
        cfg.collection.reminders_enabled = False
    if env.report_publish_to_group.lower() in ("1", "true", "yes", "on"):
        cfg.collection.publish_report_to_group = True
    elif env.report_publish_to_group.lower() in ("0", "false", "no", "off"):
        cfg.collection.publish_report_to_group = False

    cfg.credentials.encryption_key = env.pulse_credential_encryption_key

    if env.assistant_mirror_enabled:
        if env.assistant_mirror_enabled.lower() in ("1", "true", "yes", "on"):
            cfg.assistant_mirror.enabled = True
        elif env.assistant_mirror_enabled.lower() in ("0", "false", "no", "off"):
            cfg.assistant_mirror.enabled = False
    if env.assistant_mirror_base_url:
        cfg.assistant_mirror.base_url = env.assistant_mirror_base_url.rstrip("/")
    if env.assistant_service_token:
        cfg.assistant_mirror.service_token = env.assistant_service_token
    if env.pulse_internal_service_token:
        cfg.internal.service_token = env.pulse_internal_service_token
    if env.proxy_public_url.strip():
        cfg.proxy.public_url = env.proxy_public_url.strip().rstrip("/")
    if env.capability_bridge_quota_self_read.lower() in ("1", "true", "yes", "on"):
        cfg.capability_bridge.quota_self_read = True
    elif env.capability_bridge_quota_self_read.lower() in ("0", "false", "no", "off"):
        cfg.capability_bridge.quota_self_read = False
    if env.capability_bridge_cursor_key_bind.lower() in ("1", "true", "yes", "on"):
        cfg.capability_bridge.cursor_key_bind = True
    elif env.capability_bridge_cursor_key_bind.lower() in ("0", "false", "no", "off"):
        cfg.capability_bridge.cursor_key_bind = False
    if env.capability_bridge_guide_image_update.lower() in ("1", "true", "yes", "on"):
        cfg.capability_bridge.guide_image_update = True
    elif env.capability_bridge_guide_image_update.lower() in ("0", "false", "no", "off"):
        cfg.capability_bridge.guide_image_update = False

    if env.tavily_api_key:
        cfg.web_search.api_key = env.tavily_api_key
    if env.tavily_search_url:
        cfg.web_search.search_url = env.tavily_search_url.rstrip("/")
    if env.web_search_enabled.lower() in ("1", "true", "yes", "on"):
        cfg.web_search.enabled = True
    elif env.web_search_enabled.lower() in ("0", "false", "no", "off"):
        cfg.web_search.enabled = False
    if env.web_search_timeout_seconds:
        cfg.web_search.timeout_seconds = float(env.web_search_timeout_seconds)
    if env.web_search_max_results:
        cfg.web_search.max_results = int(env.web_search_max_results)
    # Enable when a key is present unless explicitly disabled.
    if cfg.web_search.api_key and env.web_search_enabled == "":
        cfg.web_search.enabled = True

    return apply_team_dingtalk_overrides(cfg)


_DINGTALK_TEAM_SETTING_KEYS = (
    "app_key",
    "app_secret",
    "robot_code",
    "group_open_conversation_id",
    "group_title",
    "chat_id",
    "sync_root_dept_id",
)


def apply_team_dingtalk_overrides(cfg: AppConfig) -> AppConfig:
    """合并 team_settings.dingtalk；优先级高于 config.yaml / .env。"""
    try:
        from pulse.team_settings_loader import read_team_setting_section

        overrides = read_team_setting_section(
            team_slug=cfg.tenant.slug,
            section="dingtalk",
            database_url=cfg.storage.database_url,
        )
    except Exception:
        return cfg
    if not overrides:
        return cfg

    dingtalk = cfg.dingtalk.model_copy(deep=True)
    for key in _DINGTALK_TEAM_SETTING_KEYS:
        if key not in overrides:
            continue
        value = overrides[key]
        if value is None or value == "":
            continue
        if key == "sync_root_dept_id":
            dingtalk.sync_root_dept_id = int(value)
        else:
            setattr(dingtalk, key, str(value))
    return cfg.model_copy(update={"dingtalk": dingtalk})
