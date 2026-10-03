from __future__ import annotations

from datetime import UTC, datetime

from pulse.proxy.membership import active_membership, effective_policy
from pulse.proxy.spend_policy import legacy_loan_rules
from pulse.storage.migrate import _migrate_legacy_spend_limits, migrate_schema
from pulse.storage.models import Base, KeyLoan, ProxyKey
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS
from sqlalchemy.orm import sessionmaker
from tests.conftest import make_team_repo, make_test_engine

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def _factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    migrate_schema(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_migration_min_merge_and_idempotent():
    sf = _factory()
    s = sf()
    team, repo = make_team_repo(s)
    member = repo.add_member("mig", "Mig")
    loan = KeyLoan(
        delivery_mode=DELIVERY_PROXY_ALIAS,
        status="active",
        borrower_member_id=member.id,
        usage_cap_rules=[{"period": "week", "pool": "auto", "limit_cents": 2000}],
    )
    loan2 = KeyLoan(
        delivery_mode=DELIVERY_PROXY_ALIAS,
        status="active",
        borrower_member_id=member.id,
        usage_cap_rules=[{"period": "week", "pool": "auto", "limit_cents": 1000}],
    )
    s.add_all([loan, loan2])
    s.flush()
    loan_id = loan.id
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(
        key_hash=kh,
        key_hint=hint,
        name="k",
        member_id=member.id,
        mode="quota",
        window_5h_cost_limit_cents=500,
    )
    s.add(key)
    s.commit()
    member_id = member.id
    key_id = key.id
    engine = s.get_bind()
    s.close()

    _migrate_legacy_spend_limits(engine)
    s = sf()
    m = active_membership(s, member_id)
    assert m is not None
    rules = effective_policy(s, m).rules
    assert any(r.period == "week" and r.pool == "auto" and r.limit_cents == 1000 for r in rules)
    assert any(r.period == "5h" and r.pool == "total" and r.limit_cents == 500 for r in rules)
    assert legacy_loan_rules(s.get(KeyLoan, loan_id)) == []
    assert s.get(ProxyKey, key_id).window_5h_cost_limit_cents is None

    _migrate_legacy_spend_limits(engine)
    s2 = sf()
    m2 = active_membership(s2, member_id)
    assert len(effective_policy(s2, m2).rules) == len(rules)
    s2.close()


def test_orphan_loan_keeps_rules():
    sf = _factory()
    s = sf()
    loan = KeyLoan(
        delivery_mode=DELIVERY_PROXY_ALIAS,
        status="active",
        usage_cap_rules=[{"period": "week", "pool": "api", "limit_cents": 1000}],
    )
    s.add(loan)
    s.commit()
    loan_id = loan.id
    engine = s.get_bind()
    s.close()
    _migrate_legacy_spend_limits(engine)
    s = sf()
    assert legacy_loan_rules(s.get(KeyLoan, loan_id))[0].pool == "api"
    s.close()


def test_pkcp_windows_untouched():
    sf = _factory()
    s = sf()
    team, repo = make_team_repo(s)
    member = repo.add_member("cp", "CP")
    from pulse.proxy.keys import generate_proxy_key

    plain, kh, hint = generate_proxy_key()
    key = ProxyKey(
        key_hash=kh,
        key_hint=hint,
        name="cpk",
        member_id=member.id,
        mode="coding_plan",
        window_5h_cost_limit_cents=999,
    )
    s.add(key)
    s.commit()
    key_id = key.id
    engine = s.get_bind()
    s.close()
    _migrate_legacy_spend_limits(engine)
    s = sf()
    row = s.get(ProxyKey, key_id)
    assert row.window_5h_cost_limit_cents == 999
    s.close()
