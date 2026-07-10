from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pulse.storage.models import AccessRequest, AiAccount, AiVendor, Member


class AccessRequestError(ValueError):
    pass


@dataclass
class AccessRequestAction:
    request: AccessRequest
    message: str


class AccessRequestService:
    def __init__(self, session: Session, team_id: str):
        self.session = session
        self.team_id = team_id

    def list_requests(
        self,
        *,
        status: str | None = None,
        for_member_id: str | None = None,
        as_manager_id: str | None = None,
        admin_view: bool = False,
    ) -> list[AccessRequest]:
        query = (
            select(AccessRequest)
            .options(
                joinedload(AccessRequest.applicant_member),
                joinedload(AccessRequest.vendor),
            )
            .where(AccessRequest.team_id == self.team_id)
        )
        if status:
            query = query.where(AccessRequest.status == status)
        if not admin_view and for_member_id and as_manager_id:
            query = query.where(
                (AccessRequest.applicant_member_id == for_member_id)
                | (AccessRequest.manager_member_id == as_manager_id)
            )
        elif not admin_view and for_member_id:
            query = query.where(AccessRequest.applicant_member_id == for_member_id)
        return list(self.session.scalars(query.order_by(AccessRequest.created_at.desc())))

    def get_request(self, request_id: str) -> AccessRequest | None:
        return self.session.scalar(
            select(AccessRequest)
            .options(
                joinedload(AccessRequest.applicant_member),
                joinedload(AccessRequest.vendor),
            )
            .where(AccessRequest.id == request_id, AccessRequest.team_id == self.team_id)
        )

    def create_draft(
        self,
        *,
        applicant: Member,
        vendor_id: str,
        reason: str | None = None,
    ) -> AccessRequest:
        vendor = self.session.get(AiVendor, vendor_id)
        if not vendor or not vendor.is_active:
            raise AccessRequestError("工具厂商不存在或已停用")

        existing = self.session.scalar(
            select(AccessRequest).where(
                AccessRequest.team_id == self.team_id,
                AccessRequest.applicant_member_id == applicant.id,
                AccessRequest.vendor_id == vendor_id,
                AccessRequest.status.in_(("draft", "pending_manager", "approved")),
            )
        )
        if existing:
            raise AccessRequestError("已有进行中的申请，请勿重复提交")

        now = datetime.now(timezone.utc)
        row = AccessRequest(
            team_id=self.team_id,
            applicant_member_id=applicant.id,
            vendor_id=vendor_id,
            reason=reason,
            status="draft",
            manager_member_id=applicant.manager_member_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def submit(self, request_id: str, applicant: Member) -> AccessRequestAction:
        row = self._require_request(request_id)
        if row.applicant_member_id != applicant.id:
            raise AccessRequestError("无权提交该申请")
        if row.status != "draft":
            raise AccessRequestError("仅草稿可提交")

        row.manager_member_id = applicant.manager_member_id
        row.status = "pending_manager"
        row.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return AccessRequestAction(
            row,
            f"申请已提交，等待主管审批。申请编号：{row.id[:8]}",
        )

    def approve(
        self,
        request_id: str,
        actor: Member,
        *,
        note: str | None = None,
        is_admin: bool = False,
    ) -> AccessRequestAction:
        row = self._require_request(request_id)
        if row.status != "pending_manager":
            raise AccessRequestError("申请不在待审批状态")
        if not is_admin and row.manager_member_id != actor.id:
            raise AccessRequestError("仅直属主管或管理员可审批")

        now = datetime.now(timezone.utc)
        row.status = "approved"
        row.decided_by_member_id = actor.id
        row.decided_at = now
        row.decision_note = note
        row.updated_at = now
        self.session.flush()
        return AccessRequestAction(row, "申请已通过，等待管理员分配试用账号。")

    def reject(
        self,
        request_id: str,
        actor: Member,
        *,
        note: str | None = None,
        is_admin: bool = False,
    ) -> AccessRequestAction:
        row = self._require_request(request_id)
        if row.status != "pending_manager":
            raise AccessRequestError("申请不在待审批状态")
        if not is_admin and row.manager_member_id != actor.id:
            raise AccessRequestError("仅直属主管或管理员可审批")

        now = datetime.now(timezone.utc)
        row.status = "rejected"
        row.decided_by_member_id = actor.id
        row.decided_at = now
        row.decision_note = note
        row.updated_at = now
        self.session.flush()
        return AccessRequestAction(row, "申请已拒绝。")

    def assign_trial(
        self,
        request_id: str,
        *,
        account_id: str | None = None,
    ) -> AccessRequestAction:
        row = self._require_request(request_id)
        if row.status not in ("approved",):
            raise AccessRequestError("仅已通过的申请可分配试用账号")

        account = self._pick_trial_account(row.vendor_id, account_id)
        if not account:
            raise AccessRequestError("无可用试用账号，请联系管理员补充台账")

        applicant = self.session.get(Member, row.applicant_member_id)
        if not applicant:
            raise AccessRequestError("申请人不存在")

        now = datetime.now(timezone.utc)
        account.primary_member_id = applicant.id
        account.status = "trial"
        account.shared_note = account.shared_note or f"试用分配自申请 {row.id[:8]}"
        account.updated_at = now

        row.assigned_account_id = account.id
        row.status = "trial_assigned"
        row.updated_at = now
        self.session.flush()

        return AccessRequestAction(
            row,
            f"已分配试用账号 {account.account_identifier}，请作为主使用人提交用量。",
        )

    def _pick_trial_account(self, vendor_id: str, account_id: str | None) -> AiAccount | None:
        if account_id:
            account = self.session.scalar(
                select(AiAccount).where(
                    AiAccount.id == account_id,
                    AiAccount.team_id == self.team_id,
                    AiAccount.vendor_id == vendor_id,
                )
            )
            if account and account.status in ("trial", "available", "shared"):
                if not account.primary_member_id:
                    return account
            raise AccessRequestError("指定账号不可用")

        candidates = list(
            self.session.scalars(
                select(AiAccount).where(
                    AiAccount.team_id == self.team_id,
                    AiAccount.vendor_id == vendor_id,
                    AiAccount.status.in_(("trial", "available", "shared")),
                    AiAccount.primary_member_id.is_(None),
                )
            )
        )
        return candidates[0] if candidates else None

    def _require_request(self, request_id: str) -> AccessRequest:
        row = self.get_request(request_id)
        if not row:
            raise AccessRequestError("申请不存在")
        return row
