from __future__ import annotations


def _escape_cell(text: str) -> str:
    return (text or "").replace("|", "｜").replace("\n", " ")


def _fmt_int(value: int | float) -> str:
    return f"{int(value):,}"


def _fmt_usd(value: float) -> str:
    return f"${value:,.2f}"


def _fmt_mom(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"{pct:+.1f}%"


def format_monthly_report(metrics: dict) -> str:
    """从事实层 metrics 生成月报（Markdown 表格，不展示个人姓名）。"""
    period = metrics["period"]
    total_events = int(metrics.get("total_events") or 0)

    lines = [
        f"## Cursor 用量月报 · {period}",
        "",
        "### 总览",
        "",
        "| 指标 | 本期 | 环比 |",
        "| :--- | ---: | ---: |",
        f"| 请求数 | {_fmt_int(total_events)} | {_fmt_mom(metrics.get('mom_events_change_pct'))} |",
        f"| Tokens | {_fmt_int(metrics.get('total_tokens') or 0)} | {_fmt_mom(metrics.get('mom_tokens_change_pct'))} |",
        f"| 费用 (USD) | {_fmt_usd(float(metrics.get('total_cost_usd') or 0))} | {_fmt_mom(metrics.get('mom_cost_change_pct'))} |",
    ]

    ledger_total = metrics.get("account_count_ledger")
    if ledger_total is not None:
        lines.extend(
            [
                "",
                "### 台账参与",
                "",
                "> 仅统计台账内已分配主使用人的账号数量，不展示姓名。",
                "",
                "| 项目 | 数量 |",
                "| :--- | ---: |",
                f"| 台账账号 | {int(ledger_total)} |",
                f"| 本期有数据 | {int(metrics.get('account_count_submitted') or 0)} |",
                f"| 本期无数据 | {int(metrics.get('account_count_unsubmitted') or 0)} |",
            ]
        )

    events_by_model = metrics.get("events_by_model") or {}
    tokens_by_model = metrics.get("tokens_by_model") or {}
    cost_by_model = metrics.get("cost_by_model") or {}
    models = sorted(
        set(events_by_model) | set(tokens_by_model) | set(cost_by_model),
        key=lambda name: (
            -int(events_by_model.get(name, 0)),
            -int(tokens_by_model.get(name, 0)),
            name,
        ),
    )

    if models:
        lines.extend(
            [
                "",
                "### 模型用量",
                "",
                "| 模型 | 请求数 | 占比 | Tokens | 费用 (USD) |",
                "| :--- | ---: | ---: | ---: | ---: |",
            ]
        )
        denom = total_events or 1
        for model in models[:20]:
            events = int(events_by_model.get(model, 0))
            tokens = int(tokens_by_model.get(model, 0))
            cost = float(cost_by_model.get(model, 0))
            share = events / denom * 100
            lines.append(
                f"| {_escape_cell(model)} | {_fmt_int(events)} | {share:.1f}% "
                f"| {_fmt_int(tokens)} | {_fmt_usd(cost)} |"
            )

    return "\n".join(lines)
