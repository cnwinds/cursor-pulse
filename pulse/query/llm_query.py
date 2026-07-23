from __future__ import annotations

import json
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from assistant_platform.llm.client import AssistantLlmClient
from pulse.periods import current_period
from pulse.query.engine import load_usage_dataframe


def build_llm_client_from_app_config(config: Any) -> AssistantLlmClient | None:
    llm = getattr(config, "assistant_llm", None)
    if llm is None:
        return None
    if not llm.enabled or not llm.api_key or not llm.model:
        return None
    return AssistantLlmClient(
        api_key=llm.api_key,
        model=llm.model,
        base_url=llm.base_url,
        timeout_seconds=30.0,
    )


def build_usage_context(
    session: Session,
    period: str,
    *,
    is_admin: bool,
    member_id: str | None,
    member_name: str | None,
) -> dict[str, Any]:
    df = load_usage_dataframe(session, period)
    if df.empty:
        return {
            "period": period,
            "empty": True,
            "scope": "team" if is_admin else "self",
            "actor_name": member_name,
        }

    scoped = df
    if not is_admin and member_id:
        scoped = df[df["member_id"] == member_id].copy()

    context: dict[str, Any] = {
        "period": period,
        "empty": scoped.empty,
        "scope": "team" if is_admin else "self",
        "actor_name": member_name,
        "totals": {
            "events": int(len(scoped)),
            "tokens": int(scoped["tokens_total"].sum()) if not scoped.empty else 0,
            "cost_usd": round(float(scoped["cost_usd"].sum()), 4) if not scoped.empty else 0.0,
        },
    }

    if scoped.empty:
        return context

    by_member = (
        scoped.groupby("member_name", as_index=False)
        .agg(events=("tokens_total", "count"), tokens=("tokens_total", "sum"), cost_usd=("cost_usd", "sum"))
        .sort_values("tokens", ascending=False)
    )
    by_model = (
        scoped.groupby("model", as_index=False)
        .agg(events=("tokens_total", "count"), tokens=("tokens_total", "sum"), cost_usd=("cost_usd", "sum"))
        .sort_values("tokens", ascending=False)
    )

    context["by_model"] = _records_from_df(by_model.head(20))
    if is_admin:
        context["by_member"] = _records_from_df(by_member.head(30))
    return context


def _records_from_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        item = dict(row)
        if "tokens" in item:
            item["tokens"] = int(item["tokens"])
        if "events" in item:
            item["events"] = int(item["events"])
        if "cost_usd" in item:
            item["cost_usd"] = round(float(item["cost_usd"]), 4)
        rows.append(item)
    return rows


def answer_usage_with_llm(
    session: Session,
    *,
    question: str,
    config: Any,
    member_name: str | None,
    member_id: str | None,
    is_admin: bool,
    client: AssistantLlmClient,
) -> str:
    period = current_period(config)
    context = build_usage_context(
        session,
        period,
        is_admin=is_admin,
        member_id=member_id,
        member_name=member_name,
    )

    if context.get("empty"):
        return (
            f"（{period} 暂无已确认入库的用量数据，无法回答。）\n"
            "若你刚绑定 Key 或手工上报，请稍等片刻后再问；数据入库后即可查询。"
        )

    system = (
        "你是小脉助手，根据提供的团队用量 JSON 数据回答用户问题。\n"
        "规则：\n"
        "1. 只依据数据作答，禁止编造未出现在数据中的成员、模型或数值。\n"
        "2. scope=self 时只能回答提问者本人数据，不可透露他人明细；"
        "若用户问团队排名，说明需管理员权限，并建议发送「我的」或问本人用量。\n"
        "3. scope=team 时可回答团队排名、对比、合计、模型分布等。\n"
        "4. 用简洁中文，必要时用列表；金额保留两位小数，tokens 用千分位。\n"
        "5. 数据不足以回答时，说明缺什么并给出 1～2 条可尝试的问法。"
    )
    user = (
        f"用户问题：{question.strip()}\n\n"
        f"数据：\n{json.dumps(context, ensure_ascii=False, indent=2)}"
    )
    return client.complete(system=system, user=user, temperature=0.2)


LLM_UNAVAILABLE_MESSAGE = (
    "用量自然语言查询需要启用助手大模型（管理员在后台配置 assistant_llm）。\n"
    "你也可以使用固定命令：「我的用量」「额度」「我的」「帮助」。"
)
