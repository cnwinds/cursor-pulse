import pytest

from pulse.pricing.cursor_tables import builtin_cursor_pricing_table, get_cursor_pricing_table
from pulse.pricing.store import (
    load_team_cursor_pricing,
    pricing_table_from_dict,
    pricing_table_to_dict,
    reset_team_cursor_pricing,
    save_team_cursor_pricing,
    validate_pricing_payload,
)
from pulse.pricing.types import estimate_token_cost
from pulse.storage.db import init_db
from conftest import make_team


@pytest.fixture
def session():
    session_factory = init_db("sqlite:///:memory:")
    db = session_factory()
    yield db
    db.close()


def test_builtin_composer_25_input_is_half_dollar():
    table = builtin_cursor_pricing_table()
    rule = table.match_rule("composer-2.5")
    assert rule is not None
    assert rule.rates.input_no_cache == pytest.approx(0.5)


def test_composer_fast_matches_before_standard():
    table = builtin_cursor_pricing_table()
    fast = table.match_rule("composer-2.5-fast")
    std = table.match_rule("composer-2.5")
    assert fast is not None and std is not None
    assert fast.rates.input_no_cache == pytest.approx(3.0)
    assert std.rates.input_no_cache == pytest.approx(0.5)


def test_gpt_56_sol_matches_sol_rates_before_gpt_codex():
    """gpt-5.6-sol* 须命中 Sol 档，不能落到 gpt-* Codex 价。"""
    table = builtin_cursor_pricing_table()
    sol = table.match_rule("gpt-5.6-sol-max")
    sol_base = table.match_rule("gpt-5.6-sol")
    codex = table.match_rule("gpt-5.2")
    assert sol is not None and sol_base is not None and codex is not None
    assert sol.pattern == "gpt-5.6-sol*"
    assert sol.rates.input_no_cache == pytest.approx(5.0)
    assert sol.rates.input_cache_write == pytest.approx(6.25)
    assert sol.rates.cache_read == pytest.approx(0.5)
    assert sol.rates.output == pytest.approx(30.0)
    assert sol_base.rates.output == pytest.approx(30.0)
    # Codex 通用档仍给其它 gpt-*
    assert codex.rates.input_no_cache == pytest.approx(2.0)
    assert codex.rates.output == pytest.approx(10.0)


def test_estimate_gpt_56_sol_max_uses_sol_rates():
    table = builtin_cursor_pricing_table()
    # 指纹样例分量：no_cache=207, cw=140243, cr=10584886, out+reason=61374
    est = estimate_token_cost(
        model="gpt-5.6-sol-max",
        max_mode=False,
        tokens_input_no_cache=207,
        tokens_input_cache_write=140_243,
        tokens_cache_read=10_584_886,
        tokens_output=61_374,
        table=table,
    )
    assert est is not None
    assert est.pricing_rule == "gpt-5.6-sol*"
    # 同分量 @ Codex ~$2.79；@ Sol ~$8.01
    assert est.cost_usd == pytest.approx(8.011, abs=0.02)


def test_validate_and_roundtrip():
    data = pricing_table_to_dict(builtin_cursor_pricing_table())
    normalized = validate_pricing_payload(data)
    table = pricing_table_from_dict(normalized)
    assert table.version == builtin_cursor_pricing_table().version
    assert len(table.rules) == len(builtin_cursor_pricing_table().rules)


def test_team_override_loaded_by_get_cursor_pricing_table(session):
    team = make_team(session, "pricing-team")
    payload = pricing_table_to_dict(builtin_cursor_pricing_table())
    payload["version"] = "team-custom"
    payload["rules"][0]["rates"]["input_no_cache"] = 9.99
    save_team_cursor_pricing(session, team_id=team.id, data=payload, member_id=None)
    session.commit()

    table, source, _ = load_team_cursor_pricing(session, team.id)
    assert source == "override"
    assert table.version == "team-custom"
    loaded = get_cursor_pricing_table(session=session, team_id=team.id)
    assert loaded.version == "team-custom"
    assert loaded.rules[0].rates.input_no_cache == pytest.approx(9.99)

    reset_team_cursor_pricing(session, team.id)
    session.commit()
    table2, source2, _ = load_team_cursor_pricing(session, team.id)
    assert source2 == "builtin"
    assert table2.version == builtin_cursor_pricing_table().version


def test_validate_rejects_negative_rate():
    data = pricing_table_to_dict(builtin_cursor_pricing_table())
    data["fallback"]["rates"]["output"] = -1
    with pytest.raises(ValueError, match="不能为负"):
        validate_pricing_payload(data)


def test_estimate_uses_override_table():
    data = pricing_table_to_dict(builtin_cursor_pricing_table())
    data["rules"] = [
        {
            "pattern": "auto",
            "match_type": "exact",
            "pool": "auto",
            "rates": {
                "input_no_cache": 10.0,
                "input_cache_write": 10.0,
                "cache_read": 0,
                "output": 0,
            },
        }
    ]
    table = pricing_table_from_dict(data)
    est = estimate_token_cost(
        model="auto",
        max_mode=False,
        tokens_input_no_cache=1_000_000,
        tokens_input_cache_write=0,
        tokens_cache_read=0,
        tokens_output=0,
        table=table,
    )
    assert est is not None
    assert est.cost_usd == pytest.approx(10.0)
