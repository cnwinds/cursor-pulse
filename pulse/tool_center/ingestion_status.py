from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from pulse.storage.models import (
    AiAccount,
    AiAccountCredential,
    Member,
    UsageIngestion,
    UsageRecord,
    UsageSummary,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.permissions import has_permission

SOURCE_TYPE_LABELS: dict[str, str] = {
    "manual_csv": "CSV 导出文件",
    "manual_vision": "控制台截图",
    "manual_text": "手工录入数值",
    "api_sync": "API 自动同步",
}

SUBMIT_METHOD_LABELS: dict[str, str] = {
    "csv": "CSV 导出文件",
    "csv_export": "CSV 导出文件",
    "screenshot": "控制台截图",
    "manual": "手工录入数值",
    "text": "文本粘贴",
    "api": "API 自动同步",
    "api_key": "API Key 自动同步",
}

_ACCOUNT_ACTIVE_STATUSES = frozenset({"trial", "shared", "dedicated"})
_SYNC_STALE_HOURS = 36


def period_date_range(period: str) -> tuple[date, date]:
    year, month = map(int, period.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_account_ingestion_status(
    account: AiAccount,
    period: str,
    credential: AiAccountCredential | None,
    summary: UsageSummary | None,
    pending_ingestion: UsageIngestion | None,
) -> str:
    if account.vendor and account.vendor.slug == "cursor":
        if not credential or credential.status != "active":
            return "no_credential"
        if credential.last_sync_status == "failed":
            return "sync_failed"
        if credential.last_sync_at and credential.last_sync_at < _utcnow() - timedelta(
            hours=_SYNC_STALE_HOURS
        ):
            return "sync_stale"
        return "synced"
    if pending_ingestion:
        return "manual_pending"
    if summary:
        return "manual_submitted"
    return "unsubmitted"


def _format_methods_label(methods: list[str]) -> str:
    if not methods:
        return "按厂家要求提交用量"
    labels = [SUBMIT_METHOD_LABELS.get(m, m) for m in methods]
    return "、".join(labels)


def _assess_date_compliance(period: str, date_min: date | None, date_max: date | None) -> list[str]:
    if date_min is None or date_max is None:
        return []
    expected_start, expected_end = period_date_range(period)
    issues: list[str] = []
    if date_min > expected_start:
        issues.append(f"数据起始日 {date_min} 晚于账期初 {expected_start}")
    if date_max < expected_end:
        issues.append(f"数据截止日 {date_max} 早于账期末 {expected_end}")
    return issues


def _ingestion_date_range(session: Session, ingestion_id: str) -> tuple[date | None, date | None]:
    rows = session.scalars(
        select(UsageRecord.event_date).where(UsageRecord.ingestion_id == ingestion_id)
    ).all()
    if not rows:
        return None, None
    return min(rows), max(rows)


def _avg_confidence(session: Session, ingestion_id: str) -> float | None:
    value = session.scalar(
        select(func.avg(UsageRecord.extraction_confidence)).where(
            UsageRecord.ingestion_id == ingestion_id
        )
    )
    if value is None:
        return None
    return round(float(value), 4)


def _member_name_map(session: Session, team_id: str) -> dict[str, str]:
    members = session.scalars(select(Member).where(Member.team_id == team_id)).all()
    return {m.id: m.display_name for m in members}


def _visible_accounts(
    accounts: list[AiAccount],
    *,
    viewer_member_id: str,
    see_all: bool,
) -> list[AiAccount]:
    if see_all:
        return accounts
    visible: list[AiAccount] = []
    for account in accounts:
        if account.primary_member_id == viewer_member_id:
            visible.append(account)
            continue
        secondary_ids = {m.member_id for m in account.secondary_members}
        if viewer_member_id in secondary_ids:
            visible.append(account)
    return visible


def _latest_ingestion(
    session: Session,
    account_id: str,
    period: str,
) -> UsageIngestion | None:
    return session.scalar(
        select(UsageIngestion)
        .where(
            UsageIngestion.account_id == account_id,
            UsageIngestion.billing_period == period,
        )
        .order_by(UsageIngestion.ingested_at.desc())
    )


def _source_type_to_input_type(source_type: str) -> str:
    mapping = {
        "manual_csv": "csv",
        "manual_vision": "screenshot",
        "manual_text": "manual",
        "api_sync": "api",
    }
    return mapping.get(source_type, source_type)


def _ingestion_payload(
    session: Session,
    ingestion: UsageIngestion,
    *,
    member_names: dict[str, str],
    usage_summary: UsageSummary | None = None,
) -> dict[str, Any]:
    date_min, date_max = _ingestion_date_range(session, ingestion.id)
    confidence = _avg_confidence(session, ingestion.id)
    return {
        "id": ingestion.id,
        "id_prefix": ingestion.id[:8],
        "status": ingestion.status,
        "source_type": ingestion.source_type,
        "input_type": _source_type_to_input_type(ingestion.source_type),
        "ingested_at": ingestion.ingested_at.isoformat(),
        "submitted_at": ingestion.ingested_at.isoformat(),
        "submitted_by_member_id": ingestion.member_id,
        "submitted_by_name": member_names.get(ingestion.member_id or ""),
        "data_date_min": date_min.isoformat() if date_min else None,
        "data_date_max": date_max.isoformat() if date_max else None,
        "extraction_confidence": confidence,
        "primary_metric_value": (
            float(usage_summary.primary_metric_value) if usage_summary else None
        ),
        "primary_metric_unit": usage_summary.primary_metric_unit if usage_summary else None,
        "quota_usage_ratio": usage_summary.quota_usage_ratio if usage_summary else None,
    }


def _credential_payload(credential: AiAccountCredential | None) -> dict[str, Any] | None:
    if not credential:
        return None
    return {
        "status": credential.status,
        "key_hint": credential.key_hint,
        "bound_at": credential.bound_at.isoformat() if credential.bound_at else None,
        "last_sync_at": credential.last_sync_at.isoformat() if credential.last_sync_at else None,
        "last_sync_status": credential.last_sync_status,
        "last_sync_error": credential.last_sync_error,
        "sync_enabled": credential.sync_enabled,
    }


def build_account_ingestion_status(
    session: Session,
    tool_repo: ToolCenterRepository,
    account: AiAccount,
    period: str,
    *,
    member_names: dict[str, str],
    usage_summary: UsageSummary | None,
    latest_ingestion: UsageIngestion | None,
    credential: AiAccountCredential | None = None,
) -> dict[str, Any]:
    period_start, period_end = period_date_range(period)
    plan = account.plan
    methods = (plan.usage_submit_methods or []) if plan else []
    expected = {
        "submit_methods": methods,
        "submit_methods_label": _format_methods_label(methods),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "period_range_label": f"{period_start} ~ {period_end}",
        "hint": (
            f"请提交覆盖 {period_start} ~ {period_end} 的用量数据"
            f"（{_format_methods_label(methods)}）"
        ),
    }

    primary_name = (
        member_names.get(account.primary_member_id) if account.primary_member_id else None
    )
    secondary_names = [
        member_names.get(m.member_id, m.member_id)
        for m in account.secondary_members
        if member_names.get(m.member_id)
    ]

    base: dict[str, Any] = {
        "account_id": account.id,
        "account_identifier": account.account_identifier,
        "vendor_id": account.vendor_id,
        "vendor_name": account.vendor.name if account.vendor else None,
        "vendor_slug": account.vendor.slug if account.vendor else None,
        "plan_name": plan.plan_name if plan else None,
        "ownership": account.ownership,
        "account_status": account.status,
        "primary_member_id": account.primary_member_id,
        "primary_member_name": primary_name,
        "secondary_member_names": secondary_names,
        "expected": expected,
        "ingestion": None,
        "submission": None,
        "credential": _credential_payload(credential),
        "issues": [],
        "action_hint": "",
    }

    if not account.primary_member_id:
        base["ingestion_state"] = "missing_primary"
        base["submission_state"] = "missing_primary"
        base["action_hint"] = "待管理员指派主使用人或代为提交"
        return base

    pending = latest_ingestion if latest_ingestion and latest_ingestion.status == "pending_review" else None
    state = resolve_account_ingestion_status(
        account, period, credential, usage_summary, pending
    )
    is_cursor = bool(account.vendor and account.vendor.slug == "cursor")

    if is_cursor:
        base["ingestion_state"] = state
        base["submission_state"] = state
        if state == "no_credential":
            base["action_hint"] = f"待主使用人 {primary_name} 绑定 API Key"
        elif state == "sync_failed":
            base["action_hint"] = "用量同步失败，请检查 API Key 是否有效"
        elif state == "sync_stale":
            base["action_hint"] = "同步滞后超过 36 小时，请联系管理员"
        else:
            base["action_hint"] = "已自动同步"
        if usage_summary:
            ingestion = (
                session.get(UsageIngestion, usage_summary.latest_ingestion_id)
                if usage_summary.latest_ingestion_id
                else latest_ingestion
            )
            if ingestion:
                payload = _ingestion_payload(
                    session, ingestion, member_names=member_names, usage_summary=usage_summary
                )
                base["ingestion"] = payload
                base["submission"] = payload
        return base

    if state == "manual_pending" and pending:
        date_min, date_max = _ingestion_date_range(session, pending.id)
        confidence = _avg_confidence(session, pending.id)
        payload = _ingestion_payload(session, pending, member_names=member_names)
        base["ingestion_state"] = "manual_pending"
        base["submission_state"] = "pending_review"
        base["ingestion"] = payload
        base["submission"] = payload
        base["issues"] = _assess_date_compliance(period, date_min, date_max)
        if confidence is not None and confidence < 0.85:
            base["issues"].append(f"截图识别置信度偏低（{confidence:.0%}）")
        base["action_hint"] = "已提交（历史待审记录，新提交直接入库）"
        return base

    if state == "manual_submitted" and usage_summary:
        ingestion = (
            session.get(UsageIngestion, usage_summary.latest_ingestion_id)
            if usage_summary.latest_ingestion_id
            else latest_ingestion
        )
        date_min, date_max = (
            _ingestion_date_range(session, ingestion.id) if ingestion else (None, None)
        )
        issues = _assess_date_compliance(period, date_min, date_max)
        if ingestion and ingestion.member_id != account.primary_member_id:
            submitter = member_names.get(ingestion.member_id or "", "他人")
            issues.append(f"提交人 {submitter} 非台账主使用人 {primary_name}")
        if ingestion and ingestion.source_type != "manual_text":
            if date_min is None:
                issues.append("提交数据缺少可校验的日期范围")
        base["ingestion_state"] = "manual_submitted"
        base["submission_state"] = "submitted_warning" if issues else "submitted_ok"
        payload = (
            _ingestion_payload(
                session,
                ingestion,
                member_names=member_names,
                usage_summary=usage_summary,
            )
            if ingestion
            else {
                "id": None,
                "id_prefix": None,
                "status": "confirmed",
                "source_type": None,
                "input_type": None,
                "ingested_at": None,
                "submitted_at": None,
                "submitted_by_member_id": usage_summary.submitted_by_member_id,
                "submitted_by_name": member_names.get(usage_summary.submitted_by_member_id),
                "data_date_min": date_min.isoformat() if date_min else None,
                "data_date_max": date_max.isoformat() if date_max else None,
                "extraction_confidence": None,
                "primary_metric_value": float(usage_summary.primary_metric_value),
                "primary_metric_unit": usage_summary.primary_metric_unit,
                "quota_usage_ratio": usage_summary.quota_usage_ratio,
            }
        )
        base["ingestion"] = payload
        base["submission"] = payload
        base["issues"] = issues
        base["action_hint"] = "已完成" if not issues else "已提交但需核对"
        return base

    base["ingestion_state"] = "unsubmitted"
    base["submission_state"] = "not_submitted"
    base["action_hint"] = f"待主使用人 {primary_name} 提交"
    return base


build_account_submission_status = build_account_ingestion_status


def _credential_map(session: Session, account_ids: list[str]) -> dict[str, AiAccountCredential]:
    if not account_ids:
        return {}
    rows = session.scalars(
        select(AiAccountCredential).where(AiAccountCredential.account_id.in_(account_ids))
    ).all()
    return {row.account_id: row for row in rows}


def build_ingestion_status_payload(
    session: Session,
    team_id: str,
    period: str,
    viewer: Member,
) -> dict[str, Any]:
    tool_repo = ToolCenterRepository(session, team_id)
    see_all = has_permission(viewer, "accounts:read")
    member_names = _member_name_map(session, team_id)

    active_accounts = list(
        session.scalars(
            select(AiAccount)
            .options(
                joinedload(AiAccount.plan),
                joinedload(AiAccount.vendor),
                joinedload(AiAccount.secondary_members),
            )
            .where(
                AiAccount.team_id == team_id,
                AiAccount.status.in_(_ACCOUNT_ACTIVE_STATUSES),
            )
            .order_by(AiAccount.account_identifier)
        ).unique()
    )
    visible = _visible_accounts(
        active_accounts,
        viewer_member_id=viewer.id,
        see_all=see_all,
    )

    summaries = {
        row.account_id: row
        for row in session.scalars(
            select(UsageSummary).where(UsageSummary.period == period)
        ).all()
    }
    credentials = _credential_map(session, [a.id for a in visible])

    account_rows: list[dict[str, Any]] = []
    state_counts: dict[str, int] = {
        "missing_primary": 0,
        "no_credential": 0,
        "sync_failed": 0,
        "sync_stale": 0,
        "synced": 0,
        "manual_pending": 0,
        "manual_submitted": 0,
        "unsubmitted": 0,
        "pending_review": 0,
        "submitted_ok": 0,
        "submitted_warning": 0,
        "not_submitted": 0,
    }

    for account in visible:
        row = build_account_ingestion_status(
            session,
            tool_repo,
            account,
            period,
            member_names=member_names,
            usage_summary=summaries.get(account.id),
            latest_ingestion=_latest_ingestion(session, account.id, period),
            credential=credentials.get(account.id),
        )
        account_rows.append(row)
        ingestion_state = row.get("ingestion_state", "")
        submission_state = row.get("submission_state", "")
        state_counts[ingestion_state] = state_counts.get(ingestion_state, 0) + 1
        if submission_state != ingestion_state:
            state_counts[submission_state] = state_counts.get(submission_state, 0) + 1

    period_start, period_end = period_date_range(period)
    cursor_done = {"synced", "sync_stale"}
    manual_done = {"manual_submitted", "manual_pending", "submitted_ok", "submitted_warning", "pending_review"}
    submitted_count = sum(
        1
        for row in account_rows
        if row.get("ingestion_state") in cursor_done
        or row.get("submission_state") in manual_done
        or row.get("ingestion_state") == "manual_submitted"
    )

    groups: dict[str, dict[str, Any]] = {}
    for row in account_rows:
        key = row["primary_member_id"] or "__admin_todo__"
        label = row["primary_member_name"] or "管理员待办"
        if key not in groups:
            groups[key] = {
                "primary_member_id": row["primary_member_id"],
                "primary_member_name": label,
                "accounts": [],
                "total": 0,
                "completed": 0,
            }
        groups[key]["accounts"].append(row)
        groups[key]["total"] += 1
        state = row.get("ingestion_state", "")
        sub_state = row.get("submission_state", "")
        if state in cursor_done or state == "manual_submitted" or sub_state in manual_done:
            groups[key]["completed"] += 1

    group_list = sorted(
        groups.values(),
        key=lambda g: (g["primary_member_id"] is None, g["primary_member_name"] or ""),
    )

    return {
        "period": period,
        "expected_range": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "label": f"{period_start} ~ {period_end}",
        },
        "viewer_scope": "all" if see_all else "self",
        "summary": {
            "total_accounts": len(account_rows),
            "submitted_count": submitted_count,
            **state_counts,
        },
        "accounts": account_rows,
        "groups": group_list,
    }


build_submission_status_payload = build_ingestion_status_payload
