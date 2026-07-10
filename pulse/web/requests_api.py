from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pulse.tool_center.requests import AccessRequestError, AccessRequestService
from pulse.web.audit import log_admin_action


class AccessRequestCreateBody(BaseModel):
    vendor_id: str
    reason: str | None = None
    submit: bool = True


class AccessRequestDecisionBody(BaseModel):
    note: str | None = None


class AccessRequestAssignBody(BaseModel):
    account_id: str | None = None


def _request_payload(row) -> dict:
    applicant = row.applicant_member
    vendor = row.vendor
    return {
        "id": row.id,
        "applicant_member_id": row.applicant_member_id,
        "applicant_name": applicant.display_name if applicant else None,
        "vendor_id": row.vendor_id,
        "vendor_name": vendor.name if vendor else None,
        "reason": row.reason,
        "status": row.status,
        "manager_member_id": row.manager_member_id,
        "decided_by_member_id": row.decided_by_member_id,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
        "assigned_account_id": row.assigned_account_id,
        "created_at": row.created_at.isoformat(),
    }


def register_access_request_routes(app, get_db, require_capability, team_repo_fn, config):
    @app.get(
        "/api/v2/access-requests",
        dependencies=[Depends(require_capability("requests:read"))],
    )
    def list_access_requests(
        status: str | None = None,
        session: Session = Depends(get_db),
        user=Depends(require_capability("requests:read")),
    ):
        team, _ = team_repo_fn(session)
        svc = AccessRequestService(session, team.id)
        is_admin = user.member.portal_role in ("owner", "operator")
        rows = svc.list_requests(
            status=status,
            for_member_id=user.member.id,
            as_manager_id=user.member.id,
            admin_view=is_admin,
        )
        return [_request_payload(r) for r in rows]

    @app.post(
        "/api/v2/access-requests",
        dependencies=[Depends(require_capability("requests:write"))],
    )
    def create_access_request(
        body: AccessRequestCreateBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("requests:write")),
    ):
        team, _ = team_repo_fn(session)
        svc = AccessRequestService(session, team.id)
        try:
            row = svc.create_draft(
                applicant=user.member,
                vendor_id=body.vendor_id,
                reason=body.reason,
            )
            message = "草稿已保存"
            if body.submit:
                action = svc.submit(row.id, user.member)
                row = action.request
                message = action.message
            session.commit()
            return {"request": _request_payload(row), "message": message}
        except AccessRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/v2/access-requests/{request_id}/approve",
        dependencies=[Depends(require_capability("requests:approve"))],
    )
    def approve_request(
        request_id: str,
        body: AccessRequestDecisionBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("requests:approve")),
    ):
        team, _ = team_repo_fn(session)
        svc = AccessRequestService(session, team.id)
        is_admin = user.member.portal_role in ("owner", "operator")
        try:
            action = svc.approve(
                request_id,
                user.member,
                note=body.note,
                is_admin=is_admin,
            )
            session.commit()
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="access_request.approve",
                capability="requests:approve",
                detail=request_id,
            )
            session.commit()
            return {"request": _request_payload(action.request), "message": action.message}
        except AccessRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/v2/access-requests/{request_id}/reject",
        dependencies=[Depends(require_capability("requests:approve"))],
    )
    def reject_request(
        request_id: str,
        body: AccessRequestDecisionBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("requests:approve")),
    ):
        team, _ = team_repo_fn(session)
        svc = AccessRequestService(session, team.id)
        is_admin = user.member.portal_role in ("owner", "operator")
        try:
            action = svc.reject(
                request_id,
                user.member,
                note=body.note,
                is_admin=is_admin,
            )
            session.commit()
            return {"request": _request_payload(action.request), "message": action.message}
        except AccessRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/v2/access-requests/{request_id}/assign-trial",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def assign_trial(
        request_id: str,
        body: AccessRequestAssignBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        svc = AccessRequestService(session, team.id)
        try:
            action = svc.assign_trial(request_id, account_id=body.account_id)
            session.commit()
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="access_request.assign_trial",
                capability="accounts:write",
                detail=request_id,
            )
            session.commit()
            return {"request": _request_payload(action.request), "message": action.message}
        except AccessRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/v2/sync/dingtalk-directory",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def sync_directory(session: Session = Depends(get_db), user=Depends(require_capability("accounts:write"))):
        from pulse.integrations.dingtalk_directory import sync_dingtalk_directory

        team, repo = team_repo_fn(session)
        try:
            stats = sync_dingtalk_directory(repo, config)
            session.commit()
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="dingtalk.directory_sync",
                capability="accounts:write",
                detail=str(stats),
            )
            session.commit()
            return stats
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"通讯录同步失败: {exc}") from exc
