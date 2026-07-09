from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.periods import current_period
from pulse.storage.models import Member, QueryLog, UsageIngestion, UsageRecord


@dataclass
class QueryResult:
    answer: str
    query_plan: str
    result_summary: dict


def load_usage_dataframe(session: Session, period: str) -> pd.DataFrame:
    ingestion_ids = session.scalars(
        select(UsageIngestion.id).where(
            UsageIngestion.billing_period == period,
            UsageIngestion.status == "confirmed",
        )
    ).all()
    if not ingestion_ids:
        return pd.DataFrame()

    records = session.scalars(
        select(UsageRecord).where(UsageRecord.ingestion_id.in_(ingestion_ids))
    ).all()
    members = {m.id: m.display_name for m in session.scalars(select(Member)).all()}

    rows = [
        {
            "member_id": r.member_id,
            "member_name": members.get(r.member_id, r.member_id),
            "event_date": r.event_date.isoformat(),
            "model": r.model,
            "kind": r.kind,
            "tokens_total": r.tokens_total,
            "cost_usd": float(r.cost_usd),
            "cost_raw": r.cost_raw,
        }
        for r in records
    ]
    return pd.DataFrame(rows)


def _is_admin(user_id: str, admin_ids: set[str]) -> bool:
    return user_id in admin_ids or not admin_ids


def answer_question(
    session: Session,
    question: str,
    *,
    user_id: str,
    admin_user_ids: list[str],
    period: str | None = None,
    config=None,
) -> QueryResult:
    period = period or (current_period(config) if config else "")
    admin_ids = set(admin_user_ids)
    is_admin = _is_admin(user_id, admin_ids)
    q = question.strip().lower()

    df = load_usage_dataframe(session, period)
    if df.empty:
        return QueryResult(
            answer=f"{period} 暂无已上报数据，无法查询。",
            query_plan="empty",
            result_summary={},
        )

    member = session.scalar(select(Member).where(Member.dingtalk_user_id == user_id))
    member_name = member.display_name if member else None

    plan, summary, answer = _dispatch_query(q, df, period, is_admin, member_name, member.id if member else None)

    if member:
        session.add(
            QueryLog(
                member_id=member.id,
                question=question,
                query_plan=plan,
                result_summary=summary,
                answer=answer,
            )
        )
    return QueryResult(answer=answer, query_plan=plan, result_summary=summary)


def _dispatch_query(
    q: str,
    df: pd.DataFrame,
    period: str,
    is_admin: bool,
    member_name: str | None,
    member_id: str | None,
) -> tuple[str, dict, str]:
    prefix = f"（基于 {period} 已上报数据）\n"

    if not is_admin and member_id:
        df = df[df["member_id"] == member_id].copy()
        if df.empty:
            return "self_only", {}, prefix + "你在本账期暂无提交记录。"

    if re.search(r"(谁|哪个|哪位).*(最多|最高|最大)", q) or "排名" in q:
        if "token" in q or "tokens" in q:
            plan = "groupby member_name tokens_total sum sort"
            ranked = df.groupby("member_name")["tokens_total"].sum().sort_values(ascending=False)
        elif "花费" in q or "付费" in q or "cost" in q or "$" in q:
            plan = "groupby member_name cost_usd sum sort"
            ranked = df.groupby("member_name")["cost_usd"].sum().sort_values(ascending=False)
        else:
            plan = "groupby member_name count sort"
            ranked = df.groupby("member_name").size().sort_values(ascending=False)
        if not is_admin:
            top = ranked.head(1)
            name, val = top.index[0], top.iloc[0]
            if "token" in plan:
                ans = f"{prefix}你的 Tokens 合计：{int(val):,}"
            elif "cost" in plan:
                ans = f"{prefix}你的付费合计：${float(val):.2f}"
            else:
                ans = f"{prefix}你的请求数：{int(val):,}"
            return plan, {"self": float(val)}, ans
        lines = [prefix + "排名："]
        for i, (name, val) in enumerate(ranked.head(10).items(), 1):
            if "token" in plan:
                lines.append(f"{i}. {name} — {int(val):,} tokens")
            elif "cost" in plan:
                lines.append(f"{i}. {name} — ${float(val):.2f}")
            else:
                lines.append(f"{i}. {name} — {int(val):,} 次")
        summary = {str(k): float(v) for k, v in ranked.head(10).items()}
        return plan, summary, "\n".join(lines)

    model_match = re.search(r"(模型|model)\s*[:：]?\s*([\w\.\-\(\)]+)", q, re.I)
    if model_match or any(m.lower() in q for m in df["model"].unique() if isinstance(m, str)):
        model = model_match.group(2) if model_match else None
        if not model:
            for m in df["model"].unique():
                if str(m).lower() in q:
                    model = m
                    break
        if model:
            sub = df[df["model"].str.lower() == str(model).lower()]
            plan = f"filter model={model}"
            if sub.empty:
                return plan, {}, prefix + f"未找到模型 {model} 的记录。"
            by_member = sub.groupby("member_name").size().sort_values(ascending=False)
            if not is_admin:
                cnt = int(by_member.iloc[0]) if len(by_member) else 0
                return plan, {"count": cnt}, prefix + f"你使用 {model} 的请求数：{cnt:,}"
            top = by_member.head(5)
            lines = [prefix + f"模型 {model} 使用排名："]
            for i, (name, cnt) in enumerate(top.items(), 1):
                lines.append(f"{i}. {name} — {int(cnt):,} 次")
            return plan, {str(k): int(v) for k, v in top.items()}, "\n".join(lines)

    if "总共" in q or "合计" in q or "总量" in q:
        plan = "period_totals"
        summary = {
            "events": len(df),
            "tokens": int(df["tokens_total"].sum()),
            "cost_usd": float(df["cost_usd"].sum()),
        }
        scope = "团队" if is_admin else "你"
        ans = (
            f"{prefix}{scope}本期：{summary['events']:,} 次请求，"
            f"{summary['tokens']:,} tokens，付费 ${summary['cost_usd']:.2f}"
        )
        return plan, summary, ans

    return (
        "unsupported",
        {},
        prefix
        + "暂不支持该问法。试试：\n"
        "· 谁用得最多\n"
        "· 谁 tokens 最多\n"
        "· 模型 composer-2.5\n"
        "· 总共多少",
    )


def looks_like_query(text: str) -> bool:
    t = text.strip()
    if not t or t.startswith("/"):
        return False
    keywords = ("谁", "哪", "多少", "排名", "最多", "模型", "token", "花费", "总共", "合计", "?")
    return any(k in t.lower() for k in keywords)
