from assistant_platform.prompts.fragments import canonical_fragments, is_business_rule_precepts_content


def test_precepts_are_persona_only_not_business_rules():
    precepts = next(f for f in canonical_fragments() if f["key"] == "precepts.md")["content"]
    assert "人设与表达" in precepts
    assert "命令格式" not in precepts
    assert not is_business_rule_precepts_content(precepts)


def test_heart_defines_persona():
    heart = next(f for f in canonical_fragments() if f["key"] == "heart.md")["content"]
    assert "小脉" in heart
    assert "function tools" in heart
