"""从镜像文本中抽取并脱敏疑似凭证。

Phase 0 不把明文 secret 持久化到 Assistant Store；ingest（Task 5）须剥离
``secret`` 字段，仅保留 ref_id / kind / hint。完整 Secret Store 后续再做。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

# Cursor User API Key 常见形态；宁可多拦不可漏拦
_CURSOR_KEY_RE = re.compile(r"\b(crsr_[A-Za-z0-9_-]{16,})\b", re.IGNORECASE)


def redact_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """从文本抽出疑似凭证，返回脱敏正文 + secret refs（含明文，仅供 Secret Store）。"""
    refs: list[dict[str, Any]] = []

    def _repl(match: re.Match[str]) -> str:
        secret = match.group(1)
        ref_id = str(uuid.uuid4())
        hint = secret[:8] + "…" + secret[-4:] if len(secret) > 12 else "****"
        refs.append(
            {
                "ref_id": ref_id,
                "kind": "cursor_api_key",
                "secret": secret,
                "hint": hint,
            }
        )
        return f"[CURSOR_KEY:{hint}]"

    redacted = _CURSOR_KEY_RE.sub(_repl, text)
    return redacted, refs
