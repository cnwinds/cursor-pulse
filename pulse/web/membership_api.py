from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pulse.proxy.credit import adjust, balance_cents, grant, refund_charge
from pulse.proxy.membership import (
    MembershipError,
    active_membership,
    cancel_membership,
    change_membership,
    open_membership,
)
from pulse.proxy.spend_policy import SpendPolicyConfigError
from pulse.proxy.team_membership import membership_required_for_team
from pulse.storage.models import Member, Membership, MembershipPlan
from pulse.util.datetime_fmt import ensure_aware
from pulse.web.audit import log_admin_action
from pulse.web.deps import PortalUser
from pulse.web.membership_present import (
    credit_summary,
    csv_rows_for_transactions,
    member_membership_row,
    membership_out,
    parse_opening_credit_usd,
    parse_plan_rules,
    parse_usd_amount,
    plan_out,
    preload_member_membership_context,
    query_transactions,
    statement_page,
    transaction_out,
    transactions_out,
)

_CSV_MAX_ROWS = 10000


class SpendRuleBody(BaseModel):
    period: Literal["5h", "week", "month"]
    pool: Literal["auto", "api", "total"]
    cost_usd: int = Field(ge=1)


class PlanCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    rules: list[SpendRuleBody] = Field(default_factory=list)
    credit_mode: Literal["unlimited", "prepaid"]
    opening_credit_usd: float | None = None


class PlanPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    rules: list[SpendRuleBody] | None = None
    credit_mode: Literal["unlimited", "prepaid"] | None = None
    opening_credit_usd: float | None = None
    status: Literal["active", "archived"] | None = None


class MembershipPutBody(BaseModel):
    plan_id: str | None = None
    rules_override: list[SpendRuleBody] | None = None
    credit_mode_override: Literal["unlimited", "prepaid"] | None = None


class GrantBody(BaseModel):
    amount_usd: float = Field(gt=0)
    note: str | None = None


class AdjustBody(BaseModel):
    amount_usd: float
    note: str = Field(min_length=1)


class RefundBody(BaseModel):
    note: str | None = None


def _team_member_or_404(session: Session, team_id: str, member_id: str) -> Member:
    member = session.get(Member, member_id)
    if member is None or member.team_id != team_id:
        raise HTTPException(status_code=404, detail="成员不存在")
    return member


def _plan_in_team_or_404(session: Session, team_id: str, plan_id: str) -> MembershipPlan:
    plan = session.get(MembershipPlan, plan_id)
    if plan is None or plan.team_id != team_id:
        raise HTTPException(status_code=404, detail="套餐不存在")
    return plan


def _parse_iso_datetime(value: str | None, *, field: str) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} 须为 ISO 8601 时间") from exc
    return ensure_aware(dt)


