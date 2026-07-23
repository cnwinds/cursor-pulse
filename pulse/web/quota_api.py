from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.proxy import service as proxy_service
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccount,
    AiAccountCredential,
    KeyLoan,
    Member,
    ProxyKeyUsage,
)
from pulse.tool_center.burn_rate import analyze_burn_rate, recommend_lenders
from pulse.tool_center.key_loans import (
    KeyLoanError,
    KeyLoanService,
    build_lender_candidates,
    issue_loan_key,
    loan_payload,
    reveal_loan_cursor_key,
    reveal_loan_user_key,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.audit import log_admin_action
from pulse.web.deps import PortalUser


class LoanKeyBody(BaseModel):
    borrower_member_id: str
    note: str | None = None
    auto_revoke_on_reset: bool = True
    key_name: str | None = None
    delivery_mode: Literal["proxy_alias", "cursor_direct"] = "proxy_alias"


def _encryption_key(config) -> str:
    key = (config.credentials.encryption_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="未配置凭证加密密钥（PULSE_CREDENTIAL_ENCRYPTION_KEY）",
        )
    return key


def _latest_snapshots_by_account(session: Session, team_id: str) -> dict[str, AccountQuotaSnapshot]:
    accounts = session.scalars(
        select(AiAccount.id).where(
            AiAccount.team_id == team_id,
            AiAccount.deleted_at.is_(None),
        )
    ).all()
    if not accounts:
        return {}
    account_ids = list(accounts)
    snapshots = session.scalars(
        select(AccountQuotaSnapshot)
        .where(AccountQuotaSnapshot.account_id.in_(account_ids))
        .order_by(AccountQuotaSnapshot.captured_at.desc())
    ).all()
    latest: dict[str, AccountQuotaSnapshot] = {}
    for snap in snapshots:
        if snap.account_id not in latest:
            latest[snap.account_id] = snap
    return latest


def _board_item(
    account: AiAccount,
    snapshot: AccountQuotaSnapshot | None,
    today: date,
    *,
    member_names: dict[str, str] | None = None,
) -> dict:
    primary_member_name = None
    if account.primary_member_id and member_names:
        primary_member_name = member_names.get(account.primary_member_id)
    base = {
        "account_id": account.id,
        "account_identifier": account.account_identifier,
        "primary_member_id": account.primary_member_id,
        "primary_member_name": primary_member_name,
        "vendor_name": account.vendor.name if account.vendor else None,
        "plan_name": account.plan.plan_name if account.plan else None,
        "usage_resets_on": account.usage_resets_on.isoformat() if account.usage_resets_on else None,
        "resets_on_source": account.resets_on_source,
        "has_snapshot": snapshot is not None,
    }
    if not snapshot:
        return {
            **base,
            "status": "unknown",
            "cycle_start": None,
            "cycle_end": None,
            "remaining_headroom_pct": None,
            "api_limit_usd": None,
            "quota_progress": None,
            "projected_exhaustion_date": None,
            "exhausts_before_reset": None,
            "days_until_reset": None,
            "total_pct": None,
            "auto_pct": None,
            "api_pct": None,
        }
    analysis = analyze_burn_rate(snapshot, today)
    return {
        **base,
        "status": analysis.status,
        "cycle_start": snapshot.cycle_start.isoformat(),
        "cycle_end": snapshot.cycle_end.isoformat(),
        "remaining_headroom_pct": analysis.remaining_headroom_pct,
        "api_limit_usd": analysis.api_limit_usd,
        "quota_progress": analysis.quota_progress,
        "projected_exhaustion_date": (
            analysis.projected_exhaustion_date.isoformat()
            if analysis.projected_exhaustion_date
            else None
        ),
        "exhausts_before_reset": analysis.exhausts_before_reset,
        "days_until_reset": analysis.days_until_reset,
        "total_pct": snapshot.total_pct,
        "auto_pct": snapshot.auto_pct,
        "api_pct": snapshot.api_pct,
        "captured_at": snapshot.captured_at.isoformat(),
    }


def _status_rank(status: str) -> int:
    return {"exhausted": 0, "warning": 1, "healthy": 2, "unknown": 3}.get(status, 4)


def register_quota_routes(app, get_db, require_capability, team_repo_fn, config):
    @app.get(
        "/api/v2/quota-board",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def quota_board(session: Session = Depends(get_db)):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        today = date.today()
        snapshots = _latest_snapshots_by_account(session, team.id)
        accounts = [
            account
            for account in repo.list_active_accounts()
            if account.vendor and account.vendor.slug == "cursor"
        ]
        member_ids = {a.primary_member_id for a in accounts if a.primary_member_id}
        member_names: dict[str, str] = {}
        if member_ids:
            members = session.scalars(
                select(Member).where(Member.id.in_(member_ids))
            ).all()
            member_names = {m.id: m.display_name for m in members}
        items = []
        for account in accounts:
            snapshot = snapshots.get(account.id)
            items.append(_board_item(account, snapshot, today, member_names=member_names))
        items.sort(key=lambda x: (_status_rank(x["status"]), -(x.get("quota_progress") or 0)))
        return items

    @app.get(
        "/api/v2/quota-board/recommend",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def quota_recommend(session: Session = Depends(get_db)):
        team, _ = team_repo_fn(session)
        today = date.today()
        candidates = build_lender_candidates(session, team.id)
        return recommend_lenders(
            candidates, today, loan_selection=config.tool_center.loan_selection
        )

    @app.get(
        "/api/v2/loans",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def list_loans(
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        base = (
            select(KeyLoan)
            .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
            .where(AiAccount.team_id == team.id)
        )
        if status:
            base = base.where(KeyLoan.status == status)

        total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
        active_count = session.scalar(
            select(func.count())
            .select_from(KeyLoan)
            .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
            .where(AiAccount.team_id == team.id, KeyLoan.status == "active")
        ) or 0
        loans = session.scalars(
            base.order_by(KeyLoan.created_at.desc()).offset(offset).limit(limit)
        ).all()
        return {
            "items": [loan_payload(loan, session) for loan in loans],
            "total": total,
            "limit": limit,
            "offset": offset,
            "active_count": active_count,
        }

    @app.post(
        "/api/v2/accounts/{account_id}/loan-key",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def loan_key(
        account_id: str,
        body: LoanKeyBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        account = repo.get_account(account_id)
        if not account or account.team_id != team.id:
            raise HTTPException(status_code=404, detail="账号不存在")
        if not account.vendor or account.vendor.slug != "cursor":
            raise HTTPException(status_code=400, detail="仅 Cursor 账号支持 Key 调配")

        borrower = session.get(Member, body.borrower_member_id)
        if not borrower or borrower.team_id != team.id:
            raise HTTPException(status_code=400, detail="借用人不存在")

        enc_key = _encryption_key(config)
        try:
            result = issue_loan_key(
                session,
                enc_key,
                team_id=team.id,
                source_account_id=account_id,
                borrower_member_id=body.borrower_member_id,
                bound_by_member_id=user.member.id,
                note=body.note,
                auto_revoke_on_reset=body.auto_revoke_on_reset,
                key_name=body.key_name,
                delivery_mode=body.delivery_mode,
                loan_selection=config.tool_center.loan_selection,
            )
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="quota.loan_key",
                capability="accounts:write",
                detail=f"{account_id}->{borrower.display_name}",
            )
            session.commit()
            return result
        except KeyLoanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v2/loans/{loan_id}/usages",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def loan_usages(
        loan_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        loan = session.scalar(
            select(KeyLoan)
            .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
            .where(KeyLoan.id == loan_id, AiAccount.team_id == team.id)
        )
        if not loan:
            raise HTTPException(status_code=404, detail="借用记录不存在")

        payload = loan_payload(loan, session)
        rows = (
            session.execute(
                select(ProxyKeyUsage)
                .where(ProxyKeyUsage.loan_id == loan_id)
                .order_by(ProxyKeyUsage.ts.desc())
            )
            .scalars()
            .all()
        )
        proxy_tokens, proxy_cost = proxy_service.loan_proxy_totals(session, loan_id)
        by_model_map: dict[str, dict] = {}
        for u in rows:
            label = (u.model or "").strip() or "（未知）"
            bucket = by_model_map.get(label)
            if bucket is None:
                bucket = {
                    "model": label,
                    "request_count": 0,
                    "total_tokens": 0,
                    "cost_cents": 0,
                }
                by_model_map[label] = bucket
            bucket["request_count"] += 1
            bucket["total_tokens"] += int(u.total_tokens or 0)
            bucket["cost_cents"] += int(u.cost_cents or 0)
        by_model = sorted(
            by_model_map.values(),
            key=lambda r: r["cost_cents"],
            reverse=True,
        )
        items = [
            {
                "id": u.id,
                "model": u.model,
                "tokens_input": u.tokens_input,
                "tokens_output": u.tokens_output,
                "tokens_cache_read": u.tokens_cache_read,
                "tokens_cache_write": u.tokens_cache_write,
                "tokens_reasoning": u.tokens_reasoning,
                "total_tokens": u.total_tokens,
                "cost_cents": u.cost_cents,
                "ts": u.ts.isoformat() if u.ts else None,
            }
            for u in rows[:limit]
        ]
        return {
            "summary": {
                "borrowed_cents": payload["borrowed_cents"],
                "proxy_cost_cents": proxy_cost,
                "proxy_total_tokens": proxy_tokens,
                "request_count": len(rows),
            },
            "by_model": by_model,
            "items": items,
        }

    @app.get(
        "/api/v2/loans/{loan_id}/client-setup",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def loan_client_setup(
        loan_id: str,
        shell: str = Query(default="powershell", pattern="^(bash|powershell)$"),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        loan = session.scalar(
            select(KeyLoan)
            .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
            .where(KeyLoan.id == loan_id, AiAccount.team_id == team.id)
        )
        if not loan:
            raise HTTPException(status_code=404, detail="借用记录不存在")
        if loan.status != "active":
            raise HTTPException(status_code=410, detail="借用已结束，无法获取代理命令")

        enc_key = _encryption_key(config)
        try:
            plaintext = reveal_loan_user_key(loan, enc_key, session)
        except KeyLoanError as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc

        proxy_url = (config.proxy.public_url or "http://127.0.0.1:8317").rstrip("/")
        command = proxy_service.build_client_command(
            shell=shell, proxy_url=proxy_url, plaintext_key=plaintext
        )
        return {
            "plaintext_key": plaintext,
            "delivery_mode": getattr(loan, "delivery_mode", None) or "cursor_direct",
            "proxy_url": proxy_url,
            "shell": shell,
            "command": command,
        }

    @app.get(
        "/api/v2/loans/{loan_id}/cursor-key",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def loan_cursor_key(
        loan_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        """管理员应急查看底层 Cursor Key（借用人不可见）。"""
        team, _ = team_repo_fn(session)
        loan = session.scalar(
            select(KeyLoan)
            .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
            .where(KeyLoan.id == loan_id, AiAccount.team_id == team.id)
        )
        if not loan:
            raise HTTPException(status_code=404, detail="借用记录不存在")
        if loan.status != "active":
            raise HTTPException(status_code=410, detail="借用已结束，无法查看底层 Key")

        enc_key = _encryption_key(config)
        try:
            cursor_key = reveal_loan_cursor_key(loan, enc_key, session)
        except KeyLoanError as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc

        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="quota.reveal_loan_cursor_key",
            capability="accounts:write",
            detail=loan_id,
        )
        session.commit()
        cred = session.get(AiAccountCredential, loan.credential_id)
        return {
            "loan_id": loan.id,
            "delivery_mode": getattr(loan, "delivery_mode", None) or "cursor_direct",
            "cursor_api_key": cursor_key,
            "key_hint": cred.key_hint if cred else None,
        }

    @app.post(
        "/api/v2/loans/{loan_id}/revoke",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def revoke_loan(
        loan_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        loan = session.scalar(
            select(KeyLoan)
            .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
            .where(KeyLoan.id == loan_id, AiAccount.team_id == team.id)
        )
        if not loan:
            raise HTTPException(status_code=404, detail="借用记录不存在")

        enc_key = _encryption_key(config)
        loan_svc = KeyLoanService(session, enc_key)
        try:
            loan, borrowed_cents = loan_svc.revoke_loan(loan_id, revoke_remote=True)
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="quota.revoke_loan",
                capability="accounts:write",
                detail=loan_id,
            )
            session.commit()
            payload = loan_payload(loan, session)
            payload["borrowed_cents"] = borrowed_cents
            payload["borrowed_usd"] = round(borrowed_cents / 100.0, 2)
            payload["attribution_note"] = "借用消耗为账号用量差值近似，非精确按 Key 统计"
            return payload
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
