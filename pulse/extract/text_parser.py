from __future__ import annotations


def looks_like_usage_csv(text: str) -> bool:
    """检测粘贴文本是否为 usage-events CSV（含表头 + 至少一行数据）。"""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    header = lines[0].lower()
    if "," not in lines[0]:
        return False
    required = ("date", "model", "cost")
    return all(token in header for token in required)
