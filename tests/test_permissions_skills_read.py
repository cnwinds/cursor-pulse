import pytest

from pulse.storage.models import Member
from pulse.web.permissions import has_permission, resolve_permissions


def _member(portal_role: str) -> Member:
    return Member(
        team_id="t1",
        dingtalk_user_id=f"{portal_role}-user",
        display_name=portal_role,
        status="active",
        portal_status="active",
        portal_role=portal_role,
    )


def test_owner_and_operator_have_skills_read_permission():
    assert "assistant:skills:read" in resolve_permissions(_member("owner"))
    assert "assistant:skills:read" in resolve_permissions(_member("operator"))


@pytest.mark.parametrize(
    "capability",
    ["assistant:prompts:write", "assistant:prompts:approve"],
)
def test_retired_prompt_write_permissions_are_denied_for_owner(capability: str):
    assert not has_permission(_member("owner"), capability)