def _apply_membership_put(session: Session, member: Member, body: MembershipPutBody, *, actor_id: str) -> Membership:
    existing = active_membership(session, member.id)
    rules_payload = None
    if body.rules_override is not None:
        rules_payload = [item.model_dump() for item in body.rules_override]

    if existing is None:
        try:
            return open_membership(
                session,
                member_id=member.id,
                plan_id=body.plan_id,
                created_by_member_id=actor_id,
                rules_override=rules_payload,
                credit_mode_override=body.credit_mode_override,
            )
        except MembershipError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    kwargs: dict = {}
    if body.plan_id is None:
        kwargs["clear_plan"] = True
    else:
        kwargs["plan_id"] = body.plan_id
    if body.rules_override is None:
        kwargs["clear_rules_override"] = True
    else:
        kwargs["rules_override"] = rules_payload
    if body.credit_mode_override is None:
        kwargs["clear_credit_mode_override"] = True
    else:
        kwargs["credit_mode_override"] = body.credit_mode_override
    try:
        return change_membership(session, existing, **kwargs)
    except MembershipError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _csv_response(
    session: Session,
    member_id: str,
    *,
    kind: str | None,
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> Response:
    all_rows: list = []
    cursor: int | None = None
    while len(all_rows) < _CSV_MAX_ROWS:
        batch, cursor = query_transactions(
            session,
            member_id,
            cursor=cursor,
            limit=min(200, _CSV_MAX_ROWS - len(all_rows)),
            kind=kind,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        if not batch:
            break
        all_rows.extend(transactions_out(session, batch))
        if cursor is None:
            break
    ymd = datetime.now(UTC).strftime("%Y%m%d")
    filename = f"credit-statement-{member_id}-{ymd}.csv"
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    for row in csv_rows_for_transactions(all_rows):
        writer.writerow(row)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def register_membership_routes(app, get_db, require_capability, team_repo_fn) -> None:
    @app.get(
        "/api/v2/membership-plans",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def list_membership_plans(
        include_archived: bool = Query(default=False),
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        q = select(MembershipPlan).where(MembershipPlan.team_id == team.id)
        if not include_archived:
            q = q.where(MembershipPlan.status == "active")
        plans = session.scalars(q.order_by(MembershipPlan.created_at.desc())).all()
        return {"items": [plan_out(session, plan) for plan in plans]}

    @app.post(
        "/api/v2/membership-plans",
        dependencies=[Depends(require_capability("accounts:write"))],
        status_code=201,
    )
    def create_membership_plan(
        body: PlanCreateBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        try:
            rules = parse_plan_rules([item.model_dump() for item in body.rules])
        except SpendPolicyConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            opening_cents = parse_opening_credit_usd(body.opening_credit_usd)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        plan = MembershipPlan(
            team_id=team.id,
            name=body.name.strip(),
            rules=rules,
            credit_mode=body.credit_mode,
            opening_credit_cents=opening_cents,
        )
        session.add(plan)
        session.flush()
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="membership.plan.create",
            capability="accounts:write",
            detail=f"{plan.id}:{plan.name}",
        )
        session.commit()
        return plan_out(session, plan)

    @app.patch(
        "/api/v2/membership-plans/{plan_id}",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def patch_membership_plan(
        plan_id: str,
        body: PlanPatchBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        plan = _plan_in_team_or_404(session, team.id, plan_id)
        data = body.model_dump(exclude_unset=True)
        if "name" in data and data["name"] is not None:
            plan.name = data["name"].strip()
        if "rules" in data and data["rules"] is not None:
            try:
                plan.rules = parse_plan_rules(data["rules"])
            except SpendPolicyConfigError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if "credit_mode" in data and data["credit_mode"] is not None:
            plan.credit_mode = data["credit_mode"]
        if "opening_credit_usd" in data:
            try:
                plan.opening_credit_cents = parse_opening_credit_usd(data["opening_credit_usd"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if "status" in data and data["status"] is not None:
            plan.status = data["status"]
        plan.updated_at = datetime.now(UTC)
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="membership.plan.update",
            capability="accounts:write",
            detail=plan.id,
        )
        session.commit()
        return plan_out(session, plan)

    @app.get(
        "/api/v2/memberships",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def list_member_memberships(
        q: str | None = Query(default=None),
        scope: Literal["all", "managed", "active", "lapsed", "candidates"] = Query(default="all"),
        session: Session = Depends(get_db),
    ):
        """scope: managed = active ∪ lapsed（有过会员或余额流水的成员不会从管理列表消失）；
        candidates = 当前无会员，可开通。"""
        team, _ = team_repo_fn(session)
        base = select(Member).where(Member.team_id == team.id, Member.status == "active")
        if q and q.strip():
            needle = f"%{q.strip()}%"
            base = base.where(or_(Member.display_name.ilike(needle), Member.channel_user_id.ilike(needle)))
        members = session.scalars(base.order_by(Member.display_name.asc())).all()
        ctx = preload_member_membership_context(session, [m.id for m in members])
        items = []
        for member in members:
            membership = ctx["memberships"].get(member.id)
            is_active = membership is not None
            is_lapsed = member.id in ctx["lapsed"]
            if scope == "active" and not is_active:
                continue
            if scope == "lapsed" and not is_lapsed:
                continue
            if scope == "managed" and not (is_active or is_lapsed):
                continue
            if scope == "candidates" and is_active:
                continue
            items.append(
                member_membership_row(
                    session,
                    member,
                    membership=membership,
                    membership_provided=True,
                    plans=ctx["plans"],
                    accounts=ctx["accounts"],
                )
            )
        return {
            "items": items,
            "membership_required": membership_required_for_team(session, team.id),
        }

    @app.put(
        "/api/v2/members/{member_id}/membership",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def put_member_membership(
        member_id: str,
        body: MembershipPutBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        member = _team_member_or_404(session, team.id, member_id)
        membership = _apply_membership_put(session, member, body, actor_id=user.member.id)
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="membership.open_or_change",
            capability="accounts:write",
            detail=f"{member.id}:{membership.id}",
        )
        session.commit()
        return member_membership_row(session, member, membership=membership)

    @app.post(
        "/api/v2/members/{member_id}/membership/cancel",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def cancel_member_membership(
        member_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        member = _team_member_or_404(session, team.id, member_id)
        membership = active_membership(session, member.id)
        if membership is None:
            raise HTTPException(status_code=404, detail="成员没有有效会员")
        cancel_membership(session, membership)
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="membership.cancel",
            capability="accounts:write",
            detail=member.id,
        )
        session.commit()
        return member_membership_row(session, member, membership=None)

    def _credit_mutation(member_id: str, session: Session, team_id: str) -> Member:
        return _team_member_or_404(session, team_id, member_id)

    @app.post(
        "/api/v2/members/{member_id}/credit/grants",
        dependencies=[Depends(require_capability("accounts:write"))],
        status_code=201,
    )
    def grant_member_credit(
        member_id: str,
        body: GrantBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        member = _credit_mutation(member_id, session, team.id)
        try:
            amount_cents = parse_usd_amount(body.amount_usd, allow_zero=False)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            txn = grant(
                session,
                member.id,
                amount_cents,
                actor_member_id=user.member.id,
                note=(body.note or "").strip() or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="credit.grant",
            capability="accounts:write",
            detail=f"{member.id}:txn={txn.id}:amount={amount_cents}",
        )
        session.commit()
        return transaction_out(session, txn)

    @app.post(
        "/api/v2/members/{member_id}/credit/adjustments",
        dependencies=[Depends(require_capability("accounts:write"))],
        status_code=201,
    )
    def adjust_member_credit(
        member_id: str,
        body: AdjustBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        member = _credit_mutation(member_id, session, team.id)
        try:
            amount_cents = parse_usd_amount(body.amount_usd, allow_zero=False, allow_negative=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        note = (body.note or "").strip()
        if not note:
            raise HTTPException(status_code=400, detail="调整须填写备注")
        try:
            txn = adjust(
                session,
                member.id,
                amount_cents,
                actor_member_id=user.member.id,
                note=note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="credit.adjust",
            capability="accounts:write",
            detail=f"{member.id}:txn={txn.id}:amount={amount_cents}:note={note}",
        )
        session.commit()
        return transaction_out(session, txn)

    @app.post(
        "/api/v2/members/{member_id}/credit/transactions/{txn_id}/refund",
        dependencies=[Depends(require_capability("accounts:write"))],
        status_code=201,
    )
    def refund_member_charge(
        member_id: str,
        txn_id: int,
        body: RefundBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        member = _credit_mutation(member_id, session, team.id)
        from pulse.storage.models import CreditTransaction

        original = session.get(CreditTransaction, txn_id)
        if original is None or original.member_id != member.id:
            raise HTTPException(status_code=400, detail="只能退还扣费流水")
        try:
            txn = refund_charge(
                session,
                txn_id,
                actor_member_id=user.member.id,
                note=(body.note or "").strip() or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="credit.refund",
            capability="accounts:write",
            detail=f"{member.id}:txn={txn.id}:ref={txn_id}",
        )
        session.commit()
        return transaction_out(session, txn)

    def _statement_response(
        session: Session,
        member_id: str,
        *,
        cursor: int | None,
        limit: int,
        kind: str | None,
        from_raw: str | None,
        to_raw: str | None,
    ) -> dict:
        if kind and kind not in ("grant", "charge", "refund", "adjust"):
            raise HTTPException(status_code=400, detail="kind 无效")
        from_ts = _parse_iso_datetime(from_raw, field="from")
        to_ts = _parse_iso_datetime(to_raw, field="to")
        return statement_page(
            session,
            member_id,
            cursor=cursor,
            limit=limit,
            kind=kind,
            from_ts=from_ts,
            to_ts=to_ts,
        )

    @app.get(
        "/api/v2/members/{member_id}/credit/transactions",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def list_member_credit_transactions(
        member_id: str,
        cursor: int | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        kind: str | None = Query(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        _team_member_or_404(session, team.id, member_id)
        return _statement_response(session, member_id, cursor=cursor, limit=limit, kind=kind, from_raw=from_, to_raw=to)

    @app.get(
        "/api/v2/members/{member_id}/credit/summary",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def member_credit_summary(
        member_id: str,
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        _team_member_or_404(session, team.id, member_id)
        from_ts = _parse_iso_datetime(from_, field="from")
        to_ts = _parse_iso_datetime(to, field="to")
        if from_ts is None or to_ts is None:
            raise HTTPException(status_code=400, detail="from 与 to 均为必填")
        try:
            return credit_summary(session, member_id, from_ts=from_ts, to_ts=to_ts)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v2/members/{member_id}/credit/transactions.csv",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def export_member_credit_csv(
        member_id: str,
        kind: str | None = Query(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        session: Session = Depends(get_db),
    ):
        team, _ = team_repo_fn(session)
        member = _team_member_or_404(session, team.id, member_id)
        if kind and kind not in ("grant", "charge", "refund", "adjust"):
            raise HTTPException(status_code=400, detail="kind 无效")
        from_ts = _parse_iso_datetime(from_, field="from")
        to_ts = _parse_iso_datetime(to, field="to")
        return _csv_response(session, member.id, kind=kind, from_ts=from_ts, to_ts=to_ts)

    @app.get("/api/v2/me/membership")
    def my_membership(
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("loans:self")),
    ):
        team, _ = team_repo_fn(session)
        membership = active_membership(session, user.member.id)
        return {
            "membership": membership_out(session, membership) if membership else None,
            "balance_cents": balance_cents(session, user.member.id),
            "membership_required": membership_required_for_team(session, team.id),
        }

    @app.get("/api/v2/me/credit/transactions")
    def my_credit_transactions(
        cursor: int | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        kind: str | None = Query(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("loans:self")),
    ):
        return _statement_response(
            session,
            user.member.id,
            cursor=cursor,
            limit=limit,
            kind=kind,
            from_raw=from_,
            to_raw=to,
        )

    @app.get("/api/v2/me/credit/summary")
    def my_credit_summary(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("loans:self")),
    ):
        from_ts = _parse_iso_datetime(from_, field="from")
        to_ts = _parse_iso_datetime(to, field="to")
        if from_ts is None or to_ts is None:
            raise HTTPException(status_code=400, detail="from 与 to 均为必填")
        try:
            return credit_summary(session, user.member.id, from_ts=from_ts, to_ts=to_ts)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v2/me/credit/transactions.csv")
    def my_credit_csv(
        kind: str | None = Query(default=None),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("loans:self")),
    ):
        member = user.member
        if kind and kind not in ("grant", "charge", "refund", "adjust"):
            raise HTTPException(status_code=400, detail="kind 无效")
        from_ts = _parse_iso_datetime(from_, field="from")
        to_ts = _parse_iso_datetime(to, field="to")
        return _csv_response(session, member.id, kind=kind, from_ts=from_ts, to_ts=to_ts)
