from __future__ import annotations

from typing import Any, Callable

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult

from pulse.capabilities.handlers.cursor_key_bind import handle_cursor_key_bind
from pulse.capabilities.handlers.guide_image_update import handle_guide_image_update
from pulse.capabilities.handlers.key_loan import (
    handle_key_loan_list,
    handle_key_loan_request,
    handle_key_loan_return,
    handle_key_loan_revoke,
    handle_key_loan_self_read,
)
from pulse.capabilities.handlers.knowledge_tip import (
    handle_knowledge_tip_create,
    handle_knowledge_tip_list,
    handle_knowledge_tip_read,
)
from pulse.capabilities.handlers.quota_self_read import handle_quota_self_read
from pulse.capabilities.handlers.text_capabilities import (
    handle_alerts_run,
    handle_bot_help,
    handle_cursor_key_unbind,
    handle_members_manage,
    handle_report_publish,
    handle_submission_self_read,
    handle_submission_status_read,
    handle_usage_aggregate,
    handle_usage_export,
    handle_usage_manual_submit,
)
from pulse.capabilities.handlers.usage_query import handle_usage_query
from pulse.capabilities.handlers.usage_self_read import handle_usage_self_read
from pulse.capabilities.handlers.web_fetch import handle_web_fetch
from pulse.capabilities.handlers.web_search import handle_web_search
from pulse.capabilities.manifest import get_manifest
from pulse.capabilities.routing_metrics import record_invoke, record_missing_handler

Handler = Callable[..., CapabilityInvokeResult]

HANDLERS: dict[tuple[str, str], Handler] = {
    ("bot.help", "1"): handle_bot_help,
    ("quota.self.read", "1"): handle_quota_self_read,
    ("submission.self.read", "1"): handle_submission_self_read,
    ("submission.status.read", "1"): handle_submission_status_read,
    ("usage.self.read", "1"): handle_usage_self_read,
    ("usage.query", "1"): handle_usage_query,
    ("usage.manual.submit", "1"): handle_usage_manual_submit,
    ("usage.aggregate", "1"): handle_usage_aggregate,
    ("usage.export", "1"): handle_usage_export,
    ("cursor.key.bind", "1"): handle_cursor_key_bind,
    ("cursor.key.unbind", "1"): handle_cursor_key_unbind,
    ("report.publish", "1"): handle_report_publish,
    ("members.manage", "1"): handle_members_manage,
    ("alerts.run", "1"): handle_alerts_run,
    ("key.loan.request", "1"): handle_key_loan_request,
    ("key.loan.return", "1"): handle_key_loan_return,
    ("key.loan.self.read", "1"): handle_key_loan_self_read,
    ("key.loan.list", "1"): handle_key_loan_list,
    ("key.loan.revoke", "1"): handle_key_loan_revoke,
    ("guide_image.update", "1"): handle_guide_image_update,
    ("knowledge.tip.create", "1"): handle_knowledge_tip_create,
    ("knowledge.tip.list", "1"): handle_knowledge_tip_list,
    ("knowledge.tip.read", "1"): handle_knowledge_tip_read,
    ("web.search", "1"): handle_web_search,
    ("web.fetch", "1"): handle_web_fetch,
}


def invoke_capability(
    session: Any,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
) -> CapabilityInvokeResult:
    op = get_manifest(request.capability_key, request.capability_version)
    if op is None or op["status"] != "active":
        return CapabilityInvokeResult(
            status="failed",
            error_code="unknown_capability",
            user_message="未知或不活跃能力",
        )
    handler = HANDLERS.get((request.capability_key, request.capability_version))
    if handler is None:
        record_missing_handler(request.capability_key)
        return CapabilityInvokeResult(
            status="failed",
            error_code="handler_not_implemented",
            user_message="能力尚未实现",
        )
    record_invoke(request.capability_key, handler_kind="dedicated")
    return handler(session, request=request, config=config, op=op)
